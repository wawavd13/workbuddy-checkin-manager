"""Grab button states, dialogs, time picker and filter menu in both themes; no account operations."""
import json
from pathlib import Path
import sys

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtWidgets import QApplication, QGridLayout, QMenu, QWidget

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import qt_app
import qt_theme
from qt_chrome import MessageDialog
from qt_controls import TimePicker

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
app = QApplication([])
app.setStyle("Fusion")
app.setFont(QFont("Segoe UI", 10))
window = qt_app.Manager(query_tasks=False)
window.local_timer.stop()
window.show()
results = []
SPECS = [("默认按钮", None, {}), ("主要按钮", "primary", {}), ("危险按钮", "danger", {}),
         ("导航选中", "nav", {"selected": True}), ("文字链接", "ghost", {}),
         ("27\n✓", "day", {"tone": "success"}), ("31\n✓", "day", {"tone": "success", "today": True}),
         ("15", "day", {"tone": "unknown", "today": True})]


def board(mode):
    host = QWidget()
    host.setStyleSheet(qt_theme.stylesheet(mode))
    grid = QGridLayout(host)
    grid.setContentsMargins(24, 24, 24, 24)
    grid.setSpacing(14)
    for row, (text, variant, props) in enumerate(SPECS):
        for col, state in enumerate(("normal", "down", "disabled")):
            b = qt_theme.button(text, variant=variant)
            for key, value in props.items():
                b.setProperty(key, value)
            if variant == "day":
                b.setFixedSize(62, 48)
            b.setDown(state == "down")
            b.setEnabled(state != "disabled")
            grid.addWidget(b, row, col)
    for col, checked in enumerate((True, False)):
        switch = qt_theme.Switch(checked)
        switch.set_theme(mode)
        grid.addWidget(switch, len(SPECS), col)
    host.resize(560, 660)
    host.show()
    return host


def dialog():
    box = MessageDialog("确认执行？", "只查询服务端状态，不领取积分，也不运行成长中心。", parent=window)
    box.show()
    return box


def error():
    box = MessageDialog("操作未完成", "网络连接失败，请检查网络后重试。签到状态未改变。", accept_text="知道了",
                        reject_text=None, glyph="!", tone="pink", accept_variant=None, parent=window)
    box.show()
    return box


def shot(widget, mode):
    """Frameless dialogs have see-through shadow gutters; composite them on the page colour, as on screen."""
    image = widget.grab().toImage()
    if not widget.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground):
        return image
    image.setDevicePixelRatio(1)
    canvas = QImage(image.width() + 48, image.height() + 48, QImage.Format.Format_ARGB32)
    canvas.fill(QColor(qt_theme.PALETTES[mode]["bg"]))
    painter = QPainter(canvas)
    painter.drawImage(24, 24, image)
    painter.end()
    return canvas


def menu():
    popup = QMenu(window)
    popup.setObjectName("filterMenu")
    for index, option in enumerate(["全部结果", "成功 / 已签", "异常", "其他"]):
        action = popup.addAction(option)
        action.setCheckable(True)
        action.setChecked(index == 0)
    popup.popup(window.mapToGlobal(QPoint(400, 300)))
    popup.setActiveAction(popup.actions()[1])
    return popup


def run(modes):
    if not modes:
        print(json.dumps(results, ensure_ascii=True))
        window.close()
        app.quit()
        return
    mode = modes.pop(0)
    window.set_theme(mode)
    widgets = {"buttons": board(mode), "dialog": dialog(), "error": error(), "picker": TimePicker("08:30", window),
               "menu": menu()}
    widgets["picker"].show()

    def capture():
        for name, widget in widgets.items():
            path = out / f"{name}_{mode}.png"
            assert shot(widget, mode).save(str(path))
            results.append(str(path))
            widget.close()
        QTimer.singleShot(150, lambda: run(modes))
    QTimer.singleShot(600, capture)


QTimer.singleShot(200, lambda: run(["light", "dark"]))
app.exec()
