# Antigravity Quota Tracker — Extension

Captures Weekly % / Five Hour % readings from Antigravity and syncs them to
the companion dashboard (see `../dashboard`).

## Setup

```bash
cd extension
npm install
npm run compile
```

Then in VS Code: `F5` to launch an Extension Development Host, or package it
with `vsce package` and install the `.vsix` manually.

## Configuration (Settings)

| Setting | Purpose |
|---|---|
| `antigravityQuotaTracker.dashboardUrl` | Base URL of your running dashboard, e.g. `http://localhost:4300` |
| `antigravityQuotaTracker.apiKey` | Bearer token, must match the dashboard's configured key |
| `antigravityQuotaTracker.accountIdentifier` | Label for the account signed in on this window |
| `antigravityQuotaTracker.captureIntervalMinutes` | Polling cadence, default 7 |

## The one real blocker: reading the quota panel

Section 8 of the spec flags this as an open item, and it's the part worth
being upfront about: **there is no supported VS Code API for one extension
to read another extension's (or the IDE's own) webview DOM.** `scrapeQuotaPanel()`
in `src/extension.ts` is a stub for exactly this reason. To finish it you have two
realistic paths:

1. **Official surface** — if Antigravity ever exposes quota state via a
   command or extension API, call that instead of touching the DOM at all.
   This is the only approach that won't break on every UI update.
2. **CDP attach** — launch the IDE with `--remote-debugging-port=<port>`,
   connect over the Chrome DevTools Protocol, find the Antigravity webview's
   frame, and run a `Runtime.evaluate` DOM query against it. Works today,
   but it's inherently fragile: any Antigravity UI change can silently break
   the selector, and this method is more visible to anti-abuse heuristics
   than a normal extension, which is worth factoring into the ToS
   consideration in the main spec.

Once you've inspected the live panel and picked an approach, replace the
`notYetImplemented` block in `scrapeQuotaPanel()` with real parsing logic for:
- Weekly % and Five Hour % (numeric)
- The reset countdown string (e.g. `"fully refresh in 4 days"`) so the
  dashboard can compute an absolute reset timestamp

## Behavior

- Captures on IDE startup, every `captureIntervalMinutes`, and on shutdown/deactivate.
- Never throws into the user's workflow — sync failures are caught and logged, and the next interval just retries.
