"""Read-only expiry card acceptance snapshots; no account operations."""
import json
from pathlib import Path
import sys
from unittest.mock import patch

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import qt_app

app = QApplication([])
app.setStyle("Fusion")
app.setFont(QFont("Segoe UI", 10))
window = qt_app.Manager(query_tasks=False)
window.local_timer.stop()
window.show()
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
cases = [(theme, width, height, state) for theme in ("light", "dark")
         for width, height in ((1160, 820), (900, 650)) for state in ("timeline", "expanded", "collapsed", "cycle")]
results = []


def run_case():
    if not cases:
        (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(results, ensure_ascii=True))
        window.close()
        app.quit()
        return
    theme, width, height, state = cases.pop(0)
    window.resize(width, height)
    window.set_theme(theme)
    window.refresh_local()
    window.show_page("overview")
    chart = window.credits_detail
    if chart.isHidden() != (state == "collapsed"):
        window.toggle_credit_detail()
    for row in chart.rows:
        row.setChecked(False)
    if state == "expanded" and chart.rows:
        QTest.mouseClick(max(chart.rows, key=lambda r: len(r.group["rows"])), Qt.MouseButton.LeftButton)
    cycle_rows = [r for r in chart.rows if r.cycle]
    if state == "cycle" and cycle_rows:
        QTest.mouseClick(cycle_rows[0], Qt.MouseButton.LeftButton)

    def position():
        balance = window.credits_value.parentWidget()
        scroll = window.page_widgets["overview"]
        scroll.verticalScrollBar().setValue(balance.y() - 16)

        def capture():
            path = out / f"expiry_{theme}_{width}_{state}.png"
            assert window.grab().save(str(path))
            clipped = [w.text() for w in chart.findChildren(QLabel)
                       if w.isVisible() and not w.wordWrap() and w.fontMetrics().horizontalAdvance(w.text()) > w.width()]
            results.append({"path": str(path), "rows": [(r.group["date"], r.countdown, r.group["remaining"], r.tag) for r in chart.rows],
                            "expanded": [r.group["date"] for r in chart.rows if r.isChecked()],
                            "clipped": clipped, "horizontal_scroll": scroll.horizontalScrollBar().maximum()})
            QTimer.singleShot(100, run_case)
        QTimer.singleShot(250, capture)
    QTimer.singleShot(400, position)


with patch.object(qt_app.credits, "refresh", side_effect=AssertionError("No network during screenshots")):
    QTimer.singleShot(100, run_case)
    app.exec()
