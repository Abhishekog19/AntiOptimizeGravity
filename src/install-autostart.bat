@echo off
:: ============================================================================
:: install-autostart.bat
:: Registers the Antigravity Quota Tracker as a persistent background service
:: that starts automatically at Windows logon and survives IDE restarts.
::
:: Run this ONCE from a regular Command Prompt or PowerShell window.
:: No admin required.
:: ============================================================================

setlocal enabledelayedexpansion

set "HERE=%~dp0"
set "ROOT=%HERE%.."
set "VBS=%HERE%start-tracker.vbs"
set "WSCRIPT=%SystemRoot%\System32\wscript.exe"
set "TASKNAME=AntigravityQuotaTracker"

echo.
echo  Antigravity Quota Tracker - Auto-start Setup
echo  ============================================

:: Kill any existing tracker instances
echo  [1/4] Stopping existing tracker processes...
taskkill /F /IM pythonw.exe /T >nul 2>&1
timeout /t 1 /nobreak >nul

:: Register with Task Scheduler (user-level, no admin needed)
echo  [2/4] Registering scheduled task (runs at logon + now)...
schtasks /create /tn "%TASKNAME%" /tr "\"%WSCRIPT%\" \"%VBS%\"" /sc onlogon /f >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo  [WARN] Task Scheduler blocked - falling back to registry Run key...
    reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "%TASKNAME%" /t REG_SZ /d "\"%WSCRIPT%\" \"%VBS%\"" /f >nul
    echo  [OK]   Registry Run key set (fires at next Windows logon)
) else (
    echo  [OK]   Scheduled task registered
)

:: Also ensure registry Run key is set as backup
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "%TASKNAME%" /t REG_SZ /d "\"%WSCRIPT%\" \"%VBS%\"" /f >nul 2>&1

:: Start the tracker RIGHT NOW via wscript (ShellExecute → explorer.exe parent)
echo  [3/4] Starting tracker now...
start "" /B "%WSCRIPT%" "%VBS%"
timeout /t 8 /nobreak >nul

:: Verify
echo  [4/4] Verifying...
curl -s -o nul -w "%%{http_code}" http://localhost:4300/ > "%TEMP%\aqt_check.txt" 2>nul
set /p STATUS=<"%TEMP%\aqt_check.txt"
del "%TEMP%\aqt_check.txt" >nul 2>&1

if "%STATUS%"=="200" (
    echo.
    echo  SUCCESS - Tracker is running! Dashboard: http://localhost:4300
    echo  The tracker will now start automatically at every Windows logon.
    echo  No manual action needed after this.
    echo.
) else (
    echo.
    echo  [WARN] Dashboard not responding yet - tracker may need a few more seconds.
    echo  Check the system tray for the coloured dot icon.
    echo.
)

echo  Setup complete. This window will close in 5 seconds.
timeout /t 5 /nobreak >nul
endlocal
