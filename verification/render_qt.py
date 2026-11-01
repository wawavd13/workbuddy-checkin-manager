"""Read-only native UI rendering for visual verification."""
import json
from pathlib import Path
import sys
import tempfile

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import qt_app

app = QApplication(sys.argv[:1])
app.setStyle("Fusion")
window = qt_app.Manager()
window.resize(1160, 860)
window.move(80, 60)
window.show()
window.raise_()
window.activateWindow()
out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.gettempdir()) / "WorkBuddyQtVerification"
out.mkdir(exist_ok=True)
cases = [(page, "dark", 1160, 860) for page in ("overview", "schedule", "growth", "history")]
cases += [("overview", "light", 1160, 860), ("schedule", "light", 1160, 860), ("overview", "dark", 920, 700), ("schedule", "dark", 920, 700)]
results = []


def run_case():
    if not cases:
        (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        window.close()
        app.quit()
        return
    page, theme, width, height = cases.pop(0)
    window.resize(width, height)
    window.set_theme(theme)
    window.show_page(page)
    window.refresh_local()
    def capture():
        name = f"{page}_{theme}_{width}.png"
        image = app.primaryScreen().grabWindow(int(window.winId()))
        if not image.save(str(out / name)):
            raise RuntimeError("Failed to save UI screenshot")
        results.append({"page": page, "theme": theme, "width": width, "path": str(out / name)})
        QTimer.singleShot(100, run_case)
    QTimer.singleShot(500, capture)


QTimer.singleShot(2300, run_case)
app.exec()
print(json.dumps(results, ensure_ascii=False))
