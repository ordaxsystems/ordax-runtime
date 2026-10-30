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

[Icons]
Name: "{group}\OrdaX MCP — Estado do dispositivo"; Filename: "{app}\runtime\python.exe"; Parameters: "-m ordax_dev_agent.owner_grants_cli --help"; WorkingDir: "{app}"; Comment: "Ferramentas administrativas; nao concede autorizacao"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "OrdaX MCP"; ValueData: """{app}\{#RuntimeExe}"""; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#RuntimeExe}"; Flags: nowait runhidden

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
    MsgBox('Esta versao independente do OrdaX MCP ainda nao pode ser instalada em paralelo ao ORDAX Studio legado. A migracao do Runtime compartilhado requer um atualizador verificado. Nenhum dado ou instalacao existente foi modificado.', mbError, MB_OK);
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
  Result := Exec(
    ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
      Script + '" -InstallRoot "' + Root + '"',
    '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := Result and (Code = 0);
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
    MsgBox('O Runtime nao foi encerrado. A desinstalacao foi bloqueada para preservar a instalacao.', mbError, MB_OK);
end;
