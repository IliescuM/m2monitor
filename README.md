# m2monitor

A small Windows desktop app that keeps an eye on every M2 game client you
have open. For each running client it shows:

- the **character name**, read from the game window with Windows' built-in OCR
- the **server** the client is connected to
- **latency (RTT)** and **traffic** in and out
- process ID, start time and memory use
- a **Disconnect** button that drops that client's connection to the server

It does not inject into, read the memory of, or modify the game, and it never
sends packets to the game server. Disconnect works like "Close Connection" in
Sysinternals TCPView: Windows resets the client's TCP connection locally.

## Requirements

- Windows 10 (version 2004 or newer) or Windows 11. Developed on Windows 11.
- Python 3.10 or newer. Developed with Python 3.14.
- An OCR-capable language installed in Windows (English is enough). Check under
  Settings > Time & language > Language & region.

## Install and run

```
git clone <this repository>
cd m2monitor
python -m pip install -r requirements.txt
python m2monitor.py
```

### Administrator rights

Without admin rights the app shows clients, servers and character names.
Latency, traffic and Disconnect need admin rights, because Windows only allows
elevated programs to turn on per-connection statistics and to reset
connections. Click **Restart as administrator** in the window, or start the
app from an elevated terminal.

## How it works

| Feature | Source |
|---|---|
| Client list, server, RAM | [`psutil`](https://github.com/giampaolo/psutil) |
| Latency and traffic | Windows TCP extended statistics (`GetPerTcpConnectionEStats`) |
| Disconnect | `SetTcpEntry` with `MIB_TCP_STATE_DELETE_TCB` |
| Screenshots | Windows Graphics Capture via [`windows-capture`](https://github.com/NiiightmareXD/windows-capture) |
| Character name | `Windows.Media.Ocr` via [`winrt`](https://github.com/pywinrt/pywinrt) |

**Character names.** The camera keeps your own character in the middle of the
screen, so your name is the text centred horizontally just above it. A
background thread screenshots each client every few seconds and reads that
spot. One screenshot can be misread when other nameplates, monsters or damage
numbers overlap yours, so a name is only shown once the same reading appears
repeatedly. In a busy fight this can take 30 to 60 seconds; the card shows
"Reading name…" until then. Screenshots stay in memory and are never saved or
sent anywhere.

**Disconnect.** By default the button asks for a second click within 3 seconds.
Untick **Confirm before disconnect** in the header to disconnect on one click.
The choice is saved in `%APPDATA%\m2monitor\settings.json`.

## Disclaimer

This is an unofficial fan project, not affiliated with or endorsed by the
publishers of M2. Check your game's terms of service before using
third-party tools. Use at your own risk.

## License

[MIT](LICENSE)
