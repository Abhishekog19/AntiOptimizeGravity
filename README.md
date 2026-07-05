# Antigravity Quota Tracker

Two-part system from the spec: an IDE extension that captures Antigravity
quota readings, and a web dashboard that stores history and recommends which
account to use next.

```
antigravity-tracker/
├── extension/     VS Code / Open VSX extension (capture + sync)
└── dashboard/     Node/Express + SQLite dashboard (storage + overview UI)
```

## Quick start

**1. Dashboard**

```bash
cd dashboard
npm install
DASHBOARD_API_KEY=changeme node server.js
```
Open `http://localhost:4300`. It'll show an empty state until readings arrive.

Uses Node's built-in `node:sqlite` (Node ≥ 22.5), so there's no native
module to compile — just `npm install` and go.

**2. Extension**

```bash
cd extension
npm install
npm run compile
```
Launch via `F5` in VS Code (Extension Development Host), or package with
`vsce package` for a real `.vsix`. Set `dashboardUrl` and `apiKey` in
settings to match step 1.

## What's real vs. stubbed

Everything is implemented and tested end-to-end **except one piece**: the
actual DOM read of Antigravity's quota panel (`scrapeQuotaPanel()` in
`extension/src/extension.ts`). That's a genuine open item, not a shortcut —
see `extension/README.md` for why (short version: there's no supported
VS Code API for reading another extension's webview, so it needs either an
official Antigravity API surface if one appears, or a CDP-based attach).
Everything downstream of that function — sync, storage, reset-time math,
ranking, and the whole dashboard UI — is fully working; I ran it end-to-end
with seeded readings to confirm scoring, history, and renaming all behave
as specified.

## The ranking formula

Implements section 6.3: an account's effective capacity is
`min(five_hour_remaining, weekly_remaining / 33 × 100)`, since a full
five-hour session costs ~33% of the weekly budget (per the spec's observed
1% : 0.33% ratio). This means a full five-hour reset with a nearly-exhausted
weekly balance scores *below* a partially-used five-hour with a healthy
weekly balance — matching the example in the spec.

## Worth reiterating from the spec's own Section 9

This is built to spec, but the spec itself flags that running multiple
accounts specifically to multiply effective Antigravity/Google AI quota may
sit outside Google's intended use terms, and that automated tooling around
it is more visible to abuse detection than manual switching. That's your
call to make, not something the code resolves — I'd just rather you go in
with eyes open than find out after the fact.
