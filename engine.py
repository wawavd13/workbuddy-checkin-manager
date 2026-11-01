"""Reviewed, dependency-injected check-in/growth engine. Never executes upstream CLI."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import importlib.util
import json
import os
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

import storage

KNOWN_ENDPOINTS = frozenset({"https://copilot.tencent.com"})
# Scheduled tasks fire on whole minutes, so an hourly poll can land a second before the
# previous attempt's timestamp; without slack the retry slips a whole extra interval.
RETRY_SLACK_SECONDS = 120
_AUTH = None


class Cancelled(Exception):
    pass


class ServiceFailure(Exception):
    def __init__(self, result, report):
        self.result, self.report = result, report
        super().__init__(report)


def validate_endpoint(endpoint):
    if not isinstance(endpoint, str):
        raise ServiceFailure("UNSAFE_ENDPOINT", "服务地址不是受信任的腾讯 HTTPS 地址")
    parsed = urllib.parse.urlsplit(endpoint)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in KNOWN_ENDPOINTS or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ServiceFailure("UNSAFE_ENDPOINT", "服务地址不是受信任的腾讯 HTTPS 地址")
    return origin


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Reject all redirects, including same-origin: authenticated requests never follow Location.
        return None


def https_transport(method, url, headers, payload=None):
    parsed = urllib.parse.urlsplit(url)
    validate_endpoint(f"{parsed.scheme}://{parsed.netloc}")
    resource_summary = parsed.path in {"/billing/meter/get-user-resource-summary",
                                       "/billing/meter/get-user-resource-paid-packages",
                                       "/billing/meter/get-user-resource-free-packages"} and method == "POST"
    if not (resource_summary or parsed.path.startswith(("/v2/billing/meter/", "/v2/activity/growth/"))) or parsed.fragment:
        raise ServiceFailure("UNSAFE_ENDPOINT", "拒绝未支持的接口路径")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(request, timeout=25) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                return 502, {}
            try:
                return response.status, json.loads(raw)
            except (ValueError, UnicodeError):
                return 502, {}
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read(2_000_000))
        except (ValueError, UnicodeError):
            return exc.code, {}
    except (OSError, urllib.error.URLError):
        return -1, {}


def _auth_backend():
    global _AUTH
    if _AUTH is None:
        # Only our reviewed declaration adapter is loaded, not the downloaded upstream module.
        path = storage.RESOURCE_DIR / "vendor" / "auth_backend.py"
        spec = importlib.util.spec_from_file_location("workbuddy_reviewed_auth", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _AUTH = module
    return _AUTH


def load_local_session():
    auth = _auth_backend()
    path, _ = auth.find_auth_file()
    if path is None:
        raise ServiceFailure("NO_SESSION", "未找到客户端登录会话，请先登录 WorkBuddy")
    session = auth.load_session_retry(path)
    validate_endpoint((session.get("auth") or {}).get("endpoint") or auth.DEFAULT_ENDPOINT)
    return auth.resolve_session(session)


def _data(body):
    if not isinstance(body, dict):
        return {}
    return body["data"] if isinstance(body.get("data"), dict) else body


def _number(value):
    return value if type(value) in (int, float) else None


def _failure(code, body):
    if code == 401:
        return ServiceFailure("AUTH_REJECTED", "认证被拒绝，请重新登录；自动重试暂停直到手动操作")
    if code == 403:
        return ServiceFailure("FORBIDDEN", "服务端拒绝操作，请核对账号权限")
    if code == -1:
        return ServiceFailure("NETWORK", "网络请求失败，等待下一重试时点")
    if not 200 <= code < 300:
        return ServiceFailure("ERROR", f"服务请求失败（HTTP {code}）")
    if isinstance(body, dict) and body.get("code") not in (None, 0, "0", 200):
        return ServiceFailure("ERROR", "服务返回业务错误，未确认操作成功")
    return None


class Engine:
    def __init__(self, config=None, session_loader=None, transport=None, now=None,
                 cancel_check=None, stage_callback=None):
        self.config = storage.validate_config(config) if config is not None else storage.load_config()
        self.session_loader = session_loader or load_local_session
        self.transport = transport or https_transport
        self.now = now or (lambda: datetime.now().astimezone())
        self.cancel_check = cancel_check or (lambda: False)
        self.stage_callback = stage_callback or (lambda stage: None)
        self.state = storage.load_state()
        self.headers = {}
        self.endpoint = None
        self.secrets = set()

    def _safe(self, value):
        value = storage.nonsecret(value)
        if isinstance(value, str):
            for secret in sorted(self.secrets, key=len, reverse=True):
                value = re.sub(re.escape(secret), "[redacted]", value)
            return value
        if isinstance(value, dict):
            return {k: self._safe(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._safe(v) for v in value]
        return value

    def _stage(self, name):
        if self.cancel_check():
            raise Cancelled()
        self.stage_callback(name)

    def _request(self, method, path, payload=None):
        self._stage(path)
        return self.transport(method, self.endpoint + path, self.headers, payload)

    def _bind(self, today):
        self._stage("authentication")
        session = self.session_loader()
        if not isinstance(session, dict):
            raise ServiceFailure("NO_SESSION", "客户端会话格式无效")
        auth, account = session.get("auth") or {}, session.get("account") or {}
        uid, token = account.get("uid"), auth.get("accessToken")
        for value in (uid, token, account.get("enterpriseId"), auth.get("domain")):
            if isinstance(value, str) and value:
                self.secrets.add(value)
        self.endpoint = validate_endpoint(auth.get("endpoint") or "https://copilot.tencent.com")
        if not isinstance(uid, str) or not uid or not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token):
            raise ServiceFailure("NO_SESSION", "客户端登录会话无效")
        identity = hashlib.sha256(json.dumps([self.endpoint, uid, account.get("enterpriseId"), auth.get("domain")], separators=(",", ":")).encode()).hexdigest()
        if self.state.get("account_idhash") != identity:
            self.state = dict(storage.STATE_DEFAULTS)
            self.state.update(account_idhash=identity, policy_date=today)
        elif self.state.get("policy_date") != today:
            hold = self.state.get("auth_failure_hold", False)
            self.state = dict(storage.STATE_DEFAULTS)
            self.state.update(account_idhash=identity, policy_date=today, auth_failure_hold=hold)
        elif self.state.get("confirmed_date") != today:
            self.state.update(confirmed_date=None, success_at=None)
            previous = self.state.get("last_query_at") or ""
            if not isinstance(previous, str) or not previous.startswith(today):
                self.state.update(last_status=None, last_query_at=None)
        self.headers = {"Accept": "application/json", "Content-Type": "application/json",
                        "Authorization": f"Bearer {token}", "X-User-Id": uid, "User-Agent": "WorkBuddy"}
        for key, names in (("enterpriseId", ("X-Enterprise-Id", "X-Tenant-Id")),):
            value = account.get(key)
            if value:
                for name in names:
                    self.headers[name] = value
        if auth.get("domain"):
            self.headers["X-Domain"] = auth["domain"]

    def _query(self, stamp):
        code, body = self._request("POST", "/v2/billing/meter/checkin-activity-status")
        self.state["last_query_at"] = stamp
        error = _failure(code, body)
        if error:
            self.state["last_status"] = None
            raise error
        data = _data(body)
        self.state["last_status"] = self._safe(data)
        signed = data.get("today_checked_in")
        if signed in (True, 1):
            self.state["confirmed_date"] = stamp[:10]
        else:
            self.state.update(confirmed_date=None, success_at=None)
        return data

    @staticmethod
    def _metrics(data):
        credit = data.get("today_credit", data.get("daily_credit"))
        return {"today_credit": _number(credit), "streak_days": _number(data.get("streak_days")),
                "total_credits": _number(data.get("total_credits")), "balance_status": "unavailable"}

    def _checkin(self, stamp, status_only=False):
        data = self._query(stamp)
        result = {"result": "STATUS", "report": "已查询服务端签到状态", "status": self._safe(data),
                  "today_signed": data.get("today_checked_in") in (True, 1) if "today_checked_in" in data else None,
                  **self._metrics(data)}
        if status_only:
            if not result["today_signed"]:
                result["today_credit"] = None
            return result
        if data.get("today_checked_in") in (True, 1):
            result.update(result="ALREADY", report="今日已签；实际领取时间未知" if not self.state.get("success_at") else "今日已签", today_signed=True)
            return result
        if data.get("active") is False:
            result.update(result="INACTIVE", report="签到活动未开启", today_signed=None, today_credit=None)
            return result
        if data.get("active") not in (True, 1) or data.get("today_checked_in") not in (False, 0):
            raise ServiceFailure("UNKNOWN", "签到活动或今日状态未知，未发送领取请求")
        code, body = self._request("POST", "/v2/billing/meter/daily-checkin")
        # Explicit idempotency response only; unrelated 400/500 null is not success.
        already = (200 <= code < 300 and body is None) or (code == 400 and isinstance(body, dict) and body.get("code") == 10001)
        if already:
            self.state["confirmed_date"] = stamp[:10]
            try:
                data = self._query(stamp)
            except ServiceFailure:
                self.state["confirmed_date"] = stamp[:10]
                data = {}
            result.update(result="ALREADY", report="服务端确认今日已签；实际领取时间未知", today_signed=True,
                          **self._metrics(data))
            return result
        error = _failure(code, body)
        if error:
            raise error
        credit = _number(_data(body).get("credit"))
        if credit is None:
            raise ServiceFailure("UNKNOWN", "签到响应未确认领取成功，请手动刷新状态")
        self.state.update(confirmed_date=stamp[:10], success_at=stamp)
        try:
            data = self._query(stamp)
        except ServiceFailure:
            data = {}
        # Receipt itself is authoritative, even when following status is stale/unavailable.
        self.state.update(confirmed_date=stamp[:10], success_at=stamp)
        result.update(result="CLAIMED", report=f"成功领取 {credit} 积分", today_signed=True,
                      **self._metrics(data), credit=credit, success_at=stamp)
        result["today_credit"] = credit
        return result

    def _slot(self, times, now, last):
        due = [f"{now.date().isoformat()}T{time}" for time in times
               if (time <= now.strftime("%H:%M") if self.config["catch_up"] else time == now.strftime("%H:%M"))]
        due = [slot for slot in due if not isinstance(last, str) or slot > last]
        return max(due) if due else None

    def _poll_slots(self, now):
        signin = self._slot(self.config["signin_times"], now, self.state.get("last_signin_slot"))
        growth = self._slot(self.config["growth_times"], now, self.state.get("last_growth_slot")) if self.config["growth"]["enabled"] else None
        attempted = self.state.get("attempts_today", 0)
        retry_due = False
        if attempted and self.config["retry_enabled"] and attempted <= self.config["retry_max_per_day"]:
            try:
                last = datetime.fromisoformat(self.state["last_attempt_at"])
                interval = self.config["retry_interval_minutes"] * 60 - RETRY_SLACK_SECONDS
                retry_due = now.timestamp() - last.timestamp() >= interval
            except (ValueError, TypeError):
                retry_due = True
        # Every automatic attempt respects the configured interval, including added initial slots.
        if attempted:
            if attempted > self.config["retry_max_per_day"] or not self.config["retry_enabled"] or not retry_due:
                signin = None
                retry_due = False
        # Union growth-only slots must not accidentally trigger an early initial check-in.
        return signin, growth, retry_due

    def run(self, mode):
        if mode not in {"status", "checkin", "growth", "daily", "poll"}:
            raise ValueError("不支持的运行模式")
        now = self.now()
        stamp, today = now.isoformat(timespec="seconds"), now.date().isoformat()
        record = {"recorded_at": stamp, "action": mode, "result": "SKIPPED", "report": "当前无到期操作",
                  "growth": None, "needs_attention": False, "today_credit": None,
                  "streak_days": None, "total_credits": None, "today_signed": None}
        code = 0
        try:
            if self.state.get("policy_date") != today:
                # Clear day-bound data before any attempt, including authentication failures.
                identity, hold = self.state.get("account_idhash"), self.state.get("auth_failure_hold", False)
                self.state = dict(storage.STATE_DEFAULTS)
                self.state.update(policy_date=today, account_idhash=identity, auth_failure_hold=hold)
            automatic = mode in {"daily", "poll"}
            if not automatic:
                self.state["auth_failure_hold"] = False
            self._bind(today)
            if automatic and self.state.get("auth_failure_hold"):
                raise ServiceFailure("AUTH_HOLD", "自动操作暂停，请重新登录后手动刷新")
            signin_slot, growth_slot, retry_due = self._poll_slots(now) if automatic else (None, None, False)
            checkin_due = mode in ("status", "checkin") or bool(signin_slot or retry_due)
            # A daily task is an initial attempt even when launched a minute after its exact slot.
            if mode == "daily" and self.state["attempts_today"] == 0:
                checkin_due = True
            growth_due = self.config["growth"]["enabled"] and (mode == "growth" or bool(growth_slot))
            if automatic and self.config["stop_after_success"] and self.state.get("confirmed_date") == today:
                checkin_due = False
                record.update(result="ALREADY", report="已确认今日签到，自动签到查询跳过", today_signed=True,
                              **self._metrics(self.state.get("last_status") or {}))
            if checkin_due:
                if signin_slot:
                    self.state["last_signin_slot"] = signin_slot
                if mode != "status":
                    self.state["attempts_today"] += 1
                    self.state["last_attempt_at"] = stamp
                record.update(self._checkin(stamp, status_only=mode == "status"))
            if growth_due:
                if growth_slot:
                    # Coalesce overdue slots once; even a failed slot is not retried at every retry minute.
                    self.state["last_growth_slot"] = growth_slot
                    record["growth_slot"] = growth_slot
                growth = self.run_growth()
                record["growth"] = growth
                if mode == "growth":
                    record.update(result=growth["result"], report=growth["report"])
                if growth.get("needs_attention"):
                    code = 1
                    record["needs_attention"] = True
            self.state["last_error"] = None if not code else "成长中心部分操作失败"
        except Cancelled:
            code = 2
            record.update(result="CANCELLED", report="当前操作已协作停止；未终止客户端", needs_attention=False)
        except ServiceFailure as exc:
            code = 1
            record.update(result=exc.result, report=exc.report, needs_attention=True)
            self.state["last_error"] = exc.report
            if exc.result in {"AUTH_REJECTED", "NO_SESSION", "AUTH_HOLD"}:
                self.state["auth_failure_hold"] = True
        except Exception as exc:
            # No raw exception text: filesystem errors/helper output can contain session data.
            # WB_DEBUG=1 appends the exception type only, never message payloads.
            code = 1
            result = getattr(exc, "result", "ERROR")
            report = "本地认证或运行失败，请检查客户端登录和配置"
            if os.environ.get("WB_DEBUG"):
                report += f"（{type(exc).__name__}，见本机日志定位）"
            record.update(result=result, report=report, needs_attention=True)
            self.state["last_error"] = record["report"]
            if result in {"NO_SESSION", "AUTH_REJECTED"}:
                self.state["auth_failure_hold"] = True
            if os.environ.get("WB_DEBUG"):
                import traceback as _traceback
                try:
                    (storage.BASE_DIR / "data").mkdir(parents=True, exist_ok=True)
                    with (storage.BASE_DIR / "data" / "debug-traceback.log").open("a", encoding="utf-8") as handle:
                        handle.write(f"{record.get('recorded_at')} {result}\n{_traceback.format_exc()}\n")
                except OSError:
                    pass
        if self.state.get("account_idhash"):
            record["account_idhash"] = self.state["account_idhash"]
        record = self._safe(record)
        storage.save_state(self._safe(self.state))
        return code, record

    def run_growth(self):
        """Independent guards surround BOTH reads and mutations for every feature."""
        options = self.config["growth"]
        out = {"result": "GROWTH", "report": "成长中心无可领取项", "credits_gained": 0,
               "needs_attention": False, "features": {}}
        if not options["enabled"]:
            out.update(result="DISABLED", report="成长中心已关闭")
            return out
        base = "/v2/activity/growth"
        reports = []
        feature_names = {"travel": "旅行", "tasks": "成长任务奖励", "makeup": "补登",
                         "redeem": "连登兑换", "lottery": "抽奖", "blindbox": "Buddy 盲盒"}

        def request(method, path, payload=None):
            code, body = self._request(method, base + path, payload)
            error = _failure(code, body)
            if error:
                raise error
            return _data(body)

        def gain(data, *keys):
            for key in keys:
                credit = _number(data.get(key))
                if credit is not None:
                    out["credits_gained"] += credit
                    return

        travel = None
        for feature in ("travel", "tasks", "makeup", "redeem", "lottery", "blindbox"):
            if not options[feature]:
                out["features"][feature] = "disabled"
                continue
            try:
                self._stage("growth:" + feature)
                if feature == "travel":
                    travel = request("GET", "/buddy/travel/status")
                    if travel.get("state") == "arrived" and travel.get("record_id"):
                        claimed = request("POST", "/buddy/travel/claim", {"record_id": travel["record_id"]})
                        gain(claimed, "reward_credit")
                        # Only an explicit reward receipt permits departure after claiming.
                        if _number(claimed.get("reward_credit")) is not None:
                            travel = dict(travel, state="idle")
                elif feature == "tasks":
                    tasks = request("GET", "/tasks").get("tasks", [])
                    tasks = [t for t in tasks if isinstance(t, dict)] if isinstance(tasks, list) else []
                    valid = [t for t in tasks if isinstance(t.get("task_code"), str)
                             and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", t["task_code"]) and not t.get("locked")]
                    pending = [t["task_code"] for t in valid if t.get("accept_status") == "not_accepted"]
                    for offset in range(0, len(pending), 20):
                        accepted = request("POST", "/tasks/accept", {"task_codes": pending[offset:offset + 20]})
                        if any(isinstance(r, dict) and r.get("status") == "error" for r in accepted.get("results", [])):
                            raise ServiceFailure("ERROR", "部分任务接单失败")
                    for task in valid:
                        if task.get("accept_status") == "completed":
                            claimed = request("POST", f"/tasks/{task['task_code']}/claim", {})
                            if not claimed.get("already_claimed"):
                                gain(claimed, "credit")
                elif feature == "makeup":
                    streak = request("GET", "/streak")
                    cards = streak.get("makeup_cards")
                    cards = cards.get("balance") if isinstance(cards, dict) else cards
                    if _number(cards) is not None and cards > 0:
                        heatmap = request("GET", "/heatmap")
                        # Reuse reviewed server-verified calendar candidate algorithm, never infer dates.
                        dates = _auth_backend()._makeup_candidates({"data": streak}, {"data": heatmap})
                        if dates:
                            request("POST", "/makeup-cards/use", {"target_date": dates[0]})
                elif feature == "redeem":
                    summary = request("GET", "/redeem/summary")
                    for tier, prefix in (("7d", "starter"), ("14d", "advanced"), ("28d", "legendary")):
                        status = summary.get(prefix + "_status")
                        if status and status not in ("locked", "claimed"):
                            import uuid
                            code, body = self._request("POST", base + "/redeem", {"tier": tier, "client_token": "u_" + uuid.uuid4().hex})
                            if code == 403 and _auth_backend()._is_tier_locked(code, body):
                                continue
                            error = _failure(code, body)
                            if error:
                                raise error
                            gain(_data(body), "credit_granted")
                elif feature == "lottery":
                    chances = request("GET", "/lottery/chances")
                    if (_number(chances.get("balance")) or 0) > 0:
                        import uuid
                        prize = request("POST", "/lottery/draw", {"client_token": "u_" + uuid.uuid4().hex})
                        gain(prize, "credit", "credit_granted")
                        if prize.get("need_address") or prize.get("require_address"):
                            reports.append("实物奖需到成长中心填写收件信息")
                            out["requires_address"] = True
                elif feature == "blindbox":
                    # Read-only prerequisite allowed with travel disabled; never claim/depart here.
                    if travel is None:
                        travel = request("GET", "/buddy/travel/status")
                    if travel.get("state") != "idle":
                        out["features"][feature] = "blocked_travel"
                        continue
                    quota = request("GET", "/buddy/quota")
                    affordable = _number(quota.get("affordable")) or 0
                    maximum = _number(quota.get("max_open_count")) or 1
                    count = int(min(affordable, maximum))
                    if count > 0:
                        import uuid
                        request("POST", "/buddy/open", {"count": count, "client_token": "u_" + uuid.uuid4().hex})
                out["features"][feature] = "checked"
                reports.append(feature_names[feature] + "已检查")
            except ServiceFailure as exc:
                out["features"][feature] = "failed"
                out["needs_attention"] = True
                reports.append(feature_names[feature] + "操作失败")
                if exc.result in {"AUTH_REJECTED", "FORBIDDEN"}:
                    self.state["auth_failure_hold"] = exc.result == "AUTH_REJECTED"
                    out["result"] = exc.result
                    break
        # Match upstream section 6b: departure AFTER task/energy blindbox processing.
        if options["travel"] and isinstance(travel, dict) and travel.get("state") == "idle" and not travel.get("daily_limit_reached") and out["result"] not in {"AUTH_REJECTED", "FORBIDDEN"}:
            try:
                locations = request("GET", "/buddy/travel/config").get("locations")
                if isinstance(locations, list) and locations and isinstance(locations[0], dict) and locations[0].get("id"):
                    code, body = self._request("POST", base + "/buddy/travel/depart", {"location_id": locations[0]["id"]})
                    # Upstream recognizes this 403 as a normal prerequisite, not authentication failure.
                    msg = str(_data(body).get("msg") or "").strip().lower()
                    if code == 403 and msg == "no active buddy":
                        reports.append("尚未领养 Buddy，未派出")
                    else:
                        error = _failure(code, body)
                        if error:
                            raise error
            except ServiceFailure as exc:
                out["features"]["travel"] = "failed"
                out["needs_attention"] = True
                reports.append("旅行派出失败")
                if exc.result == "AUTH_REJECTED":
                    self.state["auth_failure_hold"] = True
        if reports:
            out["report"] = "；".join(reports)
        return out
