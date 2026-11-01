"""Native UI regression tests. No account calls or task changes."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import datetime as dt
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import qt_app
import qt_chrome
import qt_theme


def contrast(a, b):
    def luminance(value):
        channels = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        convert = lambda x: x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4
        r, g, b = [convert(x) for x in channels]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class QtInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.application.setStyle("Fusion")

    def setUp(self):
        self.window = qt_app.Manager(query_tasks=False, scan_usage=False)

    def tearDown(self):
        self.window.close()
        self.application.processEvents()

    def test_pages_and_theme(self):
        self.assertEqual(self.window.mode, "light")
        for key in self.window.nav:
            self.window.show_page(key)
            self.assertEqual(self.window.current_page, key)
            self.assertEqual([k for k, b in self.window.nav.items() if b.property("selected")], [key])
        self.window.toggle_theme()
        self.assertEqual(self.window.mode, "dark")
        self.assertIs(qt_theme.colors(), qt_theme.PALETTES["dark"])
        self.assertTrue(all(w.mode == "dark" for w in self.window.findChildren(qt_app.Switch)))
        self.assertIn("浅色", self.window.theme_button.text())

    def test_dark_selection_hover_dim_and_flat_pressed_states(self):
        dark = qt_theme.PALETTES["dark"]
        self.assertEqual(dark["select"], qt_theme.TONE_SETS["dark"]["yellow"])
        self.assertEqual(dark["select_text"], qt_theme.INK)
        self.assertNotEqual(dark["hover_fill"], dark["raised"])
        self.assertGreaterEqual(contrast(dark["dim"], dark["surface"]), 4.5)
        self.assertGreaterEqual(contrast(dark["dim"], dark["field"]), 4.5)
        sheet = qt_theme.stylesheet("dark")
        self.assertIn("::indicator:checked", sheet)
        self.assertIn(qt_theme.TONE_SETS["dark"]["yellow"], sheet)
        ghost = qt_theme.button("链接", variant="ghost")
        time = qt_theme.button("08:00", variant="time")
        ghost.setDown(True)
        time.setDown(True)
        self.assertEqual(ghost.scheme(dark)[0], qt_theme.TONES_HOVER["yellow"])
        self.assertEqual(time.scheme(dark)[0], qt_theme.TONES_HOVER["blue"])
        switch = qt_theme.Switch(True)
        switch.set_theme("dark")
        switch.setEnabled(False)
        switch.grab()  # Disabled state must paint without inheriting hover/checked chrome.

    def test_secondary_text_contrast_both_themes(self):
        for mode, p in qt_theme.PALETTES.items():
            for role in ("muted", "dim"):
                for surface in ("bg", "side", "surface", "field"):
                    with self.subTest(mode=mode, role=role, surface=surface):
                        self.assertGreaterEqual(contrast(p[role], p[surface]), 4.5)

    def test_brutal_buttons_panels_and_calendar(self):
        primary = qt_theme.button("确定", variant="primary")
        ghost = qt_theme.button("收起", variant="ghost")
        self.assertEqual(primary.offset(), qt_theme.BUTTON_SHADOW)
        self.assertEqual(ghost.offset(), 0)
        self.assertEqual(primary.scheme(qt_theme.colors())[0], qt_theme.TONES["green"])
        card, layout = qt_theme.panel(padding=10, tone="green")
        margins = layout.contentsMargins()
        edge = 10 + qt_theme.BORDER
        self.assertEqual((margins.left(), margins.right()), (edge, edge + qt_theme.SHADOW))
        inner = qt_theme.button("刷新")
        layout.addWidget(inner)
        self.assertTrue(inner.pastel())
        self.assertEqual(inner.scheme(qt_theme.PALETTES["dark"])[1], qt_theme.INK)
        days = [self.window.cal_grid.itemAt(i).widget() for i in range(self.window.cal_grid.count())]
        days = [w for w in days if isinstance(w, qt_theme.BrutalButton)]
        self.assertTrue(days)
        self.assertTrue(all(w.variant() == "day" for w in days))
        self.assertEqual(sum(bool(w.property("today")) for w in days),
                         1 if self.window.month == dt.date.today().replace(day=1) else 0)
        self.assertEqual(len({w.height() for w in days}), 1)

    def test_switch_mouse_click(self):
        switch = self.window.fields["stop_after_success"]
        before = switch.isChecked()
        QTest.mouseClick(switch, Qt.MouseButton.LeftButton)
        self.assertNotEqual(switch.isChecked(), before)

    def test_time_chips_edit_remove_and_dedup(self):
        chips = self.window.fields["signin_times"]
        chips.set_times(["08:00", "20:00", "08:00"])
        self.assertEqual(chips.times(), ["08:00", "20:00"])
        chips.remove("08:00")
        self.assertEqual(chips.times(), ["20:00"])
        self.assertEqual(chips.text(), "20:00")

    def test_picker_and_popup_filter(self):
        picker = qt_app.TimePicker("23:05", self.window)
        picker.hour.setValue(8)
        picker.minute.setValue(30)
        self.assertEqual(picker.value(), "08:30")
        picker.close()
        self.window.result_filter.setCurrentIndex(2)
        self.assertEqual(self.window.result_filter.currentText(), "异常")
        self.assertTrue(all(not b.icon().isNull() for b in self.window.nav.values()))

    def test_number_stepper_and_no_topmost(self):
        control = self.window.fields["retry_interval_minutes"]
        control.setValue(60)
        QTest.mouseClick(control.plus, Qt.MouseButton.LeftButton)
        self.assertEqual(control.value(), 61)
        QTest.mouseClick(control.minus, Qt.MouseButton.LeftButton)
        self.assertEqual(control.value(), 60)
        control.setValue(240)
        self.assertFalse(control.plus.isEnabled())
        control.setValue(15)
        self.assertFalse(control.minus.isEnabled())
        self.assertFalse(self.window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        self.assertEqual(self.window.findChildren(qt_app.QSpinBox), [])

    def test_integrated_window_controls(self):
        self.assertTrue(self.window.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.window.show()
        QTest.mouseClick(self.window.window_controls["maximize"], Qt.MouseButton.LeftButton)
        self.assertTrue(self.window.isMaximized())
        QTest.mouseClick(self.window.window_controls["maximize"], Qt.MouseButton.LeftButton)
        self.assertFalse(self.window.isMaximized())
        QTest.mouseClick(self.window.window_controls["minimize"], Qt.MouseButton.LeftButton)
        self.assertTrue(self.window.isMinimized())
        self.window.showNormal()
        QTest.mouseDClick(self.window.topbar, Qt.MouseButton.LeftButton)
        self.assertTrue(self.window.isMaximized())

    def test_time_validation(self):
        self.window.fields["signin_times"].setText("08:00, 20:30")
        self.assertEqual(self.window.form_config()["signin_times"], ["08:00", "20:30"])
        self.window.fields["signin_times"].setText("25:00")
        with self.assertRaises(ValueError):
            self.window.form_config()

    def test_failure_not_saved(self):
        with patch.object(qt_app.scheduler, "apply_config", return_value={"ok": False, "error": "denied"}), \
             patch.object(qt_app.storage, "save_config") as save:
            with self.assertRaises(RuntimeError):
                self.window.save_apply(self.window.config_data)
            save.assert_not_called()

    def test_launch_no_account_requests(self):
        with patch.object(qt_app.subprocess, "Popen", side_effect=AssertionError("No account runner on launch")):
            self.window.refresh_local()

    def test_confirm_cancel_no_execution(self):
        with patch.object(self.window, "confirm", return_value=False), patch.object(self.window, "work") as work:
            self.window.start_run("checkin")
            self.window.apply_schedule()
            self.window.apply_growth()
            work.assert_not_called()

    def test_dialogs_share_frameless_chrome_and_cancel(self):
        dialogs = [qt_chrome.MessageDialog("确认执行？", "只查询服务端状态。", parent=self.window),
                   qt_chrome.MessageDialog("操作未完成", "网络失败", accept_text="知道了", reject_text=None, glyph="!",
                                           tone="pink", accept_variant=None, parent=self.window),
                   qt_app.TimePicker("08:30", self.window)]
        for dialog in dialogs:
            with self.subTest(title=dialog.windowTitle()):
                self.assertIsInstance(dialog, qt_chrome.BrutalDialog)
                self.assertTrue(dialog.windowFlags() & Qt.WindowType.FramelessWindowHint)
                texts = [w.text() for w in dialog.findChildren(qt_app.QLabel)]
                self.assertEqual(texts.count(dialog.windowTitle()), 1)  # Title shown once, in the strip.
                actions = dialog.findChildren(qt_theme.BrutalButton)
                self.assertTrue(all(b.offset() == qt_theme.BUTTON_SHADOW for b in actions))
                self.assertEqual(sum(b.isDefault() for b in actions), 1)
                dialog.show()
                QTest.keyClick(dialog, Qt.Key.Key_Escape)
                self.assertFalse(dialog.isVisible())
                self.assertEqual(dialog.result(), qt_app.QDialog.DialogCode.Rejected)
        closing = qt_chrome.MessageDialog("停止当前运行？", "只请求当前任务停止。", parent=self.window)
        closing.show()
        QTest.mouseClick(closing.close_button, Qt.MouseButton.LeftButton)
        self.assertEqual(closing.result(), qt_app.QDialog.DialogCode.Rejected)
        accepting = qt_chrome.MessageDialog("确认执行？", "只查询服务端状态。", parent=self.window)
        accepting.show()
        QTest.keyClick(accepting, Qt.Key.Key_Return)
        self.assertEqual(accepting.result(), qt_app.QDialog.DialogCode.Accepted)

    def test_confirm_and_error_use_brutal_dialog(self):
        shown = []
        def fake_exec(dialog):
            shown.append(dialog)
            return qt_app.QDialog.DialogCode.Accepted if dialog.windowTitle() == "确认执行？" else qt_app.QDialog.DialogCode.Rejected
        with patch.object(qt_app.MessageDialog, "exec", fake_exec):
            self.assertTrue(self.window.confirm("确认执行？", "只查询服务端状态。"))
            self.assertFalse(self.window.confirm("停止当前运行？", "只请求停止。"))
            self.window.error("网络失败")
        self.assertEqual([d.windowTitle() for d in shown], ["确认执行？", "停止当前运行？", "操作未完成"])
        self.assertEqual([b.text() for b in shown[0].findChildren(qt_theme.BrutalButton)], ["取消", "确认"])
        self.assertEqual([b.text() for b in shown[2].findChildren(qt_theme.BrutalButton)], ["知道了"])
        self.assertEqual(self.window.footer.text(), "网络失败")
        self.assertFalse(hasattr(qt_app, "QMessageBox"))

    def test_sensitive_defaults(self):
        for key in ("makeup", "redeem", "lottery", "blindbox"):
            self.assertFalse(self.window.growth_fields[key].isChecked())

    def test_history_filters_and_detail(self):
        self.window.show_page("history")
        self.window.date_filter.setText("2099-01-01")
        self.window.render_history(force=True)
        self.assertEqual(self.window.table.rowCount(), 0)
        self.window.date_filter.clear()
        self.window.render_history(force=True)
        if self.window.table.rowCount():
            self.window.table.selectRow(0)
            self.window.select_record()
            self.assertTrue(self.window.detail.text())

    def test_history_per_run_credit_and_growth(self):
        self.window.records = [{"recorded_at": "2026-10-30T13:25:37", "action": "poll", "result": "ALREADY",
                                "report": "已确认今日签到，自动签到查询跳过", "today_credit": 100,
                                "growth": {"credits_gained": 6, "report": "成长任务已检查"}}]
        self.window.render_history(force=True)
        self.assertEqual(self.window.table.columnCount(), 6)
        self.assertEqual(self.window.table.horizontalHeaderItem(3).text(), "签到新增")
        self.assertEqual(self.window.table.item(0, 2).text(), "已签，跳过查询")
        self.assertEqual(self.window.table.item(0, 3).text(), "0")
        self.assertEqual(self.window.table.item(0, 4).text(), "+6")
        self.assertIn("本次成长新增 +6", self.window.detail.text())
        self.assertNotIn("当日快照", self.window.detail.text())
        for theme in ("dark", "light"):
            self.window.set_theme(theme)
            self.assertEqual(self.window.table.item(0, 3).text(), "0")
            self.assertEqual(self.window.table.item(0, 4).text(), "+6")

    def test_history_claim_and_unknown(self):
        self.window.records = [{"recorded_at": "2026-10-30T09:00:00", "action": "checkin", "result": "CLAIMED", "today_credit": 100},
                               {"recorded_at": "2026-10-30T10:00:00", "action": "checkin", "result": "NETWORK"}]
        self.window.render_history(force=True)
        self.assertEqual(self.window.table.item(0, 3).text(), "未知")
        self.assertEqual(self.window.table.item(1, 3).text(), "+100")
        self.assertEqual(self.window.table.item(1, 2).text(), "本次签到成功")

    def test_history_summary_does_not_follow_date_filter(self):
        self.window.date_filter.setText("2099-01-01")
        self.window.refresh_local()
        self.assertIn("今日签到奖励", self.window.history_today.text())
        self.assertIn("当日奖励不是每次运行新增", self.window.history_today_hint.text())
        self.assertEqual(self.window.table.rowCount(), 0)

    def test_credits_card_and_readonly_refresh(self):
        value = {"ok": True, "remaining": 5806, "total": 6500, "used": 694, "queried_at": "2026-10-30T18:50:00"}
        with patch.object(qt_app.credits, "load_cache", return_value=value):
            self.window.render_credits()
        self.assertEqual(self.window.credits_value.text(), "5,806")
        self.assertIn("6,500", self.window.credits_capacity.text())
        self.assertIn("不代表永久有效", self.window.credits_expiry.text())
        with patch.object(self.window, "work") as work, patch.object(self.window, "confirm", side_effect=AssertionError("Read only")):
            self.window.refresh_credits()
            self.assertEqual(work.call_args.args[0], "查询积分")

    def test_overview_rereads_credit_cache_and_reports_post_run_refresh(self):
        value = {"ok": True, "remaining": 5906, "total": 5906, "used": 0,
                 "queried_at": "2026-10-31T22:05:01"}
        with patch.object(qt_app.credits, "load_cache", return_value=value) as load:
            self.window.show_page("overview")
            load.assert_called_once_with()
        self.assertEqual(self.window.credits_value.text(), "5,906")
        with patch.object(self.window, "refresh_tasks"):
            self.window.finished_run({"report": "签到成功", "credits_refreshed": True})
            self.assertIn("积分已更新", self.window.footer.text())
            self.window.finished_run({"report": "签到成功", "credits_refreshed": False})
            self.assertIn("积分未更新，请手动刷新", self.window.footer.text())

    def test_credits_failure_without_success_stays_unknown(self):
        with patch.object(qt_app.credits, "load_cache", return_value={"ok": False, "error": "网络失败"}):
            self.window.render_credits()
        self.assertEqual(self.window.credits_value.text(), "—")
        self.assertIn("网络失败", self.window.credits_hint.text())

    def test_task_chips_distinguish_missing_from_failed(self):
        self.window.tasks = [{"state": "missing", "error": "任务不存在。"},
                             {"state": "unknown", "error": "PowerShell did not return valid JSON"}]
        self.window.render_tasks()
        self.assertEqual(self.window.task_rows[0][0].text(), "未创建")
        self.assertEqual(self.window.task_rows[1][0].text(), "查询失败")
        self.assertIn("任务不存在", self.window.task_rows[0][0].toolTip())

    def test_credits_failure_keeps_last_success_marked(self):
        value = {"ok": False, "error": "网络失败", "queried_at": "2026-11-01T03:05:02+08:00",
                 "last_ok_at": "2026-11-01T02:05:06+08:00", "remaining": 5906, "total": 5906, "used": 0,
                 "expiry_status": "available", "expiry_reconciled": True,
                 "expirations": [{"remaining": 5906, "name": "赠送", "expires_at": "2027-06-30T12:00:00+08:00"}]}
        with patch.object(qt_app.credits, "load_cache", return_value=value):
            self.window.render_credits()
        self.assertEqual(self.window.credits_value.text(), "5,906")
        hint = self.window.credits_hint.text()
        self.assertIn("上次成功于 2026-11-01 02:05:06", hint)
        self.assertIn("最近一次查询失败（2026-11-01 03:05:02）：网络失败", hint)
        self.assertEqual(len(self.window.credits_detail.rows), 1)
        self.assertIn("2026-11-01 02:05:06 的成功查询", self.window.credits_detail.notice.text())
        self.assertIn("最近一次查询失败", self.window.credits_expiry.text())

    def test_expiry_timeline_rows_accordion_theme_and_refresh(self):
        value = {"ok": True, "expiry_status": "available", "expiry_reconciled": True, "expirations": [
            {"remaining": 100, "name": "赠送", "expires_at": "2026-11-30T01:24:17+08:00"},
            {"remaining": 6, "name": "赠送", "expires_at": "2026-11-30T13:25:47+08:00"},
            {"remaining": 1000, "name": "权益", "expires_at": "2027-01-23T13:57:15+08:00"},
            {"remaining": 500, "name": "体验", "refreshes": True, "expires_at": "2034-07-20T22:01:49+08:00",
             "cycle_end_at": "2026-10-31T23:59:59+08:00"}]}
        chart, today = self.window.credits_detail, dt.date(2026, 10, 31)
        chart.set_data(value, today)
        self.assertEqual([(r.group["date"], r.group["remaining"]) for r in chart.rows],
                         [("2026-10-31", 500), ("2026-11-30", 106), ("2027-01-23", 1000)])
        self.assertEqual([r.countdown for r in chart.rows], ["今天", "30 天后", "84 天后"])
        self.assertEqual([r.tag for r in chart.rows], ["周期", "2 批", ""])
        self.assertTrue(chart.rows[0].bar.cycle)
        self.assertFalse(any(r.isChecked() for r in chart.rows))
        self.assertTrue(all(r.details.isHidden() for r in chart.rows))
        QTest.mouseClick(chart.rows[1], Qt.MouseButton.LeftButton)
        self.assertFalse(chart.rows[1].details.isHidden())
        self.assertIn("13:25:47", chart.rows[1].detail_text)
        QTest.mouseClick(chart.rows[0], Qt.MouseButton.LeftButton)
        self.assertEqual([r.isChecked() for r in chart.rows], [True, False, False])
        self.assertTrue(chart.rows[1].details.isHidden())
        self.assertIn("不是本期积分有效期", chart.rows[0].detail_text)
        QTest.mouseClick(chart.rows[0], Qt.MouseButton.LeftButton)
        self.assertFalse(any(r.isChecked() for r in chart.rows))
        QTest.mouseClick(chart.rows[2], Qt.MouseButton.LeftButton)
        value["queried_at"] = "2026-10-31T19:10:00"
        chart.set_data(value, today)
        self.assertEqual([r.isChecked() for r in chart.rows], [False, False, True])
        self.assertIn("13:57:15", chart.rows[2].detail_text)
        for mode in ("light", "dark"):
            self.window.set_theme(mode)
            self.assertTrue(chart.rows[2].isChecked())
            self.assertTrue(all(r.mode == mode and r.bar.mode == mode for r in chart.rows))
        chart.set_data({"ok": False}, today)
        self.assertEqual(chart.rows, [])
        self.assertIn("暂无有效期明细", chart.notice.text())
        self.assertTrue(chart.legend.isHidden())

    def test_expiry_card_toggle_and_keyboard(self):
        chart, summary = self.window.credits_detail, self.window.credits_expiry
        self.assertFalse(chart.isHidden())
        self.assertTrue(summary.isHidden())
        self.window.toggle_credit_detail()
        self.assertTrue(chart.isHidden())
        self.assertFalse(summary.isHidden())
        self.assertIn("展开", self.window.credits_detail_button.text())
        self.window.toggle_credit_detail()
        self.assertFalse(chart.isHidden())
        self.assertTrue(summary.isHidden())
        value = {"ok": True, "expiry_status": "available", "expiry_reconciled": True, "expirations": [
            {"remaining": 100, "name": "赠送", "expires_at": "2027-06-30T12:00:00+08:00"},
            {"remaining": 1000, "name": "权益", "expires_at": "2027-01-23T13:57:15+08:00"}]}
        chart.set_data(value, dt.date(2026, 10, 31))
        self.assertEqual(chart.rows[0].focusPolicy(), Qt.FocusPolicy.TabFocus)
        chart.rows[0].setFocus()
        QTest.keyPress(chart.rows[0], Qt.Key.Key_Down)
        self.assertIs(self.window.focusWidget(), chart.rows[1])
        QTest.keyClick(chart.rows[1], Qt.Key.Key_Space)
        self.assertEqual([r.isChecked() for r in chart.rows], [False, True])
        QTest.keyPress(chart.rows[1], Qt.Key.Key_Return)
        self.assertFalse(chart.rows[1].isChecked())
        QTest.keyPress(chart.rows[1], Qt.Key.Key_Up)
        self.assertIs(self.window.focusWidget(), chart.rows[0])

    def test_small_window_scroll(self):
        self.window.resize(920, 700)
        self.window.show_page("schedule")
        self.window.show()
        self.application.processEvents()
        self.assertGreater(self.window.page_widgets["schedule"].verticalScrollBar().maximum(), 0)


class UsagePageTests(unittest.TestCase):
    """The 积分消耗 page renders a pre-parsed summary and never rescans on its own in tests."""

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = qt_app.Manager(query_tasks=False, scan_usage=False)

    def tearDown(self):
        self.window.close()
        self.application.processEvents()

    def test_usage_page_renders_local_consumption(self):
        when = dt.datetime(2026, 10, 31, 15, 5)
        bucket = {"prompt_tokens": 0, "completion_tokens": 0}
        fake = {"total": 3082.1, "calls": 1578, "first": when, "last": when, "files": 69, "loaded_at": "2026-11-01T16:00:00",
                "prompt_tokens": 12748772, "completion_tokens": 63399,
                "today": dict(bucket, credit=12.5, calls=3), "month": dict(bucket, credit=497.6, calls=90), "months": [dict(bucket, month="2026-10", credit=497.6, calls=90)],
                "days": [dict(day="2026-10-31", credit=497.6, calls=90, prompt_tokens=100, completion_tokens=10)],
                "models": [dict(model="GLM-5.1", credit=819.21, calls=171, prompt_tokens=12748772, completion_tokens=63399)],
                "sessions": [dict(bucket, session="446088aa-x", title="检查UI", credit=497.6, calls=90, last=when)]}
        self.assertIn("usage", self.window.nav)
        with patch.object(qt_app.usage_reader, "load", return_value=fake) as load:
            self.window.scan_usage = True
            self.window.load_usage(sync=True)
        load.assert_called_once()
        self.assertEqual(self.window.usage_total.text(), "3,082.10")
        self.assertEqual(self.window.usage_tiles["month"].text(), "497.60")
        self.assertEqual(self.window.usage_tiles["calls"].text(), "1,578")
        self.assertEqual(self.window.usage_tables["models"].item(0, 0).text(), "GLM-5.1")
        self.assertEqual(self.window.usage_tables["models"].item(0, 3).text(), "12.75M")
        self.assertEqual(self.window.usage_tiles["tokens"].text(), "12.81M")
        self.assertEqual(self.window.usage_tables["sessions"].item(0, 1).text(), "检查UI")
        self.assertIn("1578 次计费调用", self.window.usage_hint.text())
        with patch.object(qt_app.usage_reader, "load", side_effect=AssertionError("must reuse the loaded data")):
            self.window.show_page("usage")
            self.window.set_theme("dark")
        self.assertEqual(self.window.usage_tables["days"].item(0, 1).text(), "497.60")
        charts = self.window.usage_charts
        self.assertEqual(len(charts["days"].bars), 30)
        self.assertEqual(charts["months"].bars[0][0], "2026-10")
        self.assertEqual(charts["models"].bars, [("GLM-5.1", 819.21)])
        self.assertEqual(charts["tokens"].bars, [("GLM-5.1", 12812171.0)])
        self.assertIn("GLM-5.1 12.81M token", charts["tokens"].accessibleDescription())
        self.assertIn("GLM-5.1 819.21 积分", charts["models"].accessibleDescription())
        self.assertTrue(all(chart.mode == "dark" for chart in charts.values()))
        for chart in charts.values():
            chart.resize(420, 200)
            self.assertFalse(chart.grab().isNull())  # paints in both themes without raising

    def test_chart_ticks_and_empty_state(self):
        import qt_usage_chart
        self.assertEqual(qt_usage_chart.nice_ticks(0), [0.0])
        self.assertEqual(qt_usage_chart.nice_ticks(831.55), [0.0, 500.0, 1000.0, 1500.0])
        self.assertEqual(qt_usage_chart.nice_ticks(7.2), [0.0, 2.5, 5.0, 7.5])
        self.assertEqual(qt_usage_chart.nice_ticks(2.0), [0.0, 1.0, 2.0, 3.0])
        chart = qt_usage_chart.BarChart()
        chart.set_data([])
        chart.resize(300, 150)
        self.assertFalse(chart.grab().isNull())
        self.assertEqual(chart.accessibleDescription(), "暂无数据")

    def test_usage_page_reports_parse_failure_without_crashing(self):
        with patch.object(qt_app.usage_reader, "load", side_effect=OSError("磁盘不可读")):
            self.window.load_usage(sync=True)
        self.assertIn("读取失败", self.window.usage_hint.text())
        self.assertIsNone(self.window.usage_data)


if __name__ == "__main__":
    unittest.main()
