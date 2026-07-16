# Antigravity Quota Tracker

> **Automatically track Claude, GPT, and Gemini quota usage across multiple Antigravity IDE accounts — with a system tray icon, local dashboard, and zero Node.js required.**

---

## What it does

Antigravity IDE shows your remaining AI quota (weekly and 5-hour limits) in **Settings → Models**, but only while the app is open and you're looking at that screen. This tool:

1. **Watches** Antigravity in the background via Chrome DevTools Protocol (CDP)
2. **Captures** quota readings automatically at the right moments (five triggers — see below)
3. **Stores** readings in a local SQLite database, keyed by account email
4. **Displays** status in a system tray icon + popup, and history/trends in a local web dashboard

No data ever leaves your machine.

---

## Quick start (recommended)

### Option A — Single executable (simplest)

1. Download `AntigravityQuotaTracker.exe` from the [Releases page](https://github.com/yourname/antigravity-quota-tracker/releases)
2. Double-click it — a tray icon appears immediately
3. Open Antigravity; the tool captures quota automatically

That's it. No Python, no Node.js, no terminals.

### Option B — Run from source

**Prerequisites:** Python 3.8+, Antigravity IDE

```bash
git clone https://github.com/yourname/antigravity-quota-tracker
cd antigravity-quota-tracker

# Install dependencies
pip install -r notifier/requirements.txt

# Patch Antigravity shortcut (Windows)
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1

# Start everything with one command
python main.py
```

The tray icon appears. Right-click for the menu, left-click for the status popup.
Dashboard is at **http://localhost:4300**.

---

## Architecture

```
┌─────────────────────┐   CDP WebSocket   ┌────────────────────────────┐
│  Antigravity IDE    │ ◄────────────────► │  main.py (single process)  │
│  (--remote-debug)   │                   │                            │
└─────────────────────┘                   │  Thread 1: Flask server    │
                                          │  Thread 2: CDP watcher     │
                                          │  Main:    pystray tray     │
                                          │  Tk thread: popup window   │
                                          └────────────┬───────────────┘
                                                       │
                                          ┌────────────▼───────────────┐
                                          │  http://localhost:4300      │
                                          │  Web dashboard (unchanged)  │
                                          └────────────────────────────┘
```

---

## Five capture triggers

| # | Trigger | When | Refresh before read? |
|---|---------|------|----------------------|
| 1 | **launch** | Antigravity process appears | ✅ Yes (3 s wait) |
| 2 | **profile_menu** | Profile dropdown opens (Sign Out button visible) | ✅ Yes (3 s wait) |
| 3 | **sign_out_dialog** | Sign-out confirmation dialog appears | ✅ Yes (3 s wait) |
| 4 | **manual_refresh** | User clicks Refresh in Settings → Models | ❌ No (data already fresh) |
| 5 | **post_close** | Antigravity process exits | ✅ Yes (3 s wait, after relaunch) |
| 6 | **manual_tray** | Tray popup "Capture Now" button | ✅ Yes (3 s wait) |

Trigger 4 is the only one that skips the Refresh step because the user just clicked it — the data is already the most current available.

---

## System tray icon

| Colour | Meaning |
|--------|---------|
| 🟢 Green | All accounts have > 30% weekly remaining |
| 🟡 Amber | At least one account ≤ 30% weekly |
| 🔴 Red   | All accounts ≤ 10% weekly (or no accounts yet) |

**Left-click** → opens the status popup (account cards + activity log + Capture Now button)  
**Right-click** → menu: Open Dashboard, Capture Now, Quit

---

## Remote access from another device

### Tailscale (recommended — free, 2 min setup)

1. Install [Tailscale](https://tailscale.com/) on both devices
2. Sign in (Google / Microsoft / GitHub)
3. Access the dashboard from your phone at `http://<your-pc-tailscale-ip>:4300`

No port-forwarding, no dynamic DNS, no deployment required.

---

## Configuration

All settings in `notifier/.env` (copy from `notifier/config.example.env`):

| Key | Default | Description |
|-----|---------|-------------|
| `CDP_PORT` | `9222` | Chrome DevTools Protocol port |
| `POLL_INTERVAL_SECONDS` | `2` | How often to check for triggers |
| `DEBOUNCE_SECONDS` | `30` | Min seconds between captures |
| `DASHBOARD_URL` | `http://localhost:4300` | Dashboard URL (points to embedded Flask) |
| `DASHBOARD_API_KEY` | *(empty)* | Optional API key for remote access |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARN` / `ERROR` |
| `TESSERACT_PATH` | `tesseract` | Path to Tesseract binary (for OCR captures) |

---

## Building the executable

```bash
pip install pyinstaller
python build.py
# → dist/AntigravityQuotaTracker.exe
```

---

## Known Limitations

### "Can't get data that was never rendered"

Antigravity only fetches quota from its servers **when the Settings → Models panel is rendered**. There is no background sync and no API endpoint this tool can query directly.

### Settings panel flash on launch/post_close triggers

Triggers 1 (launch) and 5 (post_close) open the Settings panel programmatically via CDP. The notifier attempts to minimise the window using `Browser.setWindowBounds`, but this only works if Antigravity's Electron build exposes the `Browser` CDP domain. If it doesn't, the Settings panel will be **briefly visible** before being dismissed.

### psutil required for launch and post_close triggers

Without `psutil`, triggers 1 and 5 are silently disabled. Install with:
```bash
pip install psutil
```

---

## Repository structure

```
antigravity-quota-tracker/
├── main.py                    # Single entry point
├── build.py                   # PyInstaller packaging
├── state.py                   # Shared app state (thread-safe singleton)
├── server/
│   ├── flask_app.py           # Flask server (replaces dashboard/server.js)
│   ├── db.py                  # SQLite queries (replaces dashboard/db.js)
│   └── ocr.py                 # OCR processing (replaces dashboard/ocr.js)
├── tray/
│   ├── tray_icon.py           # pystray system tray icon
│   └── popup.py               # tkinter status popup
├── notifier/
│   ├── notifier.py            # CDP watcher (5 triggers, heartbeat)
│   ├── config.example.env     # Configuration template
│   └── requirements.txt       # Python dependencies
├── dashboard/
│   └── public/                # Web dashboard (HTML/CSS/JS — unchanged)
│   └── data/                  # SQLite database (quota.db)
├── scripts/
│   └── setup-windows.ps1      # Patches Antigravity shortcuts (optional)
├── README.md
└── LICENSE
```

---

## Advanced: Self-host on a VPS

If you want multi-device access without Tailscale:

1. Run `python main.py` on a Linux VPS
2. Set `DASHBOARD_API_KEY=<a-strong-secret>` in `notifier/.env`
3. Reverse-proxy port 4300 via nginx/Caddy with HTTPS
4. Set the same `DASHBOARD_API_KEY` in your browser (the dashboard reads it from localStorage)

Tailscale is simpler and recommended for personal use.

---

## License

MIT — see [LICENSE](LICENSE).
