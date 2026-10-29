"""Monitor for running M2 game clients.

Shows every client process (PROCESS_NAME) as a card with its character name,
server connections, latency and traffic. The name is read with OCR from
screenshots of the client in a background thread, or typed on the card by
the user, which then lasts until that client closes. Latency and traffic come
from Windows TCP extended statistics (EStats), which only an elevated prompt
can enable; without admin rights those fields show "-". Each client has a
Disconnect button that resets its server connections, which also needs
admin; a header checkbox decides whether it asks for a second confirming
click. Nothing else here sends, blocks
or alters any packets.
"""
import ctypes
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import webbrowser
from collections import Counter, deque
from ctypes import wintypes

import customtkinter as ctk
import psutil

import capture
import charname

PROCESS_NAME = "metin2client.exe"
REFRESH_SECONDS = 1.0
# Read a client's name this often until one is found, then less often.
NAME_FAST_SECONDS = 1.0
NAME_SLOW_SECONDS = 4.0
# A card shows the most common of the last NAME_HISTORY names read, once one
# has been read NAME_MIN_VOTES times. A different name read NAME_SWITCH_VOTES
# times in a row takes over at once, so a relog to another character shows
# quickly. Reads that find no name (AFK dialog, loading screen, a fight
# covering it) leave the name as it is.
NAME_HISTORY = 12
NAME_MIN_VOTES = 2
NAME_SWITCH_VOTES = 3
MANUAL_NAME_MAX = 24
SETTINGS_PATH = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                             "m2monitor", "settings.json")
# A built .exe unpacks bundled files under sys._MEIPASS.
ICON_PATH = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))),
                         "assets", "icon.ico")
APP_ID = "IliescuM.M2Monitor"
REPO_URL = "https://github.com/IliescuM/m2monitor"
FEEDBACK_URL = f"{REPO_URL}/discussions/new/choose"

MIB_TCP_STATE_ESTAB = 5
MIB_TCP_STATE_DELETE_TCB = 12
TCP_ESTATS_DATA = 1
TCP_ESTATS_PATH = 3


class MIB_TCPROW(ctypes.Structure):
    _fields_ = [
        ("dwState", wintypes.DWORD),
        ("dwLocalAddr", wintypes.DWORD),
        ("dwLocalPort", wintypes.DWORD),
        ("dwRemoteAddr", wintypes.DWORD),
        ("dwRemotePort", wintypes.DWORD),
    ]


class TCP_ESTATS_RW_v0(ctypes.Structure):
    # TCP_ESTATS_DATA_RW_v0 and TCP_ESTATS_PATH_RW_v0 share this layout.
    _fields_ = [("EnableCollection", ctypes.c_ubyte)]


class TCP_ESTATS_DATA_ROD_v0(ctypes.Structure):
    _fields_ = [
        ("DataBytesOut", ctypes.c_uint64),
        ("DataSegsOut", ctypes.c_uint64),
        ("DataBytesIn", ctypes.c_uint64),
        ("DataSegsIn", ctypes.c_uint64),
        ("SegsOut", ctypes.c_uint64),
        ("SegsIn", ctypes.c_uint64),
        ("SoftErrors", wintypes.ULONG),
        ("SoftErrorReason", wintypes.ULONG),
        ("SndUna", wintypes.ULONG),
        ("SndNxt", wintypes.ULONG),
        ("SndMax", wintypes.ULONG),
        ("ThruBytesAcked", ctypes.c_uint64),
        ("RcvNxt", wintypes.ULONG),
        ("ThruBytesReceived", ctypes.c_uint64),
    ]


