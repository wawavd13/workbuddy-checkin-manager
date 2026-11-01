"""Painted consumption charts in the inked style.

Each chart encodes one magnitude with one hue: identity comes from the axis labels, never from colour. Text
wears the palette's text tokens, grid lines stay recessive, only the largest bar carries a direct label, and
hovering any bar shows its exact value. Dark mode reads the dark tone set, it is not an inverted light chart.
"""
import math

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from qt_theme import BORDER, INK, PALETTES, TONES, TONES_HOVER
import usage_reader

LEFT, TOP, RIGHT, BOTTOM = 58, 16, 10, 30


def nice_ticks(maximum, count=3):
    """0 plus `count` round steps whose last tick covers `maximum`."""
    if maximum <= 0:
        return [0.0]
    raw = maximum / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = magnitude
    for multiple in (1, 2, 2.5, 5, 10):
        step = multiple * magnitude
        if step * count >= maximum:
            break
    return [step * i for i in range(count + 1)]


def tick_text(value):
    return f"{value:,.0f}" if value >= 10 or value == 0 else f"{value:g}"


class _Chart(QWidget):
    def __init__(self, tone, formatter=None, unit="积分", parent=None):
        super().__init__(parent)
        self.tone = tone
        self.formatter = formatter or usage_reader.credit_text
        self.unit = unit
        self.mode = "light"
        self.bars = []
        self.hover = None
        self.setObjectName("transparent")
        self.setMouseTracking(True)
        self.label_font = QFont("Microsoft YaHei UI", 9)
        self.number_font = QFont("Consolas", 9)
        self.strong_font = QFont("Consolas", 9, QFont.Weight.Bold)

    def set_theme(self, mode):
        self.mode = mode
        self.update()

    def set_data(self, bars):
        self.bars = [(str(name), max(0.0, float(value))) for name, value in bars]
        self.hover = None
        self.setAccessibleDescription("；".join(f"{name} {self.formatter(value)} {self.unit}" for name, value in self.bars) or "暂无数据")
        self.update()

    def fill_color(self, index):
        return TONES_HOVER[self.tone] if index == self.hover else TONES[self.tone]

    def bar_rects(self):
        return []

    def draw_empty(self, painter, p):
        painter.setFont(self.label_font)
        painter.setPen(QColor(p["muted"]))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter.value, "暂无记录")

    def mouseMoveEvent(self, event):
        position = event.position()
        found = None
        for index, (rect, hit) in enumerate(self.bar_rects()):
            if hit.contains(position):
                found = index
                break
        if found != self.hover:
            self.hover = found
            self.update()
        if found is None:
            QToolTip.hideText()
        else:
            name, value = self.bars[found]
            QToolTip.showText(event.globalPosition().toPoint(), f"{name}  ·  {self.formatter(value)} {self.unit}", self)
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.hover = None
        self.update()
        super().leaveEvent(event)


