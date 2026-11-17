param(
    [Parameter(Mandatory = $true)]
    [int]$AgentPid,
    [int]$StartupGraceSeconds = 60,
    [int]$ProbeIntervalSeconds = 10,
    [int]$MaxConsecutiveFailures = 6,
    [int]$MaxBusyMinutes = 15,
    [string]$RestartTaskName = "OrdaX Dev Agent",
    [int]$RestartDelaySeconds = 5,
    [switch]$RequireProductDispatchReadiness,
    [ValidateRange(1, 65535)]
    [int]$HealthPort = 8765
)

$ErrorActionPreference = "SilentlyContinue"
$probePath = if ($RequireProductDispatchReadiness) { "readiness" } else { "health" }
$healthUrl = "http://127.0.0.1:$HealthPort/$probePath"
$stateDir = Join-Path $env:LOCALAPPDATA "OrdaX\DevAgent"
$logPath = Join-Path $stateDir "watchdog.log"
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

# Bind the watched PID to its original creation time and executable. PID reuse
# must never terminate an unrelated user's process after the agent exits.
$original = Get-Process -Id $AgentPid -ErrorAction SilentlyContinue
if (-not $original) { exit 0 }
try {
    $originalStartUtc = $original.StartTime.ToUniversalTime().Ticks
    $originalPath = [IO.Path]::GetFullPath([string]$original.Path)
    if (-not $originalPath) { exit 0 }
} catch { exit 0 }

function Same-AgentProcess {
    param($Process)
    if (-not $Process) { return $false }
    try {
        return $Process.StartTime.ToUniversalTime().Ticks -eq $originalStartUtc -and
            [IO.Path]::GetFullPath([string]$Process.Path) -ieq $originalPath
    } catch { return $false }
}

function Write-WatchdogLog([string]$Message) {
    $stamp = [DateTime]::UtcNow.ToString("o")
    Add-Content -Path $logPath -Value "$stamp $Message" -Encoding UTF8
}

function Stop-AgentForRecovery([string]$Reason) {
    Write-WatchdogLog "RECOVERY $Reason pid=$AgentPid"
    try {
        $current = Get-Process -Id $AgentPid -ErrorAction Stop
        if (-not (Same-AgentProcess $current)) {
            Write-WatchdogLog "RECOVERY_ABORT identity changed pid=$AgentPid"
            return $false
        }
        Stop-Process -InputObject $current -Force -ErrorAction Stop
        Write-WatchdogLog "AGENT_STOPPED pid=$AgentPid"
        return $true
    } catch {
        Write-WatchdogLog "AGENT_STOP_FAIL pid=$AgentPid error=$($_.Exception.Message)"
        return $false
    }
}

function Recover-Agent([string]$Reason) {
    $stopped = Stop-AgentForRecovery $Reason
    if (-not $stopped) {
        return $false
    }
    Request-AgentRestartIfNeeded
    return $true
}

function Test-CanonicalUpgradeActive {
    $installer = @(Get-Process -Name 'OrdaX-MCP-Setup-*' -ErrorAction SilentlyContinue)
    if ($installer.Count -gt 0) { return $true }
    try {
        $upgrade = Get-ScheduledTask -TaskName 'OrdaX MCP Verified Upgrade' -ErrorAction SilentlyContinue
        return $null -ne $upgrade -and $upgrade.State -eq 'Running'
    } catch {
        return $false
    }
}

function Request-AgentRestartIfNeeded {
    Start-Sleep -Seconds $RestartDelaySeconds
    if (Test-CanonicalUpgradeActive) {
        Write-WatchdogLog "RESTART_SKIP canonical MCP upgrade active"
        return
    }

    # Avoid Win32_Process/CIM here. On the Salvador workstation WMI/CIM
    # process queries have stalled for minutes and prevented recovery itself.
    # The bounded localhost health endpoint is a stronger successor signal.
    try {
        $successorStatus = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3
        if ($successorStatus) {
            Write-WatchdogLog "SUCCESSOR health endpoint is ready; scheduled restart not needed"
            return
        }
    } catch {
        # No healthy successor yet; continue with the scheduled-task state.
    }

    # The packaged Python registry/control plane can take longer than 20s to
    # initialize. Avoid killing the new launcher during its legitimate startup.
    $deadline = [DateTime]::UtcNow.AddSeconds(90)
    while ([DateTime]::UtcNow -lt $deadline) {
        $task = Get-ScheduledTask -TaskName $RestartTaskName -ErrorAction SilentlyContinue
        if (-not $task) {
            Write-WatchdogLog "RESTART_SKIP scheduled task missing name=$RestartTaskName"
            return
        }
        if ($task.State -eq "Disabled") {
            Write-WatchdogLog "RESTART_SKIP scheduled task disabled name=$RestartTaskName"
            return
        }
        if ($task.State -ne "Running") {
            try {
                Start-ScheduledTask -TaskName $RestartTaskName -ErrorAction Stop
                Write-WatchdogLog "RESTART_REQUEST task=$RestartTaskName"
            } catch {
                Write-WatchdogLog "RESTART_FAIL task=$RestartTaskName error=$($_.Exception.Message)"
            }
            return
        }

        # The bootstrap may still be unwinding after the agent process ended.
        # Give it a bounded grace period instead of using process enumeration.
        Start-Sleep -Seconds 2
        try {
            $successorStatus = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 2
            if ($successorStatus) {
                Write-WatchdogLog "SUCCESSOR health endpoint became ready during grace period"
                return
            }
        } catch {
        }
    }

    # Parent agent is gone, no successor health endpoint appeared, and the
    # task is still marked Running. Restart only this dedicated scheduled task.
    try {
        Stop-ScheduledTask -TaskName $RestartTaskName -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        Start-ScheduledTask -TaskName $RestartTaskName -ErrorAction Stop
        Write-WatchdogLog "RESTART_FORCE task=$RestartTaskName"
    } catch {
        Write-WatchdogLog "RESTART_FORCE_FAIL task=$RestartTaskName error=$($_.Exception.Message)"
    }
}

