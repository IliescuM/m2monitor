"""Capture the client area of a process's top-level window as a PIL image.

Uses Windows Graphics Capture (through the windows-capture package), which
reads the window's own DirectX output from the compositor. Unlike
PrintWindow it works for games, for windows covered by other windows, and
for clients running as administrator. Minimized windows produce no frames
and return None.
"""
import ctypes
import threading
from ctypes import wintypes

from PIL import Image
from windows_capture import WindowsCapture

user32 = ctypes.WinDLL("user32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

FRAME_TIMEOUT_SECONDS = 2.0
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
GW_OWNER = 4

_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = [_EnumWindowsProc, wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
dwmapi.DwmGetWindowAttribute.argtypes = [
    wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
]

# Capture frames are in physical pixels; window rects must be too. This fails
# harmlessly if something (e.g. CustomTkinter) already set the awareness.
user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)


def main_window(pid):
    """Return the largest visible, unowned top-level window of a process."""
    found = []

    def callback(hwnd, _):
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if (owner_pid.value == pid and user32.IsWindowVisible(hwnd)
                and not user32.GetWindow(hwnd, GW_OWNER)):
            rect = wintypes.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(rect))
            found.append((rect.right * rect.bottom, hwnd))
        return True

    user32.EnumWindows(_EnumWindowsProc(callback), 0)
    return max(found)[1] if found else None


def client_box(hwnd):
    """Return the client area as (left, top, right, bottom) inside a capture frame.

    Window capture frames cover the DWM extended frame bounds, which include
    the title bar and borders.
    """
    frame = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
                                    ctypes.byref(frame), ctypes.sizeof(frame)):
        return None  # failed HRESULT, e.g. the window is closing
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    client = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(client))
    left = origin.x - frame.left
    top = origin.y - frame.top
    return left, top, left + client.right, top + client.bottom


def grab_frame(hwnd):
    """Return one BGRA frame of the window as a numpy array, or None."""
    result = {}
    arrived = threading.Event()
    session = WindowsCapture(cursor_capture=False, draw_border=False,
                             window_hwnd=hwnd)

    @session.event
    def on_frame_arrived(frame, control):
        result["pixels"] = frame.frame_buffer.copy()
        control.stop()
        arrived.set()

    @session.event
    def on_closed():
        arrived.set()

    control = session.start_free_threaded()
    arrived.wait(FRAME_TIMEOUT_SECONDS)
    control.stop()
    return result.get("pixels")


def capture_window(hwnd):
    if not hwnd or user32.IsIconic(hwnd):
        return None
    try:
        pixels = grab_frame(hwnd)
    except Exception:  # windows-capture raises plain Exception for closed windows
        return None
    box = client_box(hwnd)
    if pixels is None or box is None:
        return None
    left, top, right, bottom = box
    height, width = pixels.shape[:2]
    left, top = max(left, 0), max(top, 0)
    right, bottom = min(right, width), min(bottom, height)
    if right <= left or bottom <= top:
        return None
    # Crop before converting, so only the client area is copied.
    return Image.fromarray(pixels[top:bottom, left:right, 2::-1].copy())  # BGRA to RGB
