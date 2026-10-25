"""Monitor for running M2 game clients.

Shows every client process (PROCESS_NAME) as a card with its character name,
server connections, latency and traffic. The name is read with OCR from
screenshots of the client in a background thread. Latency and traffic come
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
from collections import Counter, deque
from ctypes import wintypes

import customtkinter as ctk
import psutil

import capture
import charname

PROCESS_NAME = "metin2client.exe"
REFRESH_SECONDS = 1.0
NAME_READ_SECONDS = 2.0
# A name is shown once it is the most common of the last NAME_HISTORY reads
# and was read at least NAME_MIN_VOTES times, or right away once it is read
# NAME_SWITCH_VOTES times in a row; single OCR misreads vary, so they rarely
# repeat, while the real name does.
NAME_HISTORY = 15
NAME_MIN_VOTES = 2
NAME_SWITCH_VOTES = 3
SETTINGS_PATH = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                             "m2monitor", "settings.json")
# A built .exe unpacks bundled files under sys._MEIPASS.
ICON_PATH = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))),
                         "assets", "icon.ico")
APP_ID = "IliescuM.M2Monitor"

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
        self.reads = {}  # pid -> deque of recent reads, None for "no name seen"

    def track(self, pids):
        with self.lock:
            self.pids = set(pids)
            for pid in list(self.reads):
                if pid not in self.pids:
                    del self.reads[pid]

    def name(self, pid):
        with self.lock:
            reads = [n for n in self.reads.get(pid, ()) if n]
        # The same name several reads in a row wins at once, so a relog to
        # another character shows without out-voting the old name first.
        recent = reads[-NAME_SWITCH_VOTES:]
        if len(recent) == NAME_SWITCH_VOTES and len(set(recent)) == 1:
            return recent[0]
        votes = Counter(reads)
        if not votes:
            return None
        name, count = votes.most_common(1)[0]
        return name if count >= NAME_MIN_VOTES else None

    def run(self):
        while True:
            with self.lock:
                pids = sorted(self.pids)
            for pid in pids:
                try:
                    screenshot = capture.capture_window(capture.main_window(pid))
                    name = charname.read_character_name(screenshot)
                except Exception:  # a client closing mid-read must not stop the thread
                    name = None
                with self.lock:
                    if pid in self.pids:
                        self.reads.setdefault(pid, deque(maxlen=NAME_HISTORY)).append(name)
            time.sleep(NAME_READ_SECONDS)


FONT_FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas"
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

        # Fixed width keeps the connection columns aligned across cards.
        self.title = ctk.CTkLabel(self, anchor="w", width=160,
                                  font=ctk.CTkFont(FONT_FAMILY, 15, "bold"))
        self.title.grid(row=0, column=0, sticky="w", padx=(16, 24), pady=(12, 0))
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

    def show(self, index, info, stats, admin, name):
        self.create_time = info["create_time"]
        started = ("?" if info["create_time"] is None else
                   time.strftime("%H:%M:%S", time.localtime(info["create_time"])))
        ram = info["memory_info"].rss / (1024 * 1024) if info["memory_info"] else 0
        if name:
            self.title.configure(text=name, text_color=TEXT)
        else:
            self.title.configure(text="Reading name…", text_color=MUTED)
        self.details.configure(
            text=f"Client {index}  ·  PID {info['pid']}\n"
                 f"Started {started}\n{ram:.0f} MB RAM")
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
        ctk.CTkCheckBox(header, text="Confirm before disconnect",
                        variable=self.confirm_disconnect,
                        command=self.save_confirm_setting,
                        font=ctk.CTkFont(FONT_FAMILY, 13)).grid(
            row=0, column=1, rowspan=2, sticky="e", padx=16)
        if self.admin:
            ctk.CTkLabel(header, text="  Administrator  ", fg_color=PILL,
                         corner_radius=12, text_color=GOOD,
                         font=ctk.CTkFont(FONT_FAMILY, 12, "bold")).grid(
                row=0, column=2, rowspan=2, sticky="e")
        else:
            ctk.CTkButton(header, text="Restart as administrator",
                          font=ctk.CTkFont(FONT_FAMILY, 13),
                          command=self.elevate).grid(
                row=0, column=2, rowspan=2, sticky="e")

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
        self.names.track(live)
        for pid in list(self.cards):
            if pid not in live:
                self.cards.pop(pid).destroy()

        for index, (info, stats) in enumerate(clients, start=1):
            pid = info["pid"]
            if pid not in self.cards:
                self.cards[pid] = ClientCard(self.list, self, pid)
            card = self.cards[pid]
            card.show(index, info, stats, self.admin, self.names.name(pid))
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

    def elevate(self):
        if restart_as_admin():
            self.destroy()
        else:
            self.show_message("Elevation was cancelled.", WARN)


def main():
    # Own taskbar identity, so the taskbar shows this app's icon, not Python's.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")
    MonitorApp().mainloop()


if __name__ == "__main__":
    main()
