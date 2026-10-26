"""Capture the client area of a process's top-level window as a PIL image.

Uses Windows Graphics Capture (through the windows-capture package), which
reads the window's own DirectX output from the compositor. Unlike
PrintWindow it works for games, for windows covered by other windows, and
for clients running as administrator. Minimized windows produce no frames
and return nothing. One session stays open per window; call keep_streams
to close the ones whose windows are gone.
"""
import ctypes
import os
import threading
import time
from collections import deque
from ctypes import wintypes

# numpy (pulled in by windows-capture) otherwise reserves OpenBLAS buffers for
# every CPU thread, about 600 MB on a many-core PC; nothing here needs them.
# Must be set before numpy is first imported.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

from PIL import Image  # noqa: E402
from windows_capture import WindowsCapture  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

FRAME_TIMEOUT_SECONDS = 2.0
# Keep at most this many frames per second per window; names are read far
# less often, and every kept frame is a copy of the whole window.
FRAME_INTERVAL_SECONDS = 0.5
# Recent frames kept per window, for OCR to merge (see charname.py).
FRAMES_KEPT = 5
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
    """One capture session kept open on a window, holding its latest frame.

    Starting a new capture session for every screenshot leaks about one frame
    of native memory per session (~2 MB at 800x600), which adds up to
    gigabytes within hours, so each window keeps a single session.

    Windows only sends a frame when the window's content changes, so recent
    frames are kept rather than waiting for the next one: a static screen
    (loading, AFK, a frame limiter) still has a current picture.
    """

    def __init__(self, hwnd):
        self._lock = threading.Lock()
        self._frames = deque(maxlen=FRAMES_KEPT)
        self._kept_at = 0.0
        self._first = threading.Event()
        self._closed = False
        try:
            self._control = self._start(hwnd, int(FRAME_INTERVAL_SECONDS * 1000))
        except Exception:  # Windows versions without a minimum update interval
            self._control = self._start(hwnd, None)

    def _start(self, hwnd, interval_ms):
        session = WindowsCapture(cursor_capture=False, draw_border=False,
                                 minimum_update_interval=interval_ms,
                                 window_hwnd=hwnd)

        @session.event
        def on_frame_arrived(frame, control):
            # Also throttled here, for Windows versions that ignore the interval.
            now = time.monotonic()
            if now - self._kept_at < FRAME_INTERVAL_SECONDS and self._first.is_set():
                return
            pixels = frame.frame_buffer.copy()
            with self._lock:
                self._frames.append(pixels)
                self._kept_at = now
            self._first.set()

        @session.event
        def on_closed():
            self._closed = True
            self._first.set()

        return session.start_free_threaded()

    @property
    def alive(self):
        """False once the session has ended, with or without on_closed."""
        return not self._closed and not self._control.is_finished()

    def recent(self):
        """Return the kept BGRA frames as numpy arrays, oldest first.

        The arrays are never written to again, so callers may keep them.
        """
        self._first.wait(FRAME_TIMEOUT_SECONDS)
        with self._lock:
            return list(self._frames)

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


def capture_frames(hwnd):
    """Return the window's recent client-area images (PIL), oldest first."""
    if not hwnd or user32.IsIconic(hwnd):
        return []
    stream = _streams.get(hwnd)
    if stream is not None and not stream.alive:
        _streams.pop(hwnd).stop()
        stream = None
    try:
        if stream is None:
            stream = _streams[hwnd] = WindowStream(hwnd)
        frames = stream.recent()
    except Exception:  # windows-capture raises plain Exception for closed windows
        broken = _streams.pop(hwnd, None)
        if broken is not None:
            broken.stop()
        return []
    box = client_box(hwnd)
    if box is None:
        return []
    images = []
    for pixels in frames:
        height, width = pixels.shape[:2]
        left, top = max(box[0], 0), max(box[1], 0)
        right, bottom = min(box[2], width), min(box[3], height)
        if right > left and bottom > top:
            # Crop before converting, so only the client area is copied.
            images.append(Image.fromarray(pixels[top:bottom, left:right, 2::-1].copy()))  # BGRA to RGB
    return images


def capture_window(hwnd):
    """Return the window's latest client-area image (PIL), or None."""
    images = capture_frames(hwnd)
    return images[-1] if images else None
