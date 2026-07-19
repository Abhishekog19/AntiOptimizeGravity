#!/usr/bin/env bash
# uninstall-mac.sh — Antigravity Quota Tracker
#
# Removes the Quota Tracker from this macOS machine:
#   1. Kills the running process (if any)
#   2. Removes ~/bin/antigravity-debug wrapper script
#   3. Removes compiled .app from dist/ (if present)
#   4. Offers to delete the SQLite quota history
#      REQUIRES explicit "yes" — pressing Enter keeps the data
#
# Usage (from project root):
#   bash scripts/uninstall-mac.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo ""
echo "Antigravity Quota Tracker — macOS Uninstaller"
echo ""

# ── 1. Kill running process ───────────────────────────────────────────────────

PROC_NAME="AntigravityQuotaTracker"
if pgrep -f "$PROC_NAME" > /dev/null 2>&1; then
    pkill -f "$PROC_NAME" && echo "  [STOPPED] Process $PROC_NAME" || echo "  [WARN] Could not stop $PROC_NAME (try: sudo pkill -f $PROC_NAME)"
else
    echo "  [OK] Process not running."
fi

# Also try stopping a Python main.py process referencing this project
if pgrep -f "main.py" > /dev/null 2>&1; then
    pkill -f "main.py" 2>/dev/null && echo "  [STOPPED] Stopped main.py process" || true
fi

# ── 2. Remove ~/bin/antigravity-debug wrapper ─────────────────────────────────

WRAPPER="$HOME/bin/antigravity-debug"
if [ -f "$WRAPPER" ]; then
    rm -f "$WRAPPER"
    echo "  [REMOVED] $WRAPPER"
else
    echo "  [OK] Wrapper not found: $WRAPPER"
fi

# ── 3. Remove compiled .app ───────────────────────────────────────────────────

APP_PATH="$PROJECT_ROOT/dist/AntigravityQuotaTracker.app"
if [ -d "$APP_PATH" ]; then
    rm -rf "$APP_PATH"
    echo "  [DELETED] $APP_PATH"
else
    echo "  [OK] No compiled .app found at: $APP_PATH"
fi

# ── 4. Offer to delete SQLite quota history ───────────────────────────────────
#
# IMPORTANT: defaults to NOT deleting.
# The prompt requires the user to type exactly "yes" to proceed.
# Pressing Enter (empty input) or anything other than "yes" keeps the data.

DB_PATH="$PROJECT_ROOT/dashboard/data/quota.db"

echo ""
echo "──────────────────────────────────────────────────────"
echo "Quota history database:"
if [ -f "$DB_PATH" ]; then
    DB_SIZE=$(du -sh "$DB_PATH" 2>/dev/null | cut -f1)
    echo "  $DB_PATH  ($DB_SIZE)"
else
    echo "  Not found (already deleted or never created)."
fi
echo ""
echo "Do you want to permanently delete your quota history?"
echo "This cannot be undone."
echo ""
printf "Type exactly 'yes' to delete, or press Enter to keep it: "
read -r answer

# Case-sensitive exact match required — "YES", "Yes", "y" all keep the data
if [ "$answer" = "yes" ]; then
    if [ -f "$DB_PATH" ]; then
        rm -f "$DB_PATH"
        echo "  [DELETED] $DB_PATH"
    fi
    # Also remove WAL/SHM sidecar files if present
    for sidecar in "${DB_PATH}-shm" "${DB_PATH}-wal"; do
        if [ -f "$sidecar" ]; then
            rm -f "$sidecar"
            echo "  [DELETED] $sidecar"
        fi
    done
else
    echo "  [KEPT] Quota history preserved."
    echo "  Data is at: $DB_PATH"
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "──────────────────────────────────────────────────────"
echo "Uninstall complete."
echo ""
echo "To fully clean up:"
echo "  - The ~/bin/antigravity-debug command has been removed."
echo "  - The PATH line added to your shell profile (~/.zshrc or ~/.bash_profile)"
echo "    by setup-mac.sh can be removed manually if ~/bin is no longer needed."
echo ""
