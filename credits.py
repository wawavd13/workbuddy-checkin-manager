"""Read-only account credits, cached separately from check-in policy and history."""
from __future__ import annotations

from datetime import date, datetime
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import engine
import storage

SUMMARY_PATH = "/billing/meter/get-user-resource-summary"


def capacity(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        value = float(value)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(value):
        return None
    value = max(0, value)
    return int(value) if value.is_integer() else value


def parse_summary(body):
    data = engine._data(body)
    packages = data.get("Packages")
    if not isinstance(packages, list):
        raise engine.ServiceFailure("UNKNOWN", "积分汇总未返回资源包列表，不能确认余额")
    totals = {"remaining": 0, "total": 0, "used": 0}
    fields = {"remaining": "CycleRemainCapacity", "total": "CycleTotalCapacity", "used": "CycleUsedCapacity"}
    rows = []
    for package in packages:
        if not isinstance(package, dict):
            raise engine.ServiceFailure("UNKNOWN", "积分包格式无效，不能确认余额")
        row = {"code": str(package.get("PackageCode") or ""),
               **{key: capacity(package.get(field)) for key, field in fields.items()}}
        if row["remaining"] is None:
            raise engine.ServiceFailure("UNKNOWN", "积分包缺少有效剩余额度，不能确认余额")
        for key in totals:
            totals[key] = totals[key] + row[key] if totals[key] is not None and row[key] is not None else None
        rows.append(row)
    return {**totals, "packages": rows, "expiry_status": "unavailable", "expirations": []}


def parse_time(value):
    if value in (None, "", 0, "0") or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            numeric = float(value)
            result = datetime.fromtimestamp(numeric / 1000 if numeric > 100_000_000_000 else numeric).astimezone()
        else:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone()
        return result.isoformat(timespec="seconds")
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def countdown(stamp, today=None):
    """Whole local calendar days until the stamp; unknown stays unknown."""
    parsed = parse_time(stamp)
    if not parsed:
        return "—"
    days = (datetime.fromisoformat(parsed).date() - (today or date.today())).days
    if days < 0:
        return "已过期"
    return "今天" if days == 0 else "明天" if days == 1 else f"{days} 天后"


def _refreshes(cycle_end, deduction_end):
    """A quota whose current cycle ends strictly before the whole package expires is a
    periodically refreshing one (daily free credits, trial monthly quota, ...). Equal or
    missing times mean a one-off package. No account-specific package codes are needed."""
    if not cycle_end or not deduction_end:
        return False
    try:
        return datetime.fromisoformat(cycle_end) < datetime.fromisoformat(deduction_end)
    except ValueError:
        return False


def parse_accounts(accounts, kind):
    rows = []
    for account in accounts:
        if not isinstance(account, dict) or account.get("CapacityUnit") != "credits":
            continue
        remaining = capacity(account.get("CycleCapacityRemainPrecise"))
        slices = account.get("SlicePeriodUsageDetails")
        if isinstance(slices, list) and slices and isinstance(slices[0], dict):
            sliced = capacity(slices[0].get("SlicePeriodCapacityRemainPrecise"))
            if sliced is not None:
                remaining = sliced
        if remaining is None:
            remaining = capacity(account.get("CycleCapacityRemain"))
        if remaining == 0 or account.get("Status") not in (0, "0"):
            continue
        cycle_end = parse_time(account.get("CycleEndTime"))
        code = str(account.get("PackageCode") or "")
        deduction_end = parse_time(account.get("DeductionEndTime"))
        refreshes = _refreshes(cycle_end, deduction_end)
        rows.append({"code": code, "name": str(account.get("PackageName") or "积分包"),
                     "remaining": remaining, "expires_at": deduction_end or cycle_end, "cycle_end_at": cycle_end,
                     "kind": kind, "capacity_type": account.get("CapacityType"), "refreshes": refreshes})
    return rows


def query_expirations(client, packages, stamp):
    codes = [row["code"] for row in packages if row["code"]]
    if not codes:
        return {"expiry_status": "available", "expirations": [], "expiry_reconciled": True}
    rows, seen = [], set()
    for kind in ("paid", "free"):
        page = 1
        while True:
            payload = {"PageNumber": page, "PageSize": 100, "PackageCodes": codes,
                       "Status": [0, 3], "NeedRenewInfo": True}
            if kind == "free":
                payload.update(SlicePeriodStartTime=stamp[:10] + " 00:00:00", SlicePeriodEndTime=stamp[:10] + " 23:59:59")
            code, body = client._request("POST", f"/billing/meter/get-user-resource-{kind}-packages", payload)
            error = engine._failure(code, body)
            if error:
                raise error
            data = engine._data(body)
            accounts, total = data.get("Accounts"), capacity(data.get("TotalCount"))
            if not isinstance(accounts, list) or total is None:
                raise engine.ServiceFailure("UNKNOWN", "积分包明细格式未知，无法确认完整有效期")
            selected = []
            for account in accounts:
                resource = account.get("ResourceId") if isinstance(account, dict) else None
                if resource and resource in seen:
                    continue
                if resource:
                    seen.add(resource)
                selected.append(account)
            rows.extend(parse_accounts(selected, kind))
            if page * 100 >= total:
                break
            if not accounts or page >= 100:
                raise engine.ServiceFailure("UNKNOWN", "积分包明细未完整读取，无法确认全部有效期")
            page += 1
    rows.sort(key=lambda row: row.get("expires_at") or "9999")
    expected = sum(row["remaining"] for row in packages)
    observed = sum(row["remaining"] or 0 for row in rows)
    reconciled = all(row["remaining"] is not None for row in rows) and abs(expected - observed) < 0.000001
    return {"expiry_status": "available", "expirations": rows, "expiry_reconciled": reconciled}


def cache_path():
    return storage.BASE_DIR / "data" / "credits.json"


def load_cache():
    try:
        value = json.loads(cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    identity = storage.load_state().get("account_idhash")
    if identity and value.get("account_idhash") != identity:
        return {}
    return value


KEPT_FIELDS = ("remaining", "total", "used", "packages", "expirations", "expiry_status", "expiry_error", "expiry_reconciled")


def keep_last_success(result):
    """A failed query keeps the previous successful figures of the same account, marked with last_ok_at."""
    try:
        previous = json.loads(cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return result
    if not isinstance(previous, dict):
        return result
    # Caches written before last_ok_at existed: a successful query's own time is its last success.
    last = previous.get("last_ok_at") or (previous.get("queried_at") if previous.get("ok") else None)
    identity = result.get("account_idhash")
    if not last or (identity and previous.get("account_idhash") != identity):
        return result
    kept = {key: previous[key] for key in KEPT_FIELDS if key in previous}
    return {**result, **kept, "last_ok_at": last, "account_idhash": previous.get("account_idhash")}


def has_balance(value):
    """True when the cache holds figures from a successful query, current or kept."""
    return bool(value.get("ok") or value.get("last_ok_at"))


def stale_note(value):
    if value.get("ok") or not value.get("last_ok_at"):
        return ""
    return "数据来自 " + str(value["last_ok_at"]).replace("T", " ")[:19] + " 的成功查询；最近一次查询失败"


def refresh(session_loader=None, transport=None, now=None):
    client = engine.Engine(session_loader=session_loader, transport=transport, now=now)
    stamp = client.now().isoformat(timespec="seconds")
    result = {"ok": False, "queried_at": stamp, "remaining": None, "total": None, "used": None,
              "packages": [], "expirations": [], "expiry_status": "unavailable"}
    try:
        client._bind(stamp[:10])
        result["account_idhash"] = client.state.get("account_idhash")
        if "X-Enterprise-Id" in client.headers:
            raise engine.ServiceFailure("UNSUPPORTED_ACCOUNT", "企业账号不能套用个人积分汇总，请在客户端查看")
        code, body = client._request("POST", SUMMARY_PATH, {})
        error = engine._failure(code, body)
        if error:
            raise error
        result.update(parse_summary(body), ok=True, last_ok_at=stamp)
        try:
            result.update(query_expirations(client, result["packages"], stamp))
        except engine.ServiceFailure as exc:
            result.update(expiry_status="error", expiry_error=exc.report)
        except Exception:
            result.update(expiry_status="error", expiry_error="积分包有效期读取失败")
    except engine.ServiceFailure as exc:
        result.update(error=exc.report, result=exc.result)
    except Exception:
        result.update(error="读取账号积分失败，请检查客户端登录和网络", result="ERROR")
    if not result["ok"]:
        result = keep_last_success(result)
    result = client._safe(result)
    storage.atomic_write_json(cache_path(), result)
    return result


def amount(value):
    value = capacity(value)
    return "—" if value is None else f"{value:,g}"


def expiry_text(value):
    if value.get("expiry_status") != "available":
        return "积分有效期：" + str(value.get("expiry_error") or "接口尚未提供可靠到期信息（不代表永久有效）")
    rows = value.get("expirations") or []
    expired = [row for row in rows if row.get("expires_at") and not row.get("refreshes")]
    parts = []
    if expired:
        first = min(expired, key=lambda row: row["expires_at"])
        parts.append("最近到期：" + first["expires_at"].replace("T", " ")[:19] + " · " + amount(first.get("remaining"))
                     + " 分 · " + countdown(first["expires_at"]))
    refresh = [row for row in rows if row.get("refreshes") and row.get("cycle_end_at")]
    if refresh:
        first = min(refresh, key=lambda row: row["cycle_end_at"])
        parts.append("本期结束：" + first["cycle_end_at"].replace("T", " ")[:19] + " · " + amount(first.get("remaining"))
                     + " 分周期额度 · " + countdown(first["cycle_end_at"]))
    if not parts:
        parts.append("积分有效期：未返回可确认的到期时间（不代表永久有效）")
    if not value.get("expiry_reconciled"):
        parts.append("明细与余额未完全对齐，到期信息可能不完整")
    elif any(not (row.get("cycle_end_at") if row.get("refreshes") else row.get("expires_at")) for row in rows):
        parts.append("部分积分包未返回有效期，不能推断为永久有效")
    return "\n".join(parts)


def expiry_groups(value):
    grouped = {"expiry": {}, "cycle": {}}
    for row in value.get("expirations") or []:
        kind = "cycle" if row.get("refreshes") else "expiry"
        stamp = row.get("cycle_end_at") if kind == "cycle" else row.get("expires_at")
        day = stamp[:10] if isinstance(stamp, str) and parse_time(stamp) else "未知"
        item = grouped[kind].setdefault(day, {"date": day, "remaining": 0, "rows": [], "kind": kind})
        remaining = capacity(row.get("remaining"))
        item["remaining"] = item["remaining"] + remaining if item["remaining"] is not None and remaining is not None else None
        item["rows"].append(row)
    return {key: [items[day] for day in sorted(items, key=lambda day: (day == "未知", day))]
            for key, items in grouped.items()}


def expiry_detail(value):
    rows = value.get("expirations") or []
    if not rows:
        return expiry_text(value)
    lines = ["按批次查看积分有效期；周期额度的本期结束不等于整个资源包到期。"]
    for row in rows:
        time = row.get("cycle_end_at") if row.get("refreshes") else row.get("expires_at")
        name = "本期结束" if row.get("refreshes") else "到期"
        text = (time or "未知").replace("T", " ")[:19]
        lines.append(amount(row.get("remaining")) + " 分 · " + name + " " + text + " · " + row["name"])
        if row.get("refreshes"):
            resource_time = (row.get("expires_at") or "未知").replace("T", " ")[:19]
            lines.append("    资源包有效期 " + resource_time + "（不是本期积分有效期）")
    return "\n".join(lines)


if __name__ == "__main__":
    result = refresh()
    if sys.stdout is not None:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    raise SystemExit(0 if result.get("ok") else 1)
