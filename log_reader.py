"""Tolerant legacy/event normalization; unknown amounts remain None, never fake zero."""
from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
import re

import storage

LEGACY_LOG = storage.BASE_DIR.parent / "workbuddy-auto-signin" / "signin.log"
_SUCCESS = {"CLAIMED", "SUCCESS", "ALREADY"}
_FAILED = {"ERROR", "NETWORK", "TIMEOUT", "AUTH_REJECTED", "NO_SESSION", "FORBIDDEN", "UNKNOWN"}


def _num(value):
    if type(value) in (int, float):
        return value
    if isinstance(value, str):
        try:
            return float(value) if "." in value else int(value)
        except ValueError:
            pass
    return None


def _timestamp(value):
    if not isinstance(value, str):
        return None
    value = value.strip().strip("[]")
    try:
        return datetime.fromisoformat(value).isoformat(timespec="seconds")
    except ValueError:
        return None


def _normalize(value, source, fallback=None):
    if not isinstance(value, dict):
        return None
    stamp = _timestamp(value.get("recorded_at") or value.get("timestamp") or value.get("time") or fallback)
    if stamp is None:
        return None
    result = str(value.get("result") or "UNKNOWN").upper()
    action = value.get("action") or "legacy"
    growth = value.get("growth")
    credit = value.get("today_credit")
    if credit is None and result in {"CLAIMED", "SUCCESS"}:
        credit = value.get("credit")
    normalized = {"recorded_at": stamp, "source": str(source), "action": str(action), "result": result,
                  "report": str(value.get("report") or ""), "growth": growth,
                  "today_credit": _num(credit), "credit": _num(value.get("credit")),
                  "streak_days": _num(value.get("streak_days")),
                  "total_credits": _num(value.get("total_credits")),
                  "needs_attention": bool(value.get("needs_attention", result in _FAILED)),
                  "today_signed": value.get("today_signed"),
                  "account_idhash": value.get("account_idhash"), "success_at": _timestamp(value.get("success_at"))}
    if isinstance(growth, dict):
        normalized["growth_credit"] = _num(growth.get("credits_gained"))
    else:
        normalized["growth_credit"] = _num(value.get("growth_credit", value.get("credits_gained") if action == "growth" else None))
    # Only a receipt records actual success time, never an ALREADY observation.
    if result in {"CLAIMED", "SUCCESS"} and action not in {"growth", "status"}:
        normalized["success_at"] = normalized["success_at"] or stamp
    elif result == "ALREADY":
        normalized["success_at"] = None
    return normalized


def read_records():
    records = []
    paths = [LEGACY_LOG, storage.BASE_DIR / "signin.log", storage.BASE_DIR / "data" / "events.jsonl"]
    seen_paths = set()
    for path in paths:
        path = Path(path)
        if path in seen_paths:
            continue
        seen_paths.add(path)
        try:
            lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                value = json.loads(line)
                fallback = None
            except ValueError:
                start = line.find("{")
                if start < 0:
                    continue
                fallback = line[:start].strip().strip("[]")
                # Support legacy '[timestamp] {JSON}' and 'timestamp {JSON}'.
                try:
                    value = json.loads(line[start:])
                except ValueError:
                    continue
            item = _normalize(value, path.name, fallback)
            if item:
                records.append(item)
    records.sort(key=lambda row: row["recorded_at"])
    return records


def daily_summary(records, today=None):
    today = today or date.today()
    today = today.isoformat() if isinstance(today, date) else str(today)[:10]
    rows = []
    for record in records:
        if isinstance(record, dict):
            row = _normalize(record, record.get("source", "events.jsonl"))
            if row:
                rows.append(row)
    rows.sort(key=lambda row: row["recorded_at"])
    current_identity = next((row["account_idhash"] for row in reversed(rows) if row.get("account_idhash")), None)
    if current_identity:
        rows = [row for row in rows if row.get("account_idhash") in (None, current_identity)
                and not (row["recorded_at"][:10] == today and row.get("account_idhash") is None)]
    summary = {"today_signed": None, "today_credit": None, "streak_days": None,
               "total_credits": None, "success_at": None, "last_log_at": None,
               "today_growth_credit": None, "days": {}}
    growth_values = []
    for row in rows:
        stamp, result, action = row["recorded_at"], row["result"], row["action"]
        day = stamp[:10]
        summary["last_log_at"] = stamp
        item = summary["days"].setdefault(day, {"status": "unknown", "credit": None})
        signed = result in _SUCCESS and action not in {"growth", "status"}
        if row.get("today_signed") is True:
            signed = True
        if signed:
            item["status"] = "success"
        elif result in _FAILED and item["status"] != "success" and action != "growth":
            item["status"] = "failed"
        if row["today_credit"] is not None and (signed or result == "STATUS"):
            item["credit"] = row["today_credit"]
        if day != today:
            continue
        if signed:
            summary["today_signed"] = True
        elif row.get("today_signed") is False:
            # A fresh service query can invalidate historical cache/other account rows.
            summary["today_signed"] = False
            summary["today_credit"] = None
            summary["success_at"] = None
        if row["today_credit"] is not None and (signed or (result == "STATUS" and row.get("today_signed") is not False)):
            summary["today_credit"] = row["today_credit"]
        for field in ("streak_days", "total_credits"):
            if row[field] is not None:
                summary[field] = row[field]
        if row["success_at"] and signed:
            summary["success_at"] = summary["success_at"] or row["success_at"]
        if row.get("growth_credit") is not None:
            growth_values.append(row["growth_credit"])
    if growth_values:
        summary["today_growth_credit"] = sum(growth_values)
    return summary
