#ifndef StageDir
  #error Canonical Runtime stage is required
#endif
#ifndef AppVersion
  #error Runtime version is required
#endif
#ifndef OutputDir
  #error Output directory is required
#endif
#define RuntimeExe "ORDAX Runtime.exe"

[Setup]
AppId={{B5845369-1D88-42D2-BD77-D0F73C5DB5AE}
AppName=OrdaX MCP
AppVersion={#AppVersion}
AppPublisher=ORDAX
DefaultDirName={localappdata}\Programs\OrdaX MCP
UsePreviousAppDir=no
DefaultGroupName=OrdaX MCP
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=OrdaX-MCP-Setup-{#AppVersion}-x64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
UninstallDisplayName=OrdaX MCP
UninstallDisplayIcon={app}\{#RuntimeExe}
VersionInfoVersion={#AppVersion}
VersionInfoCompany=ORDAX
VersionInfoDescription=OrdaX MCP standalone Runtime candidate

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Tasks]
Name: "desktopicon"; Description: "Criar atalho do OrdaX MCP na Área de Trabalho"; GroupDescription: "Atalhos:"

[Icons]
; Activation always targets the same singleton launcher and never enrolls or grants access.
Name: "{group}\\Iniciar ou reativar OrdaX MCP"; Filename: "{app}\\{#RuntimeExe}"; WorkingDir: "{app}"; IconFilename: "{app}\\{#RuntimeExe}"
; The desktop shortcut is an explicit installer choice, checked by default.
Name: "{userdesktop}\\OrdaX MCP"; Filename: "{app}\\runtime\\pythonw.exe"; Parameters: "-m ordax_dev_agent.owner_device_connect_gui --verificar-rede"; WorkingDir: "{app}"; IconFilename: "{app}\\{#RuntimeExe}"; Tasks: desktopicon
; This shortcut only opens the existing Owner PKCE enrollment flow in the
; packaged, private Python. It does not launch another Runtime or issue grants.
Name: "{group}\\Conectar dispositivo"; Filename: "{app}\\runtime\\pythonw.exe"; Parameters: "-m ordax_dev_agent.owner_device_connect_gui"; WorkingDir: "{app}"; IconFilename: "{app}\\{#RuntimeExe}"
; Owner chooses an exact pending session by native dialog. It reuses the
; authenticated canonical Owner selection API, never issuing grants or actions.
Name: "{group}\\Selecionar dispositivo para sessão"; Filename: "{app}\\runtime\\pythonw.exe"; Parameters: "-m ordax_dev_agent.owner_device_connect_gui --selecionar-sessao"; WorkingDir: "{app}"; IconFilename: "{app}\\{#RuntimeExe}"
; A read-only network check. Never launches OAuth or issues grants.
Name: "{group}\\OrdaX MCP"; Filename: "{app}\\runtime\\pythonw.exe"; Parameters: "-m ordax_dev_agent.owner_device_connect_gui --verificar-rede"; WorkingDir: "{app}"; IconFilename: "{app}\\{#RuntimeExe}"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "OrdaX MCP"; ValueData: """{app}\{#RuntimeExe}"""; Flags: uninsdeletevalue

[Run]
; Keep the one supervised Runtime silent and installed once; never use a
; second agent, shell bootstrap, or implicit remote permissions.
Filename: "{app}\{#RuntimeExe}"; Flags: nowait runhidden
; Normal wizard: the user chooses whether to open the existing PKCE device
; enrollment UI from the Finish page. This does not auto-enroll or grant
; computer access. It is not run by /SILENT or /VERYSILENT CI upgrades.
Filename: "{app}\runtime\pythonw.exe"; Parameters: "-m ordax_dev_agent.owner_device_connect_gui"; WorkingDir: "{app}"; Description: "Conectar este computador ao OrdaX MCP"; Flags: postinstall nowait skipifsilent

[Code]
// Legacy Studio still carries a separate Runtime executable and shares the
// same named OS mutex. Do not silently steal identity or create two installers
// owning overlapping Runtime lifecycles. Migration needs a separate upgrade
// contract and proof; this guard is deliberately temporary and fail-closed.
function InitializeSetup(): Boolean;
begin
  Result := True;
  if FileExists(ExpandConstant('{localappdata}\Programs\ORDAX\ORDAX Studio.exe')) or
     FileExists(ExpandConstant('{localappdata}\Programs\ORDAX\ORDAX Runtime.exe')) then
  begin
    SuppressibleMsgBox('Esta versao independente do OrdaX MCP ainda nao pode ser instalada em paralelo ao ORDAX Studio legado. A migracao do Runtime compartilhado requer um atualizador verificado. Nenhum dado ou instalacao existente foi modificado.', mbError, MB_OK, IDOK);
    Result := False;
  end;
end;

function QuiesceOwnedRuntime(const Root: String): Boolean;
var
  Code: Integer;
  Script: String;
begin
  Script := AddBackslash(Root) + 'ordax-upgrade-quiesce.ps1';
  if not FileExists(Script) then
  begin
    // First install, before the staged script exists, has nothing to stop.
    Result := not FileExists(AddBackslash(Root) + '{#RuntimeExe}');
    Exit;
  end;
  Code := -1;
  Result := Exec(
    ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
      Script + '" -InstallRoot "' + Root + '"',
    '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := Result and (Code = 0);
  if not Result then
    Log('ORDAX_MCP_QUIESCE_FAILED exit=' + IntToStr(Code));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if not QuiesceOwnedRuntime(ExpandConstant('{app}')) then
    Result := 'Nao foi possivel encerrar o Runtime instalado com seguranca.';
end;

function InitializeUninstall(): Boolean;
begin
  Result := QuiesceOwnedRuntime(ExpandConstant('{app}'));
  if not Result then
    SuppressibleMsgBox('O Runtime nao foi encerrado. A desinstalacao foi bloqueada para preservar a instalacao.', mbError, MB_OK, IDOK);
end;
