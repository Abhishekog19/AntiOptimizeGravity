# Antigravity Quota Tracker

> Automatically track Claude, GPT, and Gemini quota usage across multiple Antigravity IDE
> accounts — with a system tray icon, local dashboard, and zero Node.js required.

---

## Install — Windows

> **Requirements:** Windows 10/11 64-bit. Antigravity IDE must be installed first.
> No Python, Node.js, or terminal ever required.

1. **[Download AntigravityQuotaTrackerSetup.exe](https://github.com/Abhishekog19/AntiOptimizeGravity/releases/latest)** from the Releases page
2. Run it, click **Next** a few times
3. Open Antigravity IDE — the tracker starts automatically within a few seconds

Look for a **coloured dot** in your system tray (`^` button near the clock).  
Right-click it to open the dashboard or trigger a manual capture.

**Time from download to working tray icon: under 2 minutes on a clean machine.**

---

## What problem this solves

Antigravity IDE shows your remaining AI quota (weekly and 5-hour limits) in
**Settings → Models**, but only while the app is open and you're looking at
that exact screen. There's no background sync, no API endpoint, and no history.

This tool watches Antigravity in the background via Chrome DevTools Protocol
(CDP), captures quota readings automatically at the right moments, stores them
in a local SQLite database keyed by account email, and displays current status
in a system tray icon. All data stays on your machine.

---

## Install — macOS

> **Current Mac support status:** The setup script and core CDP watcher code
> are written for Mac, but have not been tested on a real Mac machine yet.
> Treat Mac support as **experimental**.

```bash
git clone https://github.com/Abhishekog19/AntiOptimizeGravity
cd AntiOptimizeGravity
pip install -r notifier/requirements.txt
bash scripts/setup-mac.sh
```

Then launch Antigravity via the created wrapper:

```bash
antigravity-debug
```

And start the tracker:

```bash
python main.py
```

---

## How it works

Antigravity IDE is an Electron app, so its UI is a web page accessible via
Chrome DevTools Protocol (CDP). This tracker:

1. **Starts Antigravity with** `--remote-debugging-port=9222` (via the patched
   shortcut — done automatically by the installer) — this exposes a local HTTP endpoint for CDP.
2. **Maintains a persistent CDP connection** to the main workbench window with
   the Network domain enabled. No DOM polling — the tracker listens passively
   for `Network.requestWillBeSent` events.
3. **On a `GetTurnDiff` network request**, fires a capture: navigates the
   Settings panel to the Models page via CDP, clicks Refresh, waits 3 seconds
   for the server response, then reads the quota numbers from the DOM text.
4. **Stores** the reading in a local SQLite database (`dashboard/data/quota.db`).
5. **Updates** the tray icon colour and the web dashboard.

**Why CDP instead of OCR or screen scraping?**
CDP gives us direct access to the actual DOM text — deterministic, fast, and
doesn't break when the UI is offscreen or minimized.

**Why GetTurnDiff?**
Every completed agent response in Antigravity IDE triggers exactly two rapid
`GetTurnDiff` network requests to the backend. Listening for this event fires
at the exact moment quota is consumed — no polling, no DOM text scans.

---

## Two capture triggers

| # | Trigger | When | Action |
|---|---------|------|---------|
| 1 | **launch** | Antigravity process appears | Navigate to Settings › Models, Refresh, read |
| 2 | **GetTurnDiff** | Agent response completes | Navigate to Settings › Models, Refresh, read |

A **manual capture** is always available via right-click → Capture Now in the tray menu.

---

## System tray icon

| Colour | Meaning |
|--------|---------|
| 🟢 Green | All accounts > 30% weekly remaining |
| 🟡 Amber | At least one account ≤ 30% weekly |
| 🔴 Red | All accounts ≤ 10% weekly (or no data yet) |

**Left-click** → opens the dashboard  
**Right-click** → menu: Open Dashboard | Capture Now | Run Diagnostics | Quit

---

## Remote access from another device

### Tailscale (recommended — free, 2-minute setup)

1. Install [Tailscale](https://tailscale.com/) on both devices
2. Sign in (Google / Microsoft / GitHub)
3. Access the dashboard from your phone at `http://<your-pc-tailscale-ip>:4300`

No port-forwarding, no dynamic DNS, no deployment required.

---

## Configuration

Copy `notifier/config.example.env` to `notifier/.env` and edit values:

| Key | Default | Description |
|-----|---------|-------------|
| `CDP_PORT` | `9222` | Chrome DevTools Protocol port |
| `POLL_INTERVAL_SECONDS` | `2` | How often to check Antigravity process state |
| `DEBOUNCE_SECONDS` | `2` | Min seconds between captures |
| `DASHBOARD_URL` | `http://localhost:4300` | Dashboard URL |
| `DASHBOARD_API_KEY` | *(empty)* | Optional API key for remote access |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARN` / `ERROR` |

---

## Known Limitations

### Data only exists when Settings › Models was rendered

Antigravity only fetches quota from its servers when the Settings › Models
panel is rendered. This tracker reads that data — it cannot retroactively
recover quota data for time periods when Settings › Models was never displayed.

### Mac support is experimental

The code is written to run on Mac, but has only been tested on Windows.

### Breaks if Antigravity changes its Settings UI text

Every string the parser looks for is hardcoded against the current Antigravity
version. A future update could rename or restructure these and silently break capture.

**What to do:** right-click the tray icon → **Run Diagnostics**. Paste the report into a GitHub issue.

---

## Troubleshooting

**Tray icon shows red / "no data" even after Antigravity is open**
- Right-click tray → Run Diagnostics — the report shows whether CDP is reachable.
- If the report says "not_open": the debug flag isn't being applied. Re-run the installer.

**"Capture failed: could not open Settings › Models"**
- Open Settings → Models manually once per session, then click "Capture Now".

**Port 9222 conflict**
- Change `CDP_PORT` in `notifier/.env` and reinstall to update the shortcut.

---

## Uninstalling

**Via installer:** use **Add/Remove Programs** → "Antigravity Quota Tracker".
The uninstaller kills running processes, removes the startup entry, reverts shortcut patches,
and optionally (prompt, default: No) deletes your quota history.

---

## Repository structure

```
AntiOptimizeGravity/
├── main.py                    # Entry point — tracker, tray icon, Flask server
├── watchdog.py                # Auto-launcher: watches for Antigravity, launches tracker
├── build.py                   # PyInstaller packaging — builds tracker + watchdog exe
├── state.py                   # Shared app state (thread-safe singleton)
├── server/                    # Flask API server + SQLite queries
├── tray/                      # pystray tray icon + diagnostic mode
├── notifier/                  # CDP watcher (2 triggers: launch + GetTurnDiff)
│   └── requirements.txt       # Python dependencies
├── dashboard/
│   └── public/                # Web dashboard (HTML/CSS/JS)
├── installer/
│   └── setup.iss              # Inno Setup 6 installer script
├── .github/workflows/
│   └── release.yml            # GitHub Actions: build + release on v* tag push
├── scripts/
│   ├── setup-windows.ps1      # Manual shortcut patcher (for run-from-source users)
│   ├── uninstall-windows.ps1  # Manual uninstall script
│   └── setup-mac.sh / uninstall-mac.sh
├── assets/
│   └── icon.ico               # App icon (auto-generated by build.py via Pillow)
└── README.md
```

---

## For Developers — Run from Source

**Prerequisites:** Python 3.10+, git

```bash
git clone https://github.com/Abhishekog19/AntiOptimizeGravity
cd AntiOptimizeGravity
pip install -r notifier/requirements.txt
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
python main.py
```

Dashboard at **http://localhost:4300**. Right-click the tray icon for the menu.

### Building the installer

```bash
pip install pyinstaller
python build.py
# Produces: dist/quota-tracker.exe + dist/quota-watchdog.exe

# Then compile the installer (requires Inno Setup 6):
# Download: https://jrsoftware.org/isdl.php
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\setup.iss
# Produces: installer/AntigravityQuotaTrackerSetup.exe
```

### Automated releases (GitHub Actions)

Tag a commit and push — the workflow handles everything:

```bash
git tag v1.0.0
git push --tags
```

GitHub Actions will build both exes, compile the installer, create a GitHub Release,
and attach `AntigravityQuotaTrackerSetup.exe` as the downloadable asset.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT — see [LICENSE](LICENSE).
