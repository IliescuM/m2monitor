"""Capture the client area of a process's top-level window as a PIL image.

Uses Windows Graphics Capture (through the windows-capture package), which
reads the window's own DirectX output from the compositor. Unlike
PrintWindow it works for games, for windows covered by other windows, and
for clients running as administrator. Minimized windows produce no frames
and return None. capture_window keeps one session open per window; call
keep_streams to close the ones whose windows are gone.
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


class WindowStream:
    """One capture session kept open on a window, handing out frames on request.

    Starting a new capture session for every screenshot leaks about one frame
    of native memory per session (~2 MB at 800x600), which adds up to
    gigabytes within hours, so each window keeps a single session.
    """

    def __init__(self, hwnd):
        self.closed = False
        self._want = False
        self._pixels = None
        self._arrived = threading.Event()
        session = WindowsCapture(cursor_capture=False, draw_border=False,
                                 window_hwnd=hwnd)

        @session.event
        def on_frame_arrived(frame, control):
            # Frames arrive many times a second; copy only the one asked for.
            if self._want:
                self._pixels = frame.frame_buffer.copy()
                self._want = False
                self._arrived.set()

        @session.event
        def on_closed():
            self.closed = True
            self._arrived.set()

        self._control = session.start_free_threaded()

    def grab(self):
        """Return the next BGRA frame as a numpy array, or None."""
        self._pixels = None
        self._arrived.clear()
        self._want = True
        self._arrived.wait(FRAME_TIMEOUT_SECONDS)
        self._want = False
        return self._pixels

    def stop(self):
        try:
            self._control.stop()
        except Exception:  # already finished, e.g. the window closed
            pass


_streams = {}  # hwnd -> WindowStream


def keep_streams(hwnds):
    """Stop the capture sessions of windows not in `hwnds`."""
    for hwnd in list(_streams):
        if hwnd not in hwnds:
            _streams.pop(hwnd).stop()


def capture_window(hwnd):
    if not hwnd or user32.IsIconic(hwnd):
        return None
    try:
        stream = _streams.get(hwnd)
        if stream is None or stream.closed:
            stream = _streams[hwnd] = WindowStream(hwnd)
        pixels = stream.grab()
    except Exception:  # windows-capture raises plain Exception for closed windows
        _streams.pop(hwnd, None)
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
