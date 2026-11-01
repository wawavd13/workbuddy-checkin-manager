"""launchd backend for the two WorkBuddy automation tasks (macOS).

Mirrors the Windows scheduler contract: identical return shapes, transactional
backups under backups/, and honest unknowns instead of guesses. The pure
configuration logic (normalize_config/_build_times) and the worker definition
stay in scheduler.py and are passed in, so this module never imports scheduler.
Importing this module never queries or changes tasks.
"""
from __future__ import annotations

import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import storage

TASK_ORDER = ("WorkBuddyAutoSignin", "WorkBuddyGrowthPoll")
TASK_LABELS = {
    "WorkBuddyAutoSignin": "com.wawavd13.workbuddy-manager.signin",
    "WorkBuddyGrowthPoll": "com.wawavd13.workbuddy-manager.poll",
}
TASK_MODES = {"WorkBuddyAutoSignin": "daily", "WorkBuddyGrowthPoll": "poll"}
LAUNCHCTL = "/bin/launchctl"
UPSTREAM_PLIST_NAME = "workbuddy-auto-signin.plist"
_LAUNCHCTL_TIMEOUT = 30
_EXIT_RE = re.compile(r"last exit code\s*=\s*(-?\d+)")
_STATE_RE = re.compile(r"^\s*state\s*=\s*(\S+)", re.MULTILINE)


def _failure(message, **details):
    return {"ok": False, "error": str(message), **details}


def _home():
    return Path.home()


def _launchctl(*args):
    """Thin launchctl wrapper so tests can stub out the binary."""
    return subprocess.run([LAUNCHCTL, *args], capture_output=True, text=True,
                          timeout=_LAUNCHCTL_TIMEOUT)


def _domain():
    # getuid only exists on POSIX; tests on other platforms stub launchctl anyway.
    if hasattr(os, "getuid"):
        return f"gui/{os.getuid()}"
    return "gui/501"


def _agents_dir():
    return _home() / "Library" / "LaunchAgents"


def _plist_path(name):
    return _agents_dir() / f"{TASK_LABELS[name]}.plist"


def _log_paths(name):
    data = storage.BASE_DIR / "data"
    return data / f"{TASK_MODES[name]}.out.log", data / f"{TASK_MODES[name]}.err.log"


def _state_path():
    return storage.BASE_DIR / "data" / "task-state.json"


