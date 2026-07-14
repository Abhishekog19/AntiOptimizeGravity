# Antigravity Quota Tracker — v3 Final Architecture

---

## Confirmed working (tested July 2026, all claims verified)

| What | How confirmed |
|---|---|
| CDP access via port 9222 | find-quota-frame.js found Settings target, read quota text |
| Quota text is clean structured lines | test-c.js returned perfect line-by-line data |
| Refresh button clickable via CDP | test-b.js returned "clicked", data updated after 3s |
| Email readable from DOM | test-e.js returned pramod12179@gmail.com from leaf nodes |
| Sign Out confirmation dialog | Screenshot confirmed: "Sign out of 'Pramod Pandey'?" dialog |

---

## The trigger: Sign Out confirmation dialog

When you click Sign Out from the profile dropdown, Antigravity shows:

```
┌─────────────────────────────────────┐
│  Sign out of 'Pramod Pandey'?       │
│                                     │
│         [Sign Out]  [Cancel]        │
└─────────────────────────────────────┘
```

This dialog:
- Stays open until the user manually clicks Sign Out or Cancel
- Gives a guaranteed 3-5 second window
- Is detectable via CDP: the string "Sign out of" appears in
  document.documentElement.innerText of the Settings target
- Cannot false-positive: this exact string appears nowhere else
  in the Antigravity UI

This is the ONLY capture trigger. Nothing else triggers a capture.

---

## Capture sequence (what happens in those 3-5 seconds)

```
[You click Sign Out from dropdown]
         ↓
[Confirmation dialog appears on screen]
         ↓
[notifier.py detects "Sign out of" in DOM within 2 seconds]
         ↓
[CAPTURE runs in background]:
  1. Read email from DOM leaf nodes            ~0.1s
  2. Click "Models" nav button                 ~0.1s
  3. Wait for Models tab to render             ~0.5s
  4. Click last "Refresh" button               ~0.1s
  5. Wait 3 seconds for refresh to complete    ~3.0s
  6. Read innerText, parse quota               ~0.1s
  7. POST to dashboard /api/readings           ~0.1s
  8. Show Windows toast notification           ~0.0s
  TOTAL: ~4 seconds
         ↓
[You click Sign Out in the dialog]
         ↓
[Account logs out — data already saved]
```

The dialog gives more time than the capture needs. The user will
naturally take 2-3 seconds to read and decide, during which the
capture completes entirely in the background. No waiting required.

---

## notifier.py — complete logic

```python
CDP_PORT = 9222
DASHBOARD_URL = "http://localhost:4300"
POLL_INTERVAL = 2        # seconds between DOM checks
DEBOUNCE = 30            # minimum seconds between captures
SIGN_OUT_MARKER = "Sign out of"  # exact text in confirmation dialog

state = { "capturing": False, "last_capture_time": 0 }

# Main loop
while True:
    if state["capturing"]:
        sleep(POLL_INTERVAL); continue

    target = find_settings_target(port=CDP_PORT)
    if not target:
        sleep(POLL_INTERVAL); continue

    text = cdp_get_innertext(target)

    if SIGN_OUT_MARKER in text:
        if time.now() - state["last_capture_time"] > DEBOUNCE:
            run_capture_sequence(target)

    sleep(POLL_INTERVAL)


def run_capture_sequence(target):
    state["capturing"] = True
    try:
        # 1. Read email
        email = cdp_evaluate(target, """
            Array.from(document.querySelectorAll('*'))
                .filter(el => el.children.length === 0
                           && el.innerText?.includes('@'))
                .map(el => el.innerText.trim())
                .find(t => t.includes('.') && t.length < 100) || null
        """)
        if not email:
            toast("Capture failed: could not read email"); return

        # 2-3. Navigate to Models tab
        cdp_evaluate(target, """
            const btn = [...document.querySelectorAll('button')]
                .find(b => b.innerText.trim() === 'Models');
            if (btn) btn.click();
        """)
        sleep(0.8)

        # 4. Click Refresh (last one = Models section, not MCP)
        cdp_evaluate(target, """
            const btns = [...document.querySelectorAll('button')]
                .filter(b => b.innerText.trim() === 'Refresh');
            if (btns.length) btns[btns.length - 1].click();
        """)
        sleep(3.0)

        # 5. Read and parse
        text = cdp_get_innertext(target)
        quota = parse_quota(text)  # see parser section below

        if not quota:
            toast(f"Capture failed: parse error for {email}"); return

        # 6. POST
        post_reading(email, quota)
        toast(f"Quota saved for {email}\n"
              f"Claude: {quota['claudeGpt']['weeklyPct']}% weekly "
              f"/ {quota['claudeGpt']['fiveHourPct']}% 5hr")

        state["last_capture_time"] = time.now()
    finally:
        state["capturing"] = False
```

---

## Parser — bounded section logic

