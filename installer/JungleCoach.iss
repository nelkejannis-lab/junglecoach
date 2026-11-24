; Inno Setup script – builds JungleCoach-Setup.exe
#define AppName "Moosi"
#define AppVersion "1.8.0"
#define AppExe "JungleCoach.exe"

[Setup]
AppId={{6F1C2B4E-8A3D-4C9B-9E21-4D2A9F01C3B7}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Moosi
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UsePreviousGroup=no
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=JungleCoach-Setup
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "de"; MessagesFile: "compiler:Languages\German.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\JungleCoach\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; old name before the rename to Moosi (v1.8)
Type: filesandordirs; Name: "{autoprograms}\Jungle Coach"
Type: files; Name: "{autodesktop}\Jungle Coach.lnk"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

; User data (settings, statistics, clips) stays in %APPDATA%\JungleCoach and survives updates/uninstall.
