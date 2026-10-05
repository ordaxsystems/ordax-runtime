param(
    [string]$AgentName = "TONECOS-HORDAX",
    [string]$HordaxPath = "C:\Users\TONECOS\Documents\github\HORDAX-game",
    [string]$BridgePath = "",
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$startup = [Environment]::GetFolderPath("Startup")
$legacyLinkPath = Join-Path $startup "OrdaX Dev Agent.lnk"
$taskName = "OrdaX Dev Agent"
$bootstrapInstaller = Join-Path $repoRoot "scripts\windows\ordax-agent-bootstrap-install.ps1"
$stateDir = Join-Path $env:LOCALAPPDATA "OrdaX\DevAgent"
$settingsPath = Join-Path $stateDir "agent-settings.json"
$bootstrapPath = Join-Path $stateDir "bootstrap\ordax-agent-bootstrap.ps1"

if (-not $BridgePath) {
    $BridgePath = $repoRoot
}

if (-not (Test-Path $python)) {
    $basePython = Get-Command python -ErrorAction SilentlyContinue
    if (-not $basePython) {
        throw "Python not found."
    }

    & $basePython.Source -m venv (Join-Path $repoRoot ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create .venv."
    }
}

$probeCommand = '\"' + $python + '\" -c \"import httpx, mcp, websockets, ordax_dev_agent\" >nul 2>&1'
& $env:ComSpec /d /c $probeCommand
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install --disable-pip-version-check -e $repoRoot
    if ($LASTEXITCODE -ne 0) { throw "OrdaX Dev Agent install failed." }
}

New-Item -ItemType Directory -Force -Path $stateDir | Out-Null


if (Test-Path $legacyLinkPath) {
    Remove-Item -Force $legacyLinkPath
}

if (-not (Test-Path $bootstrapInstaller)) {
    throw "Missing external bootstrap installer: $bootstrapInstaller"
}
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $bootstrapInstaller -RepoRoot $repoRoot
if ($LASTEXITCODE -ne 0) {
    throw "External OrdaX bootstrap installation failed with exit code $LASTEXITCODE"
}
if (-not (Test-Path $bootstrapPath)) {
    throw "External OrdaX bootstrap was not installed: $bootstrapPath"
}

Import-Module ScheduledTasks -ErrorAction Stop

$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

$powershellPath = (Get-Command powershell.exe -ErrorAction Stop).Source
$bootstrapArguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$bootstrapPath`" -RepoRoot `"$repoRoot`""
$actionParams = @{
    Execute = $powershellPath
    Argument = $bootstrapArguments
    WorkingDirectory = $repoRoot
}
$action = New-ScheduledTaskAction @actionParams
$logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$maintenanceTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At ((Get-Date).AddMinutes(1)) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$triggers = @($logonTrigger, $maintenanceTrigger)

$settingsParams = @{
    MultipleInstances = "IgnoreNew"
    RestartCount = 999
    RestartInterval = (New-TimeSpan -Minutes 1)
    StartWhenAvailable = $true
    AllowStartIfOnBatteries = $true
    DontStopIfGoingOnBatteries = $true
    ExecutionTimeLimit = [TimeSpan]::Zero
}
$settings = New-ScheduledTaskSettingsSet @settingsParams
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$task = New-ScheduledTask -Action $action -Trigger $triggers -Settings $settings -Principal $principal -Description "OrdaX Dev Agent - direct persistent Python control plane for interactive Unity/Blender"
Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null

Write-Host "OrdaX Dev Agent installed."
Write-Host "Scheduled task: $taskName"
Write-Host "Run context: $userId (interactive desktop)"
Write-Host "Restart policy: 999 attempts, 1 minute interval"
Write-Host "Maintenance trigger: every 1 minute for self-recovery; duplicate starts are ignored"
Write-Host "Local status endpoint: http://127.0.0.1:8765/status"
Write-Host ("Task executable: " + $powershellPath)
Write-Host ("Task bootstrap: " + $bootstrapPath)
Write-Host ("External bootstrap retained for maintenance: " + $bootstrapPath)

if (Test-Path $settingsPath -PathType Leaf) {
    Write-Host "Existing control-plane settings preserved."
} else {
    Write-Host "Control plane not configured. Run ordax-device-agent-setup.ps1."
}


if ($StartNow) {
    Start-ScheduledTask -TaskName $taskName
}
