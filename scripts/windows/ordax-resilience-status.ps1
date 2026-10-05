$ErrorActionPreference = "Continue"

$taskName = "OrdaX Dev Agent"
$bootstrapPath = Join-Path $env:LOCALAPPDATA "OrdaX\DevAgent\bootstrap\ordax-agent-bootstrap.ps1"
$bootstrapPolicyPath = Join-Path $env:LOCALAPPDATA "OrdaX\DevAgent\bootstrap\update_policy.py"
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$taskInfo = if ($task) { Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction SilentlyContinue } else { $null }

$runner = Get-Service -Name "actions.runner.*" -ErrorAction SilentlyContinue |
    Select-Object -First 1

$agentStatus = $null
try {
    $agentStatus = Invoke-RestMethod -Uri "http://127.0.0.1:8765/status" -TimeoutSec 2
} catch {}

$result = [ordered]@{
    timestamp_utc = [DateTime]::UtcNow.ToString("o")
    scheduled_task = if ($task) {
        [ordered]@{
            exists = $true
            state = [string]$task.State
            last_run_time = if ($taskInfo) { $taskInfo.LastRunTime } else { $null }
            last_task_result = if ($taskInfo) { $taskInfo.LastTaskResult } else { $null }
            next_run_time = if ($taskInfo) { $taskInfo.NextRunTime } else { $null }
            actions = @(
                $task.Actions | ForEach-Object {
                    [ordered]@{
                        execute = $_.Execute
                        arguments = $_.Arguments
                        working_directory = $_.WorkingDirectory
                    }
                }
            )
            triggers = @(
                $task.Triggers | ForEach-Object {
                    [ordered]@{
                        enabled = [bool]$_.Enabled
                        start_boundary = $_.StartBoundary
                        user_id = $_.UserId
                        repetition_interval = if ($_.Repetition) { [string]$_.Repetition.Interval } else { "" }
                        repetition_duration = if ($_.Repetition) { [string]$_.Repetition.Duration } else { "" }
                    }
                }
            )
        }
    } else {
        [ordered]@{ exists = $false }
    }
    agent_processes = @()
    agent_process_enumeration = "intentionally-disabled-no-cim"
    external_bootstrap = [ordered]@{
        script_exists = Test-Path $bootstrapPath
        policy_exists = Test-Path $bootstrapPolicyPath
        script_path = $bootstrapPath
        policy_path = $bootstrapPolicyPath
    }
    local_health = if ($agentStatus) { $agentStatus } else { $null }
    github_runner_service = if ($runner) {
        [ordered]@{
            exists = $true
            name = $runner.Name
            state = [string]$runner.Status
            start_type = [string]$runner.StartType
        }
    } else {
        [ordered]@{ exists = $false }
    }
}

$result | ConvertTo-Json -Depth 8