def _read_state():
    try:
        value = json.loads(_state_path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_state(state):
    storage.atomic_write_json(_state_path(), state)


def _program_arguments(name, worker):
    return [worker["program"], *shlex.split(worker["arguments"][TASK_MODES[name]])]


def build_plist(name, worker, times):
    out, err = _log_paths(name)
    return {
        "Label": TASK_LABELS[name],
        "ProgramArguments": _program_arguments(name, worker),
        "StartCalendarInterval": [{"Hour": int(t[:2]), "Minute": int(t[3:])} for t in times],
        "RunAtLoad": False,
        "StandardOutPath": str(out),
        "StandardErrorPath": str(err),
    }


def _next_run(times, now=None):
    if not times:
        return None
    now = now or datetime.now()
    soonest = None
    for value in times:
        candidate = now.replace(hour=int(value[:2]), minute=int(value[3:]), second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        if soonest is None or candidate < soonest:
            soonest = candidate
    return soonest.strftime("%Y-%m-%d %H:%M:%S")


def _backup_dir(kind):
    target = storage.BASE_DIR / "backups" / f"{kind}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    target.mkdir(parents=True, exist_ok=False)
    return target


def _snapshot(backup_dir):
    """Copy current plists plus the enabled-state file into a transaction backup."""
    for name in TASK_ORDER:
        path = _plist_path(name)
        if path.exists():
            shutil.copy2(path, backup_dir / path.name)
    if _state_path().exists():
        shutil.copy2(_state_path(), backup_dir / "task-state.json")


def _bootout(name):
    # Nonzero is fine when the label is simply not loaded right now.
    _launchctl("bootout", f"{_domain()}/{TASK_LABELS[name]}")


def _bootstrap(name):
    result = _launchctl("bootstrap", _domain(), str(_plist_path(name)))
    if result.returncode != 0:
        raise RuntimeError(f"launchctl bootstrap {TASK_LABELS[name]} 失败：{result.stderr.strip() or result.stdout.strip()}")


def _loaded(label):
    return _launchctl("print", f"{_domain()}/{label}").returncode == 0


def _row_template(name):
    return {"name": name, "state": "unknown", "enabled": None, "last_run": None,
            "last_result": None, "next_run": None, "times": [], "managed": False}


def query_tasks(worker) -> list:
    state = _read_state()
    rows = []
    for name in TASK_ORDER:
        row = _row_template(name)
        path = _plist_path(name)
        try:
            if not path.exists():
                row["state"] = "missing"
                row["error"] = "任务不存在。"
            else:
                with path.open("rb") as handle:
                    data = plistlib.load(handle)
                row["times"] = sorted({
                    f"{int(item['Hour']):02d}:{int(item['Minute']):02d}"
                    for item in (data.get("StartCalendarInterval") or [])
                    if isinstance(item, dict) and "Hour" in item and "Minute" in item
                })
                arguments = data.get("ProgramArguments") or []
                row["managed"] = bool(arguments) and arguments[0] == worker["program"]
                row["recognized"] = row["managed"]
                row["enabled"] = bool(state.get(name, True))
                row["next_run"] = _next_run(row["times"]) if row["enabled"] else None
                if not row["enabled"]:
                    row["state"] = "paused"
                    row["error"] = "已暂停：任务未注册到 launchd。"
                else:
                    out = _launchctl("print", f"{_domain()}/{TASK_LABELS[name]}")
                    if out.returncode != 0:
                        row["state"] = "unknown"
                        row["error"] = "任务未加载到 launchd，可在“保存并应用”后重试。"
                    else:
                        match = _STATE_RE.search(out.stdout)
                        row["state"] = "ready" if (match and match.group(1) == "waiting") else (
                            match.group(1).lower() if match else "ready")
                        exit_match = _EXIT_RE.search(out.stdout)
                        row["last_result"] = int(exit_match.group(1)) if exit_match else None
        except (OSError, plistlib.InvalidFileException, ValueError) as exc:
            row["state"] = "unknown"
            row["error"] = str(exc)
        rows.append(row)
    return rows


def _write_plist(name, plist):
    path = _plist_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        plistlib.dump(plist, handle)
    os.replace(temporary, path)


def apply_config(validated, times, worker) -> dict:
    backup_dir = None
    try:
        backup_dir = _backup_dir("transaction")
        _snapshot(backup_dir)
    except OSError as exc:
        return _failure(f"事务备份失败，未做任何更改：{exc}")
    try:
        for name in TASK_ORDER:
            _write_plist(name, build_plist(name, worker, times.get(name, [])))
        for name in TASK_ORDER:
            _bootout(name)
        for name in TASK_ORDER:
            _bootstrap(name)
    except (OSError, RuntimeError, plistlib.InvalidFileException) as exc:
        _write_state({name: False for name in TASK_ORDER})
        return _failure(exc, transaction_backup=str(backup_dir))
    state = _read_state()
    _write_state({**{name: True for name in TASK_ORDER},
                  **{name: state[name] for name in TASK_ORDER if name in state}})
    return {"ok": True, "transaction_backup": str(backup_dir), "tasks": query_tasks(worker)}


def set_enabled(enabled, worker) -> dict:
    if not all(_plist_path(name).exists() for name in TASK_ORDER):
        return _failure("任务尚未创建，请先保存并应用。")
    try:
        state = _read_state()
        for name in TASK_ORDER:
            label = TASK_LABELS[name]
            if enabled:
                result = _launchctl("enable", f"{_domain()}/{label}")
                if result.returncode != 0:
                    raise RuntimeError(f"launchctl enable {label} 失败：{result.stderr.strip() or result.stdout.strip()}")
                if not _loaded(label):
                    _bootstrap(name)
            else:
                _bootout(name)
                result = _launchctl("disable", f"{_domain()}/{label}")
                if result.returncode != 0:
                    raise RuntimeError(f"launchctl disable {label} 失败：{result.stderr.strip() or result.stdout.strip()}")
            state[name] = enabled
        _write_state(state)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return _failure(exc, tasks=query_tasks(worker))
    return {"ok": True, "tasks": query_tasks(worker)}


def restore_original(worker) -> dict:
    upstream = _home() / "Library" / "LaunchAgents" / UPSTREAM_PLIST_NAME
    try:
        backup_dir = _backup_dir("restore")
        _snapshot(backup_dir)
    except OSError as exc:
        return _failure(f"恢复前备份失败，未做任何更改：{exc}")
    try:
        for name in TASK_ORDER:
            _bootout(name)
            path = _plist_path(name)
            if path.exists():
                path.unlink()
        state = _read_state()
        _write_state({key: value for key, value in state.items() if key not in TASK_ORDER})
    except OSError as exc:
        return _failure(exc, transaction_backup=str(backup_dir))
    upstream_restored = False
    if upstream.exists():
        result = _launchctl("bootstrap", _domain(), str(upstream))
        upstream_restored = result.returncode == 0
    return {"ok": True, "transaction_backup": str(backup_dir), "upstream_restored": upstream_restored,
            "tasks": query_tasks(worker)}


def backup_exists() -> bool:
    backups = storage.BASE_DIR / "backups"
    return backups.exists() and any(backups.iterdir())
