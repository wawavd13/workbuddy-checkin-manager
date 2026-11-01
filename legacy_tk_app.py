"""WorkBuddy local check-in manager. No account requests on window launch."""
import argparse
import calendar
import copy
import ctypes
import datetime as dt
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import log_reader
import credits
import record_view
import scheduler
import storage

BG = "#f3f6fc"
WHITE = "#ffffff"
INK = "#17243a"
MUTED = "#64748b"
BLUE = "#2563eb"
GREEN = "#15803d"
RED = "#b91c1c"
STATUS = {
    "SUCCESS": "签到成功", "CLAIMED": "签到成功", "ALREADY": "今日已签",
    "OK": "完成", "ERROR": "执行错误", "NETWORK": "网络异常",
    "TIMEOUT": "运行超时", "AUTH_REJECTED": "登录失效", "NO_AUTH": "未找到登录态",
    "NO_SESSION": "未登录", "INACTIVE": "活动未开放", "CANCELLED": "已停止",
    "BUSY": "正在运行", "SKIPPED": "策略跳过", "STATUS": "状态查询",
    "GROWTH": "成长中心", "UNKNOWN": "待确认", "success": "已签",
    "failed": "失败", "unknown": "未知",
}


def pretty(value):
    return "—" if value is None or value == "" else str(value)


def status_data(state):
    value = state.get("last_status") or {}
    if not isinstance(value, dict):
        return {}
    if isinstance(value.get("body"), dict):
        value = value["body"]
    return value.get("data", value) if isinstance(value.get("data", value), dict) else {}


def ensure_ok(result):
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError(result.get("error", "操作失败") if isinstance(result, dict) else "操作未返回成功结果")
    return result


