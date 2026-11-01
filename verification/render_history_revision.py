"""Read-only screenshots and geometry checks for the revised record table."""
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

app = QApplication([])
app.setStyle("Fusion")
app.setFont(QFont("Segoe UI", 10))
window = qt_app.Manager(page="history", query_tasks=False)
window.local_timer.stop()
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
cases = [("history", theme, width, height) for theme in ("light", "dark")
         for width, height in ((1160, 820), (900, 650))]
cases += [("overview", theme, width, height) for theme in ("light", "dark")
          for width, height in ((1160, 820), (900, 650))]
results = []


def capture_case():
    if not cases:
        (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(results, ensure_ascii=True))
        window.close()
        app.quit()
        return
    page, theme, width, height = cases.pop(0)
    window.resize(width, height)
    window.set_theme(theme)
    window.show_page(page)
    window.refresh_local()
    window.page_widgets[page].verticalScrollBar().setValue(0)
    window.show()
    window.raise_()
    window.activateWindow()
    def save():
        name = f"{page}_{theme}_{width}.png"
        if not app.primaryScreen().grabWindow(int(window.winId())).save(str(out / name)):
            raise RuntimeError("Screenshot failed")
        columns = [window.table.columnWidth(col) for col in range(window.table.columnCount())]
        results.append({"page": page, "theme": theme, "width": width, "image": str(out / name),
                        "columns": columns, "horizontal_scroll": window.table.horizontalScrollBar().maximum(),
                        "headers": [window.table.horizontalHeaderItem(col).text() for col in range(6)],
                        "first_row": [window.table.item(0, col).text() for col in range(6)] if window.table.rowCount() else [],
                        "summary": window.history_today.text()})
        QTimer.singleShot(100, capture_case)
    QTimer.singleShot(500, save)


with patch.object(qt_app.subprocess, "Popen", side_effect=AssertionError("No account operations during verification")):
    QTimer.singleShot(100, capture_case)
    app.exec()
