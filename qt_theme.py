"""Neubrutalist native theme: thick ink borders, hard offset shadows, flat saturated fills."""
from PySide6.QtCore import Qt, QPointF, QRectF, QSize
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QAbstractButton, QFrame, QLabel, QPushButton, QVBoxLayout

INK = "#000000"
# Light keeps the stylekit neon accents. Dark uses the same hues at lower saturation so they sit calmly on
# charcoal instead of glowing. Every fill still carries black text (all clear 4.5:1 on black).
TONE_SETS = {
    "light": {"green": "#CCFF00", "blue": "#00D9FF", "yellow": "#FFE500", "orange": "#FF9500", "pink": "#FF006E"},
    "dark": {"green": "#B5E07A", "blue": "#6FCDE0", "yellow": "#EBCF5C", "orange": "#EBA353", "pink": "#E8679A"},
}
HOVER_SETS = {
    "light": {"green": "#B8E600", "blue": "#00C2E6", "yellow": "#F2D900", "orange": "#F08A00", "pink": "#FF3D8B"},
    "dark": {"green": "#C4E98F", "blue": "#86D7E7", "yellow": "#F0D873", "orange": "#F0B26B", "pink": "#EE7EAA"},
}
# Live views of the active mode's tones: call sites index these at paint time, set_active() swaps the contents.
TONES = dict(TONE_SETS["light"])
TONES_HOVER = dict(HOVER_SETS["light"])
HOVER_OF = {TONES[name]: TONES_HOVER[name] for name in TONES}
MONO = "'Cascadia Mono', 'Cascadia Code', 'Consolas', 'Microsoft YaHei UI'"
BORDER, SHADOW, BUTTON_SHADOW = 3, 6, 3
FLAT_VARIANTS = {"ghost", "stepper", "time", "timeRemove"}


def _palette(tones, **c):
    # Legacy aliases keep older call sites working.
    c.update(panel=c["surface"], hover=c["hover_fill"], line=c["rule"], accent=c["edge"],
             accent_text=c["surface"], green_bg=tones["green"], red_bg=tones["pink"])
    return c


# Light: white page, black ink. Dark keeps the same structure on charcoal (black outlines and shadows stay)
# instead of inverting it; hover and selection snap to yellow with black text in both.
_L, _D = TONE_SETS["light"], TONE_SETS["dark"]
PALETTES = {
    "light": _palette(_L, bg="#FFFFFF", side="#FFFFFF", surface="#FFFFFF", control="#FFFFFF", field="#FFFFFF", raised="#FFF7B3",
                      hover_fill=_L["yellow"], focus_field="#FFFBD6", select=_L["yellow"], select_text=INK,
                      text=INK, muted="#4B4B4B", dim="#6B6B6B", edge=INK, shadow=INK, rule="#D9D9D9",
                      green="#2E7D00", red="#C8004F"),
    # Dark: page, card, control and field step up in four greys so the black outlines and shadows still read.
    "dark": _palette(_D, bg="#1F2026", side="#1F2026", surface="#2C2E36", control="#363842", field="#23252C", raised="#363842",
                     hover_fill=_D["yellow"], focus_field="#3A3926", select=_D["yellow"], select_text=INK,
                     text="#F2F2EE", muted="#B9B8B0", dim="#A3A19A", edge=INK, shadow=INK,
                     rule="#4A4D58", green=_D["green"], red="#FF6FA6"),
}
_ACTIVE = {"mode": "light"}


def set_active(mode):
    """Painted widgets read the active palette, including dialogs created later; tones swap with the mode."""
    _ACTIVE["mode"] = mode
    TONES.clear()
    TONES.update(TONE_SETS[mode])
    TONES_HOVER.clear()
    TONES_HOVER.update(HOVER_SETS[mode])
    HOVER_OF.clear()
    HOVER_OF.update({TONES[name]: TONES_HOVER[name] for name in TONES})


