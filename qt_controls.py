"""Native, punctuation-free time editing and consistent popup filters."""
from PySide6.QtCore import Qt, QSize, QRectF
from PySide6.QtGui import QIcon, QPainter, QColor, QPixmap, QPen
from PySide6.QtWidgets import (QWidget, QGridLayout, QHBoxLayout, QVBoxLayout, QPushButton,
                             QDialog, QSpinBox, QAbstractSpinBox, QMenu, QLineEdit)
from qt_theme import MONO, BrutalButton, button, label
from qt_chrome import BrutalDialog


class NumberStepper(QWidget):
    """Compact numeric input with explicit controls, without native spin arrows."""
    def __init__(self, minimum, maximum, value, suffix="", parent=None):
        super().__init__(parent)
        from PySide6.QtGui import QIntValidator
        self.minimum, self.maximum = minimum, maximum
        self.setObjectName("numberStepper")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(40)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(3)
        self.minus = button("−", self.stepDown, "stepper")
        self.plus = button("+", self.stepUp, "stepper")
        for control in (self.minus, self.plus):
            control.setFixedSize(30, 32)
        self.minus.setAccessibleName("减少数值")
        self.plus.setAccessibleName("增加数值")
        self.edit = QLineEdit(str(value))
        self.edit.setValidator(QIntValidator(minimum, maximum, self.edit))
        self.edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.edit.setFixedWidth(38)
        self.edit.setAccessibleName("数值")
        self.edit.setObjectName("stepperValue")
        self.edit.editingFinished.connect(lambda: self.setValue(self.value()))
        layout.addWidget(self.minus)
        layout.addWidget(self.edit)
        if suffix.strip():
            unit = label(suffix.strip(), "muted")
            layout.addWidget(unit)
        layout.addWidget(self.plus)
        self.setValue(value)

    def value(self):
        return max(self.minimum, min(self.maximum, int(self.edit.text() or self.minimum)))

    def setValue(self, value):
        value = max(self.minimum, min(self.maximum, int(value)))
        self.edit.setText(str(value))
        self.minus.setEnabled(value > self.minimum)
        self.plus.setEnabled(value < self.maximum)

    def stepUp(self):
        self.setValue(self.value() + 1)

    def stepDown(self):
        self.setValue(self.value() - 1)


class TimeNumber(QLineEdit):
    def __init__(self, minimum, maximum, value):
        super().__init__(f"{value:02d}")
        from PySide6.QtGui import QIntValidator
        self.minimum, self.maximum = minimum, maximum
        self.setValidator(QIntValidator(minimum, maximum, self))
        self.setMaxLength(2)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(95, 52)
        self.setStyleSheet(f"font-family:{MONO};font-size:24px;padding:6px;")
        self.editingFinished.connect(lambda: self.setValue(self.value()))

    def value(self):
        return max(self.minimum, min(self.maximum, int(self.text() or 0)))

    def setValue(self, value):
        self.setText(f"{value:02d}")

    def stepUp(self):
        self.setValue(self.minimum if self.value() >= self.maximum else self.value() + 1)

    def stepDown(self):
        self.setValue(self.maximum if self.value() <= self.minimum else self.value() - 1)


class TimePicker(BrutalDialog):
    def __init__(self, value="08:00", parent=None):
        super().__init__("选择执行时间", "时", "blue", parent)
        row = QHBoxLayout()
        self.body.addLayout(row)
        self.hour = self.number(row, "小时", 0, 23, int(value[:2]))
        row.addWidget(label(":"))
        self.minute = self.number(row, "分钟", 0, 59, int(value[3:]))
        self.body.addWidget(label("24 小时制 · 本机时间", "muted"))
        self.add_actions(button("取消", self.reject), button("确定", self.accept, "primary"))
        # Focus starts on the hour, as before; Enter confirms through the default button.
        self.hour.setFocus()

    def number(self, row, title, minimum, maximum, value):
        col = QVBoxLayout()
        col.addWidget(label(title, "muted"))
        spin = TimeNumber(minimum, maximum, value)
        col.addWidget(spin)
        controls = QHBoxLayout()
        controls.addWidget(button("−", lambda: spin.stepDown()))
        controls.addWidget(button("+", lambda: spin.stepUp()))
        col.addLayout(controls)
        row.addLayout(col)
        return spin

    def value(self):
        return f"{self.hour.value():02d}:{self.minute.value():02d}"