Write-WatchdogLog "START pid=$AgentPid grace=$StartupGraceSeconds interval=$ProbeIntervalSeconds maxBusyMinutes=$MaxBusyMinutes restartTask=$RestartTaskName"

Start-Sleep -Seconds $StartupGraceSeconds

$consecutiveFailures = 0
$busyJobId = ""
$busySince = $null

while ($true) {
    $process = Get-Process -Id $AgentPid -ErrorAction SilentlyContinue
    if (-not (Same-AgentProcess $process)) {
        Write-WatchdogLog "EXIT parent process ended or changed identity pid=$AgentPid"
        Request-AgentRestartIfNeeded
        exit 0
    }

    $status = $null
    try {
        $status = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3
    } catch {
        $consecutiveFailures += 1
        Write-WatchdogLog "HEALTH_FAIL count=$consecutiveFailures pid=$AgentPid"
        if ($consecutiveFailures -ge $MaxConsecutiveFailures) {
            [void](Recover-Agent "local health endpoint failed $consecutiveFailures consecutive probes")
            exit 20
        }
        Start-Sleep -Seconds $ProbeIntervalSeconds
        continue
    }

    $runtime = $status.runtime
    if ($RequireProductDispatchReadiness) {
        $dispatchReady = $runtime -and $runtime.product_dispatch_ready -eq $true
        $transportState = if ($runtime) { [string]$runtime.transport_state } else { "" }
        $readinessCode = if ($runtime) { [string]$runtime.product_readiness_code } else { "" }
        if ($dispatchReady) {
            if ($consecutiveFailures -gt 0) {
                Write-WatchdogLog "PRODUCT_READY recovered after $consecutiveFailures stale probes"
            }
            $consecutiveFailures = 0
        } elseif ($transportState -eq "connected" -and $readinessCode -eq "heartbeat_stale") {
            $consecutiveFailures += 1
            Write-WatchdogLog "PRODUCT_STALE count=$consecutiveFailures code=$readinessCode pid=$AgentPid"
            if ($consecutiveFailures -ge $MaxConsecutiveFailures) {
                [void](Recover-Agent "Product heartbeat remained stale while transport reported connected")
                exit 22
            }
        } else {
            # Pairing, deliberate local disablement and a control-plane outage
            # are not evidence that the local Runtime process is hung.
            if ($consecutiveFailures -gt 0) {
                Write-WatchdogLog "PRODUCT_STALE_RESET transport=$transportState code=$readinessCode"
            }
            $consecutiveFailures = 0
        }
        Start-Sleep -Seconds $ProbeIntervalSeconds
        continue
    }

    $consecutiveFailures = 0
    $runtimeState = if ($runtime) { [string]$runtime.state } else { "" }
    $currentJob = if ($runtime) { [string]$runtime.last_job_id } else { "" }

    if ($runtimeState -eq "busy" -and $currentJob) {
        if ($currentJob -ne $busyJobId) {
            $busyJobId = $currentJob
            $busySince = [DateTime]::UtcNow
            Write-WatchdogLog "BUSY_START job=$busyJobId pid=$AgentPid"
        } elseif ($busySince) {
            $busyAge = [DateTime]::UtcNow - $busySince
            if ($busyAge.TotalMinutes -ge $MaxBusyMinutes) {
                [void](Recover-Agent ("job $busyJobId remained busy for " + [Math]::Round($busyAge.TotalMinutes, 1) + " minutes"))
                exit 21
            }
        }
    } else {
        if ($busyJobId) {
            Write-WatchdogLog "BUSY_END job=$busyJobId state=$runtimeState"
        }
        $busyJobId = ""
        $busySince = $null
    }

    Start-Sleep -Seconds $ProbeIntervalSeconds
}