def colors(mode=None):
    return PALETTES[mode or _ACTIVE["mode"]]


def stylesheet(mode="light"):
    # Tone fills are prefixed so they never shadow the palette's green/red text colours.
    v = dict(PALETTES[mode], ink=INK, b=BORDER, mono=MONO, **{"tone_" + k: c for k, c in TONE_SETS[mode].items()})
    return """
    QWidget { background: %(bg)s; color: %(text)s; font-family: 'Microsoft YaHei UI'; font-size: 14px; }
    QMainWindow, QScrollArea, QStackedWidget { border: none; }
    QFrame#sidebar { background: %(side)s; border: none; border-right: %(b)spx solid %(edge)s; }
    QFrame#sidebar QLabel { background: transparent; }
    QFrame#topbar { border: none; border-bottom: %(b)spx solid %(edge)s; }
    QFrame#topbar QLabel { background: transparent; }
    QFrame#topbar QLabel#runtime { background: %(tone_green)s; color: %(ink)s; border: %(b)spx solid %(ink)s; padding: 3px 10px; font-weight: 700; }
    QFrame#topbar QLabel#runtime[state='busy'] { background: %(tone_orange)s; }
    QLabel#footer { color: %(dim)s; border: none; border-top: %(b)spx solid %(edge)s; }
    QFrame#panel { background: transparent; border: none; }
    QFrame#panel QLabel, QFrame#panel QWidget#transparent { background: transparent; }
    QLabel[role='muted'] { color: %(muted)s; background: transparent; }
    QLabel[role='dim'] { color: %(dim)s; background: transparent; }
    QLabel[role='title'] { font-size: 28px; font-weight: 900; background: transparent; }
    QLabel[role='section'] { font-size: 17px; font-weight: 800; background: transparent; }
    QLabel[role='item'] { font-size: 15px; font-weight: 700; background: transparent; }
    QLabel[role='metric'] { font-family: %(mono)s; font-size: 32px; font-weight: 700; background: transparent; }
    QLabel[role='tilelabel'] { font-size: 13px; font-weight: 700; background: transparent; }
    QLabel[role='tilevalue'] { font-family: %(mono)s; font-size: 26px; font-weight: 700; background: transparent; }
    QLabel[role='mono'] { font-family: %(mono)s; font-size: 13px; color: %(text)s; background: transparent; }
    QLabel[role='mark'] { font-size: 15px; font-weight: 800; color: %(ink)s; }
    QFrame#panel[tone='green'] QLabel, QFrame#panel[tone='blue'] QLabel, QFrame#panel[tone='orange'] QLabel,
    QFrame#panel[tone='yellow'] QLabel, QFrame#panel[tone='pink'] QLabel { color: %(ink)s; }
    QPushButton { background: %(control)s; color: %(text)s; border: %(b)spx solid %(edge)s; padding: 8px 16px;
                  min-height: 18px; font-weight: 700; }
    QPushButton:hover { background: %(hover_fill)s; color: %(ink)s; }
    QPushButton:pressed { background: %(select)s; color: %(select_text)s; }
    QPushButton:disabled { color: %(dim)s; border-color: %(dim)s; }
    QPushButton[variant='nav'] { text-align: left; padding: 9px 12px; }
    QPushButton[variant='ghost'] { border: none; padding: 6px 4px; }
    QPushButton[variant='stepper'] { border: none; padding: 0; font-size: 17px; }
    QPushButton[variant='time'], QPushButton[variant='timeRemove'] { border: none; padding: 6px 8px;
                  font-family: %(mono)s; font-size: 14px; }
    QPushButton[variant='timeRemove'] { padding-left: 0; padding-right: 4px; }
    QPushButton[variant='day'] { padding: 2px; font-size: 11px; }
    QLineEdit, QPlainTextEdit, QComboBox { background: %(field)s; border: %(b)spx solid %(edge)s; padding: 7px 10px;
                  selection-background-color: %(tone_yellow)s; selection-color: %(ink)s; }
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { background: %(focus_field)s; }
    QWidget#numberStepper { background: %(field)s; border: %(b)spx solid %(edge)s; }
    QWidget#numberStepper QLabel { background: transparent; }
    QLineEdit#stepperValue { background: transparent; border: none; padding: 0; font-family: %(mono)s;
                  font-size: 15px; font-weight: 700; }
    QWidget#timeChip { background: %(tone_blue)s; border: %(b)spx solid %(ink)s; }
    QMenu#filterMenu { background: %(surface)s; color: %(text)s; border: %(b)spx solid %(edge)s; padding: 4px; }
    QMenu#filterMenu::item { background: transparent; padding: 9px 22px 9px 12px; margin: 1px; }
    QMenu#filterMenu::item:selected { background: %(tone_yellow)s; color: %(ink)s; }
    QMenu#filterMenu::indicator { width: 10px; height: 10px; }
    QMenu#filterMenu::indicator:unchecked { background: %(field)s; border: %(b)spx solid %(edge)s; }
    QMenu#filterMenu::indicator:checked { background: %(tone_yellow)s; border: %(b)spx solid %(ink)s; }
    QPlainTextEdit { font-family: 'Consolas', 'Microsoft YaHei UI'; font-size: 13px; }
    QTableWidget { border: none; background: transparent; gridline-color: %(rule)s; outline: none;
                  selection-background-color: %(select)s; selection-color: %(select_text)s; }
    QTableWidget::item { padding: 8px; border-bottom: 1px solid %(rule)s; }
    QTableWidget::item:selected { color: %(select_text)s; background: %(select)s; }
    QHeaderView { background: transparent; border: none; }
    QHeaderView::section { background: %(surface)s; color: %(text)s; border: none; border-bottom: %(b)spx solid %(edge)s;
                  padding: 10px 8px; font-weight: 700; font-size: 13px; }
    QScrollBar:vertical { background: transparent; width: 12px; margin: 2px 0; }
    QScrollBar::handle:vertical { background: %(tone_yellow)s; border: %(b)spx solid %(ink)s; min-height: 32px; }
    QScrollBar:horizontal { background: transparent; height: 12px; margin: 0 2px; }
    QScrollBar::handle:horizontal { background: %(tone_yellow)s; border: %(b)spx solid %(ink)s; min-width: 32px; }
    QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
    QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
    QToolTip { color: %(ink)s; background: %(tone_yellow)s; border: %(b)spx solid %(ink)s; padding: 6px; }
    QDialog { background: %(bg)s; }
    """ % v


