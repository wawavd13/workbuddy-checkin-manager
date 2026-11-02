"""Run from outside with python -I absolute/tests/test_backend.py. No real credentials/API."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import engine
import log_reader
import runner
import storage

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc)


def session(uid="mock-account", token="mock-token"):
    return {"auth": {"accessToken": token, "endpoint": "https://copilot.tencent.com"}, "account": {"uid": uid}}


class MockTransport:
    def __init__(self, signed=False, fail=None):
        self.calls = []
        self.signed = signed
        self.fail = fail

    def __call__(self, method, url, headers, payload):
        path = url.split(".com", 1)[1]
        self.calls.append((method, path, payload))
        if self.fail:
            return self.fail, {}
        if path.endswith("checkin-activity-status"):
            return 200, {"data": {"active": True, "today_checked_in": self.signed,
                                  "today_credit": 100 if self.signed else 999, "streak_days": 5,
                                  "total_credits": 500, "token": "mock-token", "uid": "mock-account"}}
        if path.endswith("daily-checkin"):
            self.signed = True
            return 200, {"data": {"credit": 100}}
        if path.endswith("/buddy/travel/status"):
            return 200, {"data": {"state": "idle"}}
        if path.endswith("/buddy/travel/config"):
            return 200, {"data": {"locations": [{"id": "mock-location"}]}}
        if path.endswith("/tasks"):
            return 200, {"data": {"tasks": [{"task_code": "mock", "accept_status": "completed"}]}}
        if path.endswith("/tasks/mock/claim"):
            return 200, {"data": {"credit": 10}}
        if path.endswith("/streak"):
            return 200, {"data": {"makeup_cards": 0}}
        if path.endswith("/redeem/summary"):
            return 200, {"data": {"starter_status": "available"}}
        if path.endswith("/redeem"):
            return 200, {"data": {"credit_granted": 20}}
        if path.endswith("/lottery/chances"):
            return 200, {"data": {"balance": 1}}
        if path.endswith("/buddy/quota"):
            return 200, {"data": {"affordable": 1, "max_open_count": 1}}
        return 200, {"data": {}}


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.patcher = patch.object(storage, "BASE_DIR", self.base)
        self.patcher.start()
        self.config = copy.deepcopy(storage.DEFAULT_CONFIG)

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def make(self, transport=None, now=NOW, uid="mock-account", config=None, **kwargs):
        return engine.Engine(config=config or self.config, transport=transport or MockTransport(),
                             session_loader=lambda: session(uid), now=lambda: now, **kwargs)

    def test_defaults_validation_atomic_nonsecret(self):
        self.assertEqual(storage.load_config()["signin_times"], ["00:05"])
        self.config["signin_times"] = ["09:00", "00:05", "09:00"]
        stored = storage.save_config(self.config)
        self.assertEqual(stored["signin_times"], ["00:05", "09:00"])
        self.assertTrue((self.base / "data" / "config.json").exists())
        for cfg in ({"signin_times": ["24:00"]}, {"retry_interval_minutes": True}, {"retry_interval_minutes": 14},
                    {"retry_max_per_day": 49}, {"growth": {"makeup": "false"}}, {"token": "x"}):
            with self.assertRaises(ValueError):
                storage.validate_config(cfg)
        storage.save_state({"last_status": {"uid": "hidden", "accessToken": "hidden", "today_credit": 10}})
        raw = (self.base / "data" / "state.json").read_text()
        self.assertNotIn("hidden", raw)

    def test_status_readonly_refresh_no_credit_when_unsigned(self):
        transport = MockTransport()
        code, record = self.make(transport).run("status")
        self.assertEqual(code, 0)
        self.assertEqual(len(transport.calls), 1)
        self.assertFalse(record["today_signed"])
        self.assertIsNone(record["today_credit"])
        self.assertNotIn("token", storage.load_state()["last_status"])
        self.assertNotIn("mock-account", json.dumps(record))

    def test_checkin_pure_receipt(self):
        transport = MockTransport()
        code, record = self.make(transport).run("checkin")
        self.assertEqual(code, 0)
        self.assertEqual(record["result"], "CLAIMED")
        self.assertEqual(record["today_credit"], 100)
        self.assertTrue(all("/growth/" not in path for _, path, _ in transport.calls))
        self.assertEqual(storage.load_state()["confirmed_date"], "2026-10-30")
        self.assertEqual(storage.load_state()["success_at"], NOW.isoformat())

    def test_already_time_unknown(self):
        code, record = self.make(MockTransport(signed=True)).run("checkin")
        self.assertEqual(record["result"], "ALREADY")
        self.assertIsNone(storage.load_state()["success_at"])
        self.assertIsNone(log_reader.daily_summary([record], "2026-10-30")["success_at"])

    def test_crossday_account_cache_clear(self):
        self.make().run("checkin")
        transport = MockTransport()
        code, record = self.make(transport, uid="other-mock").run("status")
        self.assertFalse(record["today_signed"])
        self.assertIsNone(storage.load_state()["success_at"])
        self.make().run("checkin")
        tomorrow = NOW.replace(day=31)
        code, record = self.make(MockTransport(), now=tomorrow).run("status")
        self.assertIsNone(record["today_credit"])
        self.assertIsNone(storage.load_state()["confirmed_date"])

    def test_every_growth_guard(self):
        api_groups = {"tasks": ["/tasks"], "travel": ["/buddy/travel/claim", "/buddy/travel/config", "/buddy/travel/depart"],
                      "makeup": ["/makeup-cards", "/heatmap"], "redeem": ["/redeem"],
                      "lottery": ["/lottery"], "blindbox": ["/buddy/quota", "/buddy/open"]}
        for feature in api_groups:
            with self.subTest(feature=feature):
                config = copy.deepcopy(self.config)
                config["growth"].update({k: True for k in api_groups})
                config["growth"][feature] = False
                transport = MockTransport()
                code, record = self.make(transport, config=config).run("growth")
                paths = [path for _, path, _ in transport.calls]
                self.assertFalse(any(fragment in path for fragment in api_groups[feature] for path in paths))
                self.assertEqual(record["growth"]["features"][feature], "disabled")
        config = copy.deepcopy(self.config)
        config["growth"]["enabled"] = False
        transport = MockTransport()
        self.make(transport, config=config).run("growth")
        self.assertEqual(transport.calls, [])

    def test_enabled_individual_feature_calls(self):
        for feature, expected in (("tasks", "/tasks/mock/claim"), ("travel", "/buddy/travel/depart"),
                                  ("makeup", "/streak"), ("redeem", "/redeem"),
                                  ("lottery", "/lottery/draw"), ("blindbox", "/buddy/open")):
            config = copy.deepcopy(self.config)
            config["growth"].update({k: k == feature for k in config["growth"] if k != "enabled"})
            transport = MockTransport()
            code, record = self.make(transport, config=config).run("growth")
            self.assertEqual(code, 0)
            self.assertTrue(any(path.endswith(expected) for _, path, _ in transport.calls), feature)

    def test_poll_success_skip_signin_growth_slot_once(self):
        self.make().run("checkin")
        transport = MockTransport(signed=True)
        self.make(transport).run("poll")
        self.assertTrue(any("/growth/" in p for _, p, _ in transport.calls))
        self.assertFalse(any("checkin" in p for _, p, _ in transport.calls))
        transport = MockTransport(signed=True)
        self.make(transport, now=NOW.replace(hour=10)).run("poll")
        self.assertEqual(transport.calls, [])

    def test_poll_catchup_false_not_growth_retry_minute(self):
        self.config["catch_up"] = False
        transport = MockTransport()
        self.make(transport, now=NOW.replace(minute=5)).run("poll")
        self.assertEqual(transport.calls, [])
        self.config["catch_up"] = True
        transport = MockTransport()
        self.make(transport, now=NOW.replace(minute=5)).run("poll")
        self.assertTrue(any("/growth/" in p for _, p, _ in transport.calls))
        self.assertEqual(storage.load_state()["last_growth_slot"], "2026-10-30T09:00")

    def test_retry_interval_max(self):
        self.config["growth"]["enabled"] = False
        self.config["retry_max_per_day"] = 1
        for now, expected in ((NOW, 1), (NOW.replace(minute=30), 1), (NOW.replace(hour=10), 2), (NOW.replace(hour=11), 2)):
            self.make(MockTransport(fail=500), now=now).run("poll")
            self.assertEqual(storage.load_state()["attempts_today"], expected)

    def test_retry_tolerates_scheduler_second_jitter(self):
        # 00:05:02 attempt failed; the next hourly poll fires at 01:05:01 (3599 s later).
        self.config["growth"]["enabled"] = False
        first = datetime(2026, 11, 1, 0, 5, 2, tzinfo=timezone.utc)
        self.make(MockTransport(fail=-1), now=first).run("daily")
        self.assertEqual(storage.load_state()["attempts_today"], 1)
        transport = MockTransport()
        self.make(transport, now=first.replace(minute=35)).run("poll")
        self.assertEqual(transport.calls, [])
        transport = MockTransport()
        self.make(transport, now=datetime(2026, 11, 1, 1, 5, 1, tzinfo=timezone.utc)).run("poll")
        self.assertTrue(any(path.endswith("daily-checkin") for _, path, _ in transport.calls))
        self.assertEqual(storage.load_state()["attempts_today"], 2)

    def test_auth_hold_until_manual_and_crossday(self):
        self.make(MockTransport(fail=401)).run("poll")
        transport = MockTransport()
        code, record = self.make(transport, now=NOW.replace(day=31)).run("poll")
        self.assertEqual(record["result"], "AUTH_HOLD")
        self.assertEqual(transport.calls, [])
        self.make(transport, now=NOW.replace(day=31)).run("status")
        self.assertFalse(storage.load_state()["auth_failure_hold"])

    def test_cancel_no_api(self):
        transport = MockTransport()
        code, record = self.make(transport, cancel_check=lambda: True).run("daily")
        self.assertEqual(code, 2)
        self.assertEqual(record["result"], "CANCELLED")
        self.assertEqual(transport.calls, [])

    def test_known_endpoint_redirect_policy(self):
        for url in ("http://copilot.tencent.com", "https://evil.example", "https://copilot.tencent.com.evil.example", "https://copilot.tencent.com:444", "https://copilot.tencent.com/x"):
            with self.assertRaises(engine.ServiceFailure):
                engine.validate_endpoint(url)
        self.assertIsNone(engine._NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.example"))

    def test_bad_500_null_not_already(self):
        transport = MockTransport()
        original = transport.__call__
        def callback(method, url, headers, payload):
            if url.endswith("daily-checkin"):
                return 500, None
            return original(method, url, headers, payload)
        code, record = self.make(callback).run("checkin")
        self.assertNotEqual(code, 0)
        self.assertIsNone(storage.load_state()["confirmed_date"])

    def test_logs_partial_stable_credit_unknown(self):
        with patch.object(log_reader, "LEGACY_LOG", self.base / "legacy.log"):
            (self.base / "legacy.log").write_text('[2026-10-30 08:00:00] {"result":"CLAIMED","credit":100}\nmalformed\n', encoding="utf-8")
            storage.append_event({"recorded_at": NOW.isoformat(), "action": "checkin", "result": "ALREADY", "today_credit": 100})
            with (self.base / "data" / "events.jsonl").open("a") as handle:
                handle.write('{"recorded_at":')
            rows = log_reader.read_records()
            self.assertEqual(len(rows), 2)
            summary = log_reader.daily_summary(rows, "2026-10-30")
            self.assertEqual(summary["today_credit"], 100)
            self.assertTrue(summary["today_signed"])
            self.assertTrue(summary["success_at"].startswith("2026-10-30T08:00:00"))
            empty = log_reader.daily_summary([], "2026-10-30")
            self.assertIsNone(empty["today_credit"])
            self.assertIsNone(empty["today_signed"])
            self.assertEqual(empty["days"], {})

    def test_growth_credits_summary_idempotent_normalize(self):
        record = {"recorded_at": NOW.isoformat(), "action": "growth", "result": "GROWTH", "growth": {"credits_gained": 10}}
        row = log_reader._normalize(record, "events.jsonl")
        self.assertEqual(log_reader.daily_summary([row], "2026-10-30")["today_growth_credit"], 10)

    def test_lock_crossprocess_and_runtime(self):
        snippet = "import sys;sys.path.insert(0,sys.argv[1]);import runner;\ntry:\n with runner.RunnerLock(sys.argv[2]): pass\nexcept runner.AlreadyRunning: sys.exit(3)"
        with runner.RunnerLock(self.base / "runner.lock"):
            child = subprocess.run([sys.executable, "-I", "-c", snippet, str(ROOT), str(self.base / "runner.lock")], cwd=tempfile.gettempdir(), capture_output=True)
            self.assertEqual(child.returncode, 3, child.stderr)
        def factory(**kwargs):
            return self.make(**kwargs)
        code, record = runner.run_worker("status", engine_factory=factory)
        self.assertEqual(code, 0)
        runtime = json.loads((self.base / "runtime.json").read_text())
        self.assertFalse(runtime["running"])
        self.assertEqual(runtime["mode"], "status")
        self.assertIsNotNone(runtime["finished_at"])
        self.assertTrue((self.base / "data" / "events.jsonl").exists())

    def test_worker_refreshes_credits_after_checkin_but_not_status(self):
        def factory(**kwargs):
            return self.make(**kwargs)
        with patch.object(runner.credits, "refresh", return_value={"ok": True}) as refresh:
            code, record = runner.run_worker("checkin", engine_factory=factory)
            self.assertEqual(code, 0)
            self.assertTrue(record["credits_refreshed"])
            refresh.assert_called_once_with()
        with patch.object(runner.credits, "refresh") as refresh:
            code, record = runner.run_worker("status", engine_factory=factory)
            self.assertEqual(code, 0)
            self.assertNotIn("credits_refreshed", record)
            refresh.assert_not_called()

    def test_worker_credit_refresh_failure_does_not_change_run_result(self):
        def factory(**kwargs):
            return self.make(**kwargs)
        with patch.object(runner.credits, "refresh", side_effect=OSError("disk full")):
            code, record = runner.run_worker("checkin", engine_factory=factory)
        self.assertEqual(code, 0)
        self.assertEqual(record["result"], "CLAIMED")
        self.assertFalse(record["credits_refreshed"])

    def test_worker_output_is_utf8_regardless_of_console_code_page(self):
        # The UI decodes worker stdout as UTF-8; on Chinese Windows a child Python otherwise
        # writes the ANSI code page (GBK), which rendered the growth report as mojibake.
        snippet = ("import json,sys;sys.path.insert(0,sys.argv[1]);import runner;runner._utf8_streams();"
                   "print(json.dumps({'report':'成长中心无可领取项'},ensure_ascii=False),flush=True);"
                   "sys.stderr.write('错误\\n')")
        env = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONIOENCODING", "PYTHONUTF8", "PYTHONLEGACYWINDOWSSTDIO")}
        child = subprocess.run([sys.executable, "-I", "-c", snippet, str(ROOT)], cwd=tempfile.gettempdir(),
                               capture_output=True, env=env)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(json.loads(child.stdout.decode("utf-8"))["report"], "成长中心无可领取项")
        self.assertEqual(child.stderr.decode("utf-8").strip(), "错误")

    def test_worker_skipped_patrol_with_growth_slot_does_not_query_credits(self):
        class Patrol:
            def __init__(self, **kwargs):
                pass

            def run(self, mode):
                return 0, {"recorded_at": "2026-11-01T01:00:01+08:00", "action": mode, "result": "SKIPPED",
                           "report": "当前无到期操作", "growth": {"result": "GROWTH", "credits_gained": 0},
                           "needs_attention": False}
        with patch.object(runner.credits, "refresh") as refresh:
            code, record = runner.run_worker("poll", engine_factory=Patrol)
        self.assertEqual(code, 0)
        refresh.assert_not_called()
        self.assertNotIn("credits_refreshed", record)

    def test_daily_automatic_hold_cache_slots_and_limits(self):
        self.make(MockTransport(fail=401)).run("daily")
        transport = MockTransport()
        code, record = self.make(transport).run("daily")
        self.assertEqual(record["result"], "AUTH_HOLD")
        self.assertEqual(transport.calls, [])
        self.make().run("checkin")
        transport = MockTransport(signed=True)
        self.make(transport).run("daily")
        self.assertFalse(any("checkin" in path for _, path, _ in transport.calls))
        self.assertTrue(any("/growth/" in path for _, path, _ in transport.calls))
        transport = MockTransport(signed=True)
        self.make(transport).run("daily")
        self.assertEqual(transport.calls, [])
        storage.save_state(storage.STATE_DEFAULTS)
        self.config["growth"]["enabled"] = False
        self.config["retry_max_per_day"] = 1
        for now, expected in ((NOW, 1), (NOW.replace(minute=30), 1), (NOW.replace(hour=10), 2), (NOW.replace(hour=11), 2)):
            self.make(MockTransport(fail=500), now=now).run("daily")
            self.assertEqual(storage.load_state()["attempts_today"], expected)

    def test_unknown_fields_no_claim_post(self):
        for data in ({}, {"active": True}, {"today_checked_in": False},
                     {"active": None, "today_checked_in": False}, {"active": True, "today_checked_in": None}):
            calls = []
            def transport(method, url, headers, payload):
                calls.append(url)
                return 200, {"data": data}
            code, record = self.make(transport).run("checkin")
            self.assertEqual(record["result"], "UNKNOWN")
            self.assertNotEqual(code, 0)
            self.assertEqual(len(calls), 1)
            self.assertTrue(calls[0].endswith("checkin-activity-status"))

    def test_upstream_real_contract_paths_and_departure_order(self):
        # da2bc7f run_growth: tasks lines1205..1251, quota/open1456..1465,
        # travel config locations[0].id1490..1497. Preserve exact known paths/fields.
        config = copy.deepcopy(self.config)
        config["growth"]["blindbox"] = True
        transport = MockTransport()
        code, record = self.make(transport, config=config).run("growth")
        paths = [path for _, path, _ in transport.calls]
        self.assertEqual(code, 0)
        self.assertIn("/v2/activity/growth/tasks/mock/claim", paths)
        self.assertIn("/v2/activity/growth/buddy/quota", paths)
        self.assertIn("/v2/activity/growth/buddy/open", paths)
        self.assertLess(paths.index("/v2/activity/growth/buddy/open"), paths.index("/v2/activity/growth/buddy/travel/depart"))
        depart = next(payload for _, path, payload in transport.calls if path.endswith("/buddy/travel/depart"))
        self.assertEqual(depart, {"location_id": "mock-location"})
        claim = next(payload for _, path, payload in transport.calls if path.endswith("/tasks/mock/claim"))
        self.assertEqual(claim, {})

    def test_vendor_exact(self):
        digest = hashlib.sha256((ROOT / "vendor" / "upstream_signin.py").read_bytes()).hexdigest()
        self.assertEqual(digest, "f3cf601e0cda23b684b9d6079a41b5ff40a24e2ef18d61256036afafa02d5de5")
        self.assertIn("MIT License", (ROOT / "vendor" / "LICENSE").read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
