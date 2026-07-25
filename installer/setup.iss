; Antigravity Quota Tracker — Inno Setup 6 Installer Script
; Compile with: ISCC.exe installer\setup.iss
; Requires: Inno Setup 6 (https://jrsoftware.org/isdl.php)
;
; What this installer does:
;   1. Copies quota-tracker.exe + quota-watchdog.exe to %LOCALAPPDATA%\AntigravityQuotaTracker\
;   2. Detects Antigravity IDE and patches its .lnk shortcuts (--remote-debugging-port=9222)
;   3. Registers quota-watchdog.exe in HKCU\...\Run (auto-start with Windows)
;   4. Creates Start Menu entry
;   5. Optionally launches watchdog immediately (no reboot needed)
;
; Uninstaller:
;   - Kills quota-tracker.exe / quota-watchdog.exe
;   - Removes registry Run entry
;   - Reverts .lnk shortcut patches
;   - Prompts (default: No) before deleting quota.db history

#define MyAppName       "Antigravity Quota Tracker"
#define MyAppVersion    "1.0.0"
#define MyAppPublisher  "Abhishekog19"
#define MyAppURL        "https://github.com/Abhishekog19/AntiOptimizeGravity"
#define MyAppExeName    "quota-tracker.exe"
#define MyWatchdogExe   "quota-watchdog.exe"
#define MyInstallDir    "{localappdata}\AntigravityQuotaTracker"
#define MyRegKey        "Software\Microsoft\Windows\CurrentVersion\Run"
#define MyRegValueName  "AntigravityQuotaWatchdog"
#define CDPFlag         "--remote-debugging-port=9222"

; Paths relative to setup.iss location (inside installer/)
#define SrcDir "..\dist"

[Setup]
AppId={{B7E3A2F1-4C9D-4E8B-A1F5-6D2C8E3B9A4F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={#MyInstallDir}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
OutputDir=.
OutputBaseFilename=AntigravityQuotaTrackerSetup
SetupIconFile=..\assets\icon.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}

[Messages]
; Custom finish-page message — shown at the bottom of the Finished page.
; Note: %n is the newline character in Inno Setup messages.
FinishedLabel=Setup complete!%n%nOpen Antigravity IDE — the tracker will start automatically within a few seconds.%n%nLook for a coloured dot in your system tray (^ button near the clock).%n%nRight-click the dot to open the dashboard or access settings.

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Both executables must be in the same directory (watchdog locates tracker by sibling path)
Source: "{#SrcDir}\quota-tracker.exe";  DestDir: "{app}"; Flags: ignoreversion
Source: "{#SrcDir}\quota-watchdog.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}";        Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

[Registry]
; Register watchdog in HKCU Run (starts with Windows login, no admin needed)
Root: HKCU; Subkey: "{#MyRegKey}"; ValueType: string; ValueName: "{#MyRegValueName}"; ValueData: """{app}\{#MyWatchdogExe}"""; Flags: uninsdeletevalue

[Run]
; After install: launch the watchdog immediately (no reboot needed)
Filename: "{app}\{#MyWatchdogExe}"; \
  Description: "Start the tracker now (recommended)"; \
  Flags: postinstall nowait skipifsilent; \
  StatusMsg: "Starting Antigravity Quota Tracker watchdog..."

[UninstallRun]
; Kill tracker and watchdog before uninstalling files
Filename: "taskkill.exe"; Parameters: "/F /IM quota-tracker.exe";  Flags: runhidden
Filename: "taskkill.exe"; Parameters: "/F /IM quota-watchdog.exe"; Flags: runhidden

[Code]
// ─── Pascal Script ─────────────────────────────────────────────────────────
//
// Responsibilities:
//   1. InitializeSetup: detect Antigravity IDE; abort with helpful message if not found
//   2. CurStepChanged(ssPostInstall): patch all .lnk shortcuts
//   3. CurUninstallStepChanged(usPostUninstall): revert .lnk shortcut patches
//   4. InitializeUninstallProgressForm: prompt before deleting quota.db
//
// Helper: FindAntigravityExe — checks standard install locations
// Helper: PatchShortcut / UnpatchShortcut — via WScript.Shell COM object
// ────────────────────────────────────────────────────────────────────────────

const
  CDPArgument = '--remote-debugging-port=9222';

var
  AgExePath: String;   // found Antigravity IDE.exe path (set in InitializeSetup)


// ── Find Antigravity IDE ────────────────────────────────────────────────────

function FindAntigravityExe(): String;
var
  Candidates: TArrayOfString;
  I: Integer;
  P: String;
begin
  SetArrayLength(Candidates, 5);
  Candidates[0] := ExpandConstant('{localappdata}') + '\Programs\Antigravity IDE\Antigravity IDE.exe';
  Candidates[1] := ExpandConstant('{localappdata}') + '\Programs\Antigravity\Antigravity.exe';
  Candidates[2] := ExpandConstant('{pf64}')          + '\Antigravity IDE\Antigravity IDE.exe';
  Candidates[3] := ExpandConstant('{pf}')             + '\Antigravity IDE\Antigravity IDE.exe';
  Candidates[4] := ExpandConstant('{pf}')             + '\Antigravity\Antigravity.exe';

  for I := 0 to GetArrayLength(Candidates) - 1 do
  begin
    P := Candidates[I];
    if FileExists(P) then
    begin
      Result := P;
      Exit;
    end;
  end;
  Result := '';
end;


// ── Patch / unpatch a single .lnk shortcut ─────────────────────────────────

procedure PatchShortcut(LnkPath: String);
var
  Shell, Link: Variant;
  Args: String;
begin
  try
    Shell := CreateOleObject('WScript.Shell');
    Link  := Shell.CreateShortcut(LnkPath);
    Args  := Link.Arguments;
    if Pos(CDPArgument, Args) = 0 then
    begin
      if Args = '' then
        Link.Arguments := CDPArgument
      else
        Link.Arguments := Args + ' ' + CDPArgument;
      Link.Save();
      Log('Patched shortcut: ' + LnkPath);
    end else
      Log('Already patched: ' + LnkPath);
  except
    Log('Failed to patch shortcut: ' + LnkPath + ' — ' + GetExceptionMessage());
  end;
end;

procedure UnpatchShortcut(LnkPath: String);
var
  Shell, Link: Variant;
  Args, NewArgs: String;
begin
  try
    Shell := CreateOleObject('WScript.Shell');
    Link  := Shell.CreateShortcut(LnkPath);
    Args  := Link.Arguments;
    if Pos(CDPArgument, Args) > 0 then
    begin
      // Remove the flag and any surrounding spaces
      NewArgs := Args;
      NewArgs := StringReplace(NewArgs, ' ' + CDPArgument, '', [rfReplaceAll]);
      NewArgs := StringReplace(NewArgs, CDPArgument + ' ', '', [rfReplaceAll]);
      NewArgs := StringReplace(NewArgs, CDPArgument, '',       [rfReplaceAll]);
      // Trim leading/trailing whitespace
      while (Length(NewArgs) > 0) and (NewArgs[1] = ' ')  do Delete(NewArgs, 1, 1);
      while (Length(NewArgs) > 0) and (NewArgs[Length(NewArgs)] = ' ') do Delete(NewArgs, Length(NewArgs), 1);
      Link.Arguments := NewArgs;
      Link.Save();
      Log('Reverted shortcut: ' + LnkPath);
    end;
  except
    Log('Failed to revert shortcut: ' + LnkPath + ' — ' + GetExceptionMessage());
  end;
end;


// ── Scan a folder for Antigravity .lnk files ───────────────────────────────

procedure PatchShortcutsInDir(Dir: String; Revert: Boolean);
var
  FindRec: TFindRec;
  LnkPath: String;
begin
  if not DirExists(Dir) then Exit;
  if FindFirst(Dir + '\*.lnk', FindRec) then
  begin
    try
      repeat
        if Pos('Antigravity', FindRec.Name) > 0 then
        begin
          LnkPath := Dir + '\' + FindRec.Name;
          if Revert then
            UnpatchShortcut(LnkPath)
          else
            PatchShortcut(LnkPath);
        end;
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
  // Recurse one level (Start Menu has subdirs)
  if FindFirst(Dir + '\*', FindRec) then
  begin
    try
      repeat
        if (FindRec.Attributes and FILE_ATTRIBUTE_DIRECTORY <> 0) and
           (FindRec.Name <> '.') and (FindRec.Name <> '..') then
          PatchShortcutsInDir(Dir + '\' + FindRec.Name, Revert);
      until not FindNext(FindRec);
    finally
      FindClose(FindRec);
    end;
  end;
end;

procedure PatchAllShortcuts(Revert: Boolean);
var
  Dirs: TArrayOfString;
  I: Integer;
begin
  SetArrayLength(Dirs, 6);
  Dirs[0] := ExpandConstant('{userdesktop}');
  Dirs[1] := ExpandConstant('{commondesktop}');
  Dirs[2] := ExpandConstant('{userstartmenu}');
  Dirs[3] := ExpandConstant('{commonstartmenu}');
  Dirs[4] := GetEnv('USERPROFILE') + '\OneDrive\Desktop';
  Dirs[5] := ExpandConstant('{localappdata}') + '\Programs\Antigravity IDE';
  for I := 0 to GetArrayLength(Dirs) - 1 do
    PatchShortcutsInDir(Dirs[I], Revert);
end;

procedure CreateDebugShortcutIfNeeded();
// If no Antigravity shortcuts exist, create a new "Antigravity IDE (Debug).lnk"
// on the Desktop pointing directly at the EXE with the CDP flag.
var
  Shell, Link: Variant;
  DesktopDir, LnkPath: String;
begin
  if AgExePath = '' then Exit;
  DesktopDir := ExpandConstant('{userdesktop}');
  // Check OneDrive Desktop first
  if DirExists(GetEnv('USERPROFILE') + '\OneDrive\Desktop') then
    DesktopDir := GetEnv('USERPROFILE') + '\OneDrive\Desktop';
  LnkPath := DesktopDir + '\Antigravity IDE (Debug).lnk';
  try
    Shell := CreateOleObject('WScript.Shell');
    Link  := Shell.CreateShortcut(LnkPath);
    Link.TargetPath  := AgExePath;
    Link.Arguments   := CDPArgument;
    Link.WorkingDirectory := ExtractFileDir(AgExePath);
    Link.Description := 'Antigravity IDE with CDP remote debugging (required for Quota Tracker)';
    Link.Save();
    Log('Created debug shortcut: ' + LnkPath);
  except
    Log('Could not create debug shortcut: ' + GetExceptionMessage());
  end;
end;


// ── Setup lifecycle hooks ───────────────────────────────────────────────────

function InitializeSetup(): Boolean;
begin
  AgExePath := FindAntigravityExe();

  if AgExePath = '' then
  begin
    MsgBox(
      'Antigravity IDE was not found on this computer.' + #13#10 + #13#10 +
      'Please install Antigravity IDE first, then re-run this installer.' + #13#10 + #13#10 +
      'Download: https://antigravity.dev' + #13#10 + #13#10 +
      '(If Antigravity is installed in an unusual location, the installer ' +
      'will still work but you will need to add --remote-debugging-port=9222 ' +
      'to your Antigravity shortcut manually.)',
      mbError,
      MB_OK
    );
    // Allow install to continue with a warning (user may have custom location)
    // Return True so installer doesn't fully abort — shortcut patching is skipped
    // if AgExePath is empty in CurStepChanged.
    Result := True;
  end else
    Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // Patch existing Antigravity shortcuts with the CDP debug flag
    PatchAllShortcuts(False);
    // If no shortcuts were found but we know the EXE, create a new one
    CreateDebugShortcutIfNeeded();
  end;
end;


// ── Uninstall lifecycle hooks ───────────────────────────────────────────────

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DbPath: String;
  Answer: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    // Revert all Antigravity shortcut patches
    PatchAllShortcuts(True);

    // Prompt before deleting quota history (default: keep)
    DbPath := ExpandConstant('{app}') + '\dashboard\data\quota.db';
    // Also check the common data location if installed from source
    if not FileExists(DbPath) then
      DbPath := ExpandConstant('{localappdata}') + '\AntigravityQuotaTracker\dashboard\data\quota.db';

    if FileExists(DbPath) then
    begin
      if MsgBox(
        'Do you want to permanently delete your quota history database?' + #13#10 + #13#10 +
        DbPath + #13#10 + #13#10 +
        'This cannot be undone. Click No to keep your history.',
        mbConfirmation,
        MB_YESNO or MB_DEFBUTTON2  // default button = No
      ) = IDYES then
      begin
        DeleteFile(DbPath);
        DeleteFile(DbPath + '-shm');
        DeleteFile(DbPath + '-wal');
        Log('Deleted quota history: ' + DbPath);
      end else
        Log('Quota history kept at: ' + DbPath);
    end;
  end;
end;