class Switch(QAbstractButton):
    """Square-cornered toggle: inked track, square knob, green when on."""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setFixedSize(46, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mode = _ACTIVE["mode"]
        self.setAccessibleName("功能开关")

    def set_theme(self, mode):
        self.mode = mode
        self.update()

    def paintEvent(self, _event):
        p = PALETTES[self.mode]
        enabled, active = self.isEnabled(), self.underMouse() or self.isDown()
        on = self.isChecked()
        edge = p["edge"] if enabled else p["dim"]
        fill = TONES["green"] if on else p["field"]
        if enabled and active:
            fill = TONES_HOVER["green"] if on else p["hover_fill"]
        if not enabled:
            fill = p["field"]
        s = BUTTON_SHADOW
        body = QRectF(0, 0, self.width() - s, self.height() - s)
        painter = QPainter(self)
        if enabled:
            painter.fillRect(body.translated(s, s), QColor(p["shadow"]))
        painter.fillRect(body, QColor(edge))
        painter.fillRect(body.adjusted(BORDER, BORDER, -BORDER, -BORDER), QColor(fill))
        knob = 14
        x = body.right() - BORDER - 3 - knob if on else body.left() + BORDER + 3
        # The off knob follows the text colour so it stays visible on the dark theme's charcoal field.
        ink = INK if (on and enabled) or (enabled and active) else (p["text"] if enabled else p["dim"])
        painter.fillRect(QRectF(x, body.center().y() - 5, knob, 10), QColor(ink))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(p["text"] if enabled else p["dim"]), 1, Qt.PenStyle.DashLine))
            painter.drawRect(body.adjusted(BORDER + 1, BORDER + 1, -BORDER - 2, -BORDER - 2))

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)