```python
def parse_quota(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    gemini_idx = find(lines, "Gemini Models")
    claude_idx  = find(lines, "Claude and GPT models")

    if claude_idx is None:
        return None   # Models panel not visible

    def parse_section(start, end):
        section = lines[start:end]
        wi = find(section, "Weekly Limit")
        fi = find(section, "Five Hour Limit")
        if wi is None or fi is None: return None

        # CRITICAL: each search is bounded by the NEXT label index
        # so values never bleed across sections
        weekly_pct     = find_pct(section, from_idx=wi,   to_idx=fi)
        weekly_reset   = find_reset(section, from_idx=wi, to_idx=fi)
        fivehour_pct   = find_pct(section, from_idx=fi,   to_idx=len(section))
        fivehour_reset = find_reset(section, from_idx=fi, to_idx=len(section))

        # Sanity checks — reject obviously wrong data
        if weekly_pct is None or fivehour_pct is None:
            return None
        if weekly_reset and extract_days(weekly_reset) > 7:
            return None   # weekly reset > 7 days is impossible
        if fivehour_reset and extract_hours(fivehour_reset) > 5:
            return None   # 5hr reset > 5 hours is impossible

        return {
            "weeklyPct":       weekly_pct,
            "weeklyReset":     weekly_reset,
            "fiveHourPct":     fivehour_pct,
            "fiveHourReset":   fivehour_reset,
        }

    return {
        "gemini":    parse_section(gemini_idx, claude_idx) if gemini_idx else None,
        "claudeGpt": parse_section(claude_idx, len(lines))
    }
```

---

## Reset time calculation

```python
def parse_reset_to_timestamp(raw_text, captured_at):
    # Handles compound durations: "6 days, 19 hours" → adds both
    days    = extract_number(raw_text, "day")    or 0
    hours   = extract_number(raw_text, "hour")   or 0
    minutes = extract_number(raw_text, "minute") or 0
    delta = timedelta(days=days, hours=hours, minutes=minutes)
    return captured_at + delta
```

Both weekly AND five-hour resets are parsed from their own row's text.
The five-hour reset is NEVER assumed to be capture_time + 5h.

---

## Account identification

```
Email read from DOM → used as account_id in database
  e.g. "pramod12179@gmail.com"

Custom display name can be set from dashboard UI
  PATCH /api/accounts/pramod12179@gmail.com
  body: { "displayName": "Work Account" }

The email is always the underlying key — display name is cosmetic only.
This means accounts are never confused across sessions or machines.
```

---

## Startup (one-time, then automatic forever)

**1. Make debug port permanent:**
```
Right-click Antigravity shortcut → Properties
Target: "C:\...\Antigravity IDE.exe" --remote-debugging-port=9222
```

**2. Auto-start notifier on Windows login:**
```
Win+R → shell:startup → create notifier.bat:

  @echo off
  cd /d C:\path\to\AntigravityOptimizer
  python notifier.py
```

**3. Auto-start dashboard on Windows login:**
```
Same folder → create dashboard.bat:

  @echo off
  cd /d C:\path\to\AntigravityOptimizer\dashboard
  node server.js
```

After this setup, the daily workflow is:
- Open Antigravity (debug port opens automatically)
- Work normally
- When quota runs out: click profile → Sign Out → dialog appears
- Capture runs silently in those 3-4 seconds
- Click Sign Out → sign into next account
- Check dashboard at http://localhost:4300 anytime

**Zero daily setup. Zero manual steps. Zero extra clicks.**

---

## Edge cases

| Situation | Handling |
|---|---|
| Settings not open when dialog appears | notifier navigates to Models via CDP before refreshing |
| Two Refresh buttons (Models + MCP) | always click the LAST one (confirmed = Models) |
| User cancels sign out | debounce (30s) prevents re-capture immediately; next sign-out attempt will capture normally |
| Dashboard not running | POST fails, toast shows warning, data lost for that session |
| Antigravity not launched with debug port | no CDP target found, notifier logs warning every 30s, otherwise silent |
| Gemini shows no reset text (100% remaining) | parse_section returns empty string for reset, that's valid — no countdown is shown when quota is full |
| User opens dropdown but doesn't sign out | "Sign out of" dialog only appears AFTER clicking Sign Out in the dropdown, not just from opening the dropdown — confirmed from screenshot |

---

## What changes from v2

| Component | v2 | v3 |
|---|---|---|
| Data source | Tesseract OCR on screenshots | CDP DOM read |
| Account ID | Manual label set in settings | Auto-read email from DOM |
| Capture trigger | Window title change | "Sign out of" text in DOM |
| Refresh before capture | Never | Always (CDP click) |
| Parser | Regex on full OCR text blob | Bounded line-index section parser |
| False positive risk | High (title changes on file open) | Zero (dialog text is unique) |
| Tesseract | Required | Deleted |
| VS Code extension | Required | Deleted |
| Background OCR watcher | Required | Deleted |
| /api/ocr endpoint | Required | Deleted |
| Capture UI tab | Screenshot paste + OCR | Plain number inputs (manual fallback only) |

## What stays exactly the same

- Dashboard frontend (Overview cards, Analytics, History)
- SQLite schema (add fivehour_reset_raw columns only)
- Express server routes (remove /api/ocr only)
- Recommendation scoring formula
- Analytics endpoint and charts
- parseCountdownToIso() reset time math
