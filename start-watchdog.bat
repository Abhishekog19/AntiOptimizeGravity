@echo off
taskkill /F /IM pythonw.exe /FI COMMANDLINE*watchdog* >nul 2>&1
start "" /B pythonw "%~dp0watchdog.py"
echo Watchdog started.
timeout /t 2 >nul
