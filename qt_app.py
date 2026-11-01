"""Native Qt control panel; service/worker policy remains in the existing backend."""
import calendar
import copy
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback

from PySide6.QtCore import Qt, QObject, Signal, QRunnable, QThreadPool, QTimer, QSize
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QHBoxLayout, QVBoxLayout, QGridLayout,
    QScrollArea, QStackedWidget, QLabel, QPushButton, QLineEdit, QSpinBox, QComboBox,
    QPlainTextEdit, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QDialog, QFileDialog, QSizePolicy,
)

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import log_reader
import credits
import record_view
import scheduler
import storage
import usage_reader
from qt_theme import BORDER, INK, PALETTES, TONES, BrutalPanel, Switch, label, button, mark, panel, set_active, stylesheet
from qt_controls import TimeChips, TimePicker, FilterButton, NumberStepper, line_icon
from qt_chrome import MessageDialog, WindowBar, WindowButton
from qt_credit_chart import ExpiryChart
from qt_usage_chart import BarChart, HBarChart

RESULTS = {"SUCCESS": "签到成功", "CLAIMED": "签到成功", "ALREADY": "已签到", "STATUS": "状态查询",
           "GROWTH": "成长检查", "SKIPPED": "策略跳过", "ERROR": "执行失败", "NETWORK": "网络异常",
           "AUTH_REJECTED": "登录失效", "AUTH_HOLD": "等待登录", "NO_SESSION": "未登录",
           "TIMEOUT": "超时", "CANCELLED": "已停止", "BUSY": "运行中", "INACTIVE": "活动未开放"}
MODES = record_view.MODES


def runner_command(mode):
    """Worker launch line: the frozen exe is its own worker; source uses isolated python."""
    if storage.FROZEN:
        return [sys.executable, "--mode", mode]
    return [str(Path(sys.executable).with_name("python.exe")), "-I",
            str(ROOT / "runner.py"), "--mode", mode]


def display(value):
    return "—" if value is None or value == "" else str(value)


def timestamp(value):
    return display(value).replace("T", " ")[:19]


# Exit codes of the runner and of Task Scheduler itself, in words.
TASK_RESULTS = {0: "成功", 1: "未完成", 2: "已停止", 3: "跳过（已有运行）", 267009: "运行中", 267014: "已被终止"}
NEVER_RAN = 267011


def task_time(value):
    """Scheduler times as 今天 / 明天 HH:MM, so the next run reads at a glance."""
    try:
        moment = dt.datetime.fromisoformat(str(value).replace(" ", "T"))
    except ValueError:
        return display(value)
    day = {0: "今天", 1: "明天", -1: "昨天"}.get((moment.date() - dt.date.today()).days, moment.strftime("%m-%d"))
    return day + " " + moment.strftime("%H:%M")


CHIP_STYLES = {"chip": "padding:2px 8px;font-size:12px;font-weight:700;",
               "badge": "padding:4px 10px;font-size:13px;font-weight:700;"}


def restyle_chip(widget):
    """Inline chip colours follow the active tone set, so a theme switch re-applies them."""
    tone, kind = widget.property("chip_tone"), widget.property("chip_kind") or "chip"
    widget.setStyleSheet(f"background:{TONES[tone]};color:{INK};border:{BORDER}px solid {INK};" + CHIP_STYLES[kind])


def set_chip(widget, text, tone, kind="chip"):
    widget.setText(text)
    widget.setProperty("chip_tone", tone)
    widget.setProperty("chip_kind", kind)
    restyle_chip(widget)


def swatch(tone):
    box = BrutalPanel(tone, 0)
    box.setFixedSize(16, 16)
    return box


def status_data(state):
    value = state.get("last_status") or {}
    if not isinstance(value, dict):
        return {}
    if isinstance(value.get("body"), dict):
        value = value["body"]
    return value.get("data", value)


def checked(result):
    if not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError(result.get("error", "操作未完成") if isinstance(result, dict) else "操作未返回有效结果")
    return result


class Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Job(QRunnable):
    def __init__(self, action):
        super().__init__()
        self.action = action
        self.signals = Signals()

    def run(self):
        try:
            self.signals.done.emit(self.action())
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class Manager(QMainWindow):
    def __init__(self, page="overview", initial_theme="light", query_tasks=True, scan_usage=True):
        super().__init__()
        self.setWindowTitle("WorkBuddy 签到管理")
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        self.resize(1160, 820)
        self.setMinimumSize(900, 650)
        self.mode = initial_theme
        self.config_data = storage.load_config()
        self.records = []
        self.tasks = []
        self.busy = False
        self.querying = False
        self.jobs = set()
        self.month = dt.date.today().replace(day=1)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(3)
        self.current_page = page
        self.scan_usage = scan_usage
        self.build_shell()
        self.build_overview()
        self.build_schedule()
        self.build_growth()
        self.build_history()
        self.build_usage()
        self.set_theme(initial_theme)
        self.show_page(page)
        self.refresh_local()
        self.local_timer = QTimer(self)
        self.local_timer.timeout.connect(self.refresh_local)
        self.local_timer.start(5000)
        self.task_timer = QTimer(self)
        self.task_timer.timeout.connect(self.refresh_tasks)
        if query_tasks:
            self.task_timer.start(60000)
            QTimer.singleShot(100, self.refresh_tasks)
        icon = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/WorkBuddy/WorkBuddy.exe"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))

    def build_shell(self):
        base = QWidget()
        base_layout = QHBoxLayout(base)
        base_layout.setContentsMargins(0, 0, 0, 0)
        base_layout.setSpacing(0)
        self.setCentralWidget(base)
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(195)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(14, 20, 14, 18)
        side.setSpacing(10)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        brand.addWidget(mark("WB", "pink", 36))
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(label("WorkBuddy", "section"))
        names.addWidget(label("签到与自动化", "muted"))
        brand.addLayout(names, 1)
        side.addLayout(brand)
        side.addSpacing(14)
        self.nav = {}
        self.pages = QStackedWidget()
        for key, title in [("overview", "总览"), ("schedule", "时间与策略"),
                           ("growth", "成长中心"), ("history", "执行记录"), ("usage", "积分消耗")]:
            b = button(title, lambda _=False, k=key: self.show_page(k), "nav")
            b.setIconSize(QSize(17, 17))
            side.addWidget(b)
            self.nav[key] = b
        side.addStretch()
        side.addWidget(label("运行在这台电脑", "muted"))
        side.addWidget(label("窗口关闭后，\n自动任务继续。", "dim", True))
        side.addSpacing(6)
        self.theme_button = button("☾  切换深色", self.toggle_theme)
        side.addWidget(self.theme_button)
        base_layout.addWidget(self.sidebar)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        top = WindowBar(self)
        self.topbar = top
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(30, 9, 14, 9)
        top_layout.setSpacing(8)
        self.breadcrumb = label("WorkBuddy  /  总览", "muted")
        self.breadcrumb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        top_layout.addWidget(self.breadcrumb)
        top_layout.addStretch()
        self.runtime_label = label("●  就绪")
        self.runtime_label.setObjectName("runtime")
        self.runtime_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        top_layout.addWidget(self.runtime_label)
        top_layout.addSpacing(10)
        self.window_controls = {}
        for kind in ("minimize", "maximize", "close"):
            control = WindowButton(kind, self)
            top_layout.addWidget(control)
            self.window_controls[kind] = control
        body_layout.addWidget(top)
        body_layout.addWidget(self.pages, 1)
        self.footer = label("仅在本机读取记录 · 打开窗口不会执行签到", wrap=True)
        self.footer.setObjectName("footer")
        self.footer.setContentsMargins(30, 8, 30, 10)
        body_layout.addWidget(self.footer)
        base_layout.addWidget(body, 1)
        self.page_widgets = {}

    def page(self, key, title, subtitle):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(30, 26, 30, 24)
        layout.setSpacing(15)
        layout.addWidget(label(title, "title"))
        hint = label(subtitle, "muted", True)
        layout.addWidget(hint)
        scroll.setWidget(inner)
        self.page_widgets[key] = scroll
        self.pages.addWidget(scroll)
        return layout

    def build_overview(self):
        layout = self.page("overview", "每一天，都有记录。", "查看签到、积分与后台执行情况。历史数据缺失时标为未知，不推断为漏签。")
        hero, hero_layout = panel()
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(mark("签", "yellow"))
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label("今日签到", "section"))
        self.success_hint = label("成功时间未记录 · 活动累计不等于账号可用余额", "muted", True)
        titles.addWidget(self.success_hint)
        head.addLayout(titles, 1)
        self.today_badge = label("待确认", "badge")
        head.addWidget(self.today_badge, 0, Qt.AlignmentFlag.AlignTop)
        hero_layout.addLayout(head)
        metrics = QHBoxLayout()
        metrics.setSpacing(16)
        self.metrics = {}
        for key, tone, text, unit in [("credit", "yellow", "今日签到奖励", "积分"), ("streak", "blue", "连续签到", "天"),
                                      ("total", "orange", "活动累计", "积分")]:
            tile, value = self.tile(tone, text, unit)
            metrics.addWidget(tile, 1)
            self.metrics[key] = value
        hero_layout.addLayout(metrics)
        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addWidget(button("立即签到  ↗", lambda: self.start_run("checkin"), "primary"))
        actions.addWidget(button("刷新状态", lambda: self.start_run("status")))
        actions.addStretch()
        self.pause_button = button("暂停自动任务", self.toggle_automatic)
        actions.addWidget(self.pause_button)
        actions.addWidget(button("停止当前运行", self.cancel_run))
        hero_layout.addLayout(actions)
        layout.addWidget(hero)
        balance, balance_layout = panel(tone="green")
        balance_head = QHBoxLayout()
        balance_head.addWidget(label("账号可用积分", "section"))
        balance_head.addStretch()
        self.credits_button = button("刷新积分", self.refresh_credits)
        balance_head.addWidget(self.credits_button)
        balance_layout.addLayout(balance_head)
        balance_row = QHBoxLayout()
        self.credits_value = label("—", "metric")
        balance_row.addWidget(self.credits_value)
        balance_row.addWidget(label("积分", "tilelabel"), 0, Qt.AlignmentFlag.AlignBottom)
        balance_row.addStretch()
        self.credits_capacity = label("总额度 —  ·  已用 —", "muted", True)
        balance_row.addWidget(self.credits_capacity)
        balance_layout.addLayout(balance_row)
        self.credits_hint = label("只读查询 · 与签到活动累计分开 · 打开窗口不自动联网", "dim", True)
        balance_layout.addWidget(self.credits_hint)
        layout.addWidget(balance)
        expiry, expiry_layout = panel()
        expiry_head = QHBoxLayout()
        expiry_head.setSpacing(12)
        expiry_head.addWidget(mark("期", "blue"))
        expiry_head.addWidget(label("积分有效期", "section"))
        expiry_head.addStretch()
        self.credits_detail_button = button("收起  ▴", self.toggle_credit_detail, "ghost")
        expiry_head.addWidget(self.credits_detail_button)
        expiry_layout.addLayout(expiry_head)
        self.credits_expiry = label("未查询，刷新积分后显示", "muted", True)
        self.credits_expiry.hide()
        expiry_layout.addWidget(self.credits_expiry)
        self.credits_detail = ExpiryChart()
        expiry_layout.addWidget(self.credits_detail)
        layout.addWidget(expiry)
        lower = QHBoxLayout()
        lower.setSpacing(16)
        cal, cal_layout = panel(padding=20)
        cal.setMinimumWidth(325)
        cal_header = QHBoxLayout()
        self.month_label = label("", "section")
        cal_header.addWidget(self.month_label)
        cal_header.addStretch()
        for text, delta in (("‹", -1), ("›", 1)):
            step = button(text, lambda _=False, d=delta: self.shift_month(d))
            step.setFixedSize(40, 36)
            cal_header.addWidget(step)
        cal_layout.addLayout(cal_header)
        self.cal_grid = QGridLayout()
        self.cal_grid.setSpacing(4)
        cal_layout.addLayout(self.cal_grid)
        legend = QHBoxLayout()
        legend.setSpacing(6)
        for tone, text in (("green", "✓ 已签"), ("pink", "× 失败"), ("orange", "今天待确认")):
            legend.addWidget(swatch(tone))
            legend.addWidget(label(text, "dim"))
            legend.addSpacing(12)
        legend.addStretch()
        cal_layout.addLayout(legend)
        lower.addWidget(cal, 5)
        tasks, task_layout = panel(padding=20)
        tasks_head = QHBoxLayout()
        tasks_head.addWidget(label("自动任务", "section"))
        tasks_head.addStretch()
        tasks_head.addWidget(button("刷新", self.refresh_tasks, "ghost"))
        task_layout.addLayout(tasks_head)
        self.task_rows = []
        for title, tip in (("每日签到", "每天按设定时间签到一次"),
                           ("自动巡检", "整点检查：签到失败时重试，到点运行成长中心")):
            head = QHBoxLayout()
            name = label(title, "item")
            name.setToolTip(tip)
            head.addWidget(name)
            head.addStretch()
            chip = label()
            set_chip(chip, "读取中", "orange")
            head.addWidget(chip)
            grid = QGridLayout()
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(4)
            values = []
            for line, text in enumerate(("下次运行", "上次运行")):
                grid.addWidget(label(text, "dim"), line, 0)
                values.append(label("—", "mono", True))
                grid.addWidget(values[-1], line, 1)
            grid.setColumnStretch(1, 1)
            block = QVBoxLayout()
            block.setSpacing(8)
            block.addLayout(head)
            block.addLayout(grid)
            task_layout.addLayout(block)
            self.task_rows.append((chip, *values))
        task_layout.addStretch()
        lower.addWidget(tasks, 4)
        layout.addLayout(lower)
        recent, recent_layout = panel(padding=20)
        recent_layout.addWidget(label("最近执行", "section"))
        self.recent_layout = QVBoxLayout()
        self.recent_layout.setSpacing(10)
        recent_layout.addLayout(self.recent_layout)
        layout.addWidget(recent)
        layout.addStretch()

    def tile(self, tone, title, unit):
        tile, tile_layout = panel(padding=14, tone=tone)
        tile_layout.setSpacing(6)
        tile_layout.addWidget(label(title, "tilelabel"))
        row = QHBoxLayout()
        row.setSpacing(6)
        value = label("—", "tilevalue")
        row.addWidget(value)
        row.addWidget(label(unit, "tilelabel"), 0, Qt.AlignmentFlag.AlignBottom)
        row.addStretch()
        tile_layout.addLayout(row)
        return tile, value

    def setting_row(self, parent, title, hint, control):
        row = QHBoxLayout()
        row.setSpacing(18)
        text = QVBoxLayout()
        text.setSpacing(4)
        text.addWidget(label(title))
        text.addWidget(label(hint, "muted", True))
        text.addStretch()
        row.addLayout(text, 1)
        row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        parent.addLayout(row)
        return control

    def build_schedule(self):
        layout = self.page("schedule", "让自动化适合你的节奏。", "时间采用本机时区。变更只作用于这两个 WorkBuddy 任务，应用前会确认。")
        self.fields = {}
        times, time_layout = panel()
        time_layout.addWidget(label("执行时间", "section"))
        for key, title, hint in [("signin_times", "签到时间", "点击标签修改，使用 + 添加时间"),
                                 ("growth_times", "成长检查时间", "点击 × 删除；关闭成长中心后不执行")]:
            edit = TimeChips(self.config_data[key])
            edit.setObjectName(key)
            self.fields[key] = edit
            self.setting_row(time_layout, title, hint, edit)
        layout.addWidget(times)
        retry, retry_layout = panel()
        retry_layout.addWidget(label("签到策略", "section"))
        for key, title, hint in [("stop_after_success", "当天成功后停止签到", "确认成功后不再查询或领取；次日恢复"),
                                 ("retry_enabled", "失败时自动重试", "网络失败按间隔重试；登录失效会暂停")]:
            switch = Switch(self.config_data[key])
            switch.setAccessibleName(title)
            self.fields[key] = switch
            self.setting_row(retry_layout, title, hint, switch)
        for key, title, hint, low, high, suffix in [
            ("retry_interval_minutes", "重试间隔", "重试检查受当日次数限制", 15, 240, " 分钟"),
            ("retry_max_per_day", "每日重试上限", "不包含第一次签到尝试", 1, 48, " 次")]:
            spin = NumberStepper(low, high, self.config_data[key], suffix)
            self.fields[key] = spin
            self.setting_row(retry_layout, title, hint, spin)
        layout.addWidget(retry)
        conditions, c_layout = panel()
        c_layout.addWidget(label("运行条件", "section"))
        for key, title, hint in [("catch_up", "错过后补执行", "开机且可执行时补跑；不会在关机期间执行"),
                                 ("allow_battery", "允许电池供电执行", "不主动唤醒睡眠中的电脑")]:
            switch = Switch(self.config_data[key])
            self.fields[key] = switch
            self.setting_row(c_layout, title, hint, switch)
        layout.addWidget(conditions)
        actions = QHBoxLayout()
        actions.addWidget(button("保存并应用", self.apply_schedule, "primary"))
        actions.addStretch()
        actions.addWidget(button("恢复原脚本任务", self.restore_tasks, "ghost"))
        layout.addLayout(actions)
        layout.addWidget(label("需要电脑开机、联网并保持 Windows 用户登录。任务配置变更会先备份。", "dim", True))
        layout.addStretch()

    def build_growth(self):
        layout = self.page("growth", "只运行你允许的功能。", "每个资源操作都有独立开关。设置保存不会立即消费资源。")
        self.growth_fields = {}
        master, master_layout = panel()
        enabled = Switch(self.config_data["growth"]["enabled"])
        self.growth_fields["enabled"] = enabled
        self.setting_row(master_layout, "成长中心", "按独立时间检查；签到不受此总开关影响", enabled)
        layout.addWidget(master)
        options, options_layout = panel()
        for key, title, hint in [
            ("tasks", "任务奖励", "登记成长任务，并领取已完成任务的奖励"),
            ("travel", "Buddy 旅行", "领取礼物、派遣旅行 · 可能使用旅行能量"),
            ("makeup", "补登", "使用补登卡补齐符合条件的日期"),
            ("redeem", "连登兑换", "使用服务端提供的兑换资格"),
            ("lottery", "抽奖", "消耗可用抽奖次数"),
            ("blindbox", "Buddy / 盲盒", "消耗可用能量；旅行状态满足条件才执行")]:
            switch = Switch(self.config_data["growth"][key])
            switch.setAccessibleName(title)
            self.growth_fields[key] = switch
            self.setting_row(options_layout, title, hint, switch)
        layout.addWidget(options)
        actions = QHBoxLayout()
        actions.addWidget(button("保存功能设置", self.apply_growth, "primary"))
        actions.addWidget(button("立即检查", lambda: self.start_run("growth")))
        actions.addStretch()
        actions.addWidget(button("停止当前运行", self.cancel_run))
        layout.addLayout(actions)
        notes, n_layout = panel()
        n_layout.addWidget(label("执行状态", "section"))
        self.growth_result = label("当前未运行。停止不会撤销已经完成的服务端操作，也不会关闭 WorkBuddy。", "muted", True)
        n_layout.addWidget(self.growth_result)
        layout.addWidget(notes)
        layout.addStretch()

    def build_history(self):
        layout = self.page("history", "执行记录", "每行表示一次运行，不是一次签到。签到与成长新增分开显示；未知金额不算作 0。")
        summary, summary_layout = panel(padding=18, tone="blue")
        self.history_today = label("今日签到：待确认", "section", True)
        self.history_today_hint = label("实际领取时间未记录", "dim", True)
        summary_layout.addWidget(self.history_today)
        summary_layout.addWidget(self.history_today_hint)
        layout.addWidget(summary)
        filters = QHBoxLayout()
        self.date_filter = QLineEdit()
        self.date_filter.setPlaceholderText("YYYY-MM-DD")
        self.date_filter.setToolTip("按记录日期筛选，例如 2026-10-30")
        self.date_filter.setMaximumWidth(170)
        self.result_filter = FilterButton()
        self.result_filter.addItems(["全部结果", "成功 / 已签", "异常", "其他"])
        self.action_filter = FilterButton()
        self.action_filter.addItems(["全部操作", "签到", "成长", "状态"])
        filters.addWidget(self.date_filter)
        filters.addWidget(self.result_filter)
        filters.addWidget(self.action_filter)
        filters.addWidget(button("筛选", self.render_history))
        filters.addStretch()
        filters.addWidget(button("导出", self.export_records))
        layout.addLayout(filters)
        box, box_layout = panel(padding=12)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["记录时间", "运行方式", "签到 / 执行结果", "签到新增", "成长新增", "异常提示"])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        for col, width in [(0, 112), (1, 78), (3, 92), (4, 92), (5, 70)]:
            header.resizeSection(col, width)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setMinimumSectionSize(60)
        self.table.horizontalHeaderItem(0).setToolTip("本次后台运行的记录时间，不代表真正签到时间")
        self.table.horizontalHeaderItem(3).setToolTip("仅本次签到领取新增；已签或只查询为 0，未知不推断为 0")
        self.table.horizontalHeaderItem(4).setToolTip("本次成长任务实际返回的奖励，独立于签到奖励")
        self.table.setMinimumHeight(235)
        self.table.itemSelectionChanged.connect(self.select_record)
        box_layout.addWidget(self.table)
        layout.addWidget(box)
        detail, detail_layout = panel(padding=20)
        detail_layout.addWidget(label("记录详情", "section"))
        self.detail = label("选择一条记录。", "muted", True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_layout.addWidget(self.detail)
        self.raw_button = button("显示原始记录  ▾", self.toggle_raw, "ghost")
        detail_layout.addWidget(self.raw_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setFixedHeight(180)
        self.raw.hide()
        detail_layout.addWidget(self.raw)
        layout.addWidget(detail)
        actions = QHBoxLayout()
        actions.addWidget(button("打开原日志", lambda: self.open_path(storage.BASE_DIR.parent / "workbuddy-auto-signin/signin.log"), "ghost"))
        actions.addWidget(button("打开记录目录", lambda: self.open_path(storage.BASE_DIR / "data"), "ghost"))
        actions.addStretch()
        layout.addLayout(actions)
        layout.addStretch()
        self.filtered_records = []
        self.history_signature = None

    def build_usage(self):
        layout = self.page("usage", "积分消耗", "来自本机 WorkBuddy 会话记录（.workbuddy/projects）：每次模型调用的计费明细，只读统计。"
                                             "这是模型调用的消耗，与签到奖励、账号余额是不同口径。")
        hero, hero_layout = panel(tone="green")
        head = QHBoxLayout()
        head.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label("累计消耗", "section"))
        self.usage_hint = label("尚未读取本机会话记录。", "muted", True)
        titles.addWidget(self.usage_hint)
        head.addLayout(titles, 1)
        self.usage_refresh = button("重新读取", lambda: self.load_usage(force=True))
        head.addWidget(self.usage_refresh, 0, Qt.AlignmentFlag.AlignTop)
        hero_layout.addLayout(head)
        value_row = QHBoxLayout()
        value_row.setSpacing(8)
        self.usage_total = label("—", "metric")
        value_row.addWidget(self.usage_total)
        value_row.addWidget(label("积分", "item"), 0, Qt.AlignmentFlag.AlignBottom)
        value_row.addStretch()
        hero_layout.addLayout(value_row)
        layout.addWidget(hero)
        tiles = QHBoxLayout()
        tiles.setSpacing(14)
        self.usage_tiles = {}
        for key, title, tone in [("today", "今日消耗", "yellow"), ("month", "本月消耗", "blue"), ("calls", "计费调用次数", "orange"),
                                 ("tokens", "累计 token", "green")]:
            tile, tile_layout = panel(padding=14, tone=tone)
            tile_layout.setSpacing(4)
            tile_layout.addWidget(label(title, "tilelabel"))
            value = label("—", "tilevalue")
            tile_layout.addWidget(value)
            self.usage_tiles[key] = value
            tiles.addWidget(tile)
        layout.addLayout(tiles)
        charts = QHBoxLayout()
        charts.setSpacing(14)
        self.usage_charts = {}
        for key, title, hint, chart in [("months", "按月消耗", "从首条记录起逐月，空月为 0", BarChart("green")),
                                        ("days", "最近 30 天", "按自然日，没有调用的日子为 0", BarChart("green"))]:
            box, box_layout = panel(padding=16)
            box_layout.setSpacing(6)
            box_layout.addWidget(label(title, "section"))
            box_layout.addWidget(label(hint, "dim", True))
            box_layout.addWidget(chart)
            charts.addWidget(box, 1)
            self.usage_charts[key] = chart
        layout.addLayout(charts)
        for key, title, hint, chart in [("models", "按模型积分（前 10）", "条长只表示积分消耗", HBarChart("blue")),
                                        ("tokens", "按模型 token（前 10，单位 M）", "输入加输出 token，M = 百万", HBarChart("orange", usage_reader.token_text, "token"))]:
            box, box_layout = panel(padding=16)
            box_layout.setSpacing(6)
            box_layout.addWidget(label(title, "section"))
            box_layout.addWidget(label(hint, "dim", True))
            box_layout.addWidget(chart)
            layout.addWidget(box)
            self.usage_charts[key] = chart
        self.usage_tables = {}
        specs = [
            ("models", "按模型", ["模型", "积分", "调用", "输入 token (M)", "输出 token (M)"], [(1, 110), (2, 70), (3, 125), (4, 125)], 0),
            ("days", "按日（最近 30 天）", ["日期", "积分", "调用", "输入 token (M)", "输出 token (M)"], [(1, 110), (2, 70), (3, 125), (4, 125)], 0),
            ("sessions", "按会话（消耗最高的 20 个）", ["最近调用", "会话", "积分", "调用"], [(0, 130), (2, 110), (3, 70)], 1),
        ]
        for key, title, headers, widths, stretch in specs:
            box, box_layout = panel(padding=12)
            box_layout.addWidget(label(title, "section"))
            table = QTableWidget(0, len(headers))
            table.setHorizontalHeaderLabels(headers)
            table.verticalHeader().hide()
            table.setShowGrid(False)
            table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            header = table.horizontalHeader()
            header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
            for col, width in widths:
                header.resizeSection(col, width)
            header.setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
            header.setMinimumSectionSize(60)
            table.setFixedHeight(86)
            box_layout.addWidget(table)
            layout.addWidget(box)
            self.usage_tables[key] = table
        actions = QHBoxLayout()
        actions.addWidget(button("打开会话记录目录", lambda: self.open_path(usage_reader.projects_dir()), "ghost"))
        actions.addStretch()
        layout.addLayout(actions)
        layout.addStretch()
        self.usage_data = None
        self.usage_loading = False

    def load_usage(self, force=False, sync=False):
        """Parse the session logs off the UI thread; the page keeps the last result until asked again."""
        if self.usage_loading or (self.usage_data is not None and not force):
            return
        self.usage_loading = True
        self.usage_hint.setText("正在读取本机会话记录…")
        self.usage_refresh.setEnabled(False)

        def done(value):
            self.usage_loading = False
            self.usage_refresh.setEnabled(True)
            self.usage_data = value
            self.render_usage()

        def failed(text):
            self.usage_loading = False
            self.usage_refresh.setEnabled(True)
            self.usage_hint.setText("读取失败：" + text)

        if sync:
            try:
                done(usage_reader.load())
            except Exception as exc:
                failed(str(exc))
            return
        self.submit(usage_reader.load, done, failed)

    def render_usage(self):
        data = self.usage_data
        if not data or not hasattr(self, "usage_tables"):
            return
        p = PALETTES[self.mode]
        self.usage_total.setText(usage_reader.credit_text(data["total"]))
        span = f"{data['first']:%Y-%m-%d} ~ {data['last']:%Y-%m-%d}" if data.get("first") and data.get("last") else "无记录"
        self.usage_hint.setText(f"{data['calls']} 次计费调用  ·  {span}  ·  扫描 {data['files']} 个会话文件  ·  读取于 {timestamp(data.get('loaded_at'))}")
        self.usage_tiles["today"].setText(usage_reader.credit_text(data["today"]["credit"]))
        self.usage_tiles["month"].setText(usage_reader.credit_text(data["month"]["credit"]))
        self.usage_tiles["calls"].setText(f"{data['calls']:,}")
        self.usage_tiles["tokens"].setText(usage_reader.token_text(data.get("prompt_tokens", 0) + data.get("completion_tokens", 0)))
        self.usage_charts["months"].set_data(usage_reader.month_series(data))
        self.usage_charts["days"].set_data([(day[5:], value) for day, value in usage_reader.day_series(data)])
        self.usage_charts["models"].set_data([(m["model"], m["credit"]) for m in usage_reader.top_models(data["models"])])
        by_tokens = sorted(data["models"], key=lambda m: -(m["prompt_tokens"] + m["completion_tokens"]))
        self.usage_charts["tokens"].set_data([(m["model"], m["prompt_tokens"] + m["completion_tokens"]) for m in usage_reader.top_models(by_tokens)])
        left = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        right = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        credit, tokens = usage_reader.credit_text, usage_reader.token_text

        def fill(key, rows):
            table = self.usage_tables[key]
            table.setRowCount(len(rows))
            for row, cells in enumerate(rows):
                for col, (text, align, tone) in enumerate(cells):
                    item = QTableWidgetItem(text)
                    item.setTextAlignment(align)
                    if tone:
                        item.setForeground(QColor(p[tone]))
                    table.setItem(row, col, item)
                table.setRowHeight(row, 36)
            table.setFixedHeight(table.horizontalHeader().sizeHint().height() + 36 * max(len(rows), 1) + 8)

        fill("models", [[(m["model"], left, None), (credit(m["credit"]), right, "green"), (str(m["calls"]), right, None),
                         (tokens(m["prompt_tokens"]), right, "muted"), (tokens(m["completion_tokens"]), right, "muted")]
                        for m in data["models"]])
        fill("days", [[(d["day"], left, None), (credit(d["credit"]), right, "green"), (str(d["calls"]), right, None),
                       (tokens(d["prompt_tokens"]), right, "muted"), (tokens(d["completion_tokens"]), right, "muted")]
                      for d in data["days"][:30]])
        fill("sessions", [[(s["last"].strftime("%m-%d %H:%M") if s.get("last") else "—", left, "muted"),
                           ((s.get("title") or s["session"][:8]).replace("\n", " "), left, None),
                           (credit(s["credit"]), right, "green"), (str(s["calls"]), right, None)]
                          for s in data["sessions"][:20]])

    def nav_icon(self, key):
        return line_icon(key, INK if key == self.current_page else PALETTES[self.mode]["text"])

    def set_runtime(self, text, busy=False):
        self.runtime_label.setText("●  " + text)
        if self.runtime_label.property("state") != ("busy" if busy else "ready"):
            self.runtime_label.setProperty("state", "busy" if busy else "ready")
            self.runtime_label.style().unpolish(self.runtime_label)
            self.runtime_label.style().polish(self.runtime_label)

    def show_page(self, key):
        self.current_page = key
        self.pages.setCurrentWidget(self.page_widgets[key])
        for name, b in self.nav.items():
            b.setProperty("selected", name == key)
            b.setIcon(self.nav_icon(name))
            b.update()
        names = {"overview": "总览", "schedule": "时间与策略", "growth": "成长中心", "history": "执行记录", "usage": "积分消耗"}
        self.breadcrumb.setText("WorkBuddy  /  " + names[key])
        if key == "overview" and hasattr(self, "credits_value"):
            self.render_credits()
        if key == "usage" and self.scan_usage and hasattr(self, "usage_tables"):
            self.load_usage()

    def set_theme(self, mode):
        self.mode = mode
        set_active(mode)
        self.setStyleSheet(stylesheet(mode))
        for chip in self.findChildren(QLabel):
            if chip.property("chip_tone"):
                restyle_chip(chip)
        for switch in self.findChildren(Switch):
            switch.set_theme(mode)
        for key, nav in self.nav.items():
            nav.setIcon(self.nav_icon(key))
        self.theme_button.setText("☼  切换浅色" if mode == "dark" else "☾  切换深色")
        self.credits_detail.set_theme(mode)
        self.render_calendar()
        self.render_history(force=True)
        for chart in getattr(self, "usage_charts", {}).values():
            chart.set_theme(mode)
        self.render_usage()
        self.update()

    def toggle_theme(self):
        self.set_theme("light" if self.mode == "dark" else "dark")

    def refresh_local(self):
        try:
            self.records = log_reader.read_records()
            self.state = storage.load_state()
            self.summary = log_reader.daily_summary(self.records)
            self.data = status_data(self.state)
            today = dt.date.today().isoformat()
            fresh = str(self.state.get("last_query_at", "")).startswith(today)
            signed = self.summary.get("today_signed")
            if fresh and "today_checked_in" in self.data:
                signed = bool(self.data["today_checked_in"])
            set_chip(self.today_badge, "已签到" if signed else "未签到" if signed is False else "待确认",
                     "green" if signed else "pink" if signed is False else "orange", "badge")
            values = {"credit": self.summary.get("today_credit"), "streak": self.summary.get("streak_days"), "total": self.summary.get("total_credits")}
            if fresh:
                for key, field in [("credit", "today_credit"), ("streak", "streak_days"), ("total", "total_credits")]:
                    if self.data.get(field) is not None:
                        values[key] = self.data[field]
                if not signed:
                    values["credit"] = None
            for key, value in values.items():
                self.metrics[key].setText(display(value))
            success = self.summary.get("success_at")
            if self.state.get("confirmed_date") == today:
                success = success or self.state.get("success_at")
            self.success_hint.setText(("实际领取时间 " + timestamp(success) if success else "实际领取时间未记录") + "  ·  今日奖励不按检查次数累加  ·  活动累计 ≠ 账号可用余额")
            credit = values["credit"]
            self.history_today.setText("今日签到：" + self.today_badge.text() + "  ·  今日签到奖励：" + (display(credit) + " 分" if credit is not None else "未知"))
            self.history_today_hint.setText(dt.date.today().isoformat() + "  ·  " + ("实际领取时间 " + timestamp(success) if success else "实际领取时间未记录") + "  ·  当日奖励不是每次运行新增")
            runtime = self.read_runtime()
            self.set_runtime("运行中" if runtime.get("running") else "就绪", bool(runtime.get("running")))
            if runtime.get("running"):
                self.footer.setText("当前执行：" + str(runtime.get("stage") or runtime.get("mode")))
                self.growth_result.setText("运行中：" + str(runtime.get("stage") or runtime.get("mode")))
            elif not self.busy:
                self.footer.setText("最新记录 " + timestamp(self.summary.get("last_log_at")) + "  ·  状态查询 " + timestamp(self.state.get("last_query_at")))
            self.render_credits()
            self.render_calendar()
            self.render_recent()
            self.render_history()
        except Exception as exc:
            self.footer.setText("读取本机数据失败：" + str(exc))

    def render_credits(self):
        value = credits.load_cache()
        # A failed query keeps the last successful figures; the hint says when they were read.
        known = credits.has_balance(value)
        self.credits_value.setText(credits.amount(value.get("remaining")) if known else "—")
        self.credits_capacity.setText("总额度 " + credits.amount(value.get("total")) + "\n已用 " + credits.amount(value.get("used")))
        summary = credits.expiry_text(value).removeprefix("积分有效期：") if known else "未查询，刷新积分后显示"
        self.credits_expiry.setText("\n".join(filter(None, [credits.stale_note(value), summary])))
        self.credits_detail.set_data(value)
        hint = "查询时间 " + timestamp(value.get("queried_at")) + "  ·  签到/成长后自动更新"
        if not value:
            hint = "点击刷新积分进行只读查询 · 打开窗口不自动联网"
        elif not value.get("ok") and value.get("last_ok_at"):
            hint = ("上次成功于 " + timestamp(value["last_ok_at"]) + "，最近一次查询失败（" + timestamp(value.get("queried_at"))
                    + "）：" + str(value.get("error") or "积分读取失败") + "  ·  签到/成长后自动更新")
        elif not value.get("ok"):
            hint = str(value.get("error") or "积分读取失败") + "  ·  " + hint
        self.credits_hint.setText(hint)

    def toggle_credit_detail(self):
        expand = self.credits_detail.isHidden()
        self.credits_detail.setVisible(expand)
        self.credits_expiry.setVisible(not expand)
        self.credits_detail_button.setText("收起  ▴" if expand else "展开  ▾")

    def refresh_credits(self):
        if self.busy:
            self.footer.setText("请等待当前操作完成。")
            return
        self.credits_button.setEnabled(False)
        def done(value):
            self.credits_button.setEnabled(True)
            self.render_credits()
            self.set_runtime("就绪")
            self.footer.setText("积分查询完成（只读）" if value.get("ok") else str(value.get("error") or "积分查询失败"))
        def read():
            try:
                return credits.refresh()
            except Exception:
                return {"ok": False, "error": "积分查询或缓存写入失败"}
        self.work("查询积分", read, done)

    def render_calendar(self):
        if not hasattr(self, "cal_grid"):
            return
        while self.cal_grid.count():
            item = self.cal_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.month_label.setText(self.month.strftime("%Y 年 %m 月"))
        summary = getattr(self, "summary", {}).get("days", {})
        verified = set(getattr(self, "data", {}).get("checkin_dates", []))
        today = dt.date.today().isoformat()
        for col, text in enumerate("一二三四五六日"):
            w = label(text, "dim")
            w.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.cal_grid.addWidget(w, 0, col)
            self.cal_grid.setColumnStretch(col, 1)
        for row, week in enumerate(calendar.monthcalendar(self.month.year, self.month.month), 1):
            for col, day in enumerate(week):
                if not day:
                    continue
                date = self.month.replace(day=day).isoformat()
                status = "success" if date in verified else summary.get(date, {}).get("status", "unknown")
                symbol = "✓" if status == "success" else "×" if status == "failed" else ""
                b = button(f"{day}\n{symbol}" if symbol else str(day), lambda _=False, d=date: self.show_date(d), "day")
                b.setProperty("tone", status if status in ("success", "failed") else "future" if date > today else "unknown")
                b.setProperty("today", date == today)
                b.setFixedHeight(48)
                b.setToolTip(f"{date} · " + ("已签" if status == "success" else "失败" if status == "failed" else "尚未到达" if date > today else "未知"))
                self.cal_grid.addWidget(b, row, col)

    def shift_month(self, delta):
        year, month = self.month.year, self.month.month + delta
        if month < 1:
            year, month = year - 1, 12
        elif month > 12:
            year, month = year + 1, 1
        self.month = dt.date(year, month, 1)
        self.render_calendar()

    def show_date(self, date):
        self.date_filter.setText(date)
        self.show_page("history")
        self.render_history(force=True)

    def render_recent(self):
        while self.recent_layout.count():
            item = self.recent_layout.takeAt(0)
            if item.layout():
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()
            elif item.widget():
                item.widget().deleteLater()
        for record in list(reversed(self.records))[:3]:
            row = QHBoxLayout()
            row.addWidget(label(timestamp(record.get("recorded_at"))[11:], "mono"))
            row.addWidget(label(MODES.get(record.get("action"), "执行")))
            row.addStretch()
            row.addWidget(label(record_view.outcome(record, RESULTS.get(record.get("result"), record.get("result", "未知"))), "muted"))
            self.recent_layout.addLayout(row)
        if not self.records:
            self.recent_layout.addWidget(label("尚无执行记录。", "muted"))

    def render_tasks(self):
        for i, (chip, next_run, last_run) in enumerate(self.task_rows):
            t = self.tasks[i] if len(self.tasks) > i else {}
            chip.setToolTip("")
            if t.get("error"):
                set_chip(chip, "查询失败", "pink")
                chip.setToolTip(str(t["error"]))
                next_run.setText("—")
                last_run.setText("—")
                continue
            if not t:
                set_chip(chip, "读取中", "orange")
                continue
            if t.get("enabled") is None:
                set_chip(chip, "未创建", "pink")
            elif not t.get("enabled"):
                set_chip(chip, "已暂停", "orange")
            elif not t.get("managed"):
                set_chip(chip, "原脚本运行", "orange")
                chip.setToolTip("任务仍由原脚本执行；保存一次时间设置即改由本程序运行")
            else:
                set_chip(chip, "已启用", "green")
            next_run.setText(task_time(t.get("next_run")))
            code = t.get("last_result")
            last = "尚未运行" if code == NEVER_RAN else task_time(t.get("last_run"))
            if code is not None and code != NEVER_RAN:
                last += "  ·  " + TASK_RESULTS.get(code, f"退出码 {code}")
            last_run.setText(last)
            last_run.setToolTip(f"退出码 {code}，详情见执行记录" if code not in (None, 0, NEVER_RAN) else "")
        self.pause_button.setText("恢复自动任务" if self.tasks and all(t.get("enabled") is False for t in self.tasks) else "暂停自动任务")

    def render_history(self, force=False):
        if not hasattr(self, "table"):
            return
        signature = (repr(self.records), self.date_filter.text(), self.result_filter.currentIndex(), self.action_filter.currentIndex(), self.mode)
        if not force and signature == self.history_signature:
            return
        self.history_signature = signature
        wanted, group = self.result_filter.currentIndex(), self.action_filter.currentText()
        records = []
        for r in reversed(self.records):
            if self.date_filter.text().strip() not in str(r.get("recorded_at", "")):
                continue
            success = r.get("result") in {"CLAIMED", "SUCCESS", "ALREADY"}
            error = bool(r.get("needs_attention"))
            if (wanted == 1 and not success) or (wanted == 2 and not error) or (wanted == 3 and (success or error)):
                continue
            rgroup = "状态" if r.get("action") == "status" else "成长" if r.get("action") == "growth" else "签到"
            if group != "全部操作" and group != rgroup:
                continue
            records.append(r)
        self.filtered_records = records
        self.table.blockSignals(True)
        self.table.setRowCount(len(records))
        p = PALETTES[self.mode]
        for row, r in enumerate(records):
            values = [timestamp(r.get("recorded_at")).replace(" ", "\n", 1), MODES.get(r.get("action"), "执行"),
                      record_view.outcome(r, RESULTS.get(r.get("result"), r.get("result", "未知"))),
                      record_view.gain(record_view.checkin_credit(r)), record_view.gain(record_view.growth_credit(r)),
                      "需处理" if r.get("needs_attention") else "—"]
            for col, val in enumerate(values):
                item = QTableWidgetItem(str(val))
                item.setToolTip(timestamp(r.get("recorded_at")) if col == 0 else record_view.reward_detail(r) if col in {3, 4} else str(r.get("report") or val))
                if col in {0, 3, 4, 5}:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col == 2:
                    item.setForeground(QColor(p["red"] if r.get("needs_attention") else p["green"] if r.get("result") in {"SUCCESS", "CLAIMED"} else p["muted"]))
                if col in {3, 4} and str(val).startswith("+"):
                    item.setForeground(QColor(p["green"]))
                self.table.setItem(row, col, item)
            self.table.setRowHeight(row, 54)
        self.table.blockSignals(False)
        if records:
            self.table.selectRow(0)
        self.select_record()

    def select_record(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.filtered_records):
            self.detail.setText("暂无匹配记录。缺少日志不代表签到失败。")
            self.raw.clear()
            return
        r = self.filtered_records[row]
        growth = r.get("growth")
        if isinstance(growth, dict):
            growth = growth.get("report")
        text = (timestamp(r.get("recorded_at")) + "  ·  " + MODES.get(r.get("action"), "执行") + "  ·  "
                + record_view.outcome(r, RESULTS.get(r.get("result"), "执行结果")) + "\n" + record_view.reward_detail(r))
        # For confirmed results the report only repeats the outcome; keep it where it explains something.
        if r.get("report") and r.get("result") not in {"SUCCESS", "CLAIMED", "ALREADY", "STATUS", "GROWTH"}:
            text += "\n\n" + ("需处理：" if r.get("needs_attention") else "说明：") + str(r["report"])
        if growth:
            text += "\n\n成长中心\n" + str(growth).replace("；", "\n")
        self.detail.setText(text)
        self.raw.setPlainText(json.dumps(r, ensure_ascii=False, indent=2))

    def toggle_raw(self):
        self.raw.setVisible(not self.raw.isVisible())
        self.raw_button.setText("隐藏原始记录  ▴" if self.raw.isVisible() else "显示原始记录  ▾")

    def form_config(self):
        cfg = copy.deepcopy(self.config_data)
        for key, widget in self.fields.items():
            if key.endswith("_times"):
                cfg[key] = widget.times()
            elif isinstance(widget, Switch):
                cfg[key] = widget.isChecked()
            else:
                cfg[key] = widget.value()
        return storage.validate_config(cfg)

    def confirm(self, title, text):
        return MessageDialog(title, text, parent=self).exec() == QDialog.DialogCode.Accepted

    def save_apply(self, cfg):
        old = copy.deepcopy(self.config_data)
        checked(scheduler.apply_config(cfg))
        try:
            storage.save_config(cfg)
        except Exception:
            checked(scheduler.apply_config(old))
            raise
        return cfg

    def applied(self, cfg):
        self.config_data = cfg
        self.footer.setText("设置已保存并应用。")
        self.refresh_tasks()

    def apply_schedule(self):
        try:
            cfg = self.form_config()
        except ValueError as exc:
            self.error(str(exc))
            return
        text = f"签到时间：{', '.join(cfg['signin_times'])}\n成长时间：{', '.join(cfg['growth_times']) or '无'}\n重试间隔：{cfg['retry_interval_minutes']} 分钟，上限 {cfg['retry_max_per_day']} 次\n\n仅更新两个 WorkBuddy 任务，变更前自动备份。"
        if self.confirm("应用时间与策略？", text):
            self.work("保存设置", lambda: self.save_apply(cfg), self.applied)

    def apply_growth(self):
        cfg = copy.deepcopy(self.config_data)
        cfg["growth"] = {k: w.isChecked() for k, w in self.growth_fields.items()}
        cfg = storage.validate_config(cfg)
        active = [name for key, name in [("makeup", "补登"), ("redeem", "兑换"), ("lottery", "抽奖"), ("blindbox", "开盲盒")] if cfg["growth"][key]]
        text = "资源操作：" + ("、".join(active) if active else "补登、兑换、抽奖与盲盒全部关闭")
        text += "\n旅行：" + ("开启（可能使用能量）" if cfg["growth"]["travel"] else "关闭")
        if self.confirm("保存成长功能？", text + "\n\n此操作只保存配置，不立即执行。"):
            self.work("保存成长设置", lambda: self.save_apply(cfg), self.applied)

    def start_run(self, mode):
        if self.busy:
            self.footer.setText("请等待当前操作完成。")
            return
        messages = {"status": "只查询服务端状态，不领取积分，也不运行成长中心。",
                    "checkin": "先查询状态，未签才领取。不会执行成长中心。",
                    "growth": "执行已保存的成长功能；开启的资源操作可能产生消耗。"}
        if not self.confirm("确认执行？", messages[mode]):
            return
        def run():
            process = subprocess.Popen(runner_command(mode),
                                       cwd=os.path.join(os.environ["SystemRoot"], "System32"),
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                out, err = process.communicate(timeout=620)
            except subprocess.TimeoutExpired:
                runtime = self.read_runtime()
                if runtime.get("pid") == process.pid:
                    (storage.BASE_DIR / "cancel.request").write_text(str(process.pid), encoding="ascii")
                process.communicate(timeout=35)
                raise RuntimeError("运行超时，已请求管理器停止。")
            results = []
            for line in out.splitlines():
                try:
                    results.append(json.loads(line))
                except ValueError:
                    pass
            if not results:
                raise RuntimeError((err or "任务未返回结果")[:500])
            return results[-1]
        self.work(MODES[mode], run, self.finished_run)

    def finished_run(self, result):
        growth = result.get("growth")
        report = growth.get("report") if isinstance(growth, dict) else growth
        self.growth_result.setText(str(report or result.get("report") or "已完成"))
        self.refresh_local()
        self.refresh_tasks()
        message = str(result.get("report") or "执行完成")
        if result.get("credits_refreshed") is True:
            message += " · 积分已更新"
        elif result.get("credits_refreshed") is False:
            message += " · 积分未更新，请手动刷新"
        self.footer.setText(message)
        if result.get("needs_attention"):
            self.error(result.get("report", "操作需要处理"))

    def read_runtime(self):
        try:
            return json.loads((storage.BASE_DIR / "runtime.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def cancel_run(self):
        runtime = self.read_runtime()
        if not runtime.get("running"):
            self.footer.setText("当前没有管理器任务运行。")
            return
        if self.confirm("停止当前运行？", "只请求当前任务停止，不关闭 WorkBuddy。已完成的服务端操作无法撤销。"):
            (storage.BASE_DIR / "cancel.request").write_text(str(runtime.get("pid", "")), encoding="ascii")
            self.footer.setText("停止请求已发送，等待当前接口请求完成。")

    def toggle_automatic(self):
        enabled = bool(self.tasks and all(t.get("enabled") is False for t in self.tasks))
        title = "恢复自动任务？" if enabled else "暂停自动任务？"
        if self.confirm(title, "仅影响两个 WorkBuddy 自动任务；当前运行不会被强制停止。"):
            self.work("更新自动任务", lambda: checked(scheduler.set_enabled(enabled)), lambda _r: self.refresh_tasks())

    def restore_tasks(self):
        if not self.confirm("恢复原脚本任务？", "还原管理器接管前的任务设置。原脚本可能执行完整成长流程，不再受这里的功能开关限制。"):
            return
        self.work("恢复原任务", lambda: checked(scheduler.restore_original()), lambda _r: self.refresh_tasks())

    def export_records(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出当前筛选记录", "WorkBuddy记录.json", "JSON (*.json)")
        if path:
            try:
                Path(path).write_text(json.dumps(self.filtered_records, ensure_ascii=False, indent=2), encoding="utf-8")
                self.footer.setText("已导出当前筛选记录。")
            except OSError as exc:
                self.error(str(exc))

    def open_path(self, path):
        try:
            if not path.exists():
                raise FileNotFoundError("文件尚未生成：" + str(path))
            os.startfile(str(path))
        except OSError as exc:
            self.error(str(exc))

    def error(self, text):
        self.footer.setText(text)
        MessageDialog("操作未完成", text, accept_text="知道了", reject_text=None, glyph="!", tone="pink",
                      accept_variant=None, parent=self).exec()

    def submit(self, action, success, failure):
        job = Job(action)
        self.jobs.add(job)
        def done(value):
            self.jobs.discard(job)
            success(value)
        def failed(text):
            self.jobs.discard(job)
            failure(text)
        job.signals.done.connect(done)
        job.signals.failed.connect(failed)
        self.pool.start(job)

    def work(self, title, action, callback):
        if self.busy:
            self.footer.setText("请等待当前操作完成。")
            return
        self.busy = True
        self.set_runtime(title, True)
        def done(value):
            self.busy = False
            callback(value)
        def failed(text):
            self.busy = False
            self.error(text)
        self.submit(action, done, failed)

    def refresh_tasks(self):
        if self.querying:
            return
        self.querying = True
        def done(tasks):
            self.querying = False
            self.tasks = tasks
            self.render_tasks()
        def failed(text):
            self.querying = False
            self.tasks = [{"error": text}, {"error": text}]
            self.render_tasks()
        self.submit(scheduler.query_tasks, done, failed)

    def toggle_maximized(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()
        for control in self.window_controls.values():
            control.update()

    def nativeEvent(self, event_type, message):
        if os.name == "nt" and not self.isMaximized():
            import ctypes
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0084:  # WM_NCHITTEST, retain native edge resizing.
                rect = wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(int(self.winId()), ctypes.byref(rect))
                x = ctypes.c_short(msg.lParam & 0xffff).value
                y = ctypes.c_short((msg.lParam >> 16) & 0xffff).value
                margin = max(5, round(5 * self.devicePixelRatioF()))
                left, right = x < rect.left + margin, x >= rect.right - margin
                top, bottom = y < rect.top + margin, y >= rect.bottom - margin
                hit = 13 if top and left else 14 if top and right else 16 if bottom and left else 17 if bottom and right else 10 if left else 11 if right else 12 if top else 15 if bottom else None
                if hit is not None:
                    return True, hit
        return super().nativeEvent(event_type, message)

    def closeEvent(self, event):
        if self.busy and not self.confirm("关闭管理窗口？", "当前后台操作会继续完成，自动任务不受影响。"):
            event.ignore()
            return
        self.local_timer.stop()
        self.task_timer.stop()
        event.accept()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", choices=["overview", "schedule", "growth", "history"], default="overview")
    parser.add_argument("--theme", choices=["dark", "light"], default="light")
    parser.add_argument("--quit-after", type=int, default=0)
    args = parser.parse_args()
    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setStyle("Fusion")
    application.setFont(QFont("Segoe UI", 10))
    window = Manager(args.page, args.theme)
    window.show()
    if args.quit_after:
        QTimer.singleShot(args.quit_after * 1000, window.close)
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
