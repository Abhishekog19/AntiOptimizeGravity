# Antigravity Quota Tracker — Build Spec v2

## Context

This is a personal tool to track Claude/GPT and Gemini quota usage across
multiple Google Antigravity accounts, since Antigravity has no built-in
cross-account tracking. The user manually checks Settings → Models on each
account and wants a fast way to record those numbers, see history, get
analytics, and get reminded not to forget to record before switching
accounts.

**Why not fully automatic (read this before suggesting OCR-from-background
or DOM scraping):** We already tried a background screen-watcher with OCR
(Tesseract) and a CDP-based DOM scraper. Both failed in practice:
- The Settings → Models panel is a floating window with no fixed screen
  position, so cropping to it reliably before OCR did not work.
- CDP DOM access requires launching Antigravity with
  `--remote-debugging-port`, keeping that process alive, and the target
  webview wasn't reliably discoverable via `chrome://inspect` either.
- Full-screen OCR without cropping produced too much noise for reliable
  parsing (desktop icons, taskbar, other windows all in frame).

**Decided approach:** manual-trigger capture. The user takes a screenshot
themselves (`Win+Shift+S`) of just the quota panel and pastes it into the
dashboard. The dashboard runs OCR **on that already-cropped image only**
(not the full screen), which is a much easier OCR problem and should be
reliable. This trades one small manual step (10 seconds) for reliability.

The **one thing confirmed to work for automation** is detecting when
Antigravity's window title changes (e.g. on account switch), because that's
just reading window title text via `win32gui.GetWindowText` — no image
processing involved. This powers the logout reminder (see Component 3).

---

## Architecture

```
┌──────────────────────────────────────────────┐
│              Dashboard (browser)               │
│  ┌────────────┐ ┌───────────┐ ┌─────────────┐ │
│  │  Overview   │ │ Analytics │ │   History   │ │
│  │  (cards +   │ │ (charts,  │ │  (per-      │ │
│  │  recommend) │ │  burn     │ │  account    │ │
│  │             │ │  rate)    │ │  timeline)  │ │
│  └────────────┘ └───────────┘ └─────────────┘ │
│                                                │
│  [Paste screenshot here] → OCR preview →      │
│  [Confirm numbers] → [Select account] → Save  │
└───────────────────┬────────────────────────────┘
                     │ HTTP (localhost:4300)
┌────────────────────▼───────────────────────────┐
│            Node.js + Express server             │
│  POST /api/readings         — manual save        │
│  POST /api/ocr               — image → numbers   │
│  GET  /api/accounts           — overview + rank  │
│  GET  /api/accounts/:id/history                  │
│  GET  /api/analytics          — burn/projection  │
│  PATCH /api/accounts/:id      — rename            │
└────────────────────┬───────────────────────────┘
                     │
┌────────────────────▼───────────────────────────┐
│          SQLite (node:sqlite, built-in)          │
│  accounts(id, custom_name)                       │
│  readings(id, account_id, timestamp_utc,         │
│    claude_weekly_pct, claude_fivehour_pct,       │
│    claude_reset_raw, claude_weekly_reset_at,     │
│    claude_fivehour_reset_at,                     │
│    gemini_weekly_pct, gemini_fivehour_pct, ...)  │
└──────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────┐
│   notifier.py — separate tiny background script    │
│   Polls win32gui for the Antigravity window title  │
│   every 5s. When the title changes in a way that    │
│   suggests an account/workspace switch, fires a      │
│   Windows toast notification: "Record quota before   │
│   switching?" Clicking it opens the dashboard in      │
│   the browser (http://localhost:4300).                │
└──────────────────────────────────────────────────┘
```

This reuses the existing dashboard code (Express + `node:sqlite`, already
built and tested — see `dashboard/` in the existing project zip) and adds:
1. An OCR endpoint (image upload → parsed numbers)
2. A capture UI (paste/upload + confirm flow)
3. An analytics view (charts + burn rate + projections)
4. A separate small Python notifier script

Delete/ignore the old `extension/` (CDP-based VS Code extension) and
`watcher/` (background OCR screen-watcher) folders — both approaches were
tried and abandoned per the context above. Keep `dashboard/` as the
foundation.

---

## Component 1: OCR capture endpoint

**Input:** an image (PNG/JPEG) of *just* the Settings → Models panel,
cropped by the user via their own screenshot tool. Assume the image is
already reasonably tight around the panel — do not attempt to detect or
crop a sub-region from a larger screenshot.

**Known DOM/text structure to parse** (confirmed from real captures):

The panel contains two card sections, each with a heading and two rows:

```
Gemini Models
  Weekly Limit
    [muted reset text, only sometimes present]
    100%
  Five Hour Limit
    99%

Claude and GPT models
  Weekly Limit
    You have used some of your weekly limit, it will fully refresh in 4 days, 19 hours.
    19%
  Five Hour Limit
    You have used some of your 5-hour limit, it will fully refresh in 38 minutes.
    61%
```

