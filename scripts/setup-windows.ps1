# setup-windows.ps1 — Antigravity Quota Tracker
#
# Patches all Antigravity IDE shortcuts on this machine to include the
# --remote-debugging-port=9222 launch flag required by the notifier.
#
# Usage (from project root):
#   powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1 -WhatIf
#
# Flags:
#   -WhatIf   Dry-run — prints what would change without modifying anything.
#   -Port     CDP port number (default: 9222).

param(
    [switch]$WhatIf,
    [int]$Port = 9222
)

$ErrorActionPreference = "Stop"
$cdpFlag = "--remote-debugging-port=$Port"

# Locations to scan for .lnk shortcuts
$searchRoots = @(
    [System.Environment]::GetFolderPath("Desktop"),
    [System.Environment]::GetFolderPath("CommonDesktopDirectory"),
    [System.Environment]::GetFolderPath("StartMenu"),
    [System.Environment]::GetFolderPath("CommonStartMenu"),
    "$env:APPDATA\Microsoft\Windows\Start Menu",
    "$env:ProgramData\Microsoft\Windows\Start Menu"
)

$shell = New-Object -ComObject WScript.Shell

$found   = 0
$patched = 0
$already = 0
$failed  = 0

Write-Host ""
Write-Host "Antigravity Quota Tracker — Windows Setup" -ForegroundColor Cyan
Write-Host "Looking for Antigravity shortcuts in:"
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
            $sc   = $shell.CreateShortcut($lnk)
            $args = $sc.Arguments

            if ($args -match [regex]::Escape($cdpFlag)) {
                Write-Host "  [SKIP]    Already patched: $lnk" -ForegroundColor DarkGray
                $already++
            } else {
                $newArgs = if ($args) { "$args $cdpFlag" } else { $cdpFlag }
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
            Write-Host "  [ERROR]   Cannot patch $lnk : $_" -ForegroundColor Red
            $failed++
        }
    }
}

Write-Host ""
Write-Host "─────────────────────────────────────────────"
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
    Write-Host "No Antigravity shortcuts found." -ForegroundColor Yellow
    Write-Host "If Antigravity is installed, create a shortcut manually and re-run,"
    Write-Host "or add the flag directly to the shortcut's Target field:"
    Write-Host "  Right-click shortcut → Properties → Target → append: $cdpFlag"
}

if ($patched -gt 0 -and -not $WhatIf) {
    Write-Host "Done! Re-launch Antigravity via the patched shortcut, then start the notifier:" -ForegroundColor Cyan
    Write-Host "  python notifier\notifier.py"
}
