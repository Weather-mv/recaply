; Inno Setup script for the Recaply Windows app (compiled by build/build.ps1).
; Installs for the current user only (no admin prompt) into
; %LOCALAPPDATA%\Programs\Recaply. Recaps and clips live in
; %LOCALAPPDATA%\Recaply\Library and are kept when the app is uninstalled,
; unless the user ticks the box to remove them.

#ifndef AppVersion
  #define AppVersion "2.1.0"
#endif

[Setup]
AppId={{6B8C0E0A-3F0B-4C89-9E57-2F7C2B5E4A11}
AppName=Recaply
AppVersion={#AppVersion}
AppVerName=Recaply {#AppVersion}
AppPublisher=Recaply
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Recaply
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=Recaply-Setup-{#AppVersion}
SetupIconFile=recaply.ico
UninstallDisplayIcon={app}\Recaply.exe
UninstallDisplayName=Recaply
Compression=lzma2/ultra64
SolidCompression=yes
; the 64-bit compressor in its own process (the built-in one runs out of memory on ~800 MB)
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=2
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "startup"; Description: "Start Recaply in the tray when Windows starts (for the web app)"; GroupDescription: "Web app:"; Flags: unchecked

[Registry]
; recaply:// links: the website's "Start Recaply" button starts the app in the tray for the web app.
Root: HKCU; Subkey: "Software\Classes\recaply"; ValueType: string; ValueName: ""; ValueData: "URL:Recaply"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\recaply"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\recaply\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: """{app}\Recaply.exe"",0"
Root: HKCU; Subkey: "Software\Classes\recaply\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\Recaply.exe"" --helper"
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Recaply"; ValueData: """{app}\Recaply.exe"" --helper --quiet"; Tasks: startup; Flags: uninsdeletevalue

[Files]
Source: "..\dist\Recaply\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\Recaply"; Filename: "{app}\Recaply.exe"
Name: "{userdesktop}\Recaply"; Filename: "{app}\Recaply.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Recaply.exe"; Description: "Open Recaply"; Flags: nowait postinstall skipifsilent

[Code]
var
  RemoveData: Boolean;

function InitializeUninstall(): Boolean;
begin
  // Silent uninstalls (/SUPPRESSMSGBOXES) answer No: the user's videos are kept.
  RemoveData := SuppressibleMsgBox('Also delete your recaps, clips, reels and editor projects?' + #13#10 + #13#10 +
    'They are in ' + ExpandConstant('{localappdata}\Recaply') + '. Choose No to keep them.',
    mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and RemoveData then
    DelTree(ExpandConstant('{localappdata}\Recaply'), True, True, True);
end;
