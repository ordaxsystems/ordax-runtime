param(
    [switch]$NoLaunch,
    [switch]$DesktopShortcut,
    [switch]$NonInteractive
)
$ErrorActionPreference = 'Stop'
$SourceRepo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$SetupScript = Join-Path $SourceRepo 'scripts\windows\ordax-device-agent-setup.ps1'
$StateDir = Join-Path $env:LOCALAPPDATA 'OrdaX\DevAgent'
$ManagedRepo = Join-Path $StateDir 'src'
$TaskName = 'OrdaX Dev Agent'
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    $setupArgs = @('-NoProfile','-ExecutionPolicy','Bypass','-File',$SetupScript)
    if ($NonInteractive) { $setupArgs += '-NonInteractive' }
    & powershell.exe @setupArgs
    if ($LASTEXITCODE -ne 0) { throw 'ORDAX_DEVICE_AGENT_SETUP_FAILED' }
}
if (-not (Test-Path (Join-Path $ManagedRepo '.git'))) { throw "ORDAX managed runtime is missing: $ManagedRepo" }
$Pythonw = Join-Path $ManagedRepo '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $Pythonw)) { throw "ORDAX Dev runtime is missing: $Pythonw" }
$Programs = [Environment]::GetFolderPath('Programs')
$OrdaxMenu = Join-Path $Programs 'ORDAX'
New-Item -ItemType Directory -Force -Path $OrdaxMenu | Out-Null
$Shell = New-Object -ComObject WScript.Shell
function New-OrdaxShortcut([string]$Path) {
    $Shortcut = $Shell.CreateShortcut($Path)
    $Shortcut.TargetPath = $Pythonw
    $Shortcut.Arguments = '-m ordax_studio.product_web_desktop'
    $Shortcut.WorkingDirectory = $ManagedRepo
    $Shortcut.Description = 'ORDAX Dev'
    $Shortcut.Save()
}
$StartMenuLink = Join-Path $OrdaxMenu 'ORDAX Dev.lnk'
New-OrdaxShortcut $StartMenuLink
if ($DesktopShortcut) { New-OrdaxShortcut (Join-Path ([Environment]::GetFolderPath('Desktop')) 'ORDAX Dev.lnk') }
$StudioState = Join-Path $env:LOCALAPPDATA 'OrdaX\Dev'
New-Item -ItemType Directory -Force -Path $StudioState | Out-Null
@{ installed_at=[DateTimeOffset]::UtcNow.ToString('o'); runtime=$ManagedRepo; start_menu=$StartMenuLink; background_task=$TaskName } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $StudioState 'installation.json') -Encoding UTF8
if (-not $NoLaunch) { Start-Process -FilePath $Pythonw -ArgumentList @('-m','ordax_studio.product_web_desktop') -WorkingDirectory $ManagedRepo }
Write-Output 'ORDAX_DEV=INSTALLED'
Write-Output ("START_MENU=" + $StartMenuLink)
Write-Output ("BACKGROUND_TASK=" + $TaskName)
