; FaceFrame installer (Inno Setup 6). Built by installer\build_installer.ps1 from the staged folder.
#define AppName "FaceFrame"
#ifndef AppVersion
  #define AppVersion "1.1.0"
#endif
#ifndef StageDir
  #define StageDir "..\build\stage"
#endif

[Setup]
AppId={{6F1C2E4A-9B7D-4C1E-A3F2-FACEF7A3E011}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Roy Mutwiri
AppPublisherURL=https://github.com/Roy-Mutwiri/Twitch-Face-Card
AppSupportURL=https://github.com/Roy-Mutwiri/Twitch-Face-Card/issues
AppUpdatesURL=https://github.com/Roy-Mutwiri/Twitch-Face-Card/releases
DefaultDirName={localappdata}\Programs\FaceFrame
DefaultGroupName=FaceFrame
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.18362
OutputDir=..\dist
OutputBaseFilename=FaceFrame-Setup-{#AppVersion}
SetupIconFile=assets\faceframe.ico
UninstallDisplayIcon={app}\faceframe.ico
WizardStyle=modern
WizardImageFile=assets\wizard.bmp,assets\wizard_250.bmp
WizardSmallImageFile=assets\wizard_small.bmp,assets\wizard_small_250.bmp
Compression=lzma2/ultra64
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoDescription=FaceFrame installer
InfoBeforeFile=assets\before_install.txt

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "cpuonly"; Description: "Skip the GPU AI runtime (smaller download; Fast mode only)"; GroupDescription: "Options:"; Flags: unchecked

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\FaceFrame"; Filename: "{app}\FaceFrame.exe"; IconFilename: "{app}\faceframe.ico"; Comment: "Automatic webcam framing for TikTok LIVE Studio"
Name: "{group}\Repair FaceFrame"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\bootstrap.ps1"""; WorkingDir: "{app}"; IconFilename: "{app}\faceframe.ico"
Name: "{group}\FaceFrame log folder"; Filename: "{userappdata}\FaceFrame"
Name: "{group}\Uninstall FaceFrame"; Filename: "{uninstallexe}"
Name: "{autodesktop}\FaceFrame"; Filename: "{app}\FaceFrame.exe"; IconFilename: "{app}\faceframe.ico"; Tasks: desktopicon

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\bootstrap.ps1"" -Quiet"; WorkingDir: "{app}"; StatusMsg: "Installing components (Python, AI runtime, models). A setup window shows the progress - this can take several minutes..."; Flags: waituntilterminated; Tasks: not cpuonly
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\bootstrap.ps1"" -Quiet -CpuOnly"; WorkingDir: "{app}"; StatusMsg: "Installing components (Python, models). A setup window shows the progress..."; Flags: waituntilterminated; Tasks: cpuonly
Filename: "{app}\FaceFrame.exe"; Description: "Launch FaceFrame"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\faceframe"
Type: files; Name: "{app}\installed.flag"
Type: files; Name: "{app}\install.log"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and (not FileExists(ExpandConstant('{app}\installed.flag'))) then
    SuppressibleMsgBox('Some components could not be installed. Check your internet connection, then run ' +
           '"Repair FaceFrame" from the Start menu. Details: ' + ExpandConstant('{app}\install.log'),
           mbError, MB_OK, IDOK);
end;
