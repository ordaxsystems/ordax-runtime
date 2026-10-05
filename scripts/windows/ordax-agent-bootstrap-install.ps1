param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,
    [string]$TaskName = "OrdaX Dev Agent",
    [switch]$RetargetTask
)

$ErrorActionPreference = "Stop"

$repoRootResolved = (Resolve-Path $RepoRoot).Path
$stateDir = Join-Path $env:LOCALAPPDATA "OrdaX\DevAgent"
$bootstrapDir = Join-Path $stateDir "bootstrap"
$bootstrapSource = Join-Path $repoRootResolved "scripts\windows\ordax-agent-bootstrap.ps1"
$policySource = Join-Path $repoRootResolved "ordax_dev_agent\update_policy.py"
$bootstrapPath = Join-Path $bootstrapDir "ordax-agent-bootstrap.ps1"
$policyPath = Join-Path $bootstrapDir "update_policy.py"

if (-not (Test-Path $bootstrapSource)) {
    throw "Bootstrap source missing: $bootstrapSource"
}
if (-not (Test-Path $policySource)) {
    throw "Update policy source missing: $policySource"
}

New-Item -ItemType Directory -Force -Path $bootstrapDir | Out-Null

$bootstrapTemp = "$bootstrapPath.tmp"
$policyTemp = "$policyPath.tmp"
Copy-Item -Force $bootstrapSource $bootstrapTemp
Copy-Item -Force $policySource $policyTemp
Move-Item -Force $bootstrapTemp $bootstrapPath
Move-Item -Force $policyTemp $policyPath

if ($RetargetTask) {
    Import-Module ScheduledTasks -ErrorAction Stop
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        throw "Scheduled task not found: $TaskName"
    }

    if (-not (Test-Path $bootstrapPath)) {
        throw "External OrdaX bootstrap missing: $bootstrapPath"
    }
    $powershellPath = (Get-Command powershell.exe -ErrorAction Stop).Source
    $bootstrapArguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$bootstrapPath`" -RepoRoot `"$repoRootResolved`""

    $action = New-ScheduledTaskAction `
        -Execute $powershellPath `
        -Argument $bootstrapArguments `
        -WorkingDirectory $repoRootResolved

    $taskUser = [string]$task.Principal.UserId
    if (-not $taskUser) {
        throw "Scheduled task principal is missing a user id: $TaskName"
    }
    $logonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $taskUser
    $maintenanceTrigger = New-ScheduledTaskTrigger `
        -Once `
        -At ((Get-Date).AddMinutes(1)) `
        -RepetitionInterval (New-TimeSpan -Minutes 1) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    $triggers = @($logonTrigger, $maintenanceTrigger)

    Set-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers | Out-Null
}

[pscustomobject]@{
    repo_root = $repoRootResolved
    bootstrap_path = $bootstrapPath
    policy_path = $policyPath
    task_retargeted = [bool]$RetargetTask
} | ConvertTo-Json -Compress
