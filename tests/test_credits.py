"""Credits reads use mock authentication/API and never alter check-in policy."""
import copy
from datetime import date, datetime
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import credits
import engine
import storage


def session(enterprise=None):
    return {"auth": {"accessToken": "mock-token", "endpoint": "https://copilot.tencent.com"},
            "account": {"uid": "mock-user", **({"enterpriseId": enterprise} if enterprise else {})}}


def summary():
    return {"data": {"Packages": [{"PackageCode": "a", "CycleRemainCapacity": "4306", "CycleTotalCapacity": "5000", "CycleUsedCapacity": "694"},
                                   {"PackageCode": "b", "CycleRemainCapacity": 1500, "CycleTotalCapacity": 1500, "CycleUsedCapacity": 0}]}}


class CreditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.patch = patch.object(storage, "BASE_DIR", self.base)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_summary_remaining_not_cycle_total(self):
        value = credits.parse_summary(summary())
        self.assertEqual(value["remaining"], 5806)
        self.assertEqual(value["total"], 6500)
        self.assertEqual(value["used"], 694)
        self.assertEqual(credits.amount(value["remaining"]), "5,806")

    def test_missing_values_never_fake_zero(self):
        for body in ({}, {"data": {"Packages": None}}, {"data": {"Packages": [{}]}},
                     {"data": {"Packages": [{"CycleRemainCapacity": "nan"}]}},
                     {"data": {"Packages": [{"CycleRemainCapacity": True}]}}):
            with self.assertRaises(engine.ServiceFailure):
                credits.parse_summary(body)
        value = credits.parse_summary({"data": {"Packages": []}})
        self.assertEqual(value["remaining"], 0)
        self.assertEqual(credits.amount(None), "—")

    def test_refresh_only_reads_and_caches_without_policy_mutation(self):
        state = copy.deepcopy(storage.STATE_DEFAULTS)
        state.update(confirmed_date="2026-10-30", attempts_today=3, auth_failure_hold=True)
        storage.save_state(state)
        before = (self.base / "data/state.json").read_bytes()
        calls = []
        def transport(method, url, headers, payload):
            calls.append((method, url, payload))
            return (200, summary()) if url.endswith("get-user-resource-summary") else (200, {"data": {"Accounts": [], "TotalCount": 0}})
        value = credits.refresh(session_loader=session, transport=transport)
        self.assertTrue(value["ok"])
        self.assertEqual(calls[0], ("POST", "https://copilot.tencent.com" + credits.SUMMARY_PATH, {}))
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(method == "POST" and "get-user-resource-" in url for method, url, payload in calls))
        self.assertEqual((self.base / "data/state.json").read_bytes(), before)
        self.assertFalse((self.base / "data/events.jsonl").exists())
        raw = (self.base / "data/credits.json").read_text()
        self.assertNotIn("mock-token", raw)
        self.assertNotIn("mock-user", raw)

    def test_failure_keeps_last_success_marked(self):
        first = datetime(2026, 11, 1, 2, 5, 6).astimezone()
        later = datetime(2026, 11, 1, 3, 5, 2).astimezone()
        credits.refresh(session_loader=session, transport=lambda *args: (200, summary()), now=lambda: first)
        value = credits.refresh(session_loader=session, transport=lambda *args: (401, {}), now=lambda: later)
        self.assertFalse(value["ok"])
        cached = credits.load_cache()
        self.assertEqual((cached["remaining"], cached["total"], cached["used"]), (5806, 6500, 694))
        self.assertEqual(cached["last_ok_at"], first.isoformat(timespec="seconds"))
        self.assertEqual(cached["queried_at"], later.isoformat(timespec="seconds"))
        self.assertTrue(cached["error"])
        self.assertTrue(credits.has_balance(cached))
        self.assertIn("最近一次查询失败", credits.stale_note(cached))
        again = credits.refresh(session_loader=session, transport=lambda *args: (500, {}), now=lambda: later)
        self.assertEqual((again["remaining"], again["last_ok_at"]), (5806, first.isoformat(timespec="seconds")))

    def test_failure_never_keeps_another_accounts_balance(self):
        credits.refresh(session_loader=session, transport=lambda *args: (200, summary()))
        other = lambda: {"auth": {"accessToken": "other-token", "endpoint": "https://copilot.tencent.com"},
                         "account": {"uid": "other-user"}}
        value = credits.refresh(session_loader=other, transport=lambda *args: (401, {}))
        self.assertFalse(value["ok"])
        self.assertIsNone(value["remaining"])
        self.assertNotIn("last_ok_at", value)
        self.assertFalse(credits.has_balance(value))

    def test_failure_keeps_success_cached_before_last_ok_at(self):
        storage.atomic_write_json(credits.cache_path(), {"ok": True, "queried_at": "2026-11-01T02:05:06+08:00",
                                                         "remaining": 5906, "total": 5906, "used": 0})
        value = credits.refresh(session_loader=lambda: None)
        self.assertFalse(value["ok"])
        self.assertEqual((value["remaining"], value["last_ok_at"]), (5906, "2026-11-01T02:05:06+08:00"))

    def test_account_switch_hides_cache(self):
        credits.refresh(session_loader=session, transport=lambda *args: (200, summary()))
        storage.save_state({"account_idhash": "different-account"})
        self.assertEqual(credits.load_cache(), {})

    def test_enterprise_not_using_personal_summary(self):
        with patch.object(engine, "https_transport", side_effect=AssertionError("No API")):
            value = credits.refresh(session_loader=lambda: session("mock-enterprise"))
        self.assertFalse(value["ok"])
        self.assertEqual(value["result"], "UNSUPPORTED_ACCOUNT")

    def test_expiry_and_monthly_period_are_separate(self):
        accounts = [{"ResourceId": "gift", "PackageCode": "gift", "CapacityUnit": "credits", "Status": 0,
                     "CycleCapacityRemainPrecise": "100", "DeductionEndTime": "2027-06-30 12:00:00", "CycleEndTime": "2027-06-30 12:00:00"},
                    {"ResourceId": "month", "PackageCode": "month", "CapacityUnit": "credits", "Status": 0,
                     "CycleCapacityRemainPrecise": "500", "DeductionEndTime": "2034-06-30 12:00:00", "CycleEndTime": "2026-10-31 23:59:59"}]
        rows = credits.parse_accounts(accounts, "free")
        self.assertFalse(rows[0]["refreshes"])
        self.assertTrue(rows[1]["refreshes"])
        self.assertNotEqual(rows[1]["expires_at"], rows[1]["cycle_end_at"])
        text = credits.expiry_text({"expiry_status": "available", "expirations": rows, "expiry_reconciled": True})
        self.assertIn("最近到期：", text)
        self.assertIn("2026-10-31 23:59:59", text)
        self.assertIn("周期额度", text)
        self.assertIn("不是本期积分有效期", credits.expiry_detail({"expirations": rows}))

    def test_daily_slice_and_unknown(self):
        rows = credits.parse_accounts([{"PackageCode": "daily", "CapacityUnit": "credits", "Status": 0,
                                        "CycleCapacityRemainPrecise": "999", "SlicePeriodUsageDetails": [{"SlicePeriodCapacityRemainPrecise": "10"}],
                                        "CycleEndTime": "2026-10-30 23:59:59", "DeductionEndTime": "2034-01-01 00:00:00"}], "free")
        self.assertEqual(rows[0]["remaining"], 10)
        self.assertTrue(rows[0]["refreshes"])
        self.assertEqual(rows[0]["cycle_end_at"][:10], "2026-10-30")
        self.assertEqual(rows[0]["expires_at"][:4], "2034")
        self.assertIsNone(credits.parse_time("invalid"))
        self.assertIsNone(credits.parse_time(0))
        self.assertEqual(credits.parse_accounts([{"CapacityUnit": "tokens", "Status": 0}], "free"), [])

    def test_cycle_rule_needs_no_package_codes(self):
        base = {"CapacityUnit": "credits", "Status": 0, "CycleCapacityRemainPrecise": "1"}
        rows = credits.parse_accounts([
            dict(base, PackageCode="same", CycleEndTime="2027-01-23 13:57:15", DeductionEndTime="2027-01-23 13:57:15"),
            dict(base, PackageCode="no-cycle", DeductionEndTime="2027-04-20 22:01:50"),
            dict(base, PackageCode="no-deduction", CycleEndTime="2026-12-02 00:05:02"),
            dict(base, PackageCode="period", CycleEndTime="2026-11-30 23:59:59", DeductionEndTime="2034-07-20 22:01:49")], "free")
        same, no_cycle, no_deduction, period = rows
        self.assertEqual([r["refreshes"] for r in rows], [False, False, False, True])
        self.assertEqual(same["expires_at"], same["cycle_end_at"])
        self.assertIsNone(no_cycle["cycle_end_at"])
        self.assertEqual(no_deduction["expires_at"], no_deduction["cycle_end_at"])
        self.assertEqual(period["cycle_end_at"][:10], "2026-11-30")
        self.assertEqual(period["expires_at"][:4], "2034")

    def test_pagination_dedup_and_reconciliation(self):
        client = engine.Engine(session_loader=session)
        client._bind(client.now().date().isoformat())
        account = {"ResourceId": "id1", "PackageCode": "a", "CapacityUnit": "credits", "Status": 0,
                   "CycleCapacityRemainPrecise": "100", "DeductionEndTime": "2034-03-01 08:00:00"}
        responses = [(200, {"data": {"Accounts": [account], "TotalCount": 101}}),
                     (200, {"data": {"Accounts": [account], "TotalCount": 101}}),
                     (200, {"data": {"Accounts": [account], "TotalCount": 1}})]
        with patch.object(client, "_request", side_effect=responses) as request:
            value = credits.query_expirations(client, [{"code": "a", "remaining": 100}], "2026-10-30T12:00:00")
        self.assertEqual(len(value["expirations"]), 1)
        self.assertTrue(value["expiry_reconciled"])
        self.assertEqual(request.call_args_list[1].args[2]["PageNumber"], 2)

    def test_expiry_failure_preserves_balance(self):
        def transport(method, url, headers, payload):
            return (200, summary()) if url.endswith("get-user-resource-summary") else (500, {})
        value = credits.refresh(session_loader=session, transport=transport)
        self.assertTrue(value["ok"])
        self.assertEqual(value["remaining"], 5806)
        self.assertEqual(value["expiry_status"], "error")

    def test_expiry_groups_preserve_all_batches_and_keep_cycle_separate(self):
        value = {"expirations": [
            {"remaining": 100, "expires_at": "2026-11-30T01:24:17+08:00"},
            {"remaining": 6, "expires_at": "2026-11-30T13:25:47+08:00"},
            {"remaining": 3000, "expires_at": "2027-04-20T22:01:50+08:00"},
            {"remaining": 1000, "expires_at": "2027-04-20T22:02:40+08:00"},
            {"remaining": 500, "refreshes": True, "expires_at": "2034-07-20T22:01:49+08:00", "cycle_end_at": "2026-10-31T23:59:59+08:00"}]}
        groups = credits.expiry_groups(value)
        self.assertEqual([g["remaining"] for g in groups["expiry"]], [106, 4000])
        self.assertEqual([len(g["rows"]) for g in groups["expiry"]], [2, 2])
        self.assertEqual(groups["cycle"][0]["remaining"], 500)
        self.assertEqual(groups["cycle"][0]["date"], "2026-10-31")

    def test_expiry_groups_unknown_is_not_zero_or_permanent(self):
        groups = credits.expiry_groups({"expirations": [{"remaining": None, "expires_at": "invalid"},
                                                       {"remaining": 100, "expires_at": "2026-11-27T00:00:00+08:00"},
                                                       {"remaining": 5, "expires_at": None}]})
        self.assertEqual([g["date"] for g in groups["expiry"]], ["2026-11-27", "未知"])
        self.assertIsNone(groups["expiry"][-1]["remaining"])
        self.assertEqual(len(groups["expiry"][-1]["rows"]), 2)

    def test_countdown_uses_local_calendar_days(self):
        today = date(2026, 10, 31)
        self.assertEqual(credits.countdown("2026-10-31T23:59:59", today), "今天")
        self.assertEqual(credits.countdown("2026-11-01T00:00:01", today), "明天")
        self.assertEqual(credits.countdown("2026-11-27T00:00:00", today), "27 天后")
        self.assertEqual(credits.countdown("2026-11-30", today), "30 天后")
        self.assertEqual(credits.countdown("2026-10-30T12:00:00", today), "已过期")
        self.assertEqual(credits.countdown("invalid", today), "—")
        self.assertEqual(credits.countdown(None, today), "—")

    def test_path_guard_remains_strict(self):
        for path in ("/billing/meter/daily-checkin", "/billing/meter/get-user-resource-summary/other", "/billing/meter/unknown"):
            with self.assertRaises(engine.ServiceFailure):
                engine.https_transport("POST", "https://copilot.tencent.com" + path, {}, {})


if __name__ == "__main__":
    unittest.main()
