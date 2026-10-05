param(
    [switch]$HardenRunnerService
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$agentInstaller = Join-Path $repoRoot "scripts\windows\ordax-agent-install.ps1"
$runnerInstaller = Join-Path $repoRoot "scripts\windows\ordax-runner-service.ps1"

if (-not (Test-Path $agentInstaller)) { throw "Missing agent installer: $agentInstaller" }
if (-not (Test-Path $runnerInstaller)) { throw "Missing runner service installer: $runnerInstaller" }

Write-Host "=== OrdaX resilience bootstrap ==="
Write-Host "Repository: $repoRoot"
Write-Host ""

Write-Host "[1/3] Installing/updating persistent interactive OrdaX task..."
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $agentInstaller -StartNow
if ($LASTEXITCODE -ne 0) {
    throw "OrdaX agent scheduled-task installation failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "[2/3] Checking optional GitHub self-hosted runner service..."
$runnerBefore = Get-CimInstance Win32_Service |
    Where-Object { $_.Name -like "actions.runner.*" } |
    Select-Object -First 1

if ($HardenRunnerService) {
    if (-not $runnerBefore) {
        Write-Warning "No official GitHub Actions Windows service is installed. The OrdaX Agent will still be resilient."
        Write-Warning "Windows runners configured interactively must be officially reconfigured in service mode before hardening."
    } else {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $runnerInstaller
        if ($LASTEXITCODE -ne 0) {
            throw "GitHub Actions runner service hardening failed with exit code $LASTEXITCODE"
        }
    }
} elseif (-not $runnerBefore) {
    Write-Host "Runner service: not installed (optional). Agent uptime does not depend on it."
} else {
    Write-Host ("Runner service found: " + $runnerBefore.Name)
}

Write-Host ""
Write-Host "[3/3] Verifying OrdaX persistence..."

$taskName = "OrdaX Dev Agent"
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
$taskInfo = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction Stop

$runner = Get-CimInstance Win32_Service |
    Where-Object { $_.Name -like "actions.runner.*" } |
    Select-Object -First 1

$agentStatus = $null
$deadline = [DateTime]::UtcNow.AddSeconds(45)
while ([DateTime]::UtcNow -lt $deadline) {
    try {
        $agentStatus = Invoke-RestMethod -Uri "http://127.0.0.1:8765/status" -TimeoutSec 2
        break
    } catch {
        Start-Sleep -Seconds 1
    }
}

Write-Host ""
Write-Host "OrdaX scheduled task:"
Write-Host ("  State: " + $task.State)
Write-Host ("  LastTaskResult: " + $taskInfo.LastTaskResult)
Write-Host ("  NextRunTime: " + $taskInfo.NextRunTime)
Write-Host ("  Action: " + (($task.Actions | Select-Object -First 1).Execute) + " " + (($task.Actions | Select-Object -First 1).Arguments))

Write-Host "GitHub Actions runner service:"
if ($runner) {
    Write-Host ("  Name: " + $runner.Name)
    Write-Host ("  State: " + $runner.State)
    Write-Host ("  StartMode: " + $runner.StartMode)
} else {
    Write-Host "  Not installed as service (optional)."
}

if ($agentStatus) {
    Write-Host "OrdaX local health endpoint: ONLINE"
    if ($agentStatus.agent_version) {
        Write-Host ("  Agent version: " + $agentStatus.agent_version)
    }
} else {
    Write-Warning "The scheduled task is installed but the local OrdaX health endpoint did not answer within 45 seconds."
    Write-Warning "The task/launcher restart policy will keep retrying; inspect scripts/windows/ordax-resilience-status.ps1 if it remains offline."
}

if ($HardenRunnerService -and $runner -and ($runner.State -ne "Running" -or $runner.StartMode -ne "Auto")) {
    throw "Runner service is not healthy after hardening."
}

Write-Host ""
Write-Host "Resilience bootstrap completed."
Write-Host "Primary uptime: Windows Task Scheduler -> OrdaX interactive agent"
Write-Host "Agent bootstrap: external safe Git fast-forward + retry loop"
if ($runner) {
    Write-Host "Secondary recovery channel: GitHub Actions Windows service"
} else {
    Write-Host "Secondary runner service is optional and not currently installed."
}
