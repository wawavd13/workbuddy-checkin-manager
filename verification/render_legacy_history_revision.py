"""Read-only legacy history screenshot; no scheduler/account operations."""
import ctypes
from pathlib import Path
import sys
from unittest.mock import patch
from PIL import ImageGrab

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except (AttributeError, OSError):
    pass
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import legacy_tk_app

with patch.object(legacy_tk_app.Manager, "refresh_tasks"):
    window = legacy_tk_app.Manager(initial_page="history")
    window.geometry("1400x940+40+40")
    window.lift()
    def capture():
        window.update_idletasks()
        x, y = window.winfo_rootx(), window.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x + window.winfo_width(), y + window.winfo_height())).save(sys.argv[1])
        window.closing = True
        window.destroy()
    window.after(700, capture)
    window.mainloop()
