"""Visual check of opened time picker and filter menu; no settings saved."""
from pathlib import Path
import sys
import tempfile
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QMenu
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import qt_app
app = QApplication([])
app.setStyle("Fusion")
w = qt_app.Manager(query_tasks=False)
w.resize(1080, 800)
w.show_page("schedule")
w.show()
out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.gettempdir()) / "WorkBuddyQtVerification"
out.mkdir(parents=True, exist_ok=True)


def picker():
    dialog = qt_app.TimePicker("00:05", w)
    dialog.show()
    def capture():
        app.primaryScreen().grabWindow(int(dialog.winId())).save(str(out / "time_picker.png"))
        dialog.close()
        w.show_page("history")
        QTimer.singleShot(200, popup)
    QTimer.singleShot(500, capture)


def popup():
    def capture():
        menus = [v for v in QApplication.topLevelWidgets() if isinstance(v, QMenu) and v.isVisible()]
        if not menus:
            raise RuntimeError("Filter menu not visible")
        menu = menus[0]
        app.primaryScreen().grabWindow(int(menu.winId())).save(str(out / "filter_popup.png"))
        menu.close()
        w.close()
        app.quit()
    QTimer.singleShot(500, capture)
    w.result_filter.show_popup()


QTimer.singleShot(300, picker)
app.exec()