**OCR approach:**
1. Use Tesseract (`tesseract.exe`, user already has it installed at
   `C:\Program Files\Tesseract-OCR\tesseract.exe` — make this path
   configurable via a settings file or env var, don't hardcode assuming
   it'll always be there).
2. Upscale the image 2x before OCR (small UI text benefits from this).
3. Run with `--psm 6` (single uniform block) or `--psm 3` (auto) — try
   both and use whichever parses more reliably in testing; make this
   configurable if one doesn't work well.
4. Parse line-by-line: find "Gemini" and "Claude and GPT" as section
   anchors, then within each section find "Weekly Limit" and
   "Five Hour Limit" labels, then search the next few lines for a
   `NN%` pattern and (for weekly only) a `N days/hours/minutes` reset
   string.
5. Return a structured JSON preview to the frontend **before** saving,
   e.g.:
   ```json
   {
     "claudeGpt": { "weeklyPct": 19, "fiveHourPct": 61, "resetCountdownRaw": "...4 days, 19 hours." },
     "gemini": { "weeklyPct": 100, "fiveHourPct": 99, "resetCountdownRaw": "" },
     "confidence": "high" | "low"
   }
   ```
   Set `confidence: "low"` if either weekly percentage couldn't be found,
   so the frontend can prompt the user to double check / manually correct
   before saving.
6. **Always let the user edit the parsed numbers before confirming save.**
   OCR will occasionally misread a digit — the confirm step is the safety
   net, not a formality.

**Reset time math** (already implemented in the existing `db.js`, reuse it):
- Weekly reset = capture timestamp + parsed countdown (e.g. "4 days, 19 hours")
- Five-hour reset = capture timestamp + fixed 5 hours (it's a rolling
  window, not tied to the countdown text, which may not even be shown for
  that row)

---

## Component 2: Analytics view

Add a new dashboard tab/section called **Analytics**, separate from the
existing Overview grid. Build using recharts or a simple custom SVG line
chart (existing dashboard has no frontend framework — a vanilla JS + SVG
approach matching the existing `app.js` style is fine, or introduce
Chart.js via CDN if easier).

**Required views:**

1. **Time-range toggle**: Week / Month / Year / Max — matching the
   reference screenshot behavior (buttons like "5D / 1M / 1Y / Max"), with
   the selected range highlighted and the chart re-rendering to that
   window.

2. **Combined burn-rate line chart** — X axis: time, Y axis: %. One line
   per account for Claude/GPT weekly %, so the user can see usage trend
   over the selected range at a glance (drop-down or checkboxes to
   isolate to fewer accounts if there are many).

3. **Per-account efficiency stat**: for each account, compute average %
   consumed per session (a "session" = a five-hour-limit reset cycle) —
   this tells the user which accounts tend to last longer.

4. **Weekly usage heatmap**: 7-column (Mon–Sun) grid showing which days
   historically see the most quota consumed, aggregated across all
   accounts and all weeks of history.

5. **Projection**: given the current combined burn rate (% consumed per
   day, averaged over the last 7 days of readings), estimate days
   remaining until all tracked accounts simultaneously hit their weekly
   cap. Show as a simple sentence, e.g. "At current pace, you'll exhaust
   all accounts' weekly quota in ~5 days."

6. **Total sessions used counter**: count of five-hour-reset cycles
   observed across all accounts this week/month/year (a rough proxy for
   "how many full working sessions you've burned").

Implement analytics as a new `GET /api/analytics?range=week|month|year|max`
endpoint that does the aggregation server-side (SQL group-by on
`readings`, bucketed by day/week) and returns pre-computed series for the
frontend to just plot — don't recompute this client-side from raw history
rows if the history log gets large.

---

## Component 3: Logout/switch reminder (notifier.py)

**Confirmed feasible** — this only reads window title text, not pixels,
so it avoids every problem the earlier OCR/CDP attempts ran into.

**Behavior:**
- Runs as a separate, lightweight Python script (`notifier.py`), started
  manually (`python notifier.py`) or optionally registered as a Windows
  Startup item later — start with manual run for v1.
- Every 5 seconds, enumerate visible windows via `win32gui.EnumWindows`,
  find the one(s) with "Antigravity IDE" in the title, and read the full
  title text (e.g. `"Untitled (Workspace) - Antigravity IDE - <file>"`).
- Keep the last-seen title in memory. When it changes in a way that looks
  like an account/workspace switch — **the reliable signal is the
  workspace/profile-name segment of the title changing**, not just any
  title change (switching files also changes the title) — fire a
  reminder.
  - Practical heuristic: split the title on `" - "` and watch the first
    segment (workspace name). If it changes value, treat that as a
    likely account/workspace switch and fire the reminder. Confirm this
    heuristic against a few real switches during testing before trusting
    it — worth logging title changes to console for a day of real use to
    validate the pattern before wiring up notifications for real.
- On a detected switch, show a Windows toast notification (use `win10toast`
  or `plyer`, whichever installs more reliably) reading something like:
  *"Switched Antigravity workspace — did you record quota for the
  previous account?"* with a clickable action that opens
  `http://localhost:4300` in the default browser.
- Debounce so it doesn't fire more than once per minute even if the title
  flickers.

**Explicitly do not** attempt to read quota values in this script — its
only job is detecting the switch event and prompting the human to do the
30-second manual capture. Keep it simple and reliable rather than
re-attempting the OCR-from-background approach that already failed.

---

## Non-functional requirements (unchanged from original spec)

- No data loss — history is never silently discarded.
- Dashboard accessible from any device on the local network (already
  responsive; keep it that way).
- Sync/save failures show a clear error rather than failing silently,
  since this is now a manual, user-triggered action rather than a
  background job — the user needs to know immediately if a save didn't
  go through so they can retry before the number changes.

## Explicit non-goals for this iteration

- No automatic screenshot capture (confirmed unreliable, see Context).
- No automatic account-switch detection of *which* account you switched
  to (only *that* a switch happened) — the user selects the account
  manually in the capture UI, since Antigravity's title bar doesn't
  reliably expose the Google account email.
- No mobile app — responsive web is sufficient.

## Handoff notes

The existing `dashboard/` folder (Express + `node:sqlite`, no native
compile step, already tested end-to-end for storage/ranking/history) is
the right foundation — extend it, don't rewrite it. The `db.js`
recommendation-scoring formula (`min(five_hour_remaining,
weekly_remaining / 33 * 100)`) is already correct per spec and tested;
reuse as-is.
