"""Render actual local UI pages and capture the client area for visual checks."""
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except (AttributeError, OSError):
    pass

out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.gettempdir()) / "WorkBuddyManagerVerification"
out.mkdir(exist_ok=True)
window = app.Manager()
window.geometry("1440x980+100+70")
window.attributes("-topmost", True)
window.lift()
window.update()
pages = iter(["overview", "schedule", "growth", "history"])
results = []


def capture(page):
    window.update_idletasks()
    path = out / (page + ".png")
    command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
               str(ROOT / "verification/capture.ps1"), "-OutputPath", str(path),
               "-Left", str(window.winfo_rootx()), "-Top", str(window.winfo_rooty()),
               "-Width", str(window.winfo_width()), "-Height", str(window.winfo_height())]
    subprocess.run(command, check=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    results.append({"page": page, "path": str(path), "width": window.winfo_width(), "height": window.winfo_height()})
    window.after(100, next_page)


def next_page():
    try:
        page = next(pages)
    except StopIteration:
        (out / "render_result.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        window.closing = True
        window.destroy()
        return
    window.show_page(page)
    window.refresh_local(reschedule=False)
    window.after(600, lambda: capture(page))


window.after(3000, next_page)
window.mainloop()
print(json.dumps(results, ensure_ascii=False))
