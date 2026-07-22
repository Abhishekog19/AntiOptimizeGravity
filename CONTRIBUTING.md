# Contributing to Antigravity Quota Tracker

Thanks for your interest. This doc covers:

1. [Running from source](#running-from-source)
2. [Reporting a UI-drift bug](#reporting-a-ui-drift-bug)
3. [Code style](#code-style)
4. [Submitting changes](#submitting-changes)

---

## Running from source

**Prerequisites:** Python 3.8+, Antigravity IDE installed and launched with the
CDP debug flag (see [README.md](README.md) for setup steps).

```bash
git clone https://github.com/yourname/antigravity-quota-tracker
cd antigravity-quota-tracker

# Install dependencies
pip install -r notifier/requirements.txt

# Patch Antigravity shortcut — run once
# Windows:
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
# Mac:
bash scripts/setup-mac.sh

# Start the tracker
python main.py
```

The tray icon appears immediately. The dashboard is at `http://localhost:4300`.

**Running the notifier standalone (no tray/dashboard):**

```bash
python notifier/notifier.py --dry-run   # log only, no POST / toasts
python notifier/notifier.py --verbose   # DEBUG-level logging
```

---

## Reporting a UI-drift bug

The tracker reads Antigravity's Settings › Models panel by scraping its DOM
via CDP. If Antigravity updates its UI text or structure, the parser will
silently fail to find expected strings.

**How to report this:**

1. Right-click the tray icon → **Run Diagnostics**
2. A text report opens in Notepad / TextEdit automatically.
3. The report shows which expected strings are `FOUND` vs `MISSING` in the
   current Antigravity UI.
4. Open a GitHub issue and paste the full report content.

The key strings the parser currently looks for:

| String | Purpose |
|--------|---------|
| `Claude and GPT models` | Top-level section header |
| `Gemini Models` | Gemini section header |
| `Weekly Limit` | Per-section quota label |
| `Five Hour Limit` | Per-section quota label |
| `Refresh` | Button that refreshes server-side quota data |

If any of these have changed, the diagnostic report will show them as
`MISSING` — that's exactly the information needed to fix the parser.

---

## Code style

- **Python version:** 3.8+ compatible. No walrus operator (`:=`) or 3.10+
  match statements.
- **Paths:** Use `pathlib.Path` everywhere. No hardcoded backslashes or
  platform-specific path separators. No hardcoded `C:\Users\...`.
- **Imports:** stdlib before third-party before local. All optional imports
  wrapped in `try/except ImportError` with graceful fallback.
- **Logging:** Use the `log(msg, level=)` function in `notifier/notifier.py`
  (not `print()`). Levels: `DEBUG` / `INFO` / `WARN` / `ERROR`.
- **Thread safety:** Any state shared between threads goes through
  `state.py`'s `AppState` singleton (all methods hold `self._lock`). Do not
  add bare module-level dicts that are written from multiple threads.
- **No Node.js:** The dashboard frontend (`dashboard/public/`) is static
  HTML/CSS/JS. The backend is Flask (`server/flask_app.py`). Do not add a
  `package.json` or npm dependency.

---

## Submitting changes

1. Fork the repo and create a branch: `git checkout -b fix/my-thing`
2. Make your changes. Keep commits focused — one logical change per commit.
3. Test manually:
   - `python main.py` starts cleanly
   - Right-click tray → Run Diagnostics → report opens without error
   - If changing the parser, run `python notifier/notifier.py --dry-run`
     with Antigravity open at Settings › Models and confirm quota is parsed.
4. Open a pull request with a description of what you changed and why.

For large changes, open an issue first to discuss the approach.
