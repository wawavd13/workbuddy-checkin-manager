"""Read-only screenshots of every page in both themes, with a text-clipping check."""
import json
from pathlib import Path
import sys
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QLabel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import qt_app
import qt_theme

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
app = QApplication([])
app.setStyle("Fusion")
app.setFont(QFont("Segoe UI", 10))
window = qt_app.Manager(page="overview")
window.local_timer.stop()
window.load_usage(sync=True)
window.show()
views = [("overview", "top"), ("overview", "credits"), ("overview", "bottom"), ("schedule", "top"),
         ("growth", "top"), ("history", "top"), ("usage", "top"), ("usage", "bottom")]
cases = [(theme, width, height, page, anchor) for theme in ("light", "dark")
         for width, height in ((1160, 820), (900, 650)) for page, anchor in views]
results = []


def clipped():
    found = []
    for w in window.findChildren(QLabel):
        if w.isVisible() and not w.wordWrap() and w.text():
            need = max(w.fontMetrics().horizontalAdvance(line) for line in w.text().split("\n"))
            if need > w.contentsRect().width() + 1:
                found.append(w.text())
    for b in window.findChildren(qt_theme.BrutalButton):
        if b.isVisible() and b.text():
            inked = 0 if b.variant() in qt_theme.FLAT_VARIANTS else 2 * qt_theme.BORDER
            room = b.width() - b.offset() - inked - (b.iconSize().width() + 22 if b.variant() == "nav" else 4)
            need = max(b.fontMetrics().horizontalAdvance(line) for line in b.text().split("\n"))
            if need > room:
                found.append(b.text())
    return found


def run_case():
    if not cases:
        (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"cases": len(results), "clipped": [r for r in results if r["clipped"]],
                          "hscroll": max(r["hscroll"] for r in results)}, ensure_ascii=True))
        window.close()
        app.quit()
        return
    theme, width, height, page, anchor = cases.pop(0)
    window.resize(width, height)
    window.set_theme(theme)
    window.show_page(page)
    window.refresh_local()

    def capture():
        scroll = window.page_widgets[page]
        bar = scroll.verticalScrollBar()
        bar.setValue({"top": 0, "credits": window.credits_value.parentWidget().y() - 16, "bottom": bar.maximum()}[anchor])

        def save():
            path = out / f"{page}_{anchor}_{theme}_{width}.png"
            assert window.grab().save(str(path))
            results.append({"path": str(path), "clipped": clipped(), "hscroll": scroll.horizontalScrollBar().maximum()})
            QTimer.singleShot(80, run_case)
        QTimer.singleShot(250, save)
    QTimer.singleShot(350, capture)


with patch.object(qt_app.credits, "refresh", side_effect=AssertionError("No account requests during screenshots")), \
     patch.object(window, "start_run", side_effect=AssertionError("No account runner during screenshots")):
    QTimer.singleShot(3000, run_case)
    app.exec()
