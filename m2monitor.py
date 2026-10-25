"""Read-only monitor for running M2 clients.

Lists every M2 client process with its server connections, latency
and traffic. Latency and traffic come from Windows TCP extended statistics
(EStats), which only an elevated prompt can enable; without admin rights
those columns show "-". Nothing here sends, blocks or alters any packets.
"""
import ctypes
import os
import socket
import struct
import sys
import time
from ctypes import wintypes

import psutil

PROCESS_NAME = "metin2client.exe"
REFRESH_SECONDS = 1.0

MIB_TCP_STATE_ESTAB = 5
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


def read_estats(row, estats_type, rod_cls):
    rod = rod_cls()
    err = _GetEStats(ctypes.byref(row), estats_type,
                     None, 0, 0, None, 0, 0,
                     ctypes.byref(rod), 0, ctypes.sizeof(rod))
    return rod if err == 0 else None


def find_clients():
    clients = []
    for proc in psutil.process_iter(["pid", "name", "create_time", "memory_info"]):
        if (proc.info["name"] or "").lower() != PROCESS_NAME:
            continue
        try:
            conns = [c for c in proc.net_connections(kind="tcp4")
                     if c.status == psutil.CONN_ESTABLISHED and c.raddr]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            conns = []
        clients.append((proc.info, conns))
    clients.sort(key=lambda c: c[0]["create_time"])
    return clients


def fmt_rate(value):
    return "-" if value is None else f"{value / 1024:.1f}"


def fmt_total(value):
    return "-" if value is None else f"{value / (1024 * 1024):.2f}"


def main():
    os.system("")  # enables ANSI escape handling in the classic console
    admin = is_admin()
    enabled = set()
    previous = {}

    while True:
        now = time.monotonic()
        lines = []
        header = (f"{'#':>2}  {'PID':>6}  {'Started':>8}  {'RAM MB':>7}  "
                  f"{'Server':<21}  {'RTT ms':>6}  {'In KB/s':>7}  "
                  f"{'Out KB/s':>8}  {'In MB':>7}  {'Out MB':>7}")
        lines.append(header)
        lines.append("-" * len(header))

        clients = find_clients()
        seen = set()
        for index, (info, conns) in enumerate(clients, start=1):
            started = time.strftime("%H:%M:%S", time.localtime(info["create_time"]))
            ram = info["memory_info"].rss / (1024 * 1024) if info["memory_info"] else 0
            prefix = f"{index:>2}  {info['pid']:>6}  {started:>8}  {ram:>7.0f}  "

            if not conns:
                lines.append(prefix + f"{'(no connection)':<21}")
                continue

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

                lines.append(
                    prefix
                    + f"{server:<21}  {('-' if rtt is None else rtt):>6}  "
                    f"{fmt_rate(rate_in):>7}  {fmt_rate(rate_out):>8}  "
                    f"{fmt_total(total_in):>7}  {fmt_total(total_out):>7}"
                )
                prefix = " " * len(prefix)

        for key in list(previous):
            if key not in seen:
                del previous[key]
        enabled &= seen

        lines.append("")
        lines.append(f"{len(clients)} client(s). "
                     + ("" if admin else "Run as administrator for RTT and traffic. ")
                     + "Ctrl+C to quit.")
        sys.stdout.write("\x1b[2J\x1b[H" + "\n".join(lines) + "\n")
        sys.stdout.flush()
        time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
