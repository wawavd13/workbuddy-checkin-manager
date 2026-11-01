"""Per-run credit and wording regressions; no live account operations."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import log_reader
import record_view


class RecordViewTests(unittest.TestCase):
    def test_repeated_snapshot_is_not_new_credit(self):
        row = {"action": "poll", "result": "ALREADY", "today_credit": 100,
               "report": "已确认今日签到，自动签到查询跳过", "growth": None}
        self.assertEqual(record_view.outcome(row), "已签，跳过查询")
        self.assertEqual(record_view.checkin_credit(row), 0)
        self.assertEqual(record_view.growth_credit(row), 0)
        detail = record_view.reward_detail(row)
        self.assertIn("本次签到新增 0", detail)
        self.assertNotIn("100", detail)  # The day's snapshot is never shown as this run's gain.

    def test_prior_checkin_not_this_run(self):
        self.assertEqual(record_view.outcome({"action": "checkin", "result": "ALREADY"}), "此前已签，未领取")

    def test_receipt_precedes_snapshot_and_survives_normalization(self):
        row = {"recorded_at": "2026-10-30T09:00:00", "action": "checkin", "result": "CLAIMED",
               "credit": 120, "today_credit": 100}
        normalized = log_reader._normalize(row, "test")
        self.assertEqual(record_view.checkin_credit(normalized), 120)
        self.assertEqual(record_view.checkin_credit(log_reader._normalize(normalized, "test")), 120)
        self.assertEqual(record_view.outcome(normalized), "本次签到成功")

    def test_legacy_receipt_unknown_and_zero(self):
        self.assertEqual(record_view.checkin_credit({"result": "SUCCESS", "today_credit": 100}), 100)
        self.assertIsNone(record_view.checkin_credit({"result": "CLAIMED"}))
        self.assertEqual(record_view.gain(None), "未知")
        self.assertEqual(record_view.gain(0), "0")
        self.assertEqual(record_view.gain(100), "+100")

    def test_status_query_never_claims_daily_credit(self):
        for signed, text in [(True, "查询：已签到"), (False, "查询：未签到"), (None, "查询：状态未知")]:
            row = {"action": "status", "result": "STATUS", "today_signed": signed, "today_credit": 100}
            self.assertEqual(record_view.outcome(row), text)
            self.assertEqual(record_view.checkin_credit(row), 0)

    def test_growth_separate_and_zero_preserved(self):
        row = {"action": "poll", "result": "ALREADY", "today_credit": 100, "growth": {"credits_gained": 6}}
        self.assertEqual(record_view.checkin_credit(row), 0)
        self.assertEqual(record_view.growth_credit(row), 6)
        row["growth"]["credits_gained"] = 0
        self.assertEqual(record_view.growth_credit(row), 0)
        row["growth"] = {}
        self.assertIsNone(record_view.growth_credit(row))
        self.assertIsNone(record_view.growth_credit({"action": "legacy"}))
        self.assertEqual(record_view.growth_credit({"action": "growth", "growth_credit": 6}), 6)

    def test_errors_do_not_fake_zero(self):
        for result in ("ERROR", "NETWORK", "TIMEOUT", "CANCELLED", "UNKNOWN"):
            self.assertIsNone(record_view.checkin_credit({"action": "checkin", "result": result, "today_credit": 100}))
        row = {"action": "poll", "result": "ERROR", "credit": 100, "success_at": "2026-10-30T09:00:00"}
        self.assertEqual(record_view.checkin_credit(row), 100)
        self.assertIsNone(record_view.growth_credit({"action": "poll", "result": "ERROR", "growth": None}))
        self.assertEqual(record_view.growth_credit({"action": "checkin", "result": "ERROR", "growth": None}), 0)

    def test_non_checkin_actions_do_not_claim(self):
        self.assertEqual(record_view.checkin_credit({"action": "growth", "result": "SUCCESS", "today_credit": 100}), 0)
        self.assertEqual(record_view.outcome({"action": "growth", "result": "GROWTH"}), "仅成长检查")


if __name__ == "__main__":
    unittest.main()
