"""Shared execution-record wording; daily snapshots are never per-run rewards."""

MODES = {"status": "状态查询", "checkin": "手动签到", "daily": "定时签到",
         "poll": "自动巡检", "growth": "成长检查", "legacy": "原脚本"}


def number(value):
    if type(value) in (int, float):
        return value
    if isinstance(value, str):
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            pass
    return None


def outcome(record, fallback="未知"):
    result = record.get("result")
    if result in {"CLAIMED", "SUCCESS"} and record.get("action") not in {"status", "growth"}:
        return "本次签到成功"
    if result == "ALREADY":
        if "自动签到查询跳过" in str(record.get("report", "")):
            return "已签，跳过查询"
        return "此前已签，未领取"
    if result == "STATUS":
        signed = record.get("today_signed")
        return "查询：已签到" if signed is True else "查询：未签到" if signed is False else "查询：状态未知"
    if record.get("action") == "growth" and result == "GROWTH":
        return "仅成长检查"
    return fallback


def checkin_credit(record):
    action, result = record.get("action"), record.get("result")
    if action in {"status", "growth"}:
        return 0
    credit = number(record.get("credit"))
    if credit is not None and (record.get("success_at") or result in {"CLAIMED", "SUCCESS"}):
        return credit
    if result in {"CLAIMED", "SUCCESS"}:
        return number(record.get("today_credit"))
    if result in {"ALREADY", "STATUS", "SKIPPED", "INACTIVE", "AUTH_HOLD", "NO_SESSION", "BUSY"}:
        return 0
    return None


def growth_credit(record):
    growth = record.get("growth")
    if isinstance(growth, dict):
        return number(growth.get("credits_gained"))
    credit = number(record.get("growth_credit"))
    if credit is not None:
        return credit
    uncertain = record.get("action") in {"growth", "legacy", None} or (
        record.get("action") in {"poll", "daily"} and record.get("result") in {"ERROR", "NETWORK", "TIMEOUT", "CANCELLED", "UNKNOWN"})
    return None if growth or uncertain else 0


def gain(value):
    if value is None:
        return "未知"
    return ("+" if value > 0 else "") + f"{value:g}"


def reward_detail(record):
    # Only this run's gains; the day's reward snapshot stays in the summary card and the raw record.
    return f"本次签到新增 {gain(checkin_credit(record))}  ·  本次成长新增 {gain(growth_credit(record))}"
