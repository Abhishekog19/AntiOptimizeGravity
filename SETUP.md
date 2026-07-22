# Setup Guide — Antigravity Quota Tracker

## Prerequisites

| Requirement | Version | Check |
|-------------|---------|-------|
| Python | 3.8+ | `python --version` |
| Node.js | 18+ | `node --version` |
| Antigravity IDE | any | installed |

---

## Step 1 — Install dependencies

```bash
# Python notifier
pip install -r notifier/requirements.txt

# Dashboard
cd dashboard
npm install
cd ..
```

---

## Step 2 — Patch the Antigravity shortcut (Windows)

The notifier communicates with Antigravity through Chrome DevTools Protocol (CDP).
CDP requires Antigravity to be launched with `--remote-debugging-port=9222`.

Run the setup script once from the project root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
```

**What the script does:**
- Scans all Antigravity shortcuts on your machine (Desktop, Start Menu, etc.)
- Appends `--remote-debugging-port=9222` to each shortcut's Arguments
- If no shortcuts exist, finds the EXE and creates a new "Antigravity IDE (Debug)" shortcut on your Desktop

To preview what it will do without making changes:
```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1 -WhatIf
```

**macOS:** run `bash scripts/setup-mac.sh` instead.

> **Important:** After running the script, close Antigravity completely and relaunch it via the patched shortcut (or the new Debug shortcut on your Desktop). The CDP port only opens when the app is launched with the flag.

---

## Step 3 — Configure (optional)

```bash
cp notifier/config.example.env notifier/.env
```

Edit `notifier/.env` to customise port, debounce interval, etc. The defaults work for most setups.

---

## Step 4 — Start the dashboard

```bash
cd dashboard
node server.js
```

Open [http://localhost:4300](http://localhost:4300) in your browser.

---

## Step 5 — Start the notifier

Open a new terminal from the project root:

```bash
python notifier/notifier.py
```

You should see:
```
[HH:MM:SS INFO ] Antigravity Quota Tracker v3.0  [LIVE]
[HH:MM:SS INFO ] CDP=9222  poll=2s  debounce=30s  dashboard=http://localhost:4300
[HH:MM:SS INFO ] Triggers: launch | profile_menu | sign_out_dialog | manual_refresh | post_close
```

---

## Step 6 — Verify end-to-end

1. Launch Antigravity via the patched shortcut
2. The notifier should print:
   ```
   [HH:MM:SS INFO ] Antigravity detected: PID=XXXX  exe='...'
   [HH:MM:SS INFO ] == Capture [launch] started ==
   [HH:MM:SS INFO ]   Email: you@example.com
   [HH:MM:SS INFO ]   Refresh clicked: True  (waiting 3 s for fresh data...)
   [HH:MM:SS INFO ]   Claude/GPT   weekly=XX%   5hr=XX%
   [HH:MM:SS INFO ]   Gemini       weekly=XX%   5hr=XX%
   [HH:MM:SS INFO ] == Capture [launch] finished ==
   ```
3. Check the dashboard — your quota data should appear under your account email
4. The header status dot should be **green** (pulsing)

---

## Dry-run mode

Test the notifier without posting any data to the dashboard:

```bash
python notifier/notifier.py --dry-run
```

All captures are simulated. Toast notifications fire but no data is saved.

---

## Troubleshooting

### "No CDP pages / Settings not found"
Antigravity is running but without the debug flag. Re-run `setup-windows.ps1` and relaunch via the patched shortcut.

### Port 9222 already in use
Another app is using port 9222. Change the port in `notifier/.env`:
```
CDP_PORT=9223
```
And re-run `setup-windows.ps1 -Port 9223`.

### Toast notifications not appearing
Install a notification library:
```bash
pip install win10toast    # Windows
pip install plyer         # cross-platform
```

### "psutil not installed" warning
Install it to enable launch and post_close triggers:
```bash
pip install psutil
```

### Dashboard shows "notifier offline" (red dot)
The notifier is not running or cannot reach the dashboard. Check:
- `python notifier/notifier.py` is running in a terminal
- Dashboard is at `http://localhost:4300` (or matches `DASHBOARD_URL` in `.env`)

---

## Keeping the notifier running

### Windows — Task Scheduler

Create a task that runs on login:
1. Open Task Scheduler → Create Basic Task
2. Trigger: "When I log on"
3. Action: Start a program
   - Program: `python`
   - Arguments: `C:\path\to\project\notifier\notifier.py`
   - Start in: `C:\path\to\project`

### Windows — as a background process

```powershell
Start-Process python -ArgumentList "notifier/notifier.py" -WindowStyle Hidden
```

### macOS / Linux — launchd / systemd
See the OS-specific init system documentation.
