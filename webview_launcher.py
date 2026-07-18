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
  python webview_launcher.py [url]

  url  defaults to http://localhost:4300

When run as a packaged .exe (PyInstaller --onefile), main.py intercepts
  sys.argv[1] == "--webview-launcher"
and routes to this same logic before any tray/watcher setup.
"""

import sys
import webview

if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:4300"
    webview.create_window(
        title="Quota Tracker",
        url=url,
        width=420,
        height=600,
        resizable=True,
        min_size=(360, 480),
        background_color="#0b0f1a",
    )
    webview.start()
