"""Non-secret configuration and atomic local persistence (Python 3.12)."""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile

# Frozen exe: writable data lives beside the program on Windows and in
# ~/Library/Application Support on macOS (a .app bundle must stay read-only),
# bundled read-only resources in _MEIPASS.
FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN and sys.platform == "darwin":
    BASE_DIR = Path.home() / "Library" / "Application Support" / "WorkBuddyManager"
elif FROZEN:
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
DEFAULT_CONFIG = {
    "signin_times": ["00:05"],
    "growth_times": ["01:00", "05:00", "09:00", "13:00", "17:00", "21:00"],
    "retry_enabled": True,
    "retry_interval_minutes": 60,
    "retry_max_per_day": 7,
    "stop_after_success": True,
    "catch_up": True,
    "allow_battery": True,
    "growth": {"enabled": True, "tasks": True, "travel": True,
               "makeup": False, "redeem": False, "lottery": False, "blindbox": False},
    # Server-issued package codes are account-specific; empty means every package
    # falls back to DeductionEndTime and no package is marked as periodically refreshing.
}
STATE_DEFAULTS = {
    "last_query_at": None, "last_status": None, "confirmed_date": None,
    "success_at": None, "account_idhash": None, "last_error": None,
    "policy_date": None, "attempts_today": 0, "last_attempt_at": None,
    "auth_failure_hold": False, "last_signin_slot": None, "last_growth_slot": None,
}
_SECRET_KEYS = re.compile(r"token|authorization|password|secret|credential|(^|_)(uid|user_id|enterprise_id|tenant_id)($|_)", re.I)


def nonsecret(value):
    """Remove credential/identity fields before storing service response metadata."""
    if isinstance(value, dict):
        return {str(k): nonsecret(v) for k, v in value.items() if not _SECRET_KEYS.search(str(k))}
    if isinstance(value, list):
        return [nonsecret(v) for v in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def validate_config(config):
    """Normalize supported keys; invalid input raises ValueError (never coerces bools)."""
    if not isinstance(config, dict):
        raise ValueError("配置必须是对象")
    if set(config) - set(DEFAULT_CONFIG):
        raise ValueError("配置含不支持的字段")
    result = copy.deepcopy(DEFAULT_CONFIG)
    for key, value in config.items():
        if key == "growth":
            if not isinstance(value, dict) or set(value) - set(result[key]):
                raise ValueError("成长配置字段无效")
            for name, enabled in value.items():
                if type(enabled) is not bool:
                    raise ValueError("成长开关必须为布尔值")
                result[key][name] = enabled
        elif key in ("signin_times", "growth_times"):
            if not isinstance(value, list) or len(value) > 96:
                raise ValueError("时间必须是 HH:MM 列表")
            if any(not isinstance(t, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", t) for t in value):
                raise ValueError("时间必须是 HH:MM")
            if key == "signin_times" and not value:
                raise ValueError("至少需要一个签到时间")
            result[key] = sorted(set(value))
        elif key in ("retry_interval_minutes", "retry_max_per_day"):
            low, high = (15, 240) if key == "retry_interval_minutes" else (1, 48)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{key} 必须在 {low}..{high} 范围内")
            result[key] = value
        else:
            if type(value) is not bool:
                raise ValueError(f"{key} 必须为布尔值")
            result[key] = value
    return result


def _read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return copy.deepcopy(default)


def atomic_write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def load_config():
    try:
        return validate_config(_read_json(BASE_DIR / "data" / "config.json", DEFAULT_CONFIG))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("配置文件不可读取或格式无效") from exc


def save_config(config):
    result = validate_config(config)
    atomic_write_json(BASE_DIR / "data" / "config.json", result)
    return result


def load_state():
    try:
        value = _read_json(BASE_DIR / "data" / "state.json", STATE_DEFAULTS)
    except (OSError, ValueError):
        value = {}
    result = copy.deepcopy(STATE_DEFAULTS)
    if isinstance(value, dict):
        result.update({k: nonsecret(v) for k, v in value.items() if k in result})
    if type(result["attempts_today"]) is not int or result["attempts_today"] < 0:
        result["attempts_today"] = 0
    if type(result["auth_failure_hold"]) is not bool:
        result["auth_failure_hold"] = False
    return result


def save_state(state):
    clean = {k: nonsecret(state.get(k, v)) for k, v in STATE_DEFAULTS.items()}
    atomic_write_json(BASE_DIR / "data" / "state.json", clean)
    return clean


def append_event(record):
    (BASE_DIR / "data").mkdir(parents=True, exist_ok=True)
    with (BASE_DIR / "data" / "events.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(nonsecret(record), ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