_PATH_ROD_FIELDS = (
    "FastRetran", "Timeouts", "SubsequentTimeouts", "CurTimeoutCount",
    "AbruptTimeouts", "PktsRetrans", "BytesRetrans", "DupAcksIn",
    "SacksRcvd", "SackBlocksRcvd", "CongSignals", "PreCongSumCwnd",
    "PreCongSumRtt", "PostCongSumRtt", "PostCongCountRtt", "EcnSignals",
    "EceRcvd", "SendStall", "QuenchRcvd", "RetranThresh",
    "SndDupAckEpisodes", "SumBytesReordered", "NonRecovDa",
    "NonRecovDaEpisodes", "AckAfterFr", "DsackDups", "SampleRtt",
    "SmoothedRtt", "RttVar", "MaxRtt", "MinRtt", "SumRtt", "CountRtt",
    "CurRto", "MaxRto", "MinRto", "CurMss", "MaxMss", "MinMss",
    "SpuriousRtoDetections",
)


class TCP_ESTATS_PATH_ROD_v0(ctypes.Structure):
    _fields_ = [(name, wintypes.ULONG) for name in _PATH_ROD_FIELDS]


_iphlpapi = ctypes.WinDLL("iphlpapi")

_SetEStats = _iphlpapi.SetPerTcpConnectionEStats
_SetEStats.argtypes = [
    ctypes.POINTER(MIB_TCPROW), ctypes.c_int,
    ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG,
]
_SetEStats.restype = wintypes.ULONG

_GetEStats = _iphlpapi.GetPerTcpConnectionEStats
_GetEStats.argtypes = [
    ctypes.POINTER(MIB_TCPROW), ctypes.c_int,
    ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG,
    ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG,
    ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG,
]
_GetEStats.restype = wintypes.ULONG

_SetTcpEntry = _iphlpapi.SetTcpEntry
_SetTcpEntry.argtypes = [ctypes.POINTER(MIB_TCPROW)]
_SetTcpEntry.restype = wintypes.DWORD


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(settings):
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError:
        pass  # settings are a convenience; the app works without them


def restart_as_admin():
    """Relaunch this app elevated. Returns True if the UAC prompt was accepted."""
    args = sys.argv[1:]
    if not getattr(sys, "frozen", False):  # a built .exe is sys.executable itself
        args = [os.path.abspath(sys.argv[0])] + args
    params = subprocess.list2cmdline(args)
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, None, 1)
    return result > 32


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def ip_to_dword(ip):
    return struct.unpack("<I", socket.inet_aton(ip))[0]


def make_row(conn):
    return MIB_TCPROW(
        MIB_TCP_STATE_ESTAB,
        ip_to_dword(conn.laddr.ip), socket.htons(conn.laddr.port),
        ip_to_dword(conn.raddr.ip), socket.htons(conn.raddr.port),
    )


def enable_estats(row):
    """Turn on kernel stat collection for one connection. Needs admin."""
    ok = True
    for estats_type in (TCP_ESTATS_DATA, TCP_ESTATS_PATH):
        rw = TCP_ESTATS_RW_v0(1)
        err = _SetEStats(ctypes.byref(row), estats_type,
                         ctypes.byref(rw), 0, ctypes.sizeof(rw), 0)
        ok = ok and err == 0
    return ok


def close_connection(conn):
    """Reset one TCP connection. Returns 0 on success. Needs admin."""
    row = make_row(conn)
    row.dwState = MIB_TCP_STATE_DELETE_TCB
    return _SetTcpEntry(ctypes.byref(row))


def read_estats(row, estats_type, rod_cls):
    rod = rod_cls()
    err = _GetEStats(ctypes.byref(row), estats_type,
                     None, 0, 0, None, 0, 0,
                     ctypes.byref(rod), 0, ctypes.sizeof(rod))
    return rod if err == 0 else None


def established_conns(proc):
    try:
        return [c for c in proc.net_connections(kind="tcp4")
                if c.status == psutil.CONN_ESTABLISHED and c.raddr]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []


def find_clients():
    # Ask every process only for its name: fetching more attributes for all
    # of them takes hundreds of milliseconds, mostly on protected processes.
    clients = []
    for proc in psutil.process_iter(["name"]):
        if (proc.info["name"] or "").lower() != PROCESS_NAME:
            continue
        try:
            info = proc.as_dict(["pid", "create_time", "memory_info"])
        except psutil.NoSuchProcess:
            continue
        clients.append((info, established_conns(proc)))
    clients.sort(key=lambda c: c[0]["create_time"] or 0)
    return clients


