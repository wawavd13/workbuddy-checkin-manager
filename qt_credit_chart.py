"""Native expiry timeline: one row per date, cycle quota marked separately."""
from datetime import date

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QVBoxLayout, QWidget

import credits
from qt_theme import BORDER, BUTTON_SHADOW, PALETTES, TONES, label

DATE_W, LEFT_W, AMOUNT_W, TAG_W, GAP = 92, 64, 82, 40, 12
RIGHT_CENTER = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter


def cell(text, role=None, width=None, align=None, wrap=False):
    widget = label(text, role, wrap)
    if width:
        widget.setFixedWidth(width)
    if align:
        widget.setAlignment(align)
    return widget


def points(value, suffix=" 分"):
    return "金额未知" if credits.capacity(value) is None else credits.amount(value) + suffix


class AmountBar(QWidget):
    """Track length encodes remaining credits only, never time."""

    def __init__(self, value, maximum, cycle=False, parent=None):
        super().__init__(parent)
        self.value, self.maximum, self.cycle = value, maximum, cycle
        self.mode = "light"
        self.setObjectName("transparent")
        self.setMinimumWidth(60)
        self.setFixedHeight(16)

    def paintEvent(self, _event):
        p = PALETTES[self.mode]
        painter = QPainter(self)
        track = QRectF(0, 0, self.width(), self.height())
        inner = track.adjusted(BORDER, BORDER, -BORDER, -BORDER)
        painter.fillRect(track, QColor(p["edge"]))
        painter.fillRect(inner, QColor(p["field"]))
        if self.value and self.maximum > 0:
            width = max(6, inner.width() * min(1, self.value / self.maximum))
            painter.fillRect(QRectF(inner.left(), inner.top(), width, inner.height()),
                             QColor(TONES["blue" if self.cycle else "green"]))
            if width < inner.width():
                painter.fillRect(QRectF(inner.left() + width, inner.top(), BORDER, inner.height()), QColor(p["edge"]))


class ExpiryRow(QAbstractButton):
    """Collapsed: date, countdown, amount. Expanded in place: each batch's exact time."""
    step = Signal(int)

    def __init__(self, group, maximum, today, parent=None):
        super().__init__(parent)
        self.group, self.mode, self.keyboard_focus, self.lines = group, "light", False, []
        self.cycle = group.get("kind") == "cycle"
        self.countdown = credits.countdown(group["date"], today) if group["date"] != "未知" else "—"
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12 + BUTTON_SHADOW, 10 + BUTTON_SHADOW)
        outer.setSpacing(6)
        top = QHBoxLayout()
        top.setSpacing(GAP)
        top.addWidget(cell(group["date"], width=DATE_W))
        top.addWidget(cell(self.countdown, "muted", LEFT_W))
        self.bar = AmountBar(group["remaining"], maximum, self.cycle)
        top.addWidget(self.bar, 1)
        top.addWidget(cell(points(group["remaining"]), width=AMOUNT_W, align=RIGHT_CENTER))
        batches = len(group["rows"])
        self.tag = "周期" if self.cycle else f"{batches} 批" if batches > 1 else ""
        top.addWidget(cell(self.tag, "muted", TAG_W))
        outer.addLayout(top)
        self.details = QWidget()
        self.details.setObjectName("transparent")
        self.detail_box = QVBoxLayout(self.details)
        self.detail_box.setContentsMargins(0, 0, 0, 0)
        self.detail_box.setSpacing(4)
        for row in group["rows"]:
            if self.cycle:
                stamp = str(row.get("cycle_end_at") or "")
                self.detail_line(stamp[11:19], str(row.get("name") or "积分包"), row.get("remaining"))
                resource = str(row.get("expires_at") or "")[:10] or "未知"
                note = cell("资源包有效期至 " + resource + "，不是本期积分有效期", "dim", wrap=True)
                note.setContentsMargins(DATE_W + LEFT_W + 2 * GAP, 0, 0, 0)
                self.detail_box.addWidget(note)
                self.lines.append(note.text())
            else:
                stamp = str(row.get("expires_at") or "")
                self.detail_line(stamp[11:19], str(row.get("name") or "积分包"), row.get("remaining"))
        outer.addWidget(self.details)
        self.details.hide()
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.toggled.connect(self.details.setVisible)
        summary = f"{group['date']}，{self.countdown}，{points(group['remaining'], '积分')}"
        self.setAccessibleName(summary + ("，周期额度" if self.cycle else "") + "；按空格展开或收起")
        self.setAccessibleDescription("；".join(self.lines))

    @property
    def detail_text(self):
        return "\n".join(self.lines)

    def detail_line(self, time, text, value):
        line = QHBoxLayout()
        line.setSpacing(GAP)
        line.addWidget(cell(time or "时间未知", "muted", DATE_W))
        line.addWidget(cell("", width=LEFT_W))
        line.addWidget(cell(text, "muted", wrap=True), 1)
        line.addWidget(cell(points(value), "muted", AMOUNT_W, RIGHT_CENTER))
        line.addWidget(cell("", width=TAG_W))
        self.detail_box.addLayout(line)
        self.lines.append(f"{points(value)} · {time or '时间未知'} · {text}")

    def set_mode(self, mode):
        self.mode = self.bar.mode = mode
        self.bar.update()
        self.update()

    def paintEvent(self, _event):
        p = PALETTES[self.mode]
        s = BUTTON_SHADOW
        body = QRectF(0, 0, self.width() - s, self.height() - s)
        painter = QPainter(self)
        if self.isChecked():
            # Selected row lifts into an inked card, matching the rest of the brutalist UI.
            painter.fillRect(body.translated(s, s), QColor(p["shadow"]))
            painter.fillRect(body, QColor(p["edge"]))
            painter.fillRect(body.adjusted(BORDER, BORDER, -BORDER, -BORDER), QColor(p["raised"]))
        elif self.underMouse():
            painter.fillRect(body, QColor(p["hover_fill"]))
        if self.hasFocus() and self.keyboard_focus:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(p["text"]), 1, Qt.PenStyle.DashLine))
            painter.drawRect(body.adjusted(4, 4, -5, -5))

    def focusInEvent(self, event):
        # Ring only for keyboard navigation; mouse selection stays clean.
        self.keyboard_focus = event.reason() != Qt.FocusReason.MouseFocusReason
        super().focusInEvent(event)

    def mousePressEvent(self, event):
        self.keyboard_focus = False
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.step.emit(-1 if event.key() == Qt.Key.Key_Up else 1)
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
        else:
            super().keyPressEvent(event)


class ExpiryChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode, self.signature, self.rows = "light", None, []
        self.setObjectName("transparent")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.list_layout = QVBoxLayout()
        self.list_layout.setSpacing(4)
        layout.addLayout(self.list_layout)
        self.notice = label("", "muted", True)
        self.notice.setContentsMargins(12, 0, 12, 0)
        layout.addWidget(self.notice)
        self.legend = label("", "dim", True)
        self.legend.setContentsMargins(12, 0, 12, 0)
        layout.addWidget(self.legend)

    def set_theme(self, mode):
        self.mode = mode
        for row in self.rows:
            row.set_mode(mode)

    def set_data(self, value, today=None):
        today = today or date.today()
        signature = (repr(value), today.isoformat())
        if signature == self.signature:
            return
        self.signature = signature
        expanded = next(((row.group["kind"], row.group["date"]) for row in self.rows if row.isChecked()), None)
        for row in self.rows:
            self.list_layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        self.rows = []
        grouped = credits.expiry_groups(value) if credits.has_balance(value) else {"expiry": [], "cycle": []}
        groups = sorted(grouped["cycle"] + grouped["expiry"],
                        key=lambda g: (g["date"] == "未知", g["date"], g["kind"] != "cycle"))
        maximum = max((g["remaining"] or 0 for g in groups), default=0)
        for group in groups:
            row = ExpiryRow(group, maximum, today)
            row.set_mode(self.mode)
            row.clicked.connect(lambda _checked=False, item=row: self.toggle_row(item))
            row.step.connect(lambda offset, item=row: self.move_focus(item, offset))
            self.list_layout.addWidget(row)
            self.rows.append(row)
            if expanded == (group["kind"], group["date"]):
                row.setChecked(True)
        notes = self.notes(value, groups)
        self.notice.setText("\n".join(notes))
        self.notice.setVisible(bool(notes))
        legend = "横条长度 = 剩余积分 · 点击日期查看各批次准确时间"
        if grouped["cycle"]:
            legend += " · 青色横条为周期额度，本期结束后刷新"
        self.legend.setText(legend)
        self.legend.setVisible(bool(groups))

    @staticmethod
    def notes(value, groups):
        if not credits.has_balance(value):
            return ["暂无有效期明细，点击“刷新积分”后显示"]
        notes = [note for note in [credits.stale_note(value)] if note]
        if value.get("expiry_status") != "available":
            return notes + ["暂无有效期明细：" + str(value.get("expiry_error") or "接口未返回可靠到期信息（不代表永久有效）")]
        if not value.get("expiry_reconciled"):
            notes.append("明细合计与余额未对齐，下列到期信息可能不完整")
        if not groups:
            notes.append("未返回带有效期的积分批次（不代表永久有效）")
        if any(g["date"] == "未知" for g in groups):
            notes.append("部分批次未返回有效期，不能推断为永久有效")
        return notes

    def toggle_row(self, selected):
        for row in self.rows:
            if row is not selected and row.isChecked():
                row.setChecked(False)
        if not selected.hasFocus():
            selected.setFocus(Qt.FocusReason.MouseFocusReason)

    def move_focus(self, current, offset):
        index = self.rows.index(current) + offset
        if 0 <= index < len(self.rows):
            self.rows[index].setFocus(Qt.FocusReason.TabFocusReason)
