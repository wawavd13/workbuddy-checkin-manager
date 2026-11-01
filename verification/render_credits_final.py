"""Verify credits card, real refresh button, details and both themes without network."""
import json
from pathlib import Path
import sys
from unittest.mock import patch
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import qt_app

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
app = QApplication([])
app.setStyle("Fusion")
app.setFont(QFont("Segoe UI", 10))
window = qt_app.Manager(page="overview", query_tasks=False)
window.local_timer.stop()
window.show()
cases = [(theme, width, height, detail) for theme in ("light", "dark")
         for width, height, detail in ((1160, 820, False), (900, 650, False), (1160, 820, True))]
results = []


def next_case():
    if not cases:
        cache = qt_app.credits.load_cache()
        read = patch.object(qt_app.credits, "refresh", return_value=cache)
        mock = read.start()
        QTest.mouseClick(window.credits_button, Qt.MouseButton.LeftButton)
        def verify_click():
            assert mock.call_count == 1
            assert window.credits_button.isEnabled()
            assert not window.busy
            read.stop()
            results.append({"refresh_button": "pass", "value": window.credits_value.text()})
            (out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(results, ensure_ascii=True))
            window.close()
            app.quit()
        QTimer.singleShot(600, verify_click)
        return
    theme, width, height, detail = cases.pop(0)
    window.resize(width, height)
    window.set_theme(theme)
    window.credits_detail.setVisible(detail)
    window.credits_detail_button.setText("收起有效期图表  ▴" if detail else "查看有效期图表  ▾")
    window.refresh_local()
    window.raise_()
    def capture():
        bar = window.page_widgets["overview"].verticalScrollBar()
        bar.setValue(290 if detail else 120)
        def save():
            name = f"credits_{theme}_{width}_{'details' if detail else 'summary'}.png"
            assert window.grab().save(str(out / name))
            results.append({"path": str(out / name), "remaining": window.credits_value.text(), "expiry": window.credits_expiry.text()})
            QTimer.singleShot(100, next_case)
        QTimer.singleShot(200, save)
    QTimer.singleShot(400, capture)


QTimer.singleShot(100, next_case)
app.exec()
