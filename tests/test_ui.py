"""Read-only UI smoke test; no account calls or task changes."""
import ctypes
from datetime import date
from pathlib import Path
import sys
import tkinter as tk
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if sys.platform == "darwin":
    print("skipped: legacy tkinter UI (Windows fallback)")
    raise SystemExit(0)

import legacy_tk_app as app


class InterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass

    def setUp(self):
        self.window = app.Manager()
        self.window.withdraw()
        self.window.update_idletasks()

    def tearDown(self):
        self.window.closing = True
        self.window.destroy()

    def test_all_pages_render(self):
        for page in ("overview", "schedule", "growth", "history"):
            self.window.show_page(page)
            self.window.update_idletasks()
            self.assertTrue(self.window.content.winfo_children())

    def test_no_account_request_on_launch(self):
        with patch.object(app.subprocess, "Popen", side_effect=AssertionError("must not launch account runner")):
            self.window.refresh_local(reschedule=False)
            self.window.show_page("overview")

    def test_daily_credit_is_not_balance(self):
        self.window.records = [{"recorded_at": date.today().isoformat() + " 01:26:55", "result": "ALREADY",
                                "report": "今日已签过", "today_credit": 100, "streak_days": 3,
                                "total_credits": 300, "needs_attention": False}]
        with patch.object(app.storage, "load_state", return_value={}):
            self.window.render_overview()
            self.assertEqual(self.window.metrics["total"].cget("text"), "300")

    def test_settings_parse_times(self):
        self.window.show_page("schedule")
        self.window.settings["signin_times"].set("08:00, 12:30")
        self.assertEqual(self.window.parse_form()["signin_times"], ["08:00", "12:30"])
        self.window.settings["signin_times"].set("25:00")
        with self.assertRaises(ValueError):
            self.window.parse_form()

    def test_selection_survives_page_refresh(self):
        self.window.show_page("history")
        self.window.records = []
        self.window.render_history()
        self.window.show_page("overview")
        self.window.show_page("history")
        self.assertEqual(self.window.history_tree.get_children(), ())

    def test_failed_scheduler_is_not_saved(self):
        with patch.object(app.scheduler, "apply_config", return_value={"ok": False, "error": "permission denied"}), \
             patch.object(app.storage, "save_config") as save:
            with self.assertRaises(RuntimeError):
                self.window.save_and_apply(self.window.config_data)
            save.assert_not_called()

    def test_history_per_run_credit_and_growth(self):
        self.window.show_page("history")
        self.window.records = [{"recorded_at": "2026-10-30T13:25:37", "action": "poll", "result": "ALREADY",
                                "report": "已确认今日签到，自动签到查询跳过", "today_credit": 100,
                                "growth": {"credits_gained": 6}}]
        self.window.render_history()
        values = self.window.history_tree.item("0", "values")
        self.assertEqual(values[2:5], ("已签，跳过查询", "0", "+6"))
        self.assertIn("本次签到新增 0  ·  本次成长新增 +6", self.window.history_detail.get("1.0", "end"))

    def test_sensitive_features_default_off(self):
        self.window.show_page("growth")
        for key in ("makeup", "redeem", "lottery", "blindbox"):
            self.assertFalse(self.window.growth_vars[key].get())


if __name__ == "__main__":
    unittest.main()
