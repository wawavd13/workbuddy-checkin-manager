"""Capture a process's main window with PrintWindow, even when partly covered."""
import ctypes
from ctypes import wintypes
import sys

from PIL import Image

user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
user32.SetProcessDPIAware()
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumProc, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowDC.argtypes = [wintypes.HWND]
user32.GetWindowDC.restype = wintypes.HDC
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


pid, target = int(sys.argv[1]), sys.argv[2]
windows = []


def visit(hwnd, _param):
    owner = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
    rect = wintypes.RECT()
    if owner.value == pid and user32.IsWindowVisible(hwnd) and user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        windows.append(((rect.right - rect.left) * (rect.bottom - rect.top), hwnd, rect))
    return True


user32.EnumWindows(EnumProc(visit), 0)
if not windows:
    raise SystemExit("No visible window for this process")
_area, hwnd, rect = max(windows, key=lambda item: item[0])
width, height = rect.right - rect.left, rect.bottom - rect.top
window_dc = user32.GetWindowDC(hwnd)
memory_dc = gdi32.CreateCompatibleDC(window_dc)
bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
gdi32.SelectObject(memory_dc, bitmap)
try:
    if not user32.PrintWindow(hwnd, memory_dc, 2):
        raise SystemExit("PrintWindow failed")
    header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
    pixels = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(memory_dc, bitmap, 0, height, pixels, ctypes.byref(header), 0)
    Image.frombuffer("RGB", (width, height), pixels, "raw", "BGRX", 0, 1).save(target)
finally:
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory_dc)
    user32.ReleaseDC(hwnd, window_dc)
print(target, width, height)
