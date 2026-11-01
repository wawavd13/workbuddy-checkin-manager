"""Full-window aesthetic review snapshots; reads local cache and task status only."""
import json
from pathlib import Path
import sys
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import qt_app

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
app = QApplication([])
app.setStyle("Fusion")
app.setFont(QFont("Segoe UI", 10))
window = qt_app.Manager(page="overview")
window.local_timer.stop()
window.show()
cases = [(theme, width, height, anchor) for theme in ("dark", "light")
         for width, height in ((1160, 820), (900, 650)) for anchor in ("top", "credits")]
results = []


def anchor_offset(anchor):
    if anchor == "top":
        return 0
    panel = window.credits_value.parentWidget()
    return panel.y() - 20


def run_case():
    if not cases:
        (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(results, ensure_ascii=True))
        window.close()
        app.quit()
        return
    theme, width, height, anchor = cases.pop(0)
    window.resize(width, height)
    window.set_theme(theme)
    window.refresh_local()
    def capture():
        scroll = window.page_widgets["overview"]
        scroll.verticalScrollBar().setValue(anchor_offset(anchor))
        def save():
            path = out / f"review_{theme}_{width}_{anchor}.png"
            assert window.grab().save(str(path))
            results.append({"path": str(path), "horizontal_scroll": scroll.horizontalScrollBar().maximum()})
            QTimer.singleShot(100, run_case)
        QTimer.singleShot(250, save)
    QTimer.singleShot(400, capture)


with patch.object(qt_app.credits, "refresh", side_effect=AssertionError("No account requests during review")), \
     patch.object(window, "start_run", side_effect=AssertionError("No account runner during review")):
    QTimer.singleShot(3500, run_case)
    app.exec()
