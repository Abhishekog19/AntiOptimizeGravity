# setup-windows.ps1 - Antigravity Quota Tracker
#
# Patches all Antigravity IDE shortcuts on this machine to include the
# --remote-debugging-port=9222 launch flag required by the notifier.
# If no shortcuts are found, creates a new debug shortcut on the Desktop.
#
# Usage (from project root):
#   powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1 -WhatIf
#
# Flags:
#   -WhatIf   Dry-run: prints what would change without modifying anything.
#   -Port     CDP port number (default: 9222).

param(
    [switch]$WhatIf,
    [int]$Port = 9222
)

$ErrorActionPreference = "Stop"
$cdpFlag = "--remote-debugging-port=$Port"

# Locations to scan for .lnk shortcuts
# Includes standard special folders and common OneDrive-synced paths
$searchRoots = @(
    [System.Environment]::GetFolderPath("Desktop"),
    [System.Environment]::GetFolderPath("CommonDesktopDirectory"),
    [System.Environment]::GetFolderPath("StartMenu"),
    [System.Environment]::GetFolderPath("CommonStartMenu"),
    "$env:APPDATA\Microsoft\Windows\Start Menu",
    "$env:ProgramData\Microsoft\Windows\Start Menu",
    "$env:USERPROFILE\OneDrive\Desktop",
    "$env:USERPROFILE\Desktop",
    "$env:LOCALAPPDATA\Programs\Antigravity IDE",
    "$env:LOCALAPPDATA\Programs"
) | Sort-Object -Unique

$shell   = New-Object -ComObject WScript.Shell
$found   = 0
$patched = 0
$already = 0
$failed  = 0

Write-Host ""
Write-Host "Antigravity Quota Tracker - Windows Setup" -ForegroundColor Cyan
Write-Host "Searching for Antigravity shortcuts in:"
$searchRoots | ForEach-Object { Write-Host "  $_" }
Write-Host ""

foreach ($root in $searchRoots) {
    if (-not (Test-Path $root)) { continue }
    Get-ChildItem -Path $root -Filter "*.lnk" -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -match "Antigravity" } |
    ForEach-Object {
        $lnk = $_.FullName
        $found++
        try {
            $sc      = $shell.CreateShortcut($lnk)
            $sc_args = $sc.Arguments

            if ($sc_args -match [regex]::Escape($cdpFlag)) {
                Write-Host "  [SKIP]    Already patched: $lnk" -ForegroundColor DarkGray
                $already++
            } else {
                $newArgs = if ($sc_args) { "$sc_args $cdpFlag" } else { $cdpFlag }
                if ($WhatIf) {
                    Write-Host "  [WHATIF]  Would patch: $lnk" -ForegroundColor Yellow
                    Write-Host "            Arguments: $newArgs"
                } else {
                    $sc.Arguments = $newArgs
                    $sc.Save()
                    Write-Host "  [PATCHED] $lnk" -ForegroundColor Green
                    Write-Host "            Arguments: $newArgs"
                }
                $patched++
            }
        } catch {
            Write-Host "  [ERROR]   Cannot patch ${lnk}: $_" -ForegroundColor Red
            $failed++
        }
    }
}

Write-Host ""
Write-Host "---------------------------------------------"
Write-Host "Shortcuts found:   $found"
if ($WhatIf) {
    Write-Host "Would patch:       $patched" -ForegroundColor Yellow
} else {
    Write-Host "Patched:           $patched" -ForegroundColor Green
}
Write-Host "Already had flag:  $already"
if ($failed -gt 0) { Write-Host "Errors:            $failed" -ForegroundColor Red }
Write-Host ""

if ($found -eq 0) {
    Write-Host "No existing Antigravity shortcuts found - searching for the EXE..." -ForegroundColor Yellow

    $exeCandidates = @(
        "$env:LOCALAPPDATA\Programs\Antigravity IDE\Antigravity IDE.exe",
        "$env:LOCALAPPDATA\Programs\Antigravity\Antigravity.exe",
        "$env:ProgramFiles\Antigravity IDE\Antigravity IDE.exe"
    )
    $exePath = $exeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

    if ($exePath) {
        Write-Host "  Found EXE: $exePath" -ForegroundColor Green
        $desktopLnk = Join-Path ([System.Environment]::GetFolderPath("Desktop")) "Antigravity IDE (Debug).lnk"
        $oneDriveDesktop = "$env:USERPROFILE\OneDrive\Desktop"
        if (Test-Path $oneDriveDesktop) {
            $desktopLnk = Join-Path $oneDriveDesktop "Antigravity IDE (Debug).lnk"
        }

        if ($WhatIf) {
            Write-Host "  [WHATIF] Would create shortcut: $desktopLnk" -ForegroundColor Yellow
            Write-Host "           Target: $exePath"
            Write-Host "           Arguments: $cdpFlag"
        } else {
            $sc                  = $shell.CreateShortcut($desktopLnk)
            $sc.TargetPath       = $exePath
            $sc.Arguments        = $cdpFlag
            $sc.WorkingDirectory = Split-Path $exePath
            $sc.Description      = "Antigravity IDE with CDP remote debugging enabled"
            $sc.Save()
            Write-Host "  [CREATED] $desktopLnk" -ForegroundColor Green
            Write-Host "            Arguments: $cdpFlag"
            Write-Host ""
            Write-Host "Done! Launch Antigravity via the new shortcut on your Desktop," -ForegroundColor Cyan
            Write-Host "then start the notifier:"
            Write-Host '  python notifier/notifier.py'
        }
    } else {
        Write-Host "  Antigravity EXE not found in common locations." -ForegroundColor Red
        Write-Host "  Add the flag manually to the shortcut Target field:"
        Write-Host "    $cdpFlag"
    }
} elseif ($patched -gt 0 -and -not $WhatIf) {
    Write-Host "Done! Re-launch Antigravity via the patched shortcut, then start the notifier:" -ForegroundColor Cyan
    Write-Host '  python notifier/notifier.py'
}
