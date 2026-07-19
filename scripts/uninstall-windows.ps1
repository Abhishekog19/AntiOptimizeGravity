# uninstall-windows.ps1 — Antigravity Quota Tracker
#
# Removes the Quota Tracker from this Windows machine:
#   1. Kills the running process (if any)
#   2. Removes from Windows startup registry
#   3. Removes the compiled .exe from dist\ (if present)
#   4. Offers to delete the SQLite quota history — requires explicit "yes"
#      (defaulting to NO to prevent accidental data loss)
#
# Usage (from project root):
#   powershell -ExecutionPolicy Bypass -File scripts\uninstall-windows.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\uninstall-windows.ps1 -WhatIf
#
# Flags:
#   -WhatIf   Dry-run: shows what would change without modifying anything.

param([switch]$WhatIf)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "Antigravity Quota Tracker — Windows Uninstaller" -ForegroundColor Cyan
Write-Host ""

# ── 1. Kill running process ───────────────────────────────────────────────────

$procName = "AntigravityQuotaTracker"
$procs = Get-Process -Name $procName -ErrorAction SilentlyContinue

if ($procs) {
    foreach ($p in $procs) {
        if ($WhatIf) {
            Write-Host "  [WHATIF] Would stop process: $($p.Name) (PID $($p.Id))" -ForegroundColor Yellow
        } else {
            Stop-Process -Id $p.Id -Force
            Write-Host "  [STOPPED] Process $($p.Name) (PID $($p.Id))" -ForegroundColor Green
        }
    }
} else {
    Write-Host "  [OK] Process not running." -ForegroundColor DarkGray
}

# ── 2. Remove from Windows startup registry ───────────────────────────────────

$keyPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$appName = "AntigravityQuotaTracker"

$existing = Get-ItemProperty -Path $keyPath -Name $appName -ErrorAction SilentlyContinue
if ($existing) {
    if ($WhatIf) {
        Write-Host "  [WHATIF] Would remove startup registry entry: $appName" -ForegroundColor Yellow
    } else {
        Remove-ItemProperty -Path $keyPath -Name $appName -Force
        Write-Host "  [REMOVED] Startup registry entry: $appName" -ForegroundColor Green
    }
} else {
    Write-Host "  [OK] No startup registry entry found." -ForegroundColor DarkGray
}

# ── 3. Remove compiled .exe ───────────────────────────────────────────────────

# Look relative to this script's location (project root\dist\)
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$exePath   = Join-Path (Split-Path -Parent $scriptDir) "dist\AntigravityQuotaTracker.exe"

if (Test-Path $exePath) {
    if ($WhatIf) {
        Write-Host "  [WHATIF] Would delete: $exePath" -ForegroundColor Yellow
    } else {
        Remove-Item $exePath -Force
        Write-Host "  [DELETED] $exePath" -ForegroundColor Green
    }
} else {
    Write-Host "  [OK] No compiled .exe found at: $exePath" -ForegroundColor DarkGray
}

# ── 4. Offer to delete SQLite quota history ───────────────────────────────────
#
# IMPORTANT: defaults to NOT deleting.
# The prompt requires the user to type the exact word "yes" to proceed.
# Pressing Enter (empty input) or anything other than "yes" keeps the data.

$dataDir  = Join-Path (Split-Path -Parent $scriptDir) "dashboard\data"
$dbPath   = Join-Path $dataDir "quota.db"

Write-Host ""
Write-Host "──────────────────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host "Quota history database:" -ForegroundColor White
if (Test-Path $dbPath) {
    $sizeMb = [math]::Round((Get-Item $dbPath).Length / 1MB, 2)
    Write-Host "  $dbPath  ($sizeMb MB)" -ForegroundColor White
} else {
    Write-Host "  Not found (already deleted or never created)." -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "Do you want to permanently delete your quota history?" -ForegroundColor Yellow
Write-Host "This cannot be undone. Type exactly 'yes' to delete, or press Enter to keep it:" -ForegroundColor Yellow
Write-Host ""

if ($WhatIf) {
    Write-Host "  [WHATIF] Would prompt for 'yes' before deleting: $dbPath" -ForegroundColor Yellow
} else {
    $answer = Read-Host "Delete quota history? [yes / (anything else = keep)]"

    if ($answer -ceq "yes") {
        if (Test-Path $dbPath) {
            Remove-Item $dbPath -Force
            Write-Host "  [DELETED] $dbPath" -ForegroundColor Red
        }
        # Also remove WAL/SHM sidecar files if present
        foreach ($ext in @(".db-shm", ".db-wal")) {
            $sidecar = $dbPath -replace "\.db$", $ext
            if (Test-Path $sidecar) {
                Remove-Item $sidecar -Force
                Write-Host "  [DELETED] $sidecar" -ForegroundColor Red
            }
        }
    } else {
        Write-Host "  [KEPT] Quota history preserved." -ForegroundColor Green
        Write-Host "  Data is at: $dbPath" -ForegroundColor DarkGray
    }
}

# ── Done ──────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "──────────────────────────────────────────────────────" -ForegroundColor DarkGray
if ($WhatIf) {
    Write-Host "Uninstall dry-run complete. Re-run without -WhatIf to apply." -ForegroundColor Yellow
} else {
    Write-Host "Uninstall complete." -ForegroundColor Green
    Write-Host ""
    Write-Host "To fully remove any patched Antigravity shortcuts:" -ForegroundColor White
    Write-Host "  - Right-click each Antigravity shortcut -> Properties" -ForegroundColor White
    Write-Host "  - Remove '--remote-debugging-port=9222' from the Target field" -ForegroundColor White
}
Write-Host ""
