"""Thread-safe, standard-library bridge to the two WorkBuddy scheduled tasks.

All Windows task writes are transactional in scheduler.ps1. Importing this module
never queries or changes tasks. Call public functions from the UI worker thread.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import storage
import subprocess
import sys
import tempfile
import threading

ROOT = str(Path(__file__).resolve().parent)
PS_SCRIPT = str(storage.RESOURCE_DIR / "scheduler.ps1")
# Mirrors $Pythonw in scheduler.ps1: the per-user Python 3.12 install documented in README.
PYTHONW = str(Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
              / "Programs" / "Python" / "Python312" / "pythonw.exe")
RUNNER = str(storage.BASE_DIR / "runner.py")
CONFIG_PATH = str(storage.BASE_DIR / "data" / "config.json")
ORIGINAL_BACKUP = str(storage.BASE_DIR / "backups" / "original")
SYSTEM32 = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
POWERSHELL = str(Path(SYSTEM32) / "WindowsPowerShell" / "v1.0" / "powershell.exe")
TASK_NAMES = ("WorkBuddyAutoSignin", "WorkBuddyGrowthPoll")
DEFAULT_CONFIG = {
    "signin_times": ["00:05"],
    "growth_times": ["01:00", "05:00", "09:00", "13:00", "17:00", "21:00"],
    "retry_enabled": True, "retry_interval_minutes": 60,
    "retry_max_per_day": 7, "stop_after_success": True,
    "catch_up": True, "allow_battery": True, "growth": {"enabled": True},
}
_LOCK = threading.RLock()
_TIME = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d\Z")
_SECRET = re.compile(r"token|password|passwd|secret|credential|cookie|authorization|api.?key", re.I)


def _times(value, field, *, empty=False):
    if not isinstance(value, (list, tuple)) or (not value and not empty):
        raise ValueError(f"{field} must be a {'possibly empty ' if empty else 'nonempty '}list of HH:MM")
    if any(not isinstance(t, str) or not _TIME.fullmatch(t) for t in value):
        raise ValueError(f"{field} requires 24-hour HH:MM times")
    return sorted(set(value))


def _no_secrets(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or _SECRET.search(key):
                raise ValueError("Scheduler configuration must not contain credentials or secrets")
            _no_secrets(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _no_secrets(child)


def normalize_config(config):
    """Validate and copy configuration without mutating the caller's dictionary."""
    if not isinstance(config, dict):
        raise ValueError("config must be a dictionary")
    unknown = set(config) - set(DEFAULT_CONFIG)
    if unknown:
        raise ValueError(f"Unknown scheduler fields: {', '.join(sorted(map(str, unknown)))}")
    result = copy.deepcopy(DEFAULT_CONFIG)
    result.update(copy.deepcopy(config))
    result["signin_times"] = _times(result["signin_times"], "signin_times")
    result["growth_times"] = _times(result["growth_times"], "growth_times", empty=True)
    for key in ("retry_enabled", "stop_after_success", "catch_up", "allow_battery"):
        if type(result[key]) is not bool:
            raise ValueError(f"{key} must be bool")
    for key, lower, upper in (("retry_interval_minutes", 15, 240), ("retry_max_per_day", 1, 48)):
        value = result[key]
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError(f"{key} must be an integer in {lower}..{upper}")
    growth = result["growth"]
    if not isinstance(growth, dict) or type(growth.get("enabled", True)) is not bool:
        raise ValueError("growth must be a dictionary with a boolean enabled field")
    growth.setdefault("enabled", True)
    _no_secrets(growth)
    # Also reject objects/NaN before creating any request file.
    json.dumps(result, allow_nan=False)
    _build_times(result)
    return result


def _build_times(config):
    daily = config["signin_times"]
    poll = set(config["growth_times"] if config["growth"]["enabled"] else [])
    if config["retry_enabled"]:
        first = int(daily[0][:2]) * 60 + int(daily[0][3:])
        poll.update(f"{minute // 60:02d}:{minute % 60:02d}"
                    for minute in range(first, 1440, config["retry_interval_minutes"]))
    poll.difference_update(daily)
    if len(daily) + len(poll) >= 97:
        raise ValueError("The two tasks must have fewer than 97 total triggers")
    return {TASK_NAMES[0]: list(daily), TASK_NAMES[1]: sorted(poll)}


def build_schedule(config):
    """Return sorted, deduplicated daily/poll HH:MM grids (pure function)."""
    return _build_times(normalize_config(config))


def _failure(message, **details):
    return {"ok": False, "error": str(message), **details}


def worker_definition():
    """Scheduled-task worker handed to scheduler.ps1; the frozen exe is its own worker."""
    if storage.FROZEN:
        return {"program": sys.executable, "arguments": {"daily": "--mode daily", "poll": "--mode poll"}}
    return {"program": PYTHONW,
            "arguments": {mode: f'-I "{RUNNER}" --mode {mode}' for mode in ("daily", "poll")}}


def _call(operation, **payload):
    request = {"operation": operation, **payload}
    if storage.FROZEN:
        request["worker"] = worker_definition()
    with _LOCK:
        request_path = None
        try:
            data = storage.BASE_DIR / "data"
            data.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json",
                                             prefix="scheduler-request-", dir=data, delete=False) as stream:
                request_path = str(Path(stream.name).resolve())
                json.dump(request, stream, ensure_ascii=False, allow_nan=False)
            process = subprocess.Popen(
                [POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-File", PS_SCRIPT,
                 "-RequestFile", request_path, "-DataDir", str(data)],
                cwd=SYSTEM32, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                stdout, stderr = process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                # Killing PowerShell mid-register also kills its rollback handler.
                # Keep draining/reaping it, but return uncertainty promptly to the UI.
                threading.Thread(target=process.communicate, daemon=True).start()
                raise
            raw = stdout.lstrip("\ufeff").strip()
            try:
                result = json.loads(raw)
            except (ValueError, TypeError):
                return _failure("PowerShell did not return valid JSON", stderr=stderr.strip(),
                                returncode=process.returncode)
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                return _failure("PowerShell returned an invalid response")
            if process.returncode and result["ok"]:
                return _failure("PowerShell failed", returncode=process.returncode)
            return result
        except subprocess.TimeoutExpired:
            return _failure("Task operation timed out after 30 seconds; its completion is unknown. Query tasks before retrying.",
                            completion_unknown=True)
        except (OSError, ValueError, TypeError) as exc:
            return _failure(exc)
        finally:
            if request_path:
                try:
                    Path(request_path).unlink(missing_ok=True)
                except OSError:
                    pass


def query_tasks() -> list[dict]:
    result = _call("query")
    rows = result.get("tasks")
    if isinstance(rows, list) and len(rows) == 2 and {r.get("name") for r in rows if isinstance(r, dict)} == set(TASK_NAMES):
        return rows
    error = result.get("error", "Invalid task query response")
    return [{"name": name, "state": "unknown", "enabled": None, "last_run": None,
             "last_result": None, "next_run": None, "times": [], "managed": False,
             "error": error} for name in TASK_NAMES]


def apply_config(config) -> dict:
    try:
        validated = normalize_config(config)
    except (ValueError, TypeError) as exc:
        return _failure(exc)
    return _call("apply", config=validated, times=_build_times(validated))


def set_enabled(enabled: bool) -> dict:
    if type(enabled) is not bool:
        return _failure("enabled must be bool")
    return _call("enable", enabled=enabled)


def restore_original() -> dict:
    return _call("restore")


def backup_exists() -> bool:
    result = _call("backup_exists")
    return result.get("ok") is True and result.get("exists") is True