def fmt_rate(value):
    return "-" if value is None else f"{value / 1024:.1f}"


def fmt_total(value):
    return "-" if value is None else f"{value / (1024 * 1024):.2f}"


def collect_clients(admin, enabled, previous):
    """Return (info, stats) per client, with one stats tuple per connection.

    `enabled` and `previous` carry EStats state between calls.
    """
    now = time.monotonic()
    clients = []
    seen = set()
    for info, conns in find_clients():
        stats = []
        for conn in conns:
            key = (info["pid"], conn.laddr, conn.raddr)
            seen.add(key)
            server = f"{conn.raddr.ip}:{conn.raddr.port}"
            rtt = rate_in = rate_out = total_in = total_out = None

            if admin:
                row = make_row(conn)
                if key not in enabled and enable_estats(row):
                    enabled.add(key)
                path = read_estats(row, TCP_ESTATS_PATH, TCP_ESTATS_PATH_ROD_v0)
                data = read_estats(row, TCP_ESTATS_DATA, TCP_ESTATS_DATA_ROD_v0)
                if path is not None and path.SmoothedRtt:
                    rtt = path.SmoothedRtt
                if data is not None:
                    total_in, total_out = data.DataBytesIn, data.DataBytesOut
                    if key in previous:
                        prev_in, prev_out, prev_t = previous[key]
                        elapsed = max(now - prev_t, 1e-6)
                        rate_in = (total_in - prev_in) / elapsed
                        rate_out = (total_out - prev_out) / elapsed
                    previous[key] = (total_in, total_out, now)

            stats.append((server, rtt, rate_in, rate_out, total_in, total_out))
        clients.append((info, stats))

    for key in list(previous):
        if key not in seen:
            del previous[key]
    enabled &= seen
    return clients


class NameReader(threading.Thread):
    """Reads character names in the background; OCR is too slow for the UI thread."""

    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.pids = set()
        self.reads = {}  # pid -> deque of recent names read (misses not kept)
        self.names = {}  # pid -> name shown
        self.read_at = {}  # pid -> time.monotonic() of the last read

    def track(self, pids):
        with self.lock:
            self.pids = set(pids)
            for table in (self.reads, self.names, self.read_at):
                for pid in list(table):
                    if pid not in self.pids:
                        del table[pid]

    def name(self, pid):
        with self.lock:
            return self.names.get(pid)

    def _vote(self, pid, name):
        """Record a read and update the name shown. Call with the lock held."""
        reads = self.reads.setdefault(pid, deque(maxlen=NAME_HISTORY))
        reads.append(name)
        recent = list(reads)[-NAME_SWITCH_VOTES:]
        if len(recent) == NAME_SWITCH_VOTES and len(set(recent)) == 1:
            self.names[pid] = name
            return
        counts = Counter(reads)
        top = max(counts.values())
        if top < NAME_MIN_VOTES:
            return
        if counts.get(self.names.get(pid), 0) < top:
            # Ties go to the most recent reading.
            self.names[pid] = next(n for n in reversed(reads) if counts[n] == top)

    def _due(self, pid, now):
        interval = NAME_SLOW_SECONDS if pid in self.names else NAME_FAST_SECONDS
        return now - self.read_at.get(pid, 0.0) >= interval

    def run(self):
        while True:
            now = time.monotonic()
            with self.lock:
                pids = sorted(self.pids)
                due = [pid for pid in pids if self._due(pid, now)]
            windows = set()
            for pid in pids:
                try:
                    hwnd = capture.main_window(pid)
                    windows.add(hwnd)
                    name = (charname.read_character_name(capture.capture_frames(hwnd))
                            if pid in due else None)
                except Exception:  # a client closing mid-read must not stop the thread
                    name = None
                if pid in due:
                    with self.lock:
                        if pid in self.pids:
                            self.read_at[pid] = time.monotonic()
                            if name:
                                self._vote(pid, name)
            capture.keep_streams(windows)
            time.sleep(0.25)


