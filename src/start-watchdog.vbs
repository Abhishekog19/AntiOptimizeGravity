''' start-watchdog.vbs
''' Launches the Antigravity Quota Tracker watchdog as a fully independent process.
''' Called by the Windows Startup shortcut and the desktop launcher.
'''
''' WScript.Shell.Run with windowStyle=0, bWaitOnReturn=False fires the process
''' as a detached child — it is NOT bound to this script's session and will
''' survive after wscript.exe exits and after any parent terminal closes.

Dim sPythonw, sWatchdog, sSrcDir, sCmd

' Compute paths relative to this .vbs file (which lives in src/)
sSrcDir  = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sPythonw = sSrcDir & "\..\venv\Scripts\pythonw.exe"
sWatchdog = sSrcDir & "\watchdog.py"

' Normalise the pythonw path (remove ../)
Dim fso
Set fso = CreateObject("Scripting.FileSystemObject")

' Use .venv (dot-venv) — the actual venv folder name
sPythonw = sSrcDir & "\..\.venv\Scripts\pythonw.exe"
sPythonw = fso.GetAbsolutePathName(sPythonw)

sCmd = Chr(34) & sPythonw & Chr(34) & " " & Chr(34) & sWatchdog & Chr(34)

Dim oShell
Set oShell = CreateObject("WScript.Shell")
' windowStyle=0 = hidden window, bWaitOnReturn=False = fire and forget
oShell.Run sCmd, 0, False
