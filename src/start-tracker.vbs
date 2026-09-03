''' start-tracker.vbs
''' Launches the Antigravity Quota Tracker for dev mode.
'''
''' Uses ShellExecute (via Shell.Application) instead of WScript.Shell.Run.
''' ShellExecute creates a truly independent process — it is spawned by the
''' Windows shell (explorer.exe) as its parent, NOT by wscript.exe.
''' This means it survives when wscript.exe exits and is NOT tied to the
''' PowerShell or terminal session that started this .vbs.

Dim fso, oShell, sSrcDir, sPythonw, sMain

Set fso = CreateObject("Scripting.FileSystemObject")

' This .vbs lives in src/ — resolve paths relative to it
sSrcDir  = fso.GetParentFolderName(WScript.ScriptFullName)
sPythonw = fso.GetAbsolutePathName(sSrcDir & "\..\.venv\Scripts\pythonw.exe")
sMain    = sSrcDir & "\main.py"

' Use Shell.Application.ShellExecute — parent becomes explorer.exe, NOT wscript.exe.
' This is the correct way to launch a persistent background process from VBScript.
Set oShell = CreateObject("Shell.Application")
' ShellExecute(sFile, sParams, sDir, sOp, nShow)
'   nShow = 0 means SW_HIDE (hidden window)
oShell.ShellExecute sPythonw, Chr(34) & sMain & Chr(34), sSrcDir, "open", 0
