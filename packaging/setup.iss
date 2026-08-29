; packaging/setup.iss
; Wraps dist/ShopAnalysis/ (Task 6's PyInstaller onedir output) into a
; normal Windows installer wizard. No terminal, no visible Python, ever -
; just Next, Next, Install, and a desktop shortcut.

#define MyAppName "Shop Analysis"
#define MyAppExeName "ShopAnalysis.exe"

[Setup]
AppName={#MyAppName}
AppVersion=1.0.4
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist-installer
OutputBaseFilename=Setup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=force
CloseApplicationsFilter={#MyAppExeName}
RestartApplications=no

[Files]
Source: "..\dist\ShopAnalysis\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Open Shop Analysis now"; Flags: nowait postinstall skipifsilent
Filename: "schtasks.exe"; Parameters: "/create /f /tn ""Shop Analysis - Watcher"" /tr ""\""{app}\{#MyAppExeName}\"" --watcher"" /sc onlogon /rl limited /delay 0000:30"; Flags: runhidden
Filename: "{app}\{#MyAppExeName}"; Parameters: "--watcher"; Flags: nowait runhidden
; Second, narrow, always-elevated helper task - the watcher above stays
; deliberately de-elevated (least privilege), but the silent auto-update it
; may find needs admin rights to install into Program Files. Running this
; one as SYSTEM means it never hits an interactive UAC prompt (same
; mechanism Windows' own built-in SilentCleanup task relies on) and needs no
; stored password. --data-dir is this (elevated, installing) user's own
; %LOCALAPPDATA%, captured now because SYSTEM's own %LOCALAPPDATA% is not
; the shop's - see docs/superpowers/specs/2026-08-27-update-elevation-fix.md.
Filename: "schtasks.exe"; Parameters: "/create /f /tn ""Shop Analysis - Updater"" /tr ""\""{app}\{#MyAppExeName}\"" --apply-update --data-dir \""{localappdata}\Shop Analysis\"""" /sc onlogon /rl highest /ru SYSTEM /delay 0000:45"; Flags: runhidden

[UninstallRun]
; Runs before file removal (Inno's default [UninstallRun] ordering). Without
; this, a still-running --watcher (or an elevated --apply-update mid-flight)
; holds some .pyd/.dll files open, so the file-removal pass that follows
; leaves a partial {app} behind - found during this fix's own real-machine
; verification (see docs/superpowers/specs/2026-08-27-update-elevation-fix.md).
; Exit code is ignored if the process isn't running, same as the schtasks
; /delete lines below already tolerate a missing task.
Filename: "taskkill.exe"; Parameters: "/F /IM ""{#MyAppExeName}"""; Flags: runhidden; RunOnceId: "KillRunningApp"
Filename: "schtasks.exe"; Parameters: "/delete /f /tn ""Shop Analysis - Watcher"""; Flags: runhidden; RunOnceId: "DeleteWatcherTask"
Filename: "schtasks.exe"; Parameters: "/delete /f /tn ""Shop Analysis - Updater"""; Flags: runhidden; RunOnceId: "DeleteUpdaterTask"

[Code]
var
  DatabasePage: TWizardPage;
  DatabaseEdit: TNewEdit;
  DatabaseBrowseButton: TNewButton;
  DatabaseStatusLabel: TNewStaticText;
  CloudflarePage: TWizardPage;
  CloudflareTokenEdit: TPasswordEdit;
  CloudflareAccountIdEdit: TNewEdit;
  CloudflareSlugEdit: TNewEdit;
  CloudflareOwnerEmailEdit: TNewEdit;
  CloudflareHintLabel: TNewStaticText;
  CloudflareAccountIdLabel: TNewStaticText;
  CloudflareSlugLabel: TNewStaticText;
  CloudflareOwnerEmailLabel: TNewStaticText;

function SetEnvironmentVariableW(lpName, lpValue: String): Boolean;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

// Best-effort only - there is no confirmed single "standard" R.Lynx
// install location (this dev PC's own real database lives on the
// Desktop, not under Program Files), so this is a short list of
// plausible folders, not a guarantee. The Browse button is always the
// reliable path.
function DetectDatabasePath(): String;
var
  Candidates: TArrayOfString;
  I: Integer;
  FindRec: TFindRec;
  Folder: String;
begin
  Result := '';
  SetArrayLength(Candidates, 5);
  Candidates[0] := ExpandConstant('{commonpf}') + '\R.Lynx';
  Candidates[1] := ExpandConstant('{commonpf32}') + '\R.Lynx';
  Candidates[2] := 'C:\R.Lynx';
  Candidates[3] := ExpandConstant('{userdesktop}');
  Candidates[4] := ExpandConstant('{userdocs}');

  for I := 0 to GetArrayLength(Candidates) - 1 do
  begin
    Folder := Candidates[I];
    if DirExists(Folder) then
    begin
      if FindFirst(Folder + '\*.dblx', FindRec) then
      begin
        try
          Result := Folder + '\' + FindRec.Name;
          Exit;
        finally
          FindClose(FindRec);
        end;
      end;
    end;
  end;
end;

procedure UpdateDatabaseStatusLabel();
begin
  if DatabaseEdit.Text = '' then
    DatabaseStatusLabel.Caption :=
      'No database file selected yet. Click Browse and pick the file your ' +
      'point-of-sale software saves to.'
  else
    DatabaseStatusLabel.Caption := 'Using: ' + DatabaseEdit.Text;
end;

procedure DatabaseBrowseButtonClick(Sender: TObject);
var
  FileName: String;
begin
  FileName := DatabaseEdit.Text;
  if GetOpenFileName('Select your point-of-sale database file', FileName,
     ExtractFileDir(FileName), 'Database files (*.dblx)|*.dblx|All files (*.*)|*.*',
     'dblx') then
  begin
    DatabaseEdit.Text := FileName;
    UpdateDatabaseStatusLabel();
  end;
end;

procedure InitializeWizard();
begin
  DatabasePage := CreateCustomPage(wpSelectDir,
    'Locate your point-of-sale database',
    'Shop Analysis reads from this file to build your dashboard.');

  DatabaseStatusLabel := TNewStaticText.Create(DatabasePage);
  DatabaseStatusLabel.Parent := DatabasePage.Surface;
  DatabaseStatusLabel.Left := 0;
  DatabaseStatusLabel.Top := 0;
  DatabaseStatusLabel.Width := DatabasePage.SurfaceWidth;
  DatabaseStatusLabel.AutoSize := False;
  DatabaseStatusLabel.WordWrap := True;
  DatabaseStatusLabel.Height := ScaleY(32);

  DatabaseEdit := TNewEdit.Create(DatabasePage);
  DatabaseEdit.Parent := DatabasePage.Surface;
  DatabaseEdit.Left := 0;
  DatabaseEdit.Top := DatabaseStatusLabel.Top + DatabaseStatusLabel.Height + ScaleY(8);
  DatabaseEdit.Width := DatabasePage.SurfaceWidth;
  DatabaseEdit.ReadOnly := True;

  DatabaseBrowseButton := TNewButton.Create(DatabasePage);
  DatabaseBrowseButton.Parent := DatabasePage.Surface;
  DatabaseBrowseButton.Left := 0;
  DatabaseBrowseButton.Top := DatabaseEdit.Top + DatabaseEdit.Height + ScaleY(8);
  DatabaseBrowseButton.Width := ScaleX(100);
  DatabaseBrowseButton.Height := ScaleY(23);
  DatabaseBrowseButton.Caption := 'Browse...';
  DatabaseBrowseButton.OnClick := @DatabaseBrowseButtonClick;

  CloudflarePage := CreateCustomPage(DatabasePage.ID,
    'Cloudflare remote setup (optional)',
    'Only fill this in when setting up a brand-new store for the first ' +
    'time, and only while installing as the till PC''s own day-to-day ' +
    'Windows user (not a separate admin-only account) - these settings ' +
    'are saved to that user''s own profile, the same account the ' +
    'watcher runs under. Leave the token blank for an ordinary install ' +
    'or update.');

  CloudflareHintLabel := TNewStaticText.Create(CloudflarePage);
  CloudflareHintLabel.Parent := CloudflarePage.Surface;
  CloudflareHintLabel.Left := 0;
  CloudflareHintLabel.Top := 0;
  CloudflareHintLabel.Width := CloudflarePage.SurfaceWidth;
  CloudflareHintLabel.AutoSize := False;
  CloudflareHintLabel.WordWrap := True;
  CloudflareHintLabel.Height := ScaleY(32);
  CloudflareHintLabel.Caption :=
    'One-time provisioning token (leave blank to skip this step entirely):';

  CloudflareTokenEdit := TPasswordEdit.Create(CloudflarePage);
  CloudflareTokenEdit.Parent := CloudflarePage.Surface;
  CloudflareTokenEdit.Left := 0;
  CloudflareTokenEdit.Top := CloudflareHintLabel.Top + CloudflareHintLabel.Height + ScaleY(4);
  CloudflareTokenEdit.Width := CloudflarePage.SurfaceWidth;

  CloudflareAccountIdLabel := TNewStaticText.Create(CloudflarePage);
  CloudflareAccountIdLabel.Parent := CloudflarePage.Surface;
  CloudflareAccountIdLabel.Left := 0;
  CloudflareAccountIdLabel.Top := CloudflareTokenEdit.Top + CloudflareTokenEdit.Height + ScaleY(8);
  CloudflareAccountIdLabel.Width := CloudflarePage.SurfaceWidth;
  CloudflareAccountIdLabel.Caption := 'Cloudflare account ID:';

  CloudflareAccountIdEdit := TNewEdit.Create(CloudflarePage);
  CloudflareAccountIdEdit.Parent := CloudflarePage.Surface;
  CloudflareAccountIdEdit.Left := 0;
  CloudflareAccountIdEdit.Top := CloudflareAccountIdLabel.Top + CloudflareAccountIdLabel.Height + ScaleY(2);
  CloudflareAccountIdEdit.Width := CloudflarePage.SurfaceWidth;

  CloudflareSlugLabel := TNewStaticText.Create(CloudflarePage);
  CloudflareSlugLabel.Parent := CloudflarePage.Surface;
  CloudflareSlugLabel.Left := 0;
  CloudflareSlugLabel.Top := CloudflareAccountIdEdit.Top + CloudflareAccountIdEdit.Height + ScaleY(8);
  CloudflareSlugLabel.Width := CloudflarePage.SurfaceWidth;
  CloudflareSlugLabel.Caption := 'New store project name (lowercase letters, digits, hyphens only, e.g. "storeb-pos"):';

  CloudflareSlugEdit := TNewEdit.Create(CloudflarePage);
  CloudflareSlugEdit.Parent := CloudflarePage.Surface;
  CloudflareSlugEdit.Left := 0;
  CloudflareSlugEdit.Top := CloudflareSlugLabel.Top + CloudflareSlugLabel.Height + ScaleY(2);
  CloudflareSlugEdit.Width := CloudflarePage.SurfaceWidth;

  CloudflareOwnerEmailLabel := TNewStaticText.Create(CloudflarePage);
  CloudflareOwnerEmailLabel.Parent := CloudflarePage.Surface;
  CloudflareOwnerEmailLabel.Left := 0;
  CloudflareOwnerEmailLabel.Top := CloudflareSlugEdit.Top + CloudflareSlugEdit.Height + ScaleY(8);
  CloudflareOwnerEmailLabel.Width := CloudflarePage.SurfaceWidth;
  CloudflareOwnerEmailLabel.Caption := 'Owner''s email (the only account allowed to view this store remotely):';

  CloudflareOwnerEmailEdit := TNewEdit.Create(CloudflarePage);
  CloudflareOwnerEmailEdit.Parent := CloudflarePage.Surface;
  CloudflareOwnerEmailEdit.Left := 0;
  CloudflareOwnerEmailEdit.Top := CloudflareOwnerEmailLabel.Top + CloudflareOwnerEmailLabel.Height + ScaleY(2);
  CloudflareOwnerEmailEdit.Width := CloudflarePage.SurfaceWidth;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = DatabasePage.ID then
  begin
    if DatabaseEdit.Text = '' then
      DatabaseEdit.Text := DetectDatabasePath();
    UpdateDatabaseStatusLabel();
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = DatabasePage.ID then
  begin
    if (DatabaseEdit.Text = '') or (not FileExists(DatabaseEdit.Text)) then
    begin
      MsgBox('Please click Browse and select your point-of-sale database file ' +
             '(it ends in .dblx) before continuing.', mbError, MB_OK);
      Result := False;
    end;
  end;

  if CurPageID = CloudflarePage.ID then
  begin
    if CloudflareTokenEdit.Text <> '' then
    begin
      if (CloudflareAccountIdEdit.Text = '') or (CloudflareSlugEdit.Text = '') or
         (CloudflareOwnerEmailEdit.Text = '') then
      begin
        MsgBox('A provisioning token was entered, so the account ID, project ' +
               'name, and owner email are all required too.', mbError, MB_OK);
        Result := False;
      end
      else if (Pos('"', CloudflareAccountIdEdit.Text) > 0) or
              (Pos('"', CloudflareSlugEdit.Text) > 0) or
              (Pos('"', CloudflareOwnerEmailEdit.Text) > 0) then
      begin
        // These three values are interpolated unescaped into a command
        // line in Step 3 below - a literal " would let its contents break
        // out of the quoted argument it's meant to stay inside.
        MsgBox('The account ID, project name, and owner email cannot ' +
               'contain a " character.', mbError, MB_OK);
        Result := False;
      end;
    end;
  end;
end;

// FileExists alone isn't enough: watcher.py can create a placeholder
// config.yaml (still containing the CHANGE-ME sentinel path) entirely on
// its own before the installer ever runs. Treat that placeholder as "not
// configured yet" so the installer can still patch a real path into it.
function ConfigIsConfigured(): Boolean;
var
  ConfigFile: String;
  Lines: TArrayOfString;
  I: Integer;
begin
  Result := False;
  ConfigFile := ExpandConstant('{localappdata}\Shop Analysis\config.yaml');
  if not FileExists(ConfigFile) then
    Exit;
  Result := True;
  if LoadStringsFromFile(ConfigFile, Lines) then
  begin
    for I := 0 to GetArrayLength(Lines) - 1 do
    begin
      if Pos('CHANGE-ME/point-this-at-your-database.dblx', Lines[I]) > 0 then
      begin
        Result := False;
        Exit;
      end;
    end;
  end;
end;

procedure WriteDatabaseConfig(const DBPath: String);
var
  ConfigDir, ConfigFile, TemplateFile, EscapedPath: String;
  Lines: TArrayOfString;
  I: Integer;
begin
  ConfigDir := ExpandConstant('{localappdata}\Shop Analysis');
  ConfigFile := ConfigDir + '\config.yaml';
  TemplateFile := ExpandConstant('{app}\config.template.yaml');

  if ConfigIsConfigured() then
    Exit; // never overwrite an existing, already-configured store

  if not FileExists(ConfigFile) then
  begin
    ForceDirectories(ConfigDir);

    if not FileCopy(TemplateFile, ConfigFile, False) then
      Exit; // nothing more we can do here; app's own bootstrap remains the fallback
  end;

  EscapedPath := DBPath;
  StringChangeEx(EscapedPath, '\', '/', True);

  if LoadStringsFromFile(ConfigFile, Lines) then
  begin
    for I := 0 to GetArrayLength(Lines) - 1 do
    begin
      if Pos('CHANGE-ME/point-this-at-your-database.dblx', Lines[I]) > 0 then
        Lines[I] := '  path: "' + EscapedPath + '"';
    end;
    SaveStringsToUTF8FileWithoutBOM(ConfigFile, Lines, False);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  ProvisionOutput: TExecOutput;
  ProvisionMessage: String;
  I: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    if Assigned(DatabaseEdit) and (DatabaseEdit.Text <> '') and FileExists(DatabaseEdit.Text) then
      WriteDatabaseConfig(DatabaseEdit.Text);

    if Assigned(CloudflareTokenEdit) and (CloudflareTokenEdit.Text <> '') then
    begin
      // Stop the watcher FIRST, before provisioning starts - not after.
      // provision_store() writes a placeholder site and pushes it to the
      // new Pages project *before* the Access apps that gate it exist
      // (poslib/provision.py's create_broad_access_app/create_bypass_access_app
      // run after the first push_remote). If this is a re-provisioning run
      // on a store whose watcher is already live with remote.enabled: true
      // cached (Watcher.__init__ reads config once, see watcher.py:61/213),
      // that watcher could fire its own export+push into the same project
      // during that ungated window. Killing it first closes that window
      // entirely, and also removes any question about a hard taskkill
      // landing mid-write to cache.db or the export directory.
      Exec('taskkill.exe', '/F /IM "{#MyAppExeName}"', '', SW_HIDE,
           ewWaitUntilTerminated, ResultCode);

      // verify_reachable() alone can take several minutes (Cloudflare
      // Access propagation delay) - an operator watching a seemingly-hung
      // installer with no feedback is likely to kill it, which would leave
      // a minted-but-orphaned watcher token (a second run can't re-mint
      // under the same name - see provision_store's duplicate-token guard)
      // and DirtyName possibly-created-but-unverified Access apps.
      WizardForm.StatusLabel.Caption :=
        'Setting up Cloudflare remote access - this can take a few minutes...';
      WizardForm.StatusLabel.Update;

      SetEnvironmentVariableW('POS_TOOL_PROVISION_TOKEN', CloudflareTokenEdit.Text);
      if ExecAndCaptureOutput(ExpandConstant('{app}\{#MyAppExeName}'),
         '--provision-cloudflare' +
         ' --account-id "' + CloudflareAccountIdEdit.Text + '"' +
         ' --project-slug "' + CloudflareSlugEdit.Text + '"' +
         ' --owner-email "' + CloudflareOwnerEmailEdit.Text + '"' +
         ' --data-dir "' + ExpandConstant('{localappdata}\Shop Analysis') + '"',
         '', SW_HIDE, ewWaitUntilTerminated, ResultCode, ProvisionOutput) then
      begin
        SetEnvironmentVariableW('POS_TOOL_PROVISION_TOKEN', '');
        ProvisionMessage := '';
        for I := 0 to GetArrayLength(ProvisionOutput.StdOut) - 1 do
          ProvisionMessage := ProvisionMessage + ProvisionOutput.StdOut[I] + #13#10;
        for I := 0 to GetArrayLength(ProvisionOutput.StdErr) - 1 do
          ProvisionMessage := ProvisionMessage + ProvisionOutput.StdErr[I] + #13#10;
        if ResultCode = 0 then
          ProvisionMessage := 'Cloudflare setup finished:' + #13#10#13#10 + ProvisionMessage
        else
          ProvisionMessage := 'Cloudflare setup did not finish (exit code ' +
            IntToStr(ResultCode) + '):' + #13#10#13#10 + ProvisionMessage;
        ForceDirectories(ExpandConstant('{localappdata}\Shop Analysis'));
        SaveStringToFile(
          ExpandConstant('{localappdata}\Shop Analysis\cloudflare_provision_log.txt'),
          ProvisionMessage, False);
        if ResultCode <> 0 then
          MsgBox('Cloudflare setup did not finish successfully:' + #13#10#13#10 +
                 ProvisionMessage + #13#10#13#10 +
                 'Full details were also saved to cloudflare_provision_log.txt ' +
                 'in the app data folder.', mbError, MB_OK);
      end
      else
      begin
        SetEnvironmentVariableW('POS_TOOL_PROVISION_TOKEN', '');
        ForceDirectories(ExpandConstant('{localappdata}\Shop Analysis'));
        SaveStringToFile(
          ExpandConstant('{localappdata}\Shop Analysis\cloudflare_provision_log.txt'),
          'Could not launch Cloudflare setup at all.', False);
        MsgBox('Could not launch Cloudflare setup at all. See ' +
               'cloudflare_provision_log.txt in the app data folder.',
               mbError, MB_OK);
      end;

      WizardForm.StatusLabel.Caption := '';

      // Relaunch the watcher via its own scheduled task, not a direct Exec
      // of the exe. This installer process runs elevated (PrivilegesRequired
      // defaults to admin - no override in this file); Exec'ing the exe
      // directly would launch the watcher at the parent's own elevated
      // token, defeating the /rl limited least-privilege the [Run] section
      // above deliberately gives it. schtasks /run instead runs the task at
      // its own configured run level. Always relaunch here, success or
      // failure - it was killed above either way, and on failure the store
      // should keep serving whatever config it had before this run.
      Exec('schtasks.exe', '/run /tn "Shop Analysis - Watcher"', '', SW_HIDE,
           ewNoWait, ResultCode);
    end;

    if IsAdminInstallMode() then
      MsgBox(
        'Shop Analysis saves its settings under the current Windows user''s ' +
        'own account, and the background watcher that keeps your numbers ' +
        'current only starts automatically when that same person logs in.' + #13#10#13#10 +
        'If someone else normally uses this computer day to day (not the ' +
        'account you just installed as), please log off, log back in as ' +
        'that person, and run Setup.exe again. It is safe to run more than ' +
        'once.',
        mbInformation, MB_OK);
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if PageID = DatabasePage.ID then
    Result := ConfigIsConfigured();
end;
