param([switch]$PrepareReboot, [switch]$AfterReboot, [int]$WaitSeconds = 120)
$ErrorActionPreference = 'Stop'
$stateDir = Join-Path $env:LOCALAPPDATA 'OrdaX\DevAgent'
$baseline = Join-Path $stateDir 'setup-reboot-baseline.json'
$reportPath = Join-Path $stateDir 'setup-verification.json'
$osBoot = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToUniversalTime().ToString('o')
if ($PrepareReboot) {
    @{os_boot=$osBoot;prepared_at=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $baseline -Encoding UTF8
    Write-Output 'REBOOT_BASELINE_SAVED'
    exit 0
}
if ($AfterReboot) {
    if (-not (Test-Path $baseline)) { throw 'REBOOT_BASELINE_MISSING' }
    $before = Get-Content $baseline -Raw | ConvertFrom-Json
    if ($before.os_boot -eq $osBoot) { throw 'REAL_REBOOT_NOT_OBSERVED' }
}
$deadline = [DateTime]::UtcNow.AddSeconds($WaitSeconds)
do {
    try {
        $s = Invoke-RestMethod http://127.0.0.1:8765/status -TimeoutSec 5
        $task = Get-ScheduledTask -TaskName 'OrdaX Dev Agent'
        $runnerProcesses = @(Get-Process -Name 'Runner.Listener','Runner.Worker' -ErrorAction SilentlyContinue)
        $runnerServices = @(Get-Service -Name 'actions.runner*' -ErrorAction SilentlyContinue | Where-Object Status -eq Running)
        $fresh = $s.runtime.last_heartbeat_at -and ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - $s.runtime.last_heartbeat_at) -lt 60
        if ($s.control_plane_protocol -eq 'cloudflare-v3' -and $s.runtime.supervisor_pid -gt 0 -and
            (Get-Process -Id $s.runtime.supervisor_pid -ErrorAction SilentlyContinue) -and $fresh -and
            $task.State -eq 'Running' -and $task.Actions.Arguments -match 'bootstrap\\ordax-agent-bootstrap.ps1') {
            $report = [ordered]@{
                verified_at=[DateTime]::UtcNow.ToString('o'); agent_version=$s.agent_version
                device_id=$s.device_id; protocol=$s.control_plane_protocol
                os_boot=$osBoot; real_reboot_observed=[bool]$AfterReboot
                supervisor_pid=$s.runtime.supervisor_pid; last_heartbeat_at=$s.runtime.last_heartbeat_at
                projects=@($s.projects.slug); runner_processes=$runnerProcesses.Count; runner_services=$runnerServices.Count
                without_runner=($runnerProcesses.Count -eq 0 -and $runnerServices.Count -eq 0)
                last_job_id=$s.runtime.last_job_id; last_job_action=$s.runtime.last_job_action
                last_result_ok=$s.runtime.last_result.ok
            }
            $report | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath "$reportPath.next" -Encoding UTF8
            Move-Item -LiteralPath "$reportPath.next" -Destination $reportPath -Force
            if (-not $report.without_runner) { throw 'RUNNER_PRESENT_INDEPENDENCE_TEST_INCONCLUSIVE' }
            Write-Output 'ORDAX_DEVICE_AGENT=READY'
            if ($AfterReboot) {
                Disable-ScheduledTask -TaskName 'OrdaX Setup Reboot Verification' -ErrorAction SilentlyContinue | Out-Null
                Write-Output 'REBOOT_WITHOUT_RUNNER=PASS'
            }
            exit 0
        }
    } catch {
        if ($_.Exception.Message -eq 'RUNNER_PRESENT_INDEPENDENCE_TEST_INCONCLUSIVE') { throw }
    }
    Start-Sleep -Seconds 2
} while ([DateTime]::UtcNow -lt $deadline)
throw 'DEVICE_VERIFICATION_TIMEOUT'
