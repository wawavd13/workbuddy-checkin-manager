"""Open the installed graphical expiry view without running account operations."""
from pathlib import Path
import sys
from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import qt_app

app = QApplication([])
app.setStyle("Fusion")
app.setFont(QFont("Segoe UI", 10))
window = qt_app.Manager(page="overview", initial_theme="light")
window.show()


def reveal():
    balance = window.credits_value.parentWidget()
    scroll = window.page_widgets["overview"]
    scroll.verticalScrollBar().setValue(balance.y() - 16)
    if len(sys.argv) > 1:
        QTimer.singleShot(250, lambda: window.grab().save(sys.argv[1]))


QTimer.singleShot(750, reveal)
app.exec()
