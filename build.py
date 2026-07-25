#!/usr/bin/env python3
"""
build.py - PyInstaller packaging script for Antigravity Quota Tracker

Produces two executables (Windows):
  dist/quota-tracker.exe   - the main tracker (tray icon, Flask, CDP watcher)
  dist/quota-watchdog.exe  - the watchdog (registered in Windows startup)

Usage
-----
  python build.py             # build both (default)
  python build.py --debug     # include console window for debugging
  python build.py --onedir    # directory bundle instead of single-file

PyInstaller is auto-installed if missing.
assets/icon.ico is auto-generated via Pillow if missing.
"""
from __future__ import annotations

import io
import os
import sys
import subprocess
import shutil
from pathlib import Path

# Force UTF-8 output on Windows to avoid cp1252 UnicodeEncodeError
if sys.platform == "win32" and sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT      = Path(__file__).parent
DIST_DIR  = ROOT / "dist"
BUILD_DIR = ROOT / "build"
ASSETS    = ROOT / "assets"
ICON_ICO  = ASSETS / "icon.ico"

IS_WINDOWS = sys.platform == "win32"
IS_MAC     = sys.platform == "darwin"

# ── Data files bundled with the tracker ───────────────────────────────────────
DATA_FILES = [
    (ROOT / "dashboard" / "public", "dashboard/public"),
    (ROOT / "notifier" / "config.example.env", "notifier"),
]
_ENV_FILE = ROOT / "notifier" / ".env"
if _ENV_FILE.exists():
    DATA_FILES.append((_ENV_FILE, "notifier"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ensure_pyinstaller() -> None:
    """Install PyInstaller if not present."""
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller not found — installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
        print()


def _ensure_icon() -> None:
    """Generate assets/icon.ico via Pillow if it doesn't exist."""
    if ICON_ICO.exists():
        return
    ASSETS.mkdir(exist_ok=True)
    try:
        from PIL import Image, ImageDraw
        sizes = [256, 128, 64, 48, 32, 16]
        frames = []
        for s in sizes:
            img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            pad = max(1, s // 16)
            # Red dot with white border — matches the tray icon colour
            d.ellipse(
                [pad, pad, s - pad, s - pad],
                fill=(220, 50, 50, 255),
                outline=(255, 255, 255, 255),
                width=max(1, s // 32),
            )
            frames.append(img)
        frames[0].save(
            ICON_ICO,
            format="ICO",
            sizes=[(s, s) for s in sizes],
            append_images=frames[1:],
        )
        print(f"  Generated icon: {ICON_ICO}")
    except ImportError:
        print("  [WARN] Pillow not available — skipping icon generation")


def _data_args() -> list:
    sep = ";" if IS_WINDOWS else ":"
    args = []
    for src, dst in DATA_FILES:
        src = Path(src)
        if src.exists():
            args.append(f"--add-data={src}{sep}{dst}")
        else:
            print(f"  [WARN] Data path not found, skipping: {src}")
    return args


def _run_pyinstaller(cmd: list) -> None:
    print("Running PyInstaller…")
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        print("\n✗ PyInstaller failed (see errors above)")
        sys.exit(result.returncode)


# ── Tracker build ─────────────────────────────────────────────────────────────

def build_tracker(onefile: bool = True, debug: bool = False) -> Path:
    """
    Build quota-tracker.exe — the main tracker process.

    Returns the path to the output exe.
    """
    print("=" * 60)
    print("Building quota-tracker (main tracker)…")
    print(f"  Bundle: {'one-file' if onefile else 'one-dir'}")
    print()

    exe_name = "quota-tracker" if IS_WINDOWS else "AntigravityQuotaTracker"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        f"--name={exe_name}",
        "--clean",
        "--noconfirm",
        "--onefile" if onefile else "--onedir",
        "--windowed" if IS_MAC else "--noconsole",
        f"--specpath={ROOT}",
        f"--distpath={DIST_DIR}",
        f"--workpath={BUILD_DIR}",
    ]

    if debug:
        # Override: show console for debugging
        cmd = [c for c in cmd if c not in ("--windowed", "--noconsole")]
        cmd.append("--console")

    if ICON_ICO.exists():
        cmd.append(f"--icon={ICON_ICO}")

    # Hidden imports PyInstaller commonly misses
    hidden = [
        "pystray._win32",
        "pystray._darwin",
        "pystray._gtk",
        "PIL._tkinter_finder",
        "PIL.Image",
        "flask",
        "flask.json",
        "werkzeug",
        "werkzeug.serving",
        "sqlite3",
        "win10toast",
        "pkg_resources",
    ]
    for h in hidden:
        cmd.append(f"--hidden-import={h}")

    # Collect full packages (data files + submodules) for tricky packages
    for pkg in ("pystray", "PIL", "flask", "werkzeug"):
        cmd.append(f"--collect-all={pkg}")

    cmd.extend(_data_args())
    cmd.append(str(ROOT / "main.py"))

    _run_pyinstaller(cmd)

    suffix = ".exe" if IS_WINDOWS else ""
    out = DIST_DIR / (exe_name + suffix)
    if out.exists():
        mb = out.stat().st_size / 1_048_576
        print()
        print("-" * 60)
        print("[OK] quota-tracker build complete!")
        print(f"  Output : {out}")
        print(f"  Size   : {mb:.1f} MB")
        print("-" * 60)
    return out


# ── Watchdog build ────────────────────────────────────────────────────────────

def build_watchdog(onefile: bool = True, debug: bool = False) -> Path | None:
    """
    Build quota-watchdog.exe — the minimal startup process.

    Must sit in the same directory as quota-tracker.exe so it can locate it
    via Path(sys.executable).parent / 'quota-tracker.exe'.
    """
    if not IS_WINDOWS:
        print("  [SKIP] Watchdog is Windows-only.")
        return None

    print()
    print("=" * 60)
    print("Building quota-watchdog (startup watchdog)…")
    print(f"  Bundle: {'one-file' if onefile else 'one-dir'}")
    print()

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name=quota-watchdog",
        "--clean",
        "--noconfirm",
        "--onefile" if onefile else "--onedir",
        "--noconsole",   # watchdog is always invisible
        f"--specpath={ROOT}",
        f"--distpath={DIST_DIR}",
        f"--workpath={BUILD_DIR}",
    ]

    if debug:
        cmd = [c for c in cmd if c != "--noconsole"]
        cmd.append("--console")

    if ICON_ICO.exists():
        cmd.append(f"--icon={ICON_ICO}")

    cmd.append("--hidden-import=psutil")
    cmd.append("--collect-all=psutil")
    cmd.append(str(ROOT / "watchdog.py"))

    _run_pyinstaller(cmd)

    out = DIST_DIR / "quota-watchdog.exe"
    if out.exists():
        mb = out.stat().st_size / 1_048_576
        print()
        print("-" * 60)
        print("[OK] quota-watchdog build complete!")
        print(f"  Output : {out}")
        print(f"  Size   : {mb:.1f} MB")
        print()
        print("Next steps:")
        print("  1. Run the Inno Setup compiler:")
        print("     ISCC.exe installer\\setup.iss")
        print("  2. Distribute installer\\AntigravityQuotaTrackerSetup.exe")
        print("-" * 60)
    return out


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    onefile = "--onedir" not in sys.argv   # default: one-file on all platforms
    debug   = "--debug" in sys.argv

    _ensure_pyinstaller()
    _ensure_icon()

    build_tracker(onefile=onefile, debug=debug)
    build_watchdog(onefile=onefile, debug=debug)
