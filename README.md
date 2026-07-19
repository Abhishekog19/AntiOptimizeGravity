# Antigravity Quota Tracker

> Automatically track Claude, GPT, and Gemini quota usage across multiple Antigravity IDE
> accounts — with a system tray icon, local dashboard, and zero Node.js required.

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

## Install — Windows

### Option A: Single executable (no Python required)

1. Download `AntigravityQuotaTracker.exe` from the [Releases page](https://github.com/yourname/antigravity-quota-tracker/releases)
2. Double-click — a tray icon appears in the system tray immediately
3. Run the setup script once to patch the Antigravity shortcut:
   ```
   powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
   ```
4. Launch Antigravity via the patched shortcut — the tracker connects automatically

**Time from download to working tray icon:** under 30 seconds on a clean machine.

### Option B: Run from source

**Prerequisites:** Python 3.8+

```bash
git clone https://github.com/yourname/antigravity-quota-tracker
cd antigravity-quota-tracker
pip install -r notifier/requirements.txt
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
python main.py
```

Dashboard at **http://localhost:4300**. Right-click the tray icon for the menu.

---

## Install — Mac

> **Current Mac support status:** The setup script and core CDP watcher code
> are written for Mac, but have not been tested on a real Mac machine yet.
> Treat Mac support as **experimental**. See [Known Limitations](#known-limitations).

```bash
git clone https://github.com/yourname/antigravity-quota-tracker
cd antigravity-quota-tracker
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
   shortcut) — this exposes a local HTTP endpoint for CDP.
2. **Polls** that endpoint every 2 seconds for trigger conditions (no network
   traffic, all local).
3. **On a trigger**, navigates the Settings panel to the Models page via CDP,
   clicks Refresh, waits 3 seconds for the server response, then reads the
   quota numbers from the DOM text.
4. **Stores** the reading in a local SQLite database (`dashboard/data/quota.db`).
5. **Updates** the tray icon colour and the web dashboard.

**Why CDP instead of OCR or screen scraping?**
CDP gives us direct access to the actual DOM text — deterministic, fast, and
doesn't break when the UI is offscreen or minimized. OCR requires Tesseract
and fails on high-DPI screens. Screenshot scraping breaks on theme changes.

---

## Five capture triggers

| # | Trigger | When | Refresh before read? |
|---|---------|------|----------------------|
| 1 | **launch** | Antigravity process appears | Yes (3 s wait) |
| 2 | **profile_menu** | Profile dropdown opens | Yes (3 s wait) |
| 3 | **sign_out_dialog** | Sign-out confirmation dialog appears | Yes (3 s wait) |
| 4 | **manual_refresh** | User clicks Refresh in Settings › Models | No (data already fresh) |
| 5 | **safety_net** | Every 20 minutes while Antigravity is open | Yes (3 s wait) |
| 6 | **manual_tray** | Tray menu "Capture Now" | Yes (3 s wait) |

Trigger 4 is the only one that skips the Refresh step — the user just clicked
it, so the data is already fresh.

---

## System tray icon

| Colour | Meaning |
|--------|---------|
| 🟢 Green | All accounts > 30% weekly remaining |
| 🟡 Amber | At least one account ≤ 30% weekly |
| 🔴 Red | All accounts ≤ 10% weekly (or no data yet) |

**Left-click** → opens the dashboard in a native window  
**Right-click** → menu: Open Dashboard | Capture Now | Run Diagnostics | Quit

---

## Remote access from another device

### Tailscale (recommended — free, 2-minute setup)

1. Install [Tailscale](https://tailscale.com/) on both devices
2. Sign in (Google / Microsoft / GitHub)
3. Access the dashboard from your phone at `http://<your-pc-tailscale-ip>:4300`

No port-forwarding, no dynamic DNS, no deployment required.

### VPS self-hosting (advanced)

1. Run `python main.py` on a Linux VPS
2. Set `DASHBOARD_API_KEY=<a-strong-secret>` in `notifier/.env`
3. Reverse-proxy port 4300 via nginx/Caddy with HTTPS
4. Set the same key in your browser (the dashboard reads it from localStorage)

---

## Configuration

Copy `notifier/config.example.env` to `notifier/.env` and edit values:

| Key | Default | Description |
|-----|---------|-------------|
| `CDP_PORT` | `9222` | Chrome DevTools Protocol port |
| `POLL_INTERVAL_SECONDS` | `2` | How often to check for triggers |
| `DEBOUNCE_SECONDS` | `2` | Min seconds between captures |
| `SAFETY_NET_INTERVAL` | `1200` | Seconds between safety-net captures (default 20 min) |
| `DASHBOARD_URL` | `http://localhost:4300` | Dashboard URL |
| `DASHBOARD_API_KEY` | *(empty)* | Optional API key for remote access |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARN` / `ERROR` |

---

## Known Limitations

### Data only exists when Settings › Models was rendered

Antigravity only fetches quota from its servers when the Settings › Models
panel is rendered. This tracker reads that data — it cannot retroactively
recover quota data for time periods when Settings › Models was never displayed.

The safety-net timer (every 20 minutes by default) bounds the worst-case
staleness, but cannot fill gaps from sessions where the panel was never opened.

### Mac support is experimental

The code is written to run on Mac, but as of this release it has only been
tested on Windows. Specifically unverified on Mac:

- Tray icon appearing in the menu bar (not the Dock) — code is correct per
  pystray docs but untested on real hardware
- CDP launch flag — the `antigravity-debug` shell wrapper is created by
  `setup-mac.sh` but its actual CDP connectivity has not been confirmed
- Process name detection — `psutil` may see a different process name on Mac

If you run this on Mac, please report your results by opening a GitHub issue.
If something doesn't work, use **Run Diagnostics** from the tray menu (once
you get that far) and include the report in your issue.

### Breaks if Antigravity changes its Settings UI text

Every string the parser looks for (`"Weekly Limit"`, `"Five Hour Limit"`,
`"Claude and GPT models"`, etc.) is hardcoded against the version of
Antigravity this was built against. A future Antigravity update could rename
or restructure these and silently break capture.

**What to do when this happens:** right-click the tray icon → **Run Diagnostics**.
The report shows exactly which strings are `FOUND` vs `MISSING` in the current
Antigravity UI. Paste the report into a GitHub issue — that's everything needed
to update the parser.

### psutil required for launch and close triggers

Without `psutil`, the process-detection triggers (launch and post-close) are
silently disabled. The other three triggers (profile menu, sign-out dialog,
safety-net timer) still work.

```bash
pip install psutil
```

---

## Troubleshooting

**Tray icon shows red / "no data" even after Antigravity is open**
- Check that Antigravity was launched via the debug shortcut (not a normal shortcut).
- Right-click tray → Run Diagnostics — the report shows whether CDP is reachable.
- If the report says "not_open": the debug flag isn't being applied. Re-run `setup-windows.ps1`.
- If the report says "conflict": another process is using port 9222. Change `CDP_PORT` in `notifier/.env`.

**"Capture failed: could not open Settings › Models"**
- Antigravity must be running with the CDP flag AND have Settings → Models open
  at least once per session before a capture can succeed.
- Open Settings → Models manually, then click "Capture Now" from the tray menu.

**Dashboard at http://localhost:4300 shows no data**
- The Flask server starts with the tray icon. If you see no data, check the
  terminal output (or log file) for errors.
- `notifier/.env` must have `DASHBOARD_URL=http://localhost:4300` (default).

**Port 9222 conflict warning in the log**
- Another application is bound to port 9222. Options:
  1. Stop the conflicting service.
  2. Change `CDP_PORT` in `notifier/.env` to another port (e.g. `9223`) and
     re-run `setup-windows.ps1 -Port 9223` to update the shortcut.

---

## Uninstalling

```bash
# Windows
powershell -ExecutionPolicy Bypass -File scripts\uninstall-windows.ps1

# Mac
bash scripts/uninstall-mac.sh
```

Both scripts stop the process, remove the startup entry, and optionally delete
the SQLite history. **Deleting history requires typing "yes" explicitly** —
pressing Enter keeps your data.

---

## Building the executable

```bash
pip install pyinstaller
python build.py
# → dist/AntigravityQuotaTracker.exe   (Windows)
# → dist/AntigravityQuotaTracker.app   (Mac, with LSUIElement patched)
```

---

## What this does NOT do (by design)

These are explicit non-goals for this release, not oversights:

- **No auto-update.** To update, download the new release and replace the `.exe`.
- **No Linux support.** Antigravity IDE's Linux behavior is untested. Marked
  unsupported rather than silently broken.
- **No code signing / notarization for Mac.** On first launch, Gatekeeper will
  show a security warning. Workaround: right-click the `.app` → Open.
- **No multi-user / multi-machine sync.** Each install tracks its own local
  SQLite independently. Use Tailscale or a VPS for multi-device access.

---

## Repository structure

```
antigravity-quota-tracker/
├── main.py                    # Single entry point
├── build.py                   # PyInstaller packaging (+ Mac LSUIElement patch)
├── state.py                   # Shared app state (thread-safe singleton)
├── webview_launcher.py        # Standalone PyWebView subprocess
├── server/
│   ├── flask_app.py           # Flask API server
│   ├── db.py                  # SQLite queries
│   └── ocr.py                 # OCR processing (Tesseract, optional)
├── tray/
│   └── tray_icon.py           # pystray tray icon + diagnostic mode
├── notifier/
│   ├── notifier.py            # CDP watcher (5 triggers + safety net)
│   ├── config.example.env     # Configuration template
│   └── requirements.txt       # Python dependencies
├── dashboard/
│   └── public/                # Web dashboard (HTML/CSS/JS — unchanged)
├── scripts/
│   ├── setup-windows.ps1      # Patches Antigravity shortcuts (one-time)
│   ├── setup-mac.sh           # Creates ~/bin/antigravity-debug wrapper
│   ├── uninstall-windows.ps1  # Removes tracker from Windows
│   └── uninstall-mac.sh       # Removes tracker from Mac
├── README.md
├── CONTRIBUTING.md
└── LICENSE
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

MIT — see [LICENSE](LICENSE).
