# uninstall-windows.ps1 -- Antigravity Quota Tracker
#
# Removes the Quota Tracker and Watchdog from this Windows machine:
#   1. Kills the running tracker process (if any)
#   2. Kills the running watchdog process (if any)
#   3. Removes tracker and watchdog from Windows startup registry
#   4. Removes the compiled .exe files from dist\ (if present)
#   5. Offers to delete the SQLite quota history -- requires explicit "yes"
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

# -- 1. Kill running tracker process -----------------------------------------

$procName = "quota-tracker"
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
    Write-Host "  [OK] Tracker process not running." -ForegroundColor DarkGray
}

# -- 2. Kill running watchdog process -----------------------------------------

$watchdogName = "quota-watchdog"
$watchdogs = Get-Process -Name $watchdogName -ErrorAction SilentlyContinue

if ($watchdogs) {
    foreach ($p in $watchdogs) {
        if ($WhatIf) {
            Write-Host "  [WHATIF] Would stop watchdog: $($p.Name) (PID $($p.Id))" -ForegroundColor Yellow
        } else {
            Stop-Process -Id $p.Id -Force
            Write-Host "  [STOPPED] Watchdog $($p.Name) (PID $($p.Id))" -ForegroundColor Green
        }
    }
} else {
    Write-Host "  [OK] Watchdog process not running." -ForegroundColor DarkGray
}

# -- 3. Remove from Windows startup registry ----------------------------------

$keyPath      = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$watchdogKey  = "AntigravityQuotaWatchdog"
$trackerKey   = "AntigravityQuotaTracker"   # legacy key from older versions

foreach ($regName in @($watchdogKey, $trackerKey)) {
    $existing = Get-ItemProperty -Path $keyPath -Name $regName -ErrorAction SilentlyContinue
    if ($existing) {
        if ($WhatIf) {
            Write-Host "  [WHATIF] Would remove startup registry entry: $regName" -ForegroundColor Yellow
        } else {
            Remove-ItemProperty -Path $keyPath -Name $regName -Force
            Write-Host "  [REMOVED] Startup registry entry: $regName" -ForegroundColor Green
        }
    } else {
        Write-Host "  [OK] No startup registry entry for: $regName" -ForegroundColor DarkGray
    }
}

# -- 4. Remove compiled .exe files -------------------------------------------

# Look relative to this script's location (project root\dist\)
$scriptDir       = Split-Path -Parent $MyInvocation.MyCommand.Path
$distDir         = Split-Path -Parent $scriptDir | Join-Path -ChildPath "dist"
$trackerExePath  = Join-Path $distDir "quota-tracker.exe"
$watchdogExePath = Join-Path $distDir "quota-watchdog.exe"
# Legacy name from older builds
$legacyExePath   = Join-Path $distDir "AntigravityQuotaTracker.exe"

foreach ($exePath in @($trackerExePath, $watchdogExePath, $legacyExePath)) {
    if (Test-Path $exePath) {
        if ($WhatIf) {
            Write-Host "  [WHATIF] Would delete: $exePath" -ForegroundColor Yellow
        } else {
            Remove-Item $exePath -Force
            Write-Host "  [DELETED] $exePath" -ForegroundColor Green
        }
    } else {
        Write-Host "  [OK] Not found: $exePath" -ForegroundColor DarkGray
    }
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
