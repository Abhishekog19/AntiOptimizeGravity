# Antigravity Quota Tracker

> **Automatically track Claude, GPT, and Gemini quota usage across multiple Antigravity IDE accounts — with a local web dashboard, live notifications, and zero cloud dependencies.**

![Dashboard preview](docs/dashboard-preview.png)

---

## What it does

Antigravity IDE shows your remaining AI quota (weekly and 5-hour limits) in **Settings → Models**, but only while the app is open and you're looking at that screen. This tool:

1. **Watches** Antigravity in the background via Chrome DevTools Protocol (CDP)
2. **Captures** quota readings automatically at the right moments (five triggers — see below)
3. **Stores** readings in a local SQLite database, keyed by account email
4. **Displays** history, burn-rate trends, and a reset countdown in a local web dashboard

No data ever leaves your machine.

---

## Architecture

```
┌─────────────────────┐   CDP WebSocket   ┌──────────────────────┐
│  Antigravity IDE    │ ◄────────────────► │  notifier/notifier.py │
│  (--remote-debug)   │                   │  persistent session   │
└─────────────────────┘                   │  5 triggers           │
                                          │  heartbeat every 15s  │
                                          └──────────┬───────────┘
                                                     │ POST /api/readings
                                                     │ POST /api/heartbeat
                                          ┌──────────▼───────────┐
                                          │  dashboard/server.js  │
                                          │  Express + SQLite     │
                                          └──────────┬───────────┘
                                                     │
                                          ┌──────────▼───────────┐
                                          │  localhost:4300        │
                                          │  Web dashboard        │
                                          └──────────────────────┘
```

---

## Quick start

### Prerequisites
- Python 3.8+
- Node.js 18+
- Antigravity IDE installed

### 1. Clone and install

```bash
git clone https://github.com/yourname/antigravity-quota-tracker
cd antigravity-quota-tracker
```

Install notifier dependencies:
```bash
pip install -r notifier/requirements.txt
```

Install dashboard dependencies:
```bash
cd dashboard && npm install
```

### 2. Patch Antigravity shortcut (Windows)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
```

Or manually: right-click the Antigravity shortcut → Properties → Target, and append:
```
--remote-debugging-port=9222
```

**macOS**: run `bash scripts/setup-mac.sh` or see the Automator instructions printed by that script.

### 3. Configure (optional)

```bash
cp notifier/config.example.env notifier/.env
# Edit notifier/.env to change port, debounce, etc.
```

### 4. Start the dashboard

```bash
cd dashboard && node server.js
# Open http://localhost:4300
```

### 5. Start the notifier

```bash
python notifier/notifier.py
```

Launch Antigravity via the patched shortcut. The notifier detects it and captures automatically.

---

## Five capture triggers

| # | Trigger | When | Refresh before read? |
|---|---------|------|----------------------|
| 1 | **launch** | Antigravity process appears | Yes (3 s wait) |
| 2 | **profile_menu** | Profile dropdown opens (Sign Out button visible) | Yes (3 s wait) |
| 3 | **sign_out_dialog** | Sign-out confirmation dialog appears | Yes (3 s wait) |
| 4 | **manual_refresh** | User clicks Refresh in Settings → Models | **No** (data already fresh) |
| 5 | **post_close** | Antigravity process exits | Yes (3 s wait, after relaunch) |

Trigger 4 is the only one that skips the Refresh step because the user just clicked it themselves — the data is already the most current available.

---

## Configuration

All settings live in `notifier/.env` (copy from `notifier/config.example.env`):

| Key | Default | Description |
|-----|---------|-------------|
| `CDP_PORT` | `9222` | Chrome DevTools Protocol port |
| `POLL_INTERVAL_SECONDS` | `2` | How often to check for triggers |
| `DEBOUNCE_SECONDS` | `30` | Min seconds between captures |
| `DASHBOARD_URL` | `http://localhost:4300` | Dashboard server URL |
| `DASHBOARD_API_KEY` | *(empty)* | Optional API key |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARN` / `ERROR` |

---

## Dashboard status dot

The header shows a coloured dot reflecting notifier connectivity:

| Colour | Meaning |
|--------|---------|
| 🟢 Green (pulsing) | Notifier is live — heartbeat received < 30 s ago |
| 🟡 Amber | Stale — last heartbeat 30–120 s ago |
| 🔴 Red | Offline — no heartbeat in 2+ minutes |

---

## Known Limitations

### "Can't get data that was never rendered"

Antigravity only fetches quota from its servers **when the Settings → Models panel is rendered**. There is no background sync and no API endpoint this tool can query directly. The data this tool captures is whatever Antigravity chose to display.

**Implication**: if a user closes Antigravity without any trigger firing, the last reading in the database reflects the state at the time of the *previous* capture, not the moment of closure.

### How trigger #5 (post_close) mitigates this

When the notifier detects that Antigravity has exited:
1. It **relaunches Antigravity** in the background with the CDP flag
2. Navigates to Settings → Models, clicks Refresh, waits 3 seconds
3. Reads the fresh quota data
4. **Terminates** the relaunched instance

This gives you accurate final-session data at the cost of a brief relaunch (typically 5–15 seconds). The Settings panel may flash briefly on screen during this process.

### Settings panel flash on launch/post_close triggers

Triggers 1 (launch) and 5 (post_close) open the Settings panel programmatically via CDP. The notifier attempts to minimise the window using `Browser.setWindowBounds`, but this only works if Antigravity's Electron build exposes the `Browser` CDP domain. If it doesn't, the Settings panel will be **briefly visible** (approximately 5–10 seconds) before being dismissed. This is expected behaviour and does not affect data accuracy.

### psutil required for launch and post_close triggers

Without `psutil`, triggers 1 and 5 are silently disabled. The other three triggers (profile_menu, sign_out_dialog, manual_refresh) continue to work. Install with:
```bash
pip install psutil
```

---

## Repository structure

```
antigravity-quota-tracker/
├── notifier/
│   ├── notifier.py          # Main notifier (CDP, triggers, heartbeat)
│   ├── config.example.env   # Configuration template
│   └── requirements.txt     # Python dependencies
├── dashboard/
│   ├── server.js            # Express API + SQLite
│   ├── db.js                # Database schema and queries
│   ├── public/
│   │   ├── index.html       # Dashboard UI
│   │   ├── app.js           # Frontend logic
│   │   └── style.css        # Styles
│   └── package.json
├── scripts/
│   ├── setup-windows.ps1    # Patches Antigravity shortcuts (Windows)
│   └── setup-mac.sh         # Creates wrapper script (macOS)
├── README.md
├── SETUP.md                 # Detailed installation guide
└── LICENSE                  # MIT
```

---

## License

MIT — see [LICENSE](LICENSE).
