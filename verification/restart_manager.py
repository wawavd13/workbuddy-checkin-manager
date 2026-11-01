"""Gracefully close a running manager window (WM_CLOSE) and relaunch it with current code."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
pid = int(sys.argv[1])
runtime = {}
try:
    runtime = json.loads((ROOT / "runtime.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    pass
if runtime.get("running"):
    raise SystemExit("后台任务正在运行，未关闭窗口")

user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumProc, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
windows = []


def visit(hwnd, _param):
    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    rect = wintypes.RECT()
    if owner.value == pid and user32.IsWindowVisible(hwnd) and user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        windows.append(((rect.right - rect.left) * (rect.bottom - rect.top), hwnd))
    return True


user32.EnumWindows(EnumProc(visit), 0)
if not windows:
    raise SystemExit("未找到该进程的可见窗口")
handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
user32.PostMessageW(max(windows)[1], 0x0010, 0, 0)  # WM_CLOSE
exited = kernel32.WaitForSingleObject(handle, 10000) == 0
kernel32.CloseHandle(handle)
if not exited:
    raise SystemExit("窗口未在 10 秒内关闭（可能弹出了确认框），未重新打开")
child = subprocess.Popen([str(Path(sys.executable).with_name("pythonw.exe")), "-I", str(ROOT / "app.py")],
                         cwd=r"C:\Windows\System32")
time.sleep(5)
if child.poll() is not None:
    raise SystemExit("新窗口启动后退出")
print(child.pid)
