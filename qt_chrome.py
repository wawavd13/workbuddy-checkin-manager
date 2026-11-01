"""Integrated window controls and frameless dialogs, without operating-system title bars."""
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtWidgets import QAbstractButton, QDialog, QFrame, QHBoxLayout, QLayout, QVBoxLayout, QWidget
from qt_theme import BORDER, BUTTON_SHADOW, INK, SHADOW, TONES, button, colors as palette, label, mark, panel


class WindowBar(QFrame):
    def __init__(self, window):
        super().__init__(window)
        self.owner = window
        self.setObjectName("topbar")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.owner.windowHandle()
            if handle:
                handle.startSystemMove()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.owner.toggle_maximized()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class WindowButton(QAbstractButton):
    def __init__(self, kind, window):
        super().__init__(window)
        self.kind, self.owner = kind, window
        self.hovered = self.focusReason_keyboard = False
        self.setFixedSize(38, 34)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName({"minimize": "最小化", "maximize": "最大化 / 还原", "close": "关闭窗口"}[kind])
        self.setToolTip(self.accessibleName())
        # Resolved on click so dialogs, which only close, can reuse the same control.
        self.clicked.connect(lambda: {"minimize": window.showMinimized, "close": window.close}.get(
            kind, getattr(window, "toggle_maximized", None) or window.close)())

    def enterEvent(self, event):
        self.hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event):
        colors = palette()
        s = BUTTON_SHADOW
        body = QRectF(0, 0, self.width() - s, self.height() - s)
        painter = QPainter(self)
        if self.isDown():
            body.translate(s, s)
        else:
            painter.fillRect(body.translated(s, s), QColor(colors["shadow"]))
        hot = self.hovered or (self.hasFocus() and self.focusReason_keyboard)
        fill = (TONES["pink"] if self.kind == "close" else TONES["yellow"]) if hot else colors["control"]
        ink = INK if hot else colors["text"]
        painter.fillRect(body, QColor(INK if hot else colors["edge"]))
        painter.fillRect(body.adjusted(BORDER, BORDER, -BORDER, -BORDER), QColor(fill))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(ink), 2))
        cx, cy = body.center().x(), body.center().y()
        if self.kind == "minimize":
            painter.drawLine(QPointF(cx - 6, cy + 1), QPointF(cx + 6, cy + 1))
        elif self.kind == "close":
            painter.drawLine(QPointF(cx - 5, cy - 5), QPointF(cx + 5, cy + 5))
            painter.drawLine(QPointF(cx + 5, cy - 5), QPointF(cx - 5, cy + 5))
        elif self.owner.isMaximized():
            painter.drawRect(QRectF(cx - 3, cy - 6, 9, 9))
            painter.fillRect(QRectF(cx - 6, cy - 3, 9, 9), QColor(fill))
            painter.drawRect(QRectF(cx - 6, cy - 3, 9, 9))
        else:
            painter.drawRect(QRectF(cx - 5, cy - 5, 10, 10))

    def focusInEvent(self, event):
        self.focusReason_keyboard = event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(event)


class DialogHeader(QWidget):
    """Title strip of a frameless dialog; dragging it moves the dialog."""

    def mousePressEvent(self, event):
        handle = self.window().windowHandle()
        if event.button() == Qt.MouseButton.LeftButton and handle:
            handle.startSystemMove()
            event.accept()
        else:
            super().mousePressEvent(event)

    def paintEvent(self, _event):
        # Inked rule under the strip, matching the main window's top bar.
        painter = QPainter(self)
        painter.fillRect(QRectF(0, self.height() - BORDER, self.width(), BORDER), QColor(palette()["edge"]))


class BrutalDialog(QDialog):
    """Frameless dialog on an inked card with a hard shadow; the title appears once, in the strip.

    The strip's close button and Esc both reject, as with a native dialog."""

    def __init__(self, title, glyph="?", tone="yellow", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        # The shadow gutters stay see-through, so the card casts its shadow onto whatever lies below.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        self.card, card = panel(padding=0)
        card.setSpacing(0)
        outer.addWidget(self.card)
        self.header = DialogHeader()
        self.header.setObjectName("transparent")
        strip = QHBoxLayout(self.header)
        strip.setContentsMargins(14, 8, 8, 8 + BORDER)
        strip.setSpacing(10)
        strip.addWidget(mark(glyph, tone, 28))
        self.heading = label(title, "section")
        strip.addWidget(self.heading, 1)
        self.close_button = WindowButton("close", self)
        strip.addWidget(self.close_button)
        card.addWidget(self.header)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(20, 18, 20, 20)
        self.body.setSpacing(16)
        card.addLayout(self.body)

    def add_text(self, text):
        message = label(text, wrap=True)
        message.setFixedWidth(360)
        message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.body.addWidget(message)
        return message

    def add_actions(self, *buttons):
        """Right-aligned action row; the last button is the Enter default and takes the initial focus."""
        row = QHBoxLayout()
        self.body.addLayout(row)
        row.setSpacing(10)
        row.addStretch()
        for control in buttons:
            row.addWidget(control)
        buttons[-1].setDefault(True)
        buttons[-1].setFocus()
        return row


class MessageDialog(BrutalDialog):
    """Confirmation or notice in the shared chrome; replaces QMessageBox, which repeated its title."""

    def __init__(self, title, text, accept_text="确认", reject_text="取消", glyph="?", tone="yellow",
                 accept_variant="primary", parent=None):
        super().__init__(title, glyph, tone, parent)
        self.message = self.add_text(text)
        actions = [button(reject_text, self.reject)] if reject_text else []
        self.add_actions(*actions, button(accept_text, self.accept, accept_variant))