class Manager(tk.Tk):
    def __init__(self, initial_page="overview"):
        super().__init__()
        self.title("WorkBuddy 签到管理")
        self.geometry("1400x940")
        self.minsize(1260, 820)
        self.configure(bg=BG)
        self.option_add("*Font", ("Microsoft YaHei UI", 10))
        self.config_data = storage.load_config()
        self.tasks = []
        self.records = []
        self.events = queue.Queue()
        self.busy = False
        self.closing = False
        self.task_querying = False
        self.current_page = ""
        self.month = dt.date.today().replace(day=1)
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._styles()
        self._shell()
        self.show_page(initial_page)
        self.after(100, self.refresh_local)
        self.after(250, self.refresh_tasks)
        self.after(60000, self.task_heartbeat)
        self.after(100, self.consume_events)

    def _styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=WHITE)
        style.configure("TLabel", background=BG, foreground=INK)
        style.configure("Card.TLabel", background=WHITE, foreground=INK)
        style.configure("Muted.TLabel", foreground=MUTED, background=BG)
        style.configure("TButton", padding=(13, 8), font=("Microsoft YaHei UI", 10))
        style.configure("Accent.TButton", foreground=WHITE, background=BLUE, borderwidth=0)
        style.map("Accent.TButton", background=[("active", "#1d4ed8"), ("disabled", "#a5b4cf")])
        style.configure("TCheckbutton", background=WHITE, padding=(2, 6))
        style.configure("Treeview", rowheight=33, background=WHITE, fieldbackground=WHITE,
                        foreground=INK, borderwidth=0, font=("Microsoft YaHei UI", 10))
        style.configure("Treeview.Heading", background="#edf2fa", foreground=MUTED,
                        font=("Microsoft YaHei UI", 10, "bold"), padding=8)
        style.map("Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", INK)])

    def _shell(self):
        side = tk.Frame(self, bg="#142138", width=250)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Label(side, text="WORKBUDDY", fg="#93c5fd", bg="#142138",
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=22, pady=(30, 4))
        tk.Label(side, text="签到管理", fg=WHITE, bg="#142138",
                 font=("Microsoft YaHei UI", 22, "bold")).pack(anchor="w", padx=20)
        tk.Label(side, text="本机运行 · 无需 AI 调用", fg="#94a3b8", bg="#142138",
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=20, pady=(8, 32))
        self.nav = {}
        for key, label in [("overview", "总览与签到日历"), ("schedule", "时间与签到策略"),
                           ("growth", "成长中心与运行"), ("history", "历史记录与日志")]:
            b = tk.Button(side, text=label, anchor="w", relief="flat", bd=0,
                          bg="#142138", fg="#dbe5f4", activebackground="#263b60",
                          activeforeground=WHITE, padx=18, pady=13,
                          command=lambda k=key: self.show_page(k))
            b.pack(fill="x", padx=10, pady=3)
            self.nav[key] = b
        tk.Label(side, text="关闭窗口后自动任务继续\n关机 / 休眠时无法执行\n只管理本应用的两个任务",
                 justify="left", fg="#94a3b8", bg="#142138", font=("Microsoft YaHei UI", 9),
                 wraplength=165).pack(side="bottom", anchor="w", padx=20, pady=26)
        container = tk.Frame(self, bg=BG)
        container.pack(side="left", fill="both", expand=True)
        header = tk.Frame(container, bg=WHITE)
        header.pack(fill="x")
        tk.Label(header, text="自动签到控制台", bg=WHITE, fg=INK,
                 font=("Microsoft YaHei UI", 13, "bold")).pack(side="left", padx=26, pady=14)
        self.running_label = tk.Label(header, text="就绪", bg=WHITE, fg=MUTED)
        self.running_label.pack(side="right", padx=24)
        self.content = ttk.Frame(container, padding=(25, 19))
        self.content.pack(fill="both", expand=True)
        self.footer = tk.StringVar(value="读取本机日志与任务信息；打开窗口不会自动查询账号或执行签到。")
        tk.Label(container, textvariable=self.footer, bg=BG, fg=MUTED, anchor="w",
                 font=("Microsoft YaHei UI", 9), wraplength=970).pack(fill="x", padx=25, pady=(0, 12))

    def heading(self, title, subtitle):
        tk.Label(self.content, text=title, bg=BG, fg=INK,
                 font=("Microsoft YaHei UI", 22, "bold")).pack(anchor="w")
        tk.Label(self.content, text=subtitle, bg=BG, fg=MUTED,
                 wraplength=940, justify="left").pack(anchor="w", pady=(5, 15))

    def card(self, parent, title=None):
        f = tk.Frame(parent, bg=WHITE, highlightbackground="#e2e8f0", highlightthickness=1,
                     padx=17, pady=13)
        if title:
            tk.Label(f, text=title, bg=WHITE, fg=INK,
                     font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=(0, 9))
        return f

    def show_page(self, key):
        for w in self.content.winfo_children():
            w.destroy()
        self.current_page = key
        for k, b in self.nav.items():
            b.configure(bg="#28446c" if k == key else "#142138")
        {"overview": self.overview, "schedule": self.schedule_page,
         "growth": self.growth_page, "history": self.history_page}[key]()

    def overview(self):
        self.heading("今天的签到，一目了然", "签到奖励与账号余额分开显示。缺少记录的日期标为未知，不推断为漏签。")
        cards = tk.Frame(self.content, bg=BG)
        cards.pack(fill="x")
        self.metrics = {}
        for i, (key, title) in enumerate([("status", "今日签到"), ("credit", "今日签到奖励"),
                                          ("streak", "连续签到"), ("total", "签到活动累计")]):
            cards.columnconfigure(i, weight=1, uniform="metric")
            f = self.card(cards)
            f.grid(row=0, column=i, sticky="nsew", padx=(0, 9 if i < 3 else 0))
            tk.Label(f, text=title, bg=WHITE, fg=MUTED).pack(anchor="w")
            value = tk.Label(f, text="—", bg=WHITE, fg=BLUE,
                             font=("Microsoft YaHei UI", 23, "bold"))
            value.pack(anchor="w", pady=(8, 2))
            self.metrics[key] = value
        self.overview_meta = tk.StringVar()
        tk.Label(self.content, textvariable=self.overview_meta, bg=BG, fg=MUTED,
                 wraplength=935, justify="left").pack(fill="x", pady=(11, 9))
        actions = tk.Frame(self.content, bg=BG)
        actions.pack(fill="x", pady=(0, 13))
        ttk.Button(actions, text="立即签到", style="Accent.TButton",
                   command=lambda: self.start_run("checkin")).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="刷新签到状态", command=lambda: self.start_run("status")).pack(side="left", padx=4)
        ttk.Button(actions, text="暂停自动任务", command=lambda: self.toggle_tasks(False)).pack(side="left", padx=4)
        ttk.Button(actions, text="恢复自动任务", command=lambda: self.toggle_tasks(True)).pack(side="left", padx=4)
        ttk.Button(actions, text="停止当前运行", command=self.cancel_run).pack(side="left", padx=4)
        balance = self.card(self.content, "账号可用积分（独立于签到活动累计）")
        balance.pack(fill="x", pady=(0, 12))
        self.credits_value = tk.Label(balance, text="—", bg=WHITE, fg=BLUE, font=("Microsoft YaHei UI", 23, "bold"))
        self.credits_value.pack(anchor="w")
        self.credits_hint = tk.Label(balance, bg=WHITE, fg=MUTED, wraplength=935, justify="left")
        self.credits_hint.pack(anchor="w", pady=(6, 6))
        ttk.Button(balance, text="刷新积分（只读）", command=self.refresh_credits).pack(anchor="w")
        ttk.Button(balance, text="查看各批次有效期", command=lambda: messagebox.showinfo("积分有效期", credits.expiry_detail(credits.load_cache()), parent=self)).pack(anchor="w")
        area = tk.Frame(self.content, bg=BG)
        area.pack(fill="both", expand=True)
        area.columnconfigure(0, weight=3, minsize=550)
        area.columnconfigure(1, weight=2, minsize=330)
        area.rowconfigure(0, weight=1)
        left = self.card(area)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        calhead = tk.Frame(left, bg=WHITE)
        calhead.pack(fill="x")
        ttk.Button(calhead, text="‹", width=3, command=lambda: self.shift_month(-1)).pack(side="left")
        self.calendar_title = tk.Label(calhead, bg=WHITE, fg=INK,
                                       font=("Microsoft YaHei UI", 12, "bold"))
        self.calendar_title.pack(side="left", padx=13)
        ttk.Button(calhead, text="›", width=3, command=lambda: self.shift_month(1)).pack(side="right")
        self.calendar_frame = tk.Frame(left, bg=WHITE)
        self.calendar_frame.pack(fill="both", expand=True, pady=(12, 5))
        tk.Label(left, text="绿色：已签   红色：失败且未确认成功   灰色：未知", bg=WHITE,
                 fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        right = self.card(area, "后台自动任务")
        right.grid(row=0, column=1, sticky="nsew")
        self.task_text = tk.Text(right, height=11, bg=WHITE, fg=INK, bd=0, wrap="word",
                                 font=("Microsoft YaHei UI", 10), cursor="arrow")
        self.task_text.pack(fill="both", expand=True)
        ttk.Button(right, text="刷新任务信息", command=self.refresh_tasks).pack(anchor="w", pady=(8, 0))
        tk.Label(right, text="可用余额：当前接口未支持\n活动累计 ≠ 当前可消费余额", bg=WHITE,
                 fg=MUTED, justify="left", font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(12, 0))
        self.render_overview()

    def render_overview(self):
        if self.current_page != "overview":
            return
        state = storage.load_state()
        data = status_data(state)
        summary = log_reader.daily_summary(self.records)
        today = dt.date.today().isoformat()
        fresh = str(state.get("last_query_at", "")).startswith(today)
        signed = summary.get("today_signed")
        if fresh and "today_checked_in" in data:
            signed = bool(data["today_checked_in"])
        text = "已签到" if signed else ("待签到" if signed is False else "待确认")
        values = {"status": text, "credit": summary.get("today_credit"),
                  "streak": summary.get("streak_days"), "total": summary.get("total_credits")}
        if fresh:
            for key, field in [("credit", "today_credit"), ("streak", "streak_days"), ("total", "total_credits")]:
                if data.get(field) is not None and (key != "credit" or signed):
                    values[key] = data[field]
            if not signed:
                values["credit"] = None
                if state.get("last_error"):
                    values["status"] = "签到异常"
        runtime = self.read_runtime()
        if runtime.get("running"):
            values["status"] = "查询中" if runtime.get("mode") == "status" else "运行中"
        for key, value in values.items():
            self.metrics[key].configure(text=pretty(value), fg=GREEN if key == "status" and signed else BLUE)
        self.overview_meta.set(
            "今日成功时间：%s   |   最新日志：%s\n账号状态查询：%s   |   今日成长到账：%s（仅实际返回值）" %
            (pretty(summary.get("success_at") or state.get("success_at") if state.get("confirmed_date") == today else summary.get("success_at")),
             pretty(summary.get("last_log_at")).replace("T", " "), pretty(state.get("last_query_at")).replace("T", " "), pretty(summary.get("today_growth_credit"))))
        self.calendar_title.configure(text=self.month.strftime("%Y 年 %m 月"))
        for w in self.calendar_frame.winfo_children():
            w.destroy()
        for col, text in enumerate("一二三四五六日"):
            self.calendar_frame.columnconfigure(col, weight=1, uniform="day")
            tk.Label(self.calendar_frame, text=text, bg=WHITE, fg=MUTED).grid(row=0, column=col, pady=(0, 6))
        days = summary.get("days", {})
        verified = set(data.get("checkin_dates", []))
        for row, week in enumerate(calendar.monthcalendar(self.month.year, self.month.month), 1):
            self.calendar_frame.rowconfigure(row, weight=1)
            for col, day in enumerate(week):
                if not day:
                    continue
                date = self.month.replace(day=day).isoformat()
                item = days.get(date, {})
                state_name = "success" if date in verified else item.get("status", "unknown")
                future = date > today
                label = "未到" if future else STATUS.get(state_name, "未知")
                color = GREEN if state_name == "success" else RED if state_name == "failed" else MUTED
                b = tk.Button(self.calendar_frame, text=f"{day}\n{label}", bg="#f0fdf4" if state_name == "success" else "#f8fafc",
                              fg=color, bd=0, relief="flat", padx=2, pady=8,
                              highlightthickness=1 if date == today else 0, highlightbackground=BLUE,
                              command=lambda d=date: self.show_date(d))
                b.grid(row=row, column=col, sticky="nsew", padx=3, pady=3)
        self.render_credits()
        self.render_tasks()

    def render_credits(self):
        value = credits.load_cache()
        self.credits_value.configure(text=credits.amount(value.get("remaining")) + " 积分" if value.get("ok") else "—")
        text = ("总额度 " + credits.amount(value.get("total")) + "  ·  已用 " + credits.amount(value.get("used")) + "\n"
                + credits.expiry_text(value) + "\n查询时间 " + pretty(value.get("queried_at")).replace("T", " ")) if value.get("ok") else str(value.get("error") or "点击刷新积分进行只读查询；打开窗口不自动联网")
        self.credits_hint.configure(text=text)

    def refresh_credits(self):
        def done(value):
            if self.current_page == "overview":
                self.render_credits()
            self.footer.set("积分查询完成（只读）" if value.get("ok") else str(value.get("error") or "积分查询失败"))
        self.work("查询账号积分（只读）", credits.refresh, done)

    def shift_month(self, delta):
        y, m = self.month.year, self.month.month + delta
        if m == 0:
            y, m = y - 1, 12
        elif m == 13:
            y, m = y + 1, 1
        self.month = dt.date(y, m, 1)
        self.render_overview()

    def show_date(self, date):
        self.show_page("history")
        self.filter_date.set(date)
        self.render_history()

    def render_tasks(self):
        if self.current_page != "overview":
            return
        parts = []
        for t in self.tasks:
            label = "每日签到" if t.get("name") == "WorkBuddyAutoSignin" else "成长 / 重试检查"
            if t.get("error"):
                parts.append(f"{label}\n查询失败：{t['error']}")
                continue
            enabled = "已启用" if t.get("enabled") else "已暂停"
            result = t.get("last_result")
            parts.append(f"{label} · {enabled}\n最近运行：{pretty(t.get('last_run'))}\n"
                         f"退出码：{pretty(result)}{'（成功）' if result == 0 else ''}\n"
                         f"下次运行：{pretty(t.get('next_run'))}\n"
                         f"执行入口：{'管理器' if t.get('managed') else '原脚本'}")
        self.set_text(self.task_text, "\n\n".join(parts) or "正在查询 Windows 任务计划程序…")

    def schedule_page(self):
        self.heading("按你的时间与策略执行", "保存前会展示配置摘要。只调整两个 WorkBuddy 任务，关闭窗口不影响后台执行。")
        card = self.card(self.content, "执行时间（本机时间，24 小时制）")
        card.pack(fill="x", pady=(0, 12))
        self.settings = {}
        for key, label in [("signin_times", "每日签到时间"), ("growth_times", "成长检查时间")]:
            row = tk.Frame(card, bg=WHITE)
            row.pack(fill="x", pady=5)
            tk.Label(row, text=label, width=16, anchor="w", bg=WHITE, fg=INK).pack(side="left")
            var = tk.StringVar(value=", ".join(self.config_data.get(key, [])))
            self.settings[key] = var
            ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)
        tk.Label(card, text="多个时间用逗号分隔，例如 08:00, 12:30, 20:00。成长时间与签到时间分别设置。",
                 bg=WHITE, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(7, 0))
        grid = tk.Frame(self.content, bg=BG)
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)
        retry = self.card(grid, "成功停止与失败重试")
        retry.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        for key, text in [("stop_after_success", "当天确认成功后，不再查询 / 领取签到"),
                          ("retry_enabled", "自动重试失败的签到")]:
            var = tk.BooleanVar(value=self.config_data.get(key, True))
            self.settings[key] = var
            ttk.Checkbutton(retry, text=text, variable=var).pack(anchor="w")
        for key, text, lo, hi in [("retry_interval_minutes", "重试间隔（分钟）", 15, 240),
                                  ("retry_max_per_day", "每日自动重试上限", 1, 48)]:
            r = tk.Frame(retry, bg=WHITE)
            r.pack(fill="x", pady=7)
            tk.Label(r, text=text, bg=WHITE, fg=INK).pack(side="left")
            var = tk.StringVar(value=str(self.config_data.get(key, lo)))
            self.settings[key] = var
            ttk.Spinbox(r, from_=lo, to=hi, width=7, textvariable=var).pack(side="right")
        opts = self.card(grid, "运行条件")
        opts.grid(row=0, column=1, sticky="nsew")
        for key, text in [("catch_up", "错过时间后，在可运行时补执行"),
                          ("allow_battery", "允许电池供电时执行")]:
            var = tk.BooleanVar(value=self.config_data.get(key, True))
            self.settings[key] = var
            ttk.Checkbutton(opts, text=text, variable=var).pack(anchor="w")
        tk.Label(opts, text="不会自动唤醒休眠电脑。\n用户需保持 Windows 登录。\n登录失效后自动重试会停止；\n重新登录后可手动签到恢复。", bg=WHITE,
                 fg=MUTED, justify="left").pack(anchor="w", pady=(13, 0))
        self.config_summary = tk.StringVar()
        tk.Label(self.content, textvariable=self.config_summary, bg=BG, fg=MUTED,
                 wraplength=945, justify="left").pack(anchor="w", pady=14)
        bar = tk.Frame(self.content, bg=BG)
        bar.pack(fill="x")
        ttk.Button(bar, text="保存并应用时间 / 策略", style="Accent.TButton",
                   command=self.apply_settings).pack(side="left")
        ttk.Button(bar, text="恢复原脚本任务", command=self.restore_tasks).pack(side="right")
        self.config_summary.set("重试检查按设置间隔触发，并受当日次数限制；成功后可直接跳过签到。\n"
                                "恢复原脚本任务会还原安装前的时间与动作（原脚本可能执行整套成长功能）。")

    def parse_form(self):
        cfg = copy.deepcopy(self.config_data)
        for key, var in self.settings.items():
            val = var.get()
            if key.endswith("_times"):
                val = [s.strip() for s in str(val).replace("，", ",").split(",") if s.strip()]
            elif key.startswith("retry_") and key != "retry_enabled":
                val = int(val)
            cfg[key] = val
        return storage.validate_config(cfg)

    def save_and_apply(self, cfg):
        old = copy.deepcopy(self.config_data)
        ensure_ok(scheduler.apply_config(cfg))
        try:
            storage.save_config(cfg)
        except Exception:
            ensure_ok(scheduler.apply_config(old))
            raise
        return cfg

    def apply_settings(self):
        try:
            cfg = self.parse_form()
        except (ValueError, TypeError) as exc:
            messagebox.showerror("设置无效", str(exc), parent=self)
            return
        summary = f"签到：{', '.join(cfg['signin_times'])}\n成长：{', '.join(cfg['growth_times']) or '不定时执行'}\n"
        summary += f"重试：{'开启' if cfg['retry_enabled'] else '关闭'}，间隔 {cfg['retry_interval_minutes']} 分钟，上限 {cfg['retry_max_per_day']} 次\n"
        summary += f"当天成功后停止签到：{'是' if cfg['stop_after_success'] else '否'}\n\n应用会更新本应用的两个计划任务，并先备份。"
        if messagebox.askokcancel("确认应用策略", summary, parent=self):
            self.work("正在应用时间与策略…", lambda: self.save_and_apply(cfg), self.config_applied)

    def config_applied(self, cfg):
        self.config_data = cfg
        self.footer.set("设置已保存并应用，两个自动任务已由管理器控制。")
        self.refresh_tasks()

    def growth_page(self):
        self.heading("成长功能，由你选择", "资源消耗功能默认关闭。旅行沿用原配置；补登、兑换、抽奖需要明确启用。")
        self.growth_vars = {}
        card = self.card(self.content, "功能开关")
        card.pack(fill="x")
        options = [("enabled", "启用成长中心自动检查"),
                   ("tasks", "登记成长任务 / 领取已完成任务奖励"),
                   ("travel", "领取旅行礼物 / 派遣 Buddy（可能使用旅行能量）"),
                   ("makeup", "补登（消耗补登卡）"),
                   ("redeem", "连登兑换（按服务端规则使用兑换资格）"),
                   ("lottery", "抽奖（消耗抽奖次数）"),
                   ("blindbox", "开启 Buddy / 盲盒（消耗能量）")]
        for key, label in options:
            var = tk.BooleanVar(value=self.config_data.get("growth", {}).get(key, False))
            self.growth_vars[key] = var
            ttk.Checkbutton(card, text=label, variable=var).pack(anchor="w")
        bar = tk.Frame(self.content, bg=BG)
        bar.pack(fill="x", pady=14)
        ttk.Button(bar, text="保存成长功能", style="Accent.TButton", command=self.apply_growth).pack(side="left")
        ttk.Button(bar, text="立即执行成长检查", command=lambda: self.start_run("growth")).pack(side="left", padx=8)
        ttk.Button(bar, text="停止当前运行", command=self.cancel_run).pack(side="right")
        self.execution_text = tk.Text(self.content, height=8, bg=WHITE, fg=INK, relief="flat",
                                      wrap="word", padx=15, pady=12, font=("Microsoft YaHei UI", 10))
        self.execution_text.pack(fill="both", expand=True)
        self.set_text(self.execution_text, "当前没有手动执行。\n\n停止仅作用于管理器的当前任务，不关闭 WorkBuddy。\n已发送的接口操作无法撤销。保存设置不会立即消费资源。")

    def apply_growth(self):
        cfg = copy.deepcopy(self.config_data)
        cfg["growth"] = {key: var.get() for key, var in self.growth_vars.items()}
        try:
            cfg = storage.validate_config(cfg)
        except ValueError as exc:
            messagebox.showerror("设置无效", str(exc), parent=self)
            return
        chosen = [key for key in ("makeup", "redeem", "lottery", "blindbox") if cfg["growth"][key]]
        text = "启用的资源操作：" + ("、".join(chosen) if chosen else "无补登 / 兑换 / 抽奖 / 开盲盒")
        text += "\n旅行：" + ("启用（可能使用旅行能量）" if cfg["growth"]["travel"] else "关闭")
        if messagebox.askokcancel("确认成长设置", text + "\n\n仅保存配置并更新自动任务，不立即执行。", parent=self):
            self.work("正在保存成长设置…", lambda: self.save_and_apply(cfg), self.config_applied)

    def history_page(self):
        self.heading("每一次执行，都有记录", "每行表示一次运行，不是一次签到。签到与成长新增分开显示；未知金额不算作 0。")
        self.history_today = tk.StringVar()
        tk.Label(self.content, textvariable=self.history_today, bg=BG, fg=INK,
                 wraplength=935, justify="left").pack(fill="x", pady=(0, 12))
        bar = tk.Frame(self.content, bg=BG)
        bar.pack(fill="x", pady=(0, 12))
        self.filter_date = tk.StringVar()
        self.filter_status = tk.StringVar(value="全部")
        self.filter_action = tk.StringVar(value="全部")
        tk.Label(bar, text="日期", bg=BG, fg=MUTED).pack(side="left")
        ttk.Entry(bar, textvariable=self.filter_date, width=13).pack(side="left", padx=7)
        ttk.Combobox(bar, textvariable=self.filter_status, values=["全部", "成功 / 已签", "异常", "其他"],
                     state="readonly", width=13).pack(side="left", padx=5)
        ttk.Combobox(bar, textvariable=self.filter_action, values=["全部", "签到", "成长", "状态"],
                     state="readonly", width=8).pack(side="left", padx=5)
        ttk.Button(bar, text="筛选", command=self.render_history).pack(side="left", padx=5)
        ttk.Button(bar, text="导出记录", command=self.export_records).pack(side="right")
        pane = ttk.Panedwindow(self.content, orient="vertical")
        pane.pack(fill="both", expand=True)
        table = tk.Frame(pane, bg=WHITE)
        self.history_tree = ttk.Treeview(table, columns=("time", "action", "result", "credit", "growth", "attention"), show="headings", height=7)
        for key, text, width in [("time", "记录时间", 175), ("action", "运行方式", 95), ("result", "签到 / 执行结果", 200),
                                 ("credit", "签到新增", 95), ("growth", "成长新增", 95), ("attention", "异常提示", 95)]:
            self.history_tree.heading(key, text=text)
            self.history_tree.column(key, width=width, minwidth=75, stretch=True)
        sb = ttk.Scrollbar(table, orient="vertical", command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=sb.set)
        self.history_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        pane.add(table, weight=3)
        details = tk.Frame(pane, bg=WHITE)
        self.history_detail = tk.Text(details, wrap="word", bg=WHITE, fg=INK, bd=0, padx=15, pady=12,
                                      font=("Microsoft YaHei UI", 10), height=9)
        detail_scroll = ttk.Scrollbar(details, orient="vertical", command=self.history_detail.yview)
        self.history_detail.configure(yscrollcommand=detail_scroll.set)
        self.history_detail.pack(side="left", fill="both", expand=True)
        detail_scroll.pack(side="right", fill="y")
        pane.add(details, weight=2)
        self.history_tree.bind("<<TreeviewSelect>>", self.select_record)
        self.history_records = {}
        self.history_signature = None
        bottom = tk.Frame(self.content, bg=BG)
        bottom.pack(fill="x", pady=(12, 0))
        ttk.Button(bottom, text="打开原日志", command=lambda: self.open_path(Path(os.environ["USERPROFILE"]) / "Tools/workbuddy-auto-signin/signin.log")).pack(side="left")
        ttk.Button(bottom, text="打开管理器日志目录", command=lambda: self.open_path(ROOT)).pack(side="left", padx=8)
        self.render_history()

    def render_history(self):
        if self.current_page != "history":
            return
        summary = log_reader.daily_summary(self.records)
        state = storage.load_state()
        data = status_data(state)
        today = dt.date.today().isoformat()
        signed, credit = summary.get("today_signed"), summary.get("today_credit")
        if str(state.get("last_query_at", "")).startswith(today) and "today_checked_in" in data:
            signed = bool(data["today_checked_in"])
            credit = data.get("today_credit", credit) if signed else None
        status = "已签到" if signed else "未签到" if signed is False else "待确认"
        success = summary.get("success_at") or (state.get("success_at") if state.get("confirmed_date") == today else None)
        self.history_today.set(f"今日签到：{status}  ·  今日签到奖励：{pretty(credit)} 分（当日奖励，不是每次运行新增）\n"
                               + ("实际领取时间：" + str(success).replace("T", " ") if success else "实际领取时间未记录"))
        filters = (self.filter_date.get(), self.filter_status.get(), self.filter_action.get())
        signature = (repr(self.records), filters)
        if getattr(self, "history_signature", None) == signature:
            return
        self.history_signature = signature
        selected = self.history_tree.selection()
        selected_record = self.history_records.get(selected[0]) if selected else None
        self.history_tree.delete(*self.history_tree.get_children())
        self.history_records = {}
        for i, record in enumerate(reversed(self.records)):
            if self.filter_date.get().strip() not in str(record.get("recorded_at", "")):
                continue
            result = str(record.get("result", "UNKNOWN"))
            wanted = self.filter_status.get()
            success = result in ("SUCCESS", "ALREADY", "CLAIMED")
            error = bool(record.get("needs_attention"))
            if (wanted == "成功 / 已签" and not success) or (wanted == "异常" and not error) or (wanted == "其他" and (success or error)):
                continue
            action = str(record.get("action", ""))
            group = "状态" if action == "status" else "成长" if action == "growth" else "签到"
            if self.filter_action.get() != "全部" and self.filter_action.get() != group:
                continue
            iid = str(i)
            self.history_records[iid] = record
            self.history_tree.insert("", "end", iid=iid, values=(
                                     str(record.get("recorded_at", "")).replace("T", " ")[:19], record_view.MODES.get(action, "执行"),
                                     record_view.outcome(record, STATUS.get(result, result)), record_view.gain(record_view.checkin_credit(record)),
                                     record_view.gain(record_view.growth_credit(record)), "需要处理" if error else "—"))
            if selected_record == record:
                self.history_tree.selection_set(iid)
        if not self.history_tree.selection() and self.history_tree.get_children():
            self.history_tree.selection_set(self.history_tree.get_children()[0])
        self.select_record()

    def select_record(self, _event=None):
        chosen = self.history_tree.selection()
        if not chosen:
            self.set_text(self.history_detail, "暂无匹配记录。缺少日志不代表签到失败。")
            return
        r = self.history_records[chosen[0]]
        growth = r.get("growth")
        if isinstance(growth, dict):
            growth = growth.get("report") or "无成长记录"
        growth = str(growth or "无成长记录").replace("；", "\n• ")
        text = f"{r.get('recorded_at', '')}  ·  {record_view.outcome(r, STATUS.get(str(r.get('result')), r.get('result')))}\n\n"
        text += record_view.reward_detail(r) + f"\n\n{r.get('report', '')}\n\n成长中心\n• {growth}\n\n原始记录（已脱敏）\n"
        text += json.dumps(r, ensure_ascii=False, indent=2)
        self.set_text(self.history_detail, text)

    def export_records(self):
        path = filedialog.asksaveasfilename(parent=self, title="导出当前筛选记录", defaultextension=".json",
                                           initialfile="WorkBuddy签到记录.json", filetypes=[("JSON", "*.json")])
        if path:
            try:
                Path(path).write_text(json.dumps(list(self.history_records.values()), ensure_ascii=False, indent=2), encoding="utf-8")
                self.footer.set("已导出当前筛选记录：" + path)
            except OSError as exc:
                messagebox.showerror("导出失败", str(exc), parent=self)

    def open_path(self, path):
        try:
            if not path.exists():
                raise FileNotFoundError("文件尚未生成：" + str(path))
            os.startfile(str(path))
        except OSError as exc:
            messagebox.showerror("无法打开", str(exc), parent=self)

    def start_run(self, mode):
        if self.busy:
            messagebox.showinfo("任务处理中", "请等待当前操作结束。", parent=self)
            return
        text = {"status": "只查询服务端签到状态，不领取、不执行成长任务。",
                "checkin": "查询状态，尚未签到才领取积分；不会执行成长中心。",
                "growth": "按照已保存的成长开关运行，可能使用已启用项目的活动资源。"}[mode]
        if not messagebox.askokcancel("确认执行", text, parent=self):
            return
        def execute():
            exe = str(Path(sys.executable).with_name("python.exe"))
            proc = subprocess.Popen([exe, "-I", str(ROOT / "runner.py"), "--mode", mode],
                                    cwd=os.path.join(os.environ["SystemRoot"], "System32"),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                    errors="replace", creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                out, err = proc.communicate(timeout=620)
            except subprocess.TimeoutExpired:
                runtime = self.read_runtime()
                if runtime.get("pid") == proc.pid:
                    (ROOT / "cancel.request").write_text(str(proc.pid), encoding="ascii")
                proc.communicate(timeout=35)
                raise RuntimeError("运行超时，已请求停止本管理器任务；已完成的服务端操作不会撤销。")
            records = []
            for line in out.splitlines():
                try:
                    records.append(json.loads(line))
                except ValueError:
                    pass
            result = records[-1] if records else {"report": "运行未返回结果", "needs_attention": True}
            if proc.returncode and not records:
                raise RuntimeError((err or "执行失败").strip()[:800])
            return result
        self.work("正在" + ("查询签到状态…" if mode == "status" else "执行任务…"), execute, self.run_finished)

    def run_finished(self, result):
        report = result.get("report") or str(result)
        self.footer.set(report)
        if self.current_page == "growth":
            self.set_text(self.execution_text, json.dumps(result, ensure_ascii=False, indent=2))
        self.refresh_local(reschedule=False)
        self.refresh_tasks()
        if result.get("needs_attention"):
            messagebox.showwarning("需要处理", report, parent=self)

    def cancel_run(self):
        if not messagebox.askokcancel("停止当前运行", "请求当前管理器任务停止。已完成的接口操作不会撤销；不会关闭 WorkBuddy。", parent=self):
            return
        try:
            runtime = self.read_runtime()
            if not runtime.get("running"):
                self.footer.set("当前没有管理器任务运行。")
                return
            (ROOT / "cancel.request").write_text(str(runtime.get("pid", "")), encoding="ascii")
            self.footer.set("已请求停止，等待当前请求结束。")
        except OSError as exc:
            messagebox.showerror("停止失败", str(exc), parent=self)

    def toggle_tasks(self, enabled):
        text = "恢复两个自动任务。" if enabled else "暂停两个自动任务；正在运行的任务不会因此自动停止。"
        if messagebox.askokcancel("确认自动执行状态", text, parent=self):
            self.work("正在更新自动任务状态…", lambda: ensure_ok(scheduler.set_enabled(enabled)),
                      lambda _r: (self.footer.set("自动任务已恢复。" if enabled else "自动任务已暂停。"), self.refresh_tasks()))

    def restore_tasks(self):
        if not scheduler.backup_exists():
            messagebox.showinfo("尚无备份", "首次应用管理器设置后，会保存原任务备份。", parent=self)
            return
        if messagebox.askokcancel("恢复原脚本", "恢复安装管理器前的两个任务。原脚本可能执行整套成长功能；管理器策略将不再控制这些任务。", parent=self):
            self.work("正在恢复原任务…", lambda: ensure_ok(scheduler.restore_original()),
                      lambda _r: (self.footer.set("已恢复原脚本任务。"), self.refresh_tasks()))

    @staticmethod
    def set_text(widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def work(self, text, action, callback):
        if self.busy:
            messagebox.showinfo("任务处理中", "请等待当前操作结束。", parent=self)
            return
        self.busy = True
        self.running_label.configure(text=text, fg=BLUE)
        self.footer.set(text)
        def thread():
            try:
                self.events.put(("done", action(), callback))
            except Exception as exc:
                self.events.put(("error", str(exc), None))
        threading.Thread(target=thread, daemon=True).start()

    def task_heartbeat(self):
        if not self.closing:
            self.refresh_tasks()
            self.after(60000, self.task_heartbeat)

    def refresh_tasks(self):
        if self.task_querying or self.closing:
            return
        self.task_querying = True
        def query():
            try:
                self.events.put(("tasks", scheduler.query_tasks(), None))
            except Exception as exc:
                self.events.put(("tasks", [{"name": "WorkBuddyAutoSignin", "error": str(exc)}], None))
        threading.Thread(target=query, daemon=True).start()

    def read_runtime(self):
        try:
            return json.loads((ROOT / "runtime.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def refresh_local(self, reschedule=True):
        if self.closing:
            return
        try:
            self.records = log_reader.read_records()
            self.render_overview()
            self.render_history()
            runtime = self.read_runtime()
            if runtime.get("running"):
                self.running_label.configure(text="运行中 · " + pretty(runtime.get("stage") or runtime.get("mode")), fg=BLUE)
            elif not self.busy:
                self.running_label.configure(text="就绪 · 自动任务独立运行", fg=GREEN)
        except Exception as exc:
            self.footer.set("读取本地数据失败：" + str(exc))
        if reschedule:
            self.after(5000, self.refresh_local)

    def consume_events(self):
        if self.closing:
            return
        while True:
            try:
                kind, value, callback = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "tasks":
                self.task_querying = False
                self.tasks = value
                self.render_tasks()
            elif kind == "done":
                self.busy = False
                self.running_label.configure(text="就绪", fg=GREEN)
                if callback:
                    callback(value)
            else:
                self.busy = False
                self.running_label.configure(text="操作失败", fg=RED)
                self.footer.set(value)
                messagebox.showerror("操作未完成", value, parent=self)
        self.after(100, self.consume_events)

    def on_close(self):
        if self.busy and not messagebox.askokcancel("关闭管理窗口", "当前操作会继续完成，自动任务不受影响。确认关闭窗口？", parent=self):
            return
        self.closing = True
        self.destroy()


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument("--page", choices=["overview", "schedule", "growth", "history"], default="overview")
    parser.add_argument("--quit-after", type=int, default=0)
    args = parser.parse_args()
    app = Manager(args.page)
    if args.quit_after:
        app.after(args.quit_after * 1000, app.destroy)
    app.mainloop()


if __name__ == "__main__":
    main()