class BarChart(_Chart):
    """Vertical bars over an ordered axis (days or months); labels are thinned so they never collide."""

    def __init__(self, tone="green", formatter=None, unit="积分", parent=None):
        super().__init__(tone, formatter, unit, parent)
        self.setMinimumHeight(200)
        self.setAccessibleName("按时间消耗柱状图")

    def plot(self):
        return QRectF(LEFT, TOP, max(10, self.width() - LEFT - RIGHT), max(10, self.height() - TOP - BOTTOM))

    def bar_rects(self):
        if not self.bars:
            return []
        plot = self.plot()
        top = nice_ticks(max(value for _, value in self.bars))[-1] or 1
        slot = plot.width() / len(self.bars)
        gap = 2 if slot > 7 else 1
        width = max(2, slot - gap)
        rects = []
        for index, (_, value) in enumerate(self.bars):
            height = plot.height() * value / top
            x = plot.left() + index * slot + (slot - width) / 2
            bar = QRectF(x, plot.bottom() - height, width, height)
            hit = QRectF(plot.left() + index * slot, plot.top(), slot, plot.height())
            rects.append((bar, hit))
        return rects

    def paintEvent(self, _event):
        p = PALETTES[self.mode]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        if not self.bars:
            self.draw_empty(painter, p)
            return
        plot = self.plot()
        values = [value for _, value in self.bars]
        ticks = nice_ticks(max(values))
        top = ticks[-1] or 1
        painter.setFont(self.number_font)
        for tick in ticks:
            y = round(plot.bottom() - plot.height() * tick / top)
            if tick == 0:
                painter.fillRect(QRectF(plot.left(), y - 1, plot.width(), 2), QColor(p["edge"]))
            else:
                painter.fillRect(QRectF(plot.left(), y, plot.width(), 1), QColor(p["rule"]))
            painter.setPen(QColor(p["dim"]))
            painter.drawText(QRectF(0, y - 9, LEFT - 8, 18), (Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter).value, tick_text(tick))
        rects = self.bar_rects()
        peak = values.index(max(values))
        for index, (bar, _) in enumerate(rects):
            if bar.height() <= 0:
                continue
            fill = QColor(self.fill_color(index))
            if bar.width() >= 6:
                painter.fillRect(bar.adjusted(0, -BORDER + 1, 0, 0), QColor(INK))
                painter.fillRect(bar.adjusted(2, 0, -2, 0), fill)
            else:
                painter.fillRect(bar, fill)
        painter.setFont(self.label_font)
        painter.setPen(QColor(p["muted"]))
        step = max(1, math.ceil(len(self.bars) / max(1, int(plot.width() // 64))))
        for index, (name, _) in enumerate(self.bars):
            if index % step and index != len(self.bars) - 1:
                continue
            _, hit = rects[index]
            painter.drawText(QRectF(hit.left() - 20, plot.bottom() + 6, hit.width() + 40, 20), Qt.AlignmentFlag.AlignHCenter.value, name)
        if max(values) > 0:
            bar, _ = rects[peak]
            painter.setFont(self.strong_font)
            painter.setPen(QColor(p["text"]))
            painter.drawText(QRectF(bar.center().x() - 60, max(0, bar.top() - 18), 120, 16), Qt.AlignmentFlag.AlignHCenter.value,
                             self.formatter(values[peak]))


class HBarChart(_Chart):
    """Horizontal bars for a ranked list; the label column carries identity, the bar carries magnitude."""

    ROW, LABEL_W, VALUE_W = 30, 150, 80

    def __init__(self, tone="blue", formatter=None, unit="积分", parent=None):
        super().__init__(tone, formatter, unit, parent)
        self.setAccessibleName("按模型消耗条形图")
        self.setFixedHeight(self.ROW + 12)

    def set_data(self, bars):
        super().set_data(bars)
        self.setFixedHeight(self.ROW * max(1, len(self.bars)) + 12)

    def bar_rects(self):
        if not self.bars:
            return []
        top = max(value for _, value in self.bars) or 1
        width = max(10, self.width() - self.LABEL_W - self.VALUE_W - 8)
        rects = []
        for index, (_, value) in enumerate(self.bars):
            y = 6 + index * self.ROW
            bar = QRectF(self.LABEL_W, y + 6, width * value / top, self.ROW - 12)
            hit = QRectF(0, y, self.width(), self.ROW)
            rects.append((bar, hit))
        return rects

    def paintEvent(self, _event):
        p = PALETTES[self.mode]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        if not self.bars:
            self.draw_empty(painter, p)
            return
        rects = self.bar_rects()
        baseline = QRectF(self.LABEL_W - 2, 4, 2, self.ROW * len(self.bars) + 4)
        painter.fillRect(baseline, QColor(p["edge"]))
        for index, ((bar, hit), (name, value)) in enumerate(zip(rects, self.bars)):
            painter.setFont(self.label_font)
            painter.setPen(QColor(p["text"]))
            text = painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, self.LABEL_W - 14)
            painter.drawText(QRectF(0, hit.top(), self.LABEL_W - 10, hit.height()), (Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter).value, text)
            if bar.width() > 0:
                painter.fillRect(bar.adjusted(0, 0, BORDER - 1, 0), QColor(INK))
                painter.fillRect(bar.adjusted(0, 2, 0, -2), QColor(self.fill_color(index)))
            painter.setFont(self.strong_font if index == 0 else self.number_font)
            painter.setPen(QColor(p["text"] if index == 0 else p["muted"]))
            painter.drawText(QRectF(bar.right() + 10, hit.top(), self.VALUE_W, hit.height()), (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter).value,
                             self.formatter(value))