def label(text="", role=None, wrap=False):
    w = QLabel(text)
    if role:
        w.setProperty("role", role)
    w.setWordWrap(wrap)
    w.setTextFormat(Qt.TextFormat.PlainText)
    return w


class BrutalButton(QPushButton):
    """Painted button; the face sinks into its hard shadow when pressed."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.keyboard_focus = False

    def variant(self):
        return self.property("variant") or "default"

    def offset(self):
        # Day cells all reserve the same gutter so every cell face is equal; only "today" casts a shadow.
        return 0 if self.variant() in FLAT_VARIANTS else BUTTON_SHADOW

    def sizeHint(self):
        hint = super().sizeHint()
        return QSize(hint.width() + self.offset(), hint.height() + self.offset())

    def pastel(self):
        """Buttons on a coloured card keep ink-on-white contrast in both themes."""
        widget = self.parentWidget()
        while widget is not None:
            if isinstance(widget, BrutalPanel) and widget.property("tone") in TONES:
                return True
            widget = widget.parentWidget()
        return False

    def scheme(self, p):
        variant = self.variant()
        hover = self.underMouse() and self.isEnabled()
        down = self.isDown() and self.isEnabled()
        surface, text, edge, shadow = ("#FFFFFF", INK, INK, INK) if self.pastel() else (p["control"], p["text"], p["edge"], p["shadow"])
        hover_fill = TONES["yellow"] if self.pastel() else p["hover_fill"]
        tone = self.property("tone")
        if variant in ("primary", "danger"):
            fill = TONES["green" if variant == "primary" else "pink"]
            return (HOVER_OF[fill] if hover else fill), INK, INK, shadow
        if variant == "nav" and self.property("selected"):
            return TONES["yellow"], INK, INK, shadow
        if variant == "day":
            lift = shadow if self.property("today") else None
            if tone in ("success", "failed"):
                fill = TONES["green" if tone == "success" else "pink"]
                return (HOVER_OF[fill] if hover else fill), INK, INK, lift
            if self.property("today"):
                # Today without a confirmed result is pending: orange, like the 待确认 badge.
                return (hover_fill if hover else TONES["orange"]), INK, INK, lift
            if hover:
                return hover_fill, INK, edge, lift
            return None, p["dim"] if tone == "future" else p["muted"], None, None
        if variant in ("time", "timeRemove"):
            if down:
                return TONES_HOVER["blue"], INK, None, None
            return (TONES["blue"] if hover else None), INK, None, None
        if variant in ("ghost", "stepper"):
            if down:
                return TONES_HOVER["yellow"], INK, None, None
            return (TONES["yellow"] if hover else None), (INK if hover else text), None, None
        return (hover_fill if hover else surface), (INK if hover else text), edge, shadow

    def paintEvent(self, _event):
        p = colors()
        fill, text, edge, shadow = self.scheme(p)
        if not self.isEnabled():
            text, shadow = p["dim"], None
            edge = p["dim"] if edge else None
            fill = ("#FFFFFF" if self.pastel() else p["control"]) if fill else None
        o = self.offset()
        body = QRectF(0, 0, self.width() - o, self.height() - o)
        painter = QPainter(self)
        if shadow and o and not self.isDown():
            painter.fillRect(body.translated(o, o), QColor(shadow))
        if self.isDown() and o:
            body.translate(o, o)
        inner = body.adjusted(BORDER, BORDER, -BORDER, -BORDER) if edge else body
        if edge:
            painter.fillRect(body, QColor(edge))
        if fill:
            painter.fillRect(inner, QColor(fill))
        painter.setFont(self.font())
        painter.setPen(QColor(text))
        self.paint_content(painter, inner)
        if self.hasFocus() and self.keyboard_focus:
            painter.setPen(QPen(QColor(text), 1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(inner.adjusted(3, 3, -4, -4))

    def paint_content(self, painter, rect):
        text, metrics = self.text(), self.fontMetrics()
        if self.variant() == "day":
            # Every number sits on the same upper line; the ✓ / × status, when there is one, goes underneath.
            day, _, sign = text.partition("\n")
            split = rect.top() + rect.height() * 0.6
            font = painter.font()
            font.setPixelSize(14)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(QRectF(rect.left(), rect.top(), rect.width(), split - rect.top()),
                             (Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom).value, day)
            if sign:
                font.setPixelSize(12)
                painter.setFont(font)
                painter.drawText(QRectF(rect.left(), split, rect.width(), rect.bottom() - split),
                                 (Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop).value, sign)
            return
        if self.variant() == "nav":
            x = rect.left() + 12
            if not self.icon().isNull():
                size = self.iconSize()
                painter.drawPixmap(QPointF(x, rect.center().y() - size.height() / 2), self.icon().pixmap(size))
                x += size.width() + 10
            painter.drawText(QRectF(x, rect.top(), rect.right() - x, rect.height()),
                             (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter).value, text)
            return
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter.value, text)
        if self.variant() == "ghost" and text:
            width = metrics.horizontalAdvance(text)
            baseline = rect.center().y() + (metrics.ascent() - metrics.descent()) / 2
            painter.fillRect(QRectF(rect.center().x() - width / 2, baseline + 3, width, 2 if self.underMouse() else 1),
                             painter.pen().color())

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def focusInEvent(self, event):
        self.keyboard_focus = event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self.keyboard_focus = False
        super().focusOutEvent(event)


def button(text, callback=None, variant=None):
    w = BrutalButton(text)
    if variant:
        w.setProperty("variant", variant)
    if callback:
        w.clicked.connect(callback)
    return w


class BrutalPanel(QFrame):
    """Card with an inked border and a hard offset shadow; tone fills it with a flat colour."""

    def __init__(self, tone=None, shadow=SHADOW, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.shadow = shadow
        if tone:
            self.setProperty("tone", tone)

    def paintEvent(self, _event):
        p = colors()
        tone, s = self.property("tone"), self.shadow
        body = QRectF(0, 0, self.width() - s, self.height() - s)
        painter = QPainter(self)
        if s:
            painter.fillRect(body.translated(s, s), QColor(p["shadow"]))
        painter.fillRect(body, QColor(INK if tone in TONES else p["edge"]))
        painter.fillRect(body.adjusted(BORDER, BORDER, -BORDER, -BORDER), QColor(TONES.get(tone, p["surface"])))


def panel(parent=None, padding=22, tone=None, shadow=SHADOW):
    w = BrutalPanel(tone, shadow, parent)
    layout = QVBoxLayout(w)
    edge = padding + BORDER
    layout.setContentsMargins(edge, edge, edge + shadow, edge + shadow)
    layout.setSpacing(14)
    return w, layout


def mark(glyph, tone="yellow", size=34):
    """Small square seal beside a card title."""
    box = BrutalPanel(tone, BUTTON_SHADOW)
    box.setFixedSize(size + BUTTON_SHADOW, size + BUTTON_SHADOW)
    layout = QVBoxLayout(box)
    layout.setContentsMargins(BORDER, BORDER, BORDER + BUTTON_SHADOW, BORDER + BUTTON_SHADOW)
    text = label(glyph, "mark")
    text.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(text)
    return box