def client_key(pid, create_time):
    """Identify one client process; PIDs alone are reused by Windows."""
    return f"{pid}:{create_time}"


def open_in_browser(url):
    """Open a link in the default browser, never elevated.

    Started from an elevated app, the browser would run as administrator
    too; Explorer hands the link to the user's normal desktop session.
    """
    try:
        subprocess.Popen(["explorer.exe", url])
    except OSError:
        webbrowser.open(url)


FONT_FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas"
ICON_FAMILY = "Segoe MDL2 Assets"  # Windows' icon font, on Windows 10 and 11
CONFIRM_SECONDS = 3
MESSAGE_SECONDS = 6

# (light, dark) colour pairs; CustomTkinter picks one to match the system theme.
TEXT = ("gray10", "gray90")
MUTED = ("gray40", "gray65")
GOOD = ("#1a7f37", "#3fb950")
WARN = ("#9a6700", "#d29922")
BAD = ("#cf222e", "#f85149")
DANGER = ("#cf222e", "#da3633")
DANGER_HOVER = ("#a40e26", "#b62324")
CONFIRM = ("#9a6700", "#bb8009")
CONFIRM_HOVER = ("#7d4e00", "#9e6a03")
PILL = ("gray85", "gray25")


def rtt_color(rtt):
    if rtt is None:
        return MUTED
    return GOOD if rtt < 80 else WARN if rtt < 150 else BAD


def format_connection(stats):
    """Return (rtt, texts) where texts fill the connection row left to right."""
    server, rtt, rate_in, rate_out, total_in, total_out = stats
    return rtt, (server,
                 "- ms" if rtt is None else f"{rtt} ms",
                 f"In {fmt_rate(rate_in)} KB/s",
                 f"Out {fmt_rate(rate_out)} KB/s",
                 f"{fmt_total(total_in)} / {fmt_total(total_out)} MB")