class TimeChips(QWidget):
    def __init__(self, values, parent=None):
        super().__init__(parent)
        self.values = sorted(set(values))
        self.setObjectName("transparent")
        self.setMinimumWidth(290)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(10)
        self.render()

    def times(self):
        return list(self.values)

    def set_times(self, values):
        self.values = sorted(set(values))
        self.render()

    def text(self):
        return ",".join(self.values)

    def setText(self, value):
        self.set_times([s.strip() for s in value.replace("，", ",").split(",") if s.strip()])

    def render(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for index, value in enumerate(self.values):
            chip = QWidget()
            chip.setObjectName("timeChip")
            layout = QHBoxLayout(chip)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            edit = button(value, lambda _=False, v=value: self.pick(v), "time")
            edit.setToolTip("点击修改时间")
            remove = button("×", lambda _=False, v=value: self.remove(v), "timeRemove")
            remove.setToolTip("删除此时间")
            remove.setFixedWidth(28)
            layout.addWidget(edit)
            layout.addWidget(remove)
            self.grid.addWidget(chip, index // 3, index % 3)
        index = len(self.values)
        self.grid.addWidget(button("+ 添加", lambda: self.pick()), index // 3, index % 3)
        for col in range(3):
            self.grid.setColumnStretch(col, 1)

    def remove(self, value):
        self.values.remove(value)
        self.render()

    def pick(self, old=None):
        dialog = TimePicker(old or "08:00", self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            values = set(self.values)
            if old:
                values.discard(old)
            values.add(dialog.value())
            self.set_times(values)


class FilterButton(BrutalButton):
    def __init__(self, parent=None):
        super().__init__("", parent)
        self.options = []
        self.index = 0
        self.setProperty("variant", "filter")
        self.clicked.connect(self.show_popup)

    def addItems(self, options):
        self.options.extend(options)
        self.setCurrentIndex(0)

    def currentIndex(self):
        return self.index

    def currentText(self):
        return self.options[self.index] if self.options else ""

    def setCurrentIndex(self, index):
        self.index = index
        self.setText(self.currentText() + "  ⌄")

    def show_popup(self):
        menu = QMenu(self)
        menu.setObjectName("filterMenu")
        menu.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        menu.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        for index, option in enumerate(self.options):
            action = menu.addAction(option)
            action.setCheckable(True)
            action.setChecked(index == self.index)
            action.triggered.connect(lambda _=False, i=index: self.setCurrentIndex(i))
        menu.exec(self.mapToGlobal(self.rect().bottomLeft()))


def line_icon(kind, color):
    # Drawn at 2x so scaled displays stay crisp; heavier stroke matches the inked style.
    pix = QPixmap(40, 40)
    pix.setDevicePixelRatio(2)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(color), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap, Qt.PenJoinStyle.MiterJoin))
    if kind == "overview":
        for x, y in [(3, 3), (11, 3), (3, 11), (11, 11)]:
            painter.drawRect(QRectF(x, y, 5, 5))
    elif kind == "schedule":
        painter.drawEllipse(QRectF(3, 3, 14, 14))
        painter.drawLine(10, 6, 10, 10)
        painter.drawLine(10, 10, 14, 12)
    elif kind == "growth":
        from PySide6.QtGui import QPolygonF
        from PySide6.QtCore import QPointF
        painter.drawPolygon(QPolygonF([QPointF(10, 2), QPointF(17, 10), QPointF(10, 18), QPointF(3, 10)]))
    elif kind == "usage":
        for x, top in ((3, 11), (8, 6), (13, 2)):
            painter.drawRect(QRectF(x, top, 4, 17 - top))
    else:
        for y in (5, 10, 15):
            painter.drawLine(4, y, 16, y)
    painter.end()
    return QIcon(pix)
