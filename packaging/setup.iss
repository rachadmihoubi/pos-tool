; packaging/setup.iss
; Wraps dist/ShopAnalysis/ (Task 6's PyInstaller onedir output) into a
; normal Windows installer wizard. No terminal, no visible Python, ever -
; just Next, Next, Install, and a desktop shortcut.

#define MyAppName "Shop Analysis"
#define MyAppExeName "ShopAnalysis.exe"

[Setup]
AppName={#MyAppName}
AppVersion=1.0.1
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

[UninstallRun]
Filename: "schtasks.exe"; Parameters: "/delete /f /tn ""Shop Analysis - Watcher"""; Flags: runhidden; RunOnceId: "DeleteWatcherTask"

[Code]
var
  DatabasePage: TWizardPage;
  DatabaseEdit: TNewEdit;
  DatabaseBrowseButton: TNewButton;
  DatabaseStatusLabel: TNewStaticText;

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
begin
  if CurStep = ssPostInstall then
  begin
    if Assigned(DatabaseEdit) and (DatabaseEdit.Text <> '') and FileExists(DatabaseEdit.Text) then
      WriteDatabaseConfig(DatabaseEdit.Text);

    if IsAdminInstallMode() then
      MsgBox(
        'Shop Analysis saves its settings under the current Windows user''s ' +
        'own account, and the background updater that keeps your numbers ' +
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