class ClientCard(ctk.CTkFrame):
    """One rounded card per client: identity, connections, disconnect button."""

    CONN_WIDTHS = (170, 70, 120, 120, 130)

    def __init__(self, master, app, pid):
        super().__init__(master, corner_radius=10)
        self.app = app
        self.pid = pid
        self.create_time = None
        self.confirm_job = None
        self.conn_rows = []
        self.grid_columnconfigure(1, weight=1)

        self.name = None  # name shown, read or typed
        self.entry = None  # text field while the name is being edited
        # Fixed width keeps the connection columns aligned across cards.
        # A fixed size, so editing the name (field plus two buttons) never
        # shifts the columns.
        self.name_box = ctk.CTkFrame(self, fg_color="transparent", width=164, height=28)
        self.name_box.grid_propagate(False)
        self.name_box.grid(row=0, column=0, sticky="w", padx=(16, 24), pady=(12, 0))
        self.title = ctk.CTkLabel(self.name_box, anchor="w", width=136, cursor="hand2",
                                  font=ctk.CTkFont(FONT_FAMILY, 15, "bold"))
        self.title.grid(row=0, column=0, sticky="w")
        self.title.bind("<Button-1>", lambda event: self.start_edit())
        # Windows' own "Edit" pencil from its built-in icon font.
        self.edit_button = self.icon_button("", MUTED, self.start_edit)
        self.edit_button.grid(row=0, column=1, padx=(2, 0))
        self.edit_controls = []  # text field and save/cancel buttons while editing
        self.details = ctk.CTkLabel(self, anchor="w", justify="left",
                                    text_color=MUTED,
                                    font=ctk.CTkFont(FONT_FAMILY, 12))
        self.details.grid(row=1, column=0, sticky="nw", padx=(16, 24), pady=(0, 12))

        self.conn_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.conn_frame.grid(row=0, column=1, rowspan=2, sticky="ew", pady=12)

        self.button = ctk.CTkButton(self, text="Disconnect", width=120,
                                    fg_color=DANGER, hover_color=DANGER_HOVER,
                                    font=ctk.CTkFont(FONT_FAMILY, 13, "bold"),
                                    command=self.on_click)
        self.button.grid(row=0, column=2, rowspan=2, padx=16, pady=12)

    def show(self, index, info, stats, admin, name, manual=False):
        self.create_time = info["create_time"]
        started = ("?" if info["create_time"] is None else
                   time.strftime("%H:%M:%S", time.localtime(info["create_time"])))
        ram = info["memory_info"].rss / (1024 * 1024) if info["memory_info"] else 0
        self.name = name
        if name:
            self.title.configure(text=name, text_color=TEXT)
        else:
            self.title.configure(text="Reading name…", text_color=MUTED)
        # On the shortest line, so the column keeps its width.
        source = "  ·  named by you" if manual else ""
        self.details.configure(
            text=f"Client {index}  ·  PID {info['pid']}\n"
                 f"Started {started}\n{ram:.0f} MB RAM{source}")
        self.show_connections(stats)

        if admin and stats:
            self.button.configure(state="normal")
            if self.confirm_job is None:
                self.button.configure(fg_color=DANGER)
        else:
            self.reset_confirm()
            self.button.configure(state="disabled", fg_color=PILL)

    def show_connections(self, stats):
        rows = [format_connection(s) for s in stats]
        wanted = max(len(rows), 1)
        while len(self.conn_rows) < wanted:
            self.conn_rows.append(self.make_conn_row(len(self.conn_rows)))
        while len(self.conn_rows) > wanted:
            for label in self.conn_rows.pop():
                label.destroy()

        if not rows:
            labels = self.conn_rows[0]
            labels[0].configure(text="No connection", text_color=MUTED)
            for label in labels[1:]:
                label.configure(text="")
            return
        for labels, (rtt, texts) in zip(self.conn_rows, rows):
            for label, text in zip(labels, texts):
                label.configure(text=text)
            labels[0].configure(text_color=TEXT)
            labels[1].configure(text_color=rtt_color(rtt))

    def make_conn_row(self, row):
        mono = ctk.CTkFont(MONO_FAMILY, 13)
        labels = []
        for col, width in enumerate(self.CONN_WIDTHS):
            label = ctk.CTkLabel(self.conn_frame, width=width, anchor="w",
                                 font=mono, text_color=MUTED)
            label.grid(row=row, column=col, sticky="w", padx=(0, 8))
            labels.append(label)
        return labels

    def icon_button(self, glyph, color, command):
        return ctk.CTkButton(self.name_box, text=glyph, width=24, height=24,
                             fg_color="transparent", hover_color=PILL, text_color=color,
                             font=ctk.CTkFont(ICON_FAMILY, 12), command=command)

    def start_edit(self):
        """Swap the name for a text field with save and cancel buttons.
        Enter and Esc work too; an empty name goes back to reading it
        automatically."""
        if self.entry is not None:
            return
        # Field plus two buttons take the same width as name plus pencil, so
        # the card's columns do not shift while editing.
        self.entry = ctk.CTkEntry(self.name_box, width=108, placeholder_text="Empty = automatic",
                                  font=ctk.CTkFont(FONT_FAMILY, 14))
        if self.name:
            self.entry.insert(0, self.name)
        save = self.icon_button("", GOOD, lambda: self.finish_edit(save=True))
        cancel = self.icon_button("", MUTED, lambda: self.finish_edit(save=False))
        self.title.grid_remove()
        self.edit_button.grid_remove()
        self.entry.grid(row=0, column=0, sticky="w")
        save.grid(row=0, column=1, padx=(2, 0))
        cancel.grid(row=0, column=2)
        self.edit_controls = [self.entry, save, cancel]
        self.entry.focus_set()
        self.entry.select_range(0, "end")
        self.entry.bind("<Return>", lambda event: self.finish_edit(save=True))
        self.entry.bind("<Escape>", lambda event: self.finish_edit(save=False))

    def finish_edit(self, save):
        if self.entry is None:
            return
        text = self.entry.get().strip()[:MANUAL_NAME_MAX]
        for widget in self.edit_controls:
            widget.destroy()
        self.entry, self.edit_controls = None, []
        self.title.grid()
        self.edit_button.grid()
        if save:
            self.app.set_manual_name(self.pid, self.create_time, text)

    def on_click(self):
        if self.confirm_job is None and self.app.confirm_disconnect.get():
            self.button.configure(text="Confirm?", fg_color=CONFIRM,
                                  hover_color=CONFIRM_HOVER)
            self.confirm_job = self.after(CONFIRM_SECONDS * 1000, self.reset_confirm)
            return
        self.reset_confirm()
        self.app.disconnect(self.pid, self.create_time)

    def reset_confirm(self):
        if self.confirm_job is not None:
            self.after_cancel(self.confirm_job)
            self.confirm_job = None
        self.button.configure(text="Disconnect", fg_color=DANGER,
                              hover_color=DANGER_HOVER)

    def destroy(self):
        # A pending confirm timeout would otherwise fire on a dead button.
        if self.confirm_job is not None:
            self.after_cancel(self.confirm_job)
            self.confirm_job = None
        super().destroy()


class MonitorApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.admin = is_admin()
        self.enabled = set()
        self.previous = {}
        self.cards = {}  # pid -> ClientCard
        self.message_job = None
        self.settings = load_settings()
        self.confirm_disconnect = ctk.BooleanVar(
            value=self.settings.get("confirm_disconnect", True))
        # Names typed by the user, keyed by client process; see client_key.
        self.manual_names = dict(self.settings.get("manual_names", {}))
        self.names = NameReader()
        self.names.start()

        self.title("M2 Monitor")
        if os.path.exists(ICON_PATH):
            self.iconbitmap(ICON_PATH)  # also stops CustomTkinter applying its own icon
        self.geometry("1060x540")
        self.minsize(900, 300)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 8))
        header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(header, text="M2 Monitor",
                     font=ctk.CTkFont(FONT_FAMILY, 22, "bold")).grid(
            row=0, column=0, sticky="w")
        self.summary = ctk.CTkLabel(header, text_color=MUTED,
                                    font=ctk.CTkFont(FONT_FAMILY, 13))
        self.summary.grid(row=1, column=0, columnspan=2, sticky="w")
        ctk.CTkButton(header, text="Feedback", width=90, fg_color="transparent",
                      border_width=1, border_color=PILL, text_color=TEXT,
                      hover_color=PILL, font=ctk.CTkFont(FONT_FAMILY, 13),
                      command=lambda: open_in_browser(FEEDBACK_URL)).grid(
            row=0, column=2, rowspan=2, sticky="e", padx=(16, 0))
        ctk.CTkCheckBox(header, text="Confirm before disconnect",
                        variable=self.confirm_disconnect,
                        command=self.save_confirm_setting,
                        font=ctk.CTkFont(FONT_FAMILY, 13)).grid(
            row=0, column=3, rowspan=2, sticky="e", padx=16)
        if self.admin:
            ctk.CTkLabel(header, text="  Administrator  ", fg_color=PILL,
                         corner_radius=12, text_color=GOOD,
                         font=ctk.CTkFont(FONT_FAMILY, 12, "bold")).grid(
                row=0, column=4, rowspan=2, sticky="e")
        else:
            ctk.CTkButton(header, text="Restart as administrator",
                          font=ctk.CTkFont(FONT_FAMILY, 13),
                          command=self.elevate).grid(
                row=0, column=4, rowspan=2, sticky="e")

        self.list = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list.grid(row=1, column=0, sticky="nsew", padx=12)
        self.list.grid_columnconfigure(0, weight=1)
        self.empty = ctk.CTkLabel(self.list, text="No M2 clients running.",
                                  text_color=MUTED,
                                  font=ctk.CTkFont(FONT_FAMILY, 14))

        self.status = ctk.CTkLabel(self, text="", anchor="w",
                                   font=ctk.CTkFont(FONT_FAMILY, 13))
        self.status.grid(row=2, column=0, sticky="ew", padx=20, pady=(4, 12))
        self.refresh()

    def refresh(self):
        # Reschedule even if an update fails, so one error can't freeze the window.
        try:
            self.update_cards()
        finally:
            self.after(int(REFRESH_SECONDS * 1000), self.refresh)

    def update_cards(self):
        clients = collect_clients(self.admin, self.enabled, self.previous)
        live = {info["pid"] for info, _ in clients}
        keys = {client_key(info["pid"], info["create_time"]) for info, _ in clients}
        # A typed name lasts as long as its client: drop those of closed clients.
        closed = [key for key in self.manual_names if key not in keys]
        for key in closed:
            del self.manual_names[key]
        if closed:
            self.save_manual_names()
        # Clients with a typed name need no reading.
        self.names.track({info["pid"] for info, _ in clients
                          if client_key(info["pid"], info["create_time"]) not in self.manual_names})
        for pid in list(self.cards):
            if pid not in live:
                self.cards.pop(pid).destroy()

        for index, (info, stats) in enumerate(clients, start=1):
            pid = info["pid"]
            if pid not in self.cards:
                self.cards[pid] = ClientCard(self.list, self, pid)
            card = self.cards[pid]
            manual = self.manual_names.get(client_key(pid, info["create_time"]))
            card.show(index, info, stats, self.admin, manual or self.names.name(pid),
                      manual=bool(manual))
            card.grid(row=index, column=0, sticky="ew", padx=8, pady=(0, 10))

        if clients:
            self.empty.grid_forget()
        else:
            self.empty.grid(row=0, column=0, pady=60)

        connected = sum(1 for _, stats in clients if stats)
        summary = f"{len(clients)} client(s), {connected} connected"
        if not self.admin:
            summary += "  ·  RTT, traffic and disconnect need administrator rights"
        self.summary.configure(text=summary)

    def disconnect(self, pid, create_time):
        # The PID may have been reused since the card was drawn; only touch
        # the same client process the user clicked on.
        try:
            proc = psutil.Process(pid)
            same = (proc.name().lower() == PROCESS_NAME
                    and proc.create_time() == create_time)
        except psutil.Error:
            same = False
        if not same:
            self.show_message(f"PID {pid}: client has exited.", WARN)
            return
        conns = established_conns(proc)
        errors = [err for err in map(close_connection, conns) if err]
        if not conns:
            self.show_message(f"PID {pid}: no connection to close.", WARN)
        elif errors:
            self.show_message(f"PID {pid}: failed to close {len(errors)} of "
                              f"{len(conns)} connection(s) (error {errors[0]}).", BAD)
        else:
            self.show_message(f"PID {pid}: closed {len(conns)} connection(s).", GOOD)

    def show_message(self, text, color):
        if self.message_job is not None:
            self.after_cancel(self.message_job)
        self.status.configure(text=text, text_color=color)
        self.message_job = self.after(
            MESSAGE_SECONDS * 1000, lambda: self.status.configure(text=""))

    def save_confirm_setting(self):
        self.settings["confirm_disconnect"] = self.confirm_disconnect.get()
        save_settings(self.settings)

    def set_manual_name(self, pid, create_time, name):
        """Show `name` for this client until it closes; empty reads it again."""
        key = client_key(pid, create_time)
        if name:
            self.manual_names[key] = name
        elif self.manual_names.pop(key, None) is None:
            return
        self.save_manual_names()
        self.update_cards()

    def save_manual_names(self):
        self.settings["manual_names"] = dict(self.manual_names)
        save_settings(self.settings)

    def elevate(self):
        if restart_as_admin():
            self.destroy()
        else:
            self.show_message("Elevation was cancelled.", WARN)


def main():
    # Never compete with the game clients for CPU: under heavy load the game
    # (and its anti-cheat) can crash.
    psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    # Own taskbar identity, so the taskbar shows this app's icon, not Python's.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")
    MonitorApp().mainloop()


if __name__ == "__main__":
    main()
