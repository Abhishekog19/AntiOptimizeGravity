#!/usr/bin/env python3
"""
webview_launcher.py — Minimal standalone PyWebView window launcher.

This script is spawned as a SUBPROCESS by tray/tray_icon.py every time the
user clicks the tray icon or "Open Dashboard" menu item.  Running webview in
a dedicated subprocess avoids the main-thread conflict with pystray:

  pystray  needs the main thread of the TRAY process
  webview  needs the main thread of ITS OWN process

This script IS the main thread of its own process, so there is no conflict.

Usage:
  python webview_launcher.py [url] [--x X] [--y Y]

  url  defaults to http://localhost:4300
  --x  window left position in screen pixels (default: let OS decide)
  --y  window top position in screen pixels  (default: let OS decide)

When run as a packaged .exe (PyInstaller --onefile), main.py intercepts
  sys.argv[1] == "--webview-launcher"
and routes to this same logic before any tray/watcher setup.
"""

import sys
import webview

if __name__ == "__main__":
    args = sys.argv[1:]

    # Parse URL (first positional arg that doesn't start with --)
    url = "http://localhost:4300"
    for a in args:
        if not a.startswith("--"):
            url = a
            break

    # Parse optional --x and --y position overrides
    def _get_arg(name: str):
        try:
            idx = args.index(name)
            return int(args[idx + 1])
        except (ValueError, IndexError):
            return None

    x = _get_arg("--x")
    y = _get_arg("--y")

    # Build kwargs — only pass x/y if explicitly provided so that
    # default OS window placement applies when running standalone.
    window_kwargs = dict(
        title="Quota Tracker",
        url=url,
        width=420,
        height=600,
        resizable=True,
        min_size=(360, 480),
        background_color="#0b0f1a",
    )
    if x is not None:
        window_kwargs["x"] = x
    if y is not None:
        window_kwargs["y"] = y

    webview.create_window(**window_kwargs)
    webview.start()

