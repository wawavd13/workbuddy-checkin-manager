"""Read-only credit consumption from the local WorkBuddy session logs; nothing is sent anywhere.

Every model call the WorkBuddy client makes is appended to ``~/.workbuddy/projects/<project>/<session>.jsonl``
with ``providerData.rawUsage.credit`` (the credits that call cost), the model name and token counts. The
server exposes no itemised consumption API for personal accounts, so these files are the only per-call record.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3


def workbuddy_home():
    override = os.environ.get("WORKBUDDY_HOME")
    return Path(override) if override else Path(os.path.expanduser("~")) / ".workbuddy"


def projects_dir(home=None):
    return Path(home or workbuddy_home()) / "projects"


def database_path(home=None):
    return Path(home or workbuddy_home()) / "workbuddy.db"


def _num(value):
    return value if type(value) in (int, float) else None


def parse_file(path, seen=None):
    """Billable calls in one session log. ``seen`` de-duplicates retries that re-log the same step."""
    seen = set() if seen is None else seen
    calls = []
    fallback_session = Path(path).stem
    with open(path, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if '"credit"' not in line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if not isinstance(record, dict):
                continue
            provider = record.get("providerData") or {}
            usage = provider.get("rawUsage") or {}
            credit = _num(usage.get("credit"))
            if credit is None or credit <= 0:
                continue
            key = (provider.get("messageId"), provider.get("stepSeq"), record.get("id"))
            if key in seen:
                continue
            seen.add(key)
            stamp = _num(record.get("timestamp"))
            when = datetime.fromtimestamp(stamp / 1000) if stamp else None
            calls.append({
                "at": when,
                "day": when.strftime("%Y-%m-%d") if when else None,
                "model": provider.get("requestModelName") or provider.get("model") or "未知模型",
                "session": record.get("sessionId") or fallback_session,
                "credit": float(credit),
                "prompt_tokens": int(_num(usage.get("prompt_tokens")) or 0),
                "completion_tokens": int(_num(usage.get("completion_tokens")) or 0),
                "cached_tokens": int(_num(usage.get("prompt_cache_hit_tokens")) or 0),
            })
    return calls


def scan(root=None):
    root = Path(root) if root else projects_dir()
    files = sorted(root.glob("*/*.jsonl")) if root.exists() else []
    seen, calls = set(), []
    for path in files:
        try:
            calls.extend(parse_file(path, seen))
        except OSError:
            continue
    return {"calls": calls, "files": len(files), "root": str(root)}


def session_titles(db_path=None):
    """Session id → title from the client's database, opened read-only; empty when unavailable."""
    path = Path(db_path) if db_path else database_path()
    if not path.exists():
        return {}
    try:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            rows = connection.execute("select id, custom_title, title from sessions").fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        return {}
    return {row[0]: str(row[1] or row[2] or "") for row in rows}


def _bucket():
    return {"credit": 0.0, "calls": 0, "prompt_tokens": 0, "completion_tokens": 0}


def aggregate(calls, titles=None, today=None):
    titles = titles or {}
    today = today or date.today()
    by_day, by_month, by_model = defaultdict(_bucket), defaultdict(_bucket), defaultdict(_bucket)
    by_session = defaultdict(lambda: dict(_bucket(), last=None))
    total, first, last = 0.0, None, None
    for call in calls:
        buckets = [by_model[call["model"]], by_session[call["session"]]]
        if call["day"]:
            buckets += [by_day[call["day"]], by_month[call["day"][:7]]]
        for bucket in buckets:
            bucket["credit"] += call["credit"]
            bucket["calls"] += 1
            bucket["prompt_tokens"] += call["prompt_tokens"]
            bucket["completion_tokens"] += call["completion_tokens"]
        total += call["credit"]
        if call["at"]:
            first = call["at"] if first is None or call["at"] < first else first
            last = call["at"] if last is None or call["at"] > last else last
            session = by_session[call["session"]]
            if session["last"] is None or call["at"] > session["last"]:
                session["last"] = call["at"]
    return {
        "total": total,
        "calls": len(calls),
        "prompt_tokens": sum(call["prompt_tokens"] for call in calls),
        "completion_tokens": sum(call["completion_tokens"] for call in calls),
        "first": first,
        "last": last,
        "today": by_day.get(today.isoformat(), _bucket()),
        "month": by_month.get(today.strftime("%Y-%m"), _bucket()),
        "days": [dict(day=day, **by_day[day]) for day in sorted(by_day, reverse=True)],
        "months": [dict(month=month, **by_month[month]) for month in sorted(by_month, reverse=True)],
        "models": [dict(model=model, **by_model[model]) for model in sorted(by_model, key=lambda m: -by_model[m]["credit"])],
        "sessions": [dict(session=sid, title=titles.get(sid, ""), **by_session[sid])
                     for sid in sorted(by_session, key=lambda s: -by_session[s]["credit"])],
    }


def load(root=None, db_path=None):
    """Scan plus aggregate; safe to run on a worker thread, touches nothing but local files."""
    scanned = scan(root)
    summary = aggregate(scanned["calls"], session_titles(db_path))
    summary.update(files=scanned["files"], root=scanned["root"],
                   loaded_at=datetime.now().isoformat(timespec="seconds"))
    return summary


def month_series(summary, today=None):
    """Every month from the first record to now, zero-filled, oldest first."""
    months = {m["month"]: m["credit"] for m in summary.get("months", [])}
    if not months:
        return []
    today = today or date.today()
    last = max(max(months), today.strftime("%Y-%m"))
    year, month = (int(part) for part in min(months).split("-"))
    series = []
    while True:
        key = f"{year:04d}-{month:02d}"
        series.append((key, months.get(key, 0.0)))
        if key >= last:
            return series
        month += 1
        if month > 12:
            year, month = year + 1, 1


def day_series(summary, today=None, count=30):
    """The last `count` calendar days ending today, zero-filled, oldest first."""
    days = {d["day"]: d["credit"] for d in summary.get("days", [])}
    today = today or date.today()
    keys = [(today - timedelta(days=offset)).isoformat() for offset in range(count - 1, -1, -1)]
    return [(key, days.get(key, 0.0)) for key in keys]


def top_models(models, limit=10):
    """Ranked models; everything past the limit folds into one 其他 row instead of more bars."""
    if len(models) <= limit:
        return list(models)
    rest = models[limit - 1:]
    other = {"model": f"其他 {len(rest)} 个模型", "credit": sum(m["credit"] for m in rest), "calls": sum(m["calls"] for m in rest),
             "prompt_tokens": sum(m["prompt_tokens"] for m in rest), "completion_tokens": sum(m["completion_tokens"] for m in rest)}
    return list(models[:limit - 1]) + [other]


def credit_text(value):
    return f"{value:,.2f}"


def token_text(value):
    """Tokens in millions (M); small counts fall back to K so they do not round to 0.00M."""
    value = float(value)
    if value >= 10_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{int(value):,}"


def tokens_m(value):
    return float(value) / 1_000_000
