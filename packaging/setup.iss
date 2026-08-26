; packaging/setup.iss
; Wraps dist/ShopAnalysis/ (Task 6's PyInstaller onedir output) into a
; normal Windows installer wizard. No terminal, no visible Python, ever -
; just Next, Next, Install, and a desktop shortcut.

#define MyAppName "Shop Analysis"
#define MyAppExeName "ShopAnalysis.exe"

[Setup]
AppName={#MyAppName}
AppVersion=1.0.0
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist-installer
OutputBaseFilename=Setup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\ShopAnalysis\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Open Shop Analysis now"; Flags: nowait postinstall skipifsilent
