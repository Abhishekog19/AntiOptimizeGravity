"""
notifier/__init__.py
====================
Re-exports every name that the rest of the codebase imports from the
``notifier`` package, so that ``from notifier import fire_capture`` (and
friends) works correctly in **both** of these contexts:

  1. Running from source:   python src/main.py
     Python resolves ``notifier`` as the *package* at src/notifier/ and
     loads this __init__, which in turn loads notifier.notifier.

  2. Frozen .exe (PyInstaller):
     Without this __init__, PyInstaller either can't see the directory as a
     package or bundles it incorrectly, causing:
       ImportError: cannot import name 'fire_capture' from 'notifier'
     With __init__ present + ``--collect-all notifier`` in build.py, the
     package is properly collected and all names are importable.

Adding a new public name here is the single place to update when notifier.py
grows a new exported symbol.
"""

from notifier.notifier import (      # noqa: F401  (re-exports)
    # ── Core capture / watcher ───────────────────────────────────────────────
    fire_capture,
    run_capture_sequence,
    run_watcher,

    # ── CDP helpers used by tray_icon.py ────────────────────────────────────
    CDP_PORT,
    check_cdp_port,
    find_settings_target,
    cdp_evaluate,
    _get_all_page_targets,
    _is_settings_panel,

    # ── Misc helpers imported elsewhere ─────────────────────────────────────
    log,
    toast,
    post_reading,
    post_heartbeat,
    setup_network_listener,
    teardown_network_listener,
    invalidate_settings_session,
    get_settings_session,
    ensure_settings_open,
    parse_quota,
    parse_reset_to_timestamp,
)
