#ifndef StageDir
  #error StageDir must be provided by the build pipeline
#endif
#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

#define AppName "ORDAX Studio"
#define AppPublisher "ORDAX"
#define AppExeName "ORDAX Studio.exe"
#define LegacyAppExeName "ORDAX Dev.exe"
#define RuntimeExeName "ORDAX Runtime.exe"
#define WorkbenchExeName "ORDAX Workbench.exe"

[Setup]
AppId={{0D31F22D-8451-4CF4-9E34-F0D4D857F55F}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\ORDAX Studio
DefaultGroupName=ORDAX
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=ORDAX-Studio-Setup-{#AppVersion}-x64
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
RestartApplications=no
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription=ORDAX Studio installer
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: filesandordirs; Name: "{app}\browser_extension"
Type: files; Name: "{userstartup}\OrdaX Dev Agent.lnk"
Type: files; Name: "{group}\ORDAX Dev.lnk"
Type: files; Name: "{userdesktop}\ORDAX Dev.lnk"

[Icons]
Name: "{group}\ORDAX Studio"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{userdesktop}\ORDAX Studio"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Criar atalho do ORDAX Studio na área de trabalho"; GroupDescription: "Atalhos adicionais:"; Flags: unchecked

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ORDAX Runtime"; ValueData: """{app}\{#RuntimeExeName}"""; Flags: uninsdeletevalue

[Run]
Filename: "{app}\redist\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Instalando Microsoft Edge WebView2..."; Flags: waituntilterminated skipifdoesntexist; Check: NeedsWebView2
Filename: "{app}\{#AppExeName}"; Description: "Abrir ORDAX Studio"; Flags: nowait postinstall skipifsilent

[Code]
const
  EVENT_MODIFY_STATE = $0002;
  SYNCHRONIZE = $00100000;

function OpenEvent(dwDesiredAccess: LongWord; bInheritHandle: Boolean; lpName: String): THandle;
  external 'OpenEventW@kernel32.dll stdcall';
function SetEvent(hEvent: THandle): Boolean;
  external 'SetEvent@kernel32.dll stdcall';
function CloseHandle(hObject: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

function NeedsWebView2(): Boolean;
var
  WebViewPath: String;
begin
  WebViewPath := ExpandConstant('{pf32}\Microsoft\EdgeWebView\Application');
  Result := not DirExists(WebViewPath);
end;

function SignalShutdownEvent(const EventName: String): Boolean;
var
  EventHandle: THandle;
begin
  EventHandle := OpenEvent(EVENT_MODIFY_STATE, False, EventName);
  if EventHandle = 0 then
  begin
    Result := False;
    Exit;
  end;
  Result := SetEvent(EventHandle);
  CloseHandle(EventHandle);
end;

function WaitForShutdownEventGone(const EventName: String): Boolean;
var
  Attempt: Integer;
  EventHandle: THandle;
begin
  for Attempt := 1 to 50 do
  begin
    EventHandle := OpenEvent(SYNCHRONIZE, False, EventName);
    if EventHandle = 0 then
    begin
      Result := True;
      Exit;
    end;
    CloseHandle(EventHandle);
    Sleep(100);
  end;
  Result := False;
end;

function StopOrdaxProcess(const EventName, ExeName: String): Boolean;
var
  ResultCode: Integer;
  Started: Boolean;
begin
  { Current launchers expose a cooperative shutdown event. }
  if SignalShutdownEvent(EventName) then
  begin
    if WaitForShutdownEventGone(EventName) then
    begin
      Result := True;
      Exit;
    end;
  end;

  { Compatibility path for launchers that predate the shutdown event. }
  Started := Exec(
    ExpandConstant('{sys}\taskkill.exe'),
    '/F /T /IM "' + ExeName + '"',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );
  if not Started then
  begin
    Result := False;
    Exit;
  end;

  { taskkill returns 128 when no matching process exists. }
  Result := (ResultCode = 0) or (ResultCode = 128);
  if Result then
    Sleep(500);
end;

function LegacyScheduledTaskExists(): Boolean;
var
  ResultCode: Integer;
  Started: Boolean;
begin
  Started := Exec(
    ExpandConstant('{sys}\schtasks.exe'),
    '/Query /TN "OrdaX Dev Agent"',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );
  Result := Started and (ResultCode = 0);
end;

function RetireLegacyScheduledTask(): Boolean;
var
  ResultCode: Integer;
  Started: Boolean;
begin
  if not LegacyScheduledTaskExists() then
  begin
    Result := True;
    Exit;
  end;

  { End the historical Scheduled Task first so its Python child releases
    localhost:8765 before the packaged ORDAX Runtime starts. The state
    directory is deliberately preserved because it owns device identity,
    project registration and local runtime data. }
  Exec(
    ExpandConstant('{sys}\schtasks.exe'),
    '/End /TN "OrdaX Dev Agent"',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );
  Sleep(750);

  Started := Exec(
    ExpandConstant('{sys}\schtasks.exe'),
    '/Delete /F /TN "OrdaX Dev Agent"',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );
  Result := Started and (ResultCode = 0);
  if Result then
    Sleep(750);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  NeedsRestart := False;

  if not StopOrdaxProcess('Local\ORDAXStudioShutdown', '{#AppExeName}') then
  begin
    Result := 'Não foi possível encerrar o ORDAX Studio para atualizar os arquivos.';
    Exit;
  end;

  { Explicitly retire pre-migration ORDAX Dev launchers that may not expose
    the cooperative Studio shutdown event. The compatibility alias installed
    by current builds is byte-identical to ORDAX Studio.exe. }
  if not StopOrdaxProcess('Local\ORDAXStudioShutdown', '{#LegacyAppExeName}') then
  begin
    Result := 'Não foi possível encerrar o launcher legado ORDAX Dev para atualizar os arquivos.';
    Exit;
  end;

  { Older Studio builds could leave Workbench outside the launcher Job Object.
    Kill that process tree explicitly before replacing the private Python runtime,
    because its workbench_bridge child loads DLLs from the installed runtime directory. }
  if not StopOrdaxProcess('Local\ORDAXWorkbenchShutdown', '{#WorkbenchExeName}') then
  begin
    Result := 'Não foi possível encerrar a ORDAX Workbench para atualizar os arquivos.';
    Exit;
  end;

  if not StopOrdaxProcess('Local\ORDAXRuntimeShutdown', '{#RuntimeExeName}') then
  begin
    Result := 'Não foi possível encerrar o ORDAX Runtime para atualizar os arquivos.';
    Exit;
  end;

  if not RetireLegacyScheduledTask() then
  begin
    Result := 'Não foi possível aposentar o supervisor legado OrdaX Dev Agent.';
    Exit;
  end;

  Result := '';
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    StopOrdaxProcess('Local\ORDAXStudioShutdown', '{#AppExeName}');
    StopOrdaxProcess('Local\ORDAXStudioShutdown', '{#LegacyAppExeName}');
    StopOrdaxProcess('Local\ORDAXWorkbenchShutdown', '{#WorkbenchExeName}');
    StopOrdaxProcess('Local\ORDAXRuntimeShutdown', '{#RuntimeExeName}');
    RetireLegacyScheduledTask();
  end;
end;
