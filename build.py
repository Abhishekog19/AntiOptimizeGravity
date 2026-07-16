#!/usr/bin/env python3
"""
build.py — PyInstaller packaging script for Antigravity Quota Tracker

Produces a single-file executable:
  Windows: dist/AntigravityQuotaTracker.exe
  macOS:   dist/AntigravityQuotaTracker.app

Usage
─────
  pip install pyinstaller
  python build.py

Optional flags
──────────────
  --onefile     Force single-file bundle (default on Windows)
  --onedir      Directory bundle instead (faster startup, default on macOS)
  --debug       Include console window and verbose output
"""

from __future__ import annotations
import os
import sys
import subprocess
import shutil
from pathlib import Path

ROOT      = Path(__file__).parent
DIST_DIR  = ROOT / "dist"
BUILD_DIR = ROOT / "build"
SPEC_DIR  = ROOT

IS_WINDOWS = sys.platform == "win32"
IS_MAC     = sys.platform == "darwin"

# ── Asset paths ───────────────────────────────────────────────────────────────
ICON_ICO  = ROOT / "assets" / "icon.ico"    # Windows
ICON_ICNS = ROOT / "assets" / "icon.icns"   # macOS

# ── Data files to bundle (source, dest-dir-in-bundle) ────────────────────────
DATA_FILES = [
    (ROOT / "dashboard" / "public",           "dashboard/public"),
    (ROOT / "notifier" / "config.example.env", "notifier"),
]

# Optionally include the user's .env if present (pre-configured builds)
_ENV_FILE = ROOT / "notifier" / ".env"
if _ENV_FILE.exists():
    DATA_FILES.append((_ENV_FILE, "notifier"))


def _check_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller not found. Install it with:")
        print("  pip install pyinstaller")
        sys.exit(1)


def _build_data_args() -> list:
    sep = ";" if IS_WINDOWS else ":"
    args = []
    for src, dst in DATA_FILES:
        src_path = Path(src)
        if src_path.exists():
            args.append(f"--add-data={src_path}{sep}{dst}")
        else:
            print(f"  [WARN] Data path not found, skipping: {src_path}")
    return args


def run_build(onefile: bool = IS_WINDOWS, debug: bool = False) -> None:
    print(f"Building Antigravity Quota Tracker…")
    print(f"  Platform: {sys.platform}")
    print(f"  Bundle:   {'one-file' if onefile else 'one-dir'}")
    print()

    _check_pyinstaller()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name=AntigravityQuotaTracker",
        "--clean",
        "--noconfirm",
    ]

    # Single file vs directory
    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    # Console window
    if debug:
        cmd.append("--console")
    else:
        cmd.append("--windowed" if IS_MAC else "--noconsole")

    # Icon
    if IS_WINDOWS and ICON_ICO.exists():
        cmd.append(f"--icon={ICON_ICO}")
    elif IS_MAC and ICON_ICNS.exists():
        cmd.append(f"--icon={ICON_ICNS}")

    # Hidden imports that PyInstaller often misses
    hidden = [
        "pystray._win32",
        "pystray._darwin",
        "pystray._gtk",
        "PIL._tkinter_finder",
        "flask",
        "flask.json",
        "werkzeug",
        "sqlite3",
        "win10toast",
    ]
    for h in hidden:
        cmd.append(f"--hidden-import={h}")

    # Data files
    cmd.extend(_build_data_args())

    # Spec and build directories
    cmd.extend([
        f"--specpath={SPEC_DIR}",
        f"--distpath={DIST_DIR}",
        f"--workpath={BUILD_DIR}",
    ])

    # Entry point
    cmd.append(str(ROOT / "main.py"))

    print("Running:", " ".join(cmd[:6]), "…\n")
    result = subprocess.run(cmd, cwd=ROOT)

    if result.returncode == 0:
        exe_name = "AntigravityQuotaTracker"
        if IS_WINDOWS:
            exe_name += ".exe"
        exe_path = DIST_DIR / exe_name
        print()
        print("━" * 50)
        print(f"✓ Build complete!")
        if exe_path.exists():
            size_mb = exe_path.stat().st_size / (1024 * 1024)
            print(f"  Output: {exe_path}")
            print(f"  Size:   {size_mb:.1f} MB")
        print()
        print("First-run note:")
        print("  On Windows, double-click the .exe.")
        print("  The tray icon will appear in the system tray.")
        print("  Open http://localhost:4300 in your browser for the dashboard.")
        print("━" * 50)
    else:
        print()
        print("✗ Build failed (see errors above)")
        sys.exit(result.returncode)


def _first_run_setup() -> None:
    """
    Called automatically when the packaged .exe runs for the first time.
    Detects Antigravity and patches its shortcut.
    Embedded in main.py in the distributed build, but kept here for reference.
    """
    import winreg
    import glob

    # Standard Antigravity install locations
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Antigravity IDE" / "Antigravity IDE.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Antigravity IDE" / "Antigravity IDE.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Antigravity IDE" / "Antigravity IDE.exe",
    ]

    ag_exe = next((str(p) for p in candidates if p.exists()), None)
    if not ag_exe:
        return

    flag = "--remote-debugging-port=9222"

    # Check if already patched
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\App Paths\Antigravity IDE.exe",
        )
        val, _ = winreg.QueryValueEx(key, "")
        if flag in val:
            return
    except Exception:
        pass

    # Patch via startup shortcut (simplest approach — doesn't modify registry)
    startup = (
        Path(os.environ.get("APPDATA", ""))
        / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        / "AntigravityQuotaTracker.bat"
    )
    startup.write_text(
        f'@echo off\nstart "" "{ag_exe}" {flag}\n',
        encoding="utf-8",
    )


if __name__ == "__main__":
    onefile = "--onefile" in sys.argv or IS_WINDOWS
    if "--onedir" in sys.argv:
        onefile = False
    debug = "--debug" in sys.argv
    run_build(onefile=onefile, debug=debug)
