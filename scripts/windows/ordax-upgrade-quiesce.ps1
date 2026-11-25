param(
    [Parameter(Mandatory = $true)]
    [string]$InstallRoot,
    [int]$TimeoutSeconds = 15,
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$rootPrefix = ($root + '\').ToLowerInvariant()
$ownedExecutables = @(
    'ORDAX Studio.exe',
    'ORDAX Dev.exe',
    'ORDAX Runtime.exe',
    'ORDAX Workbench.exe',
    'python.exe',
    'pythonw.exe'
)
$ownedProcessNames = @(
    'ORDAX Studio', 'ORDAX Dev', 'ORDAX Runtime',
    'ORDAX Workbench', 'python', 'pythonw'
)

function Get-OrdaxPackagedProcesses {
    # WMI/CIM can stall for minutes on real Windows hosts. Use the kernel
    # process handles, then filter by the one canonical installation root.
    # Do not use names alone: a non-OrdaX Python is NEVER ours to stop.
    foreach ($candidate in @(Get-Process -Name $ownedProcessNames -ErrorAction Ignore)) {
        try {
            $path = [string]$candidate.Path
            if (-not $path) { continue }
            $full = [IO.Path]::GetFullPath($path)
            if (-not $full.ToLowerInvariant().StartsWith($rootPrefix)) { continue }
            if ($ownedExecutables -notcontains [IO.Path]::GetFileName($full)) { continue }
            $started = $candidate.StartTime.ToUniversalTime().Ticks
        } catch {
            # No executable path/creation proof means no authority to stop it.
            continue
        }
        [pscustomobject]@{
            ProcessId = [int]$candidate.Id
            StartTimeTicks = [long]$started
            Name = [string]$candidate.ProcessName
            ExecutablePath = $full
        }
    }
}

function Get-ExactProcessOrNull {
    param([int]$TargetPid)
    try {
        return Get-Process -Id $TargetPid -ErrorAction Stop
    } catch {
        # Only an explicitly absent PID is a benign race. Permission errors,
        # unavailable process metadata and unknown failures remain fail-closed.
        if ($_.FullyQualifiedErrorId -like 'NoProcessFoundForGivenId,*') {
            return $null
        }
        throw
    }
}

function Assert-NoLiveOrphanSupervisedChild {
    param($Record)
    # A legacy manager can die before recording a terminal receipt while its
    # external child keeps serving requests. It must not be silently discarded
    # merely because manager_pid is gone. Never match by PID alone: Windows
    # can recycle a PID into a completely unrelated process.
    $childPid = 0
    try { $childPid = [int]$Record.child_pid }
    catch { throw 'ORDAX_UPGRADE_SUPERVISOR_CHILD_IDENTITY_UNVERIFIED' }
    if ($childPid -le 0) { return }
    $child = Get-ExactProcessOrNull -TargetPid $childPid
    if ($null -eq $child) { return }
    try {
        $actualPath = [IO.Path]::GetFullPath([string]$child.Path)
        $actualStarted = ([double]$child.StartTime.ToUniversalTime().Ticks - 621355968000000000) / 10000000
        $exactBirth = $null -ne $Record.PSObject.Properties['child_created_at_unix']
        $exactImage = $null -ne $Record.PSObject.Properties['child_executable_path']
        if ($exactBirth -ne $exactImage) {
            throw 'ORDAX_UPGRADE_SUPERVISOR_CHILD_IDENTITY_UNVERIFIED'
        }
        if ($exactBirth) {
            $recordedStarted = [double]$Record.child_created_at_unix
        } else {
            $recordedStarted = [double]$Record.running_at_unix
        }
    } catch {
        # Unknown ownership is not evidence that stopping the owner is safe.
        throw 'ORDAX_UPGRADE_SUPERVISOR_CHILD_IDENTITY_UNVERIFIED'
    }
    if ($recordedStarted -le 0) {
        throw 'ORDAX_UPGRADE_SUPERVISOR_CHILD_IDENTITY_UNVERIFIED'
    }
    $tolerance = 10
    if ($exactBirth) { $tolerance = 0.01 }
    if ([Math]::Abs($actualStarted - $recordedStarted) -gt $tolerance) {
        # Definitively different start time: a recycled PID, not this child.
        return
    }
    if ($exactImage) {
        $command = @([string]$Record.child_executable_path)
    } else {
        $command = @($Record.argv)
    }
    if ($command.Count -lt 1 -or
        !($command[0] -is [string]) -or
        ![IO.Path]::IsPathRooted([string]$command[0])) {
        throw 'ORDAX_UPGRADE_SUPERVISOR_CHILD_IDENTITY_UNVERIFIED'
    }
    $expectedPath = [IO.Path]::GetFullPath([string]$command[0])
    if ($actualPath -ine $expectedPath) {
        throw 'ORDAX_UPGRADE_SUPERVISOR_CHILD_IDENTITY_UNVERIFIED'
    }
    throw ("ORDAX_UPGRADE_BLOCKED_ORPHAN_SUPERVISED_CHILD process_id={0} child_pid={1}" -f
        $Record.process_id, $childPid)
}

function Assert-NoActiveSupervisedManagers {
    # This is the existing Runtime supervisor store, not a second task registry.
    # Upgrading must never kill a persistent worker while leaving its child alive
    # and falsely recording the mission as stopped.
    $supervisorRoot = Join-Path $env:LOCALAPPDATA 'OrdaX\DevAgent'
    foreach ($kind in @('device-processes', 'processes')) {
        $namespace = Join-Path $supervisorRoot $kind
        if (!(Test-Path -LiteralPath $namespace -PathType Container)) { continue }
        foreach ($scope in @(Get-ChildItem -LiteralPath $namespace -Directory -ErrorAction Stop)) {
            if (($scope.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'ORDAX_UPGRADE_SUPERVISOR_SCOPE_UNVERIFIED'
            }
            foreach ($file in @(Get-ChildItem -LiteralPath $scope.FullName -Filter '*.json' -File -ErrorAction Stop)) {
                if ($file.BaseName -cnotmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$') {
                    continue
                }
                try {
                    $record = [IO.File]::ReadAllText($file.FullName, [Text.Encoding]::UTF8) |
                        ConvertFrom-Json -ErrorAction Stop
                } catch {
                    throw 'ORDAX_UPGRADE_SUPERVISOR_RECEIPT_UNREADABLE'
                }
                if ($record.state -notin @('starting', 'running', 'stopping')) { continue }
                $manager = [int]$record.manager_pid
                if ($manager -le 0) {
                    Assert-NoLiveOrphanSupervisedChild -Record $record
                    continue
                }
                $handle = Get-ExactProcessOrNull -TargetPid $manager
                if ($null -eq $handle) {
                    Assert-NoLiveOrphanSupervisedChild -Record $record
                    continue
                }
                try {
                    $actualPath = [IO.Path]::GetFullPath([string]$handle.Path)
                    $startedUnix = ([DateTimeOffset]$handle.StartTime.ToUniversalTime()).ToUnixTimeSeconds()
                } catch {
                    throw 'ORDAX_UPGRADE_SUPERVISOR_MANAGER_IDENTITY_UNVERIFIED'
                }
                # Generation workers execute outside the mutable install.
                # Keep live handoff fail-closed until the full lifecycle is proven.
                if ($record.worker_generation) {
                    $generation = [string]$record.worker_generation
                    if ($generation -cnotmatch '^[0-9a-f]{64}$') {
                        throw 'ORDAX_UPGRADE_SUPERVISOR_MANAGER_IDENTITY_UNVERIFIED'
                    }
                    $expectedWorker = Join-Path $env:LOCALAPPDATA ("OrdaX\worker-generations\$generation\python.exe")
                    if ($actualPath -ieq [IO.Path]::GetFullPath($expectedWorker)) {
                        throw ("ORDAX_UPGRADE_BLOCKED_SUPERVISED_PROCESS process_id={0} manager_pid={1}" -f
                            $record.process_id, $manager)
                    }
                }
                if (!$actualPath.ToLowerInvariant().StartsWith($rootPrefix) -or
                    ([IO.Path]::GetFileName($actualPath) -notin @('python.exe', 'pythonw.exe'))) {
                    # The manager PID now points to another process. Check that
                    # its old child PID was also recycled or has exited.
                    Assert-NoLiveOrphanSupervisedChild -Record $record
                    continue
                }
                $recorded = [double]$record.manager_ready_at_unix
                if ($recorded -le 0) { $recorded = [double]$record.started_at_unix }
                if ($recorded -le 0 -or [Math]::Abs($startedUnix - $recorded) -gt 300) {
                    throw 'ORDAX_UPGRADE_SUPERVISOR_MANAGER_IDENTITY_UNVERIFIED'
                }
                throw ("ORDAX_UPGRADE_BLOCKED_SUPERVISED_PROCESS process_id={0} manager_pid={1}" -f
                    $record.process_id, $manager)
            }
        }
    }
}
Assert-NoActiveSupervisedManagers
if ($PreflightOnly) {
    Write-Output 'ORDAX_UPGRADE_SUPERVISOR_PREFLIGHT_CLEAR'
    exit 0
}

$deadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(1, $TimeoutSeconds))
do {
    $processes = @(Get-OrdaxPackagedProcesses)
    if (-not $processes.Count) {
        Write-Output "ORDAX_UPGRADE_QUIESCED"
        exit 0
    }

    foreach ($process in $processes) {
        $pidValue = [int]$process.ProcessId
        if ($pidValue -le 0 -or $pidValue -eq $PID) { continue }
        $handle = Get-ExactProcessOrNull -TargetPid $pidValue
        if ($null -eq $handle) { continue }
        try {
            $actualStart = $handle.StartTime.ToUniversalTime().Ticks
            $actualPath = [IO.Path]::GetFullPath([string]$handle.Path)
        } catch {
            if ($null -eq (Get-ExactProcessOrNull -TargetPid $pidValue)) { continue }
            throw "ORDAX_UPGRADE_PROCESS_PATH_UNVERIFIED pid=$pidValue"
        }
        if ($actualStart -ne $process.StartTimeTicks) {
            throw "ORDAX_UPGRADE_IDENTITY_CHANGED pid=$pidValue"
        }
        if ($actualPath -ine $process.ExecutablePath -or
            -not $actualPath.ToLowerInvariant().StartsWith($rootPrefix)) {
            throw "ORDAX_UPGRADE_PROCESS_PATH_UNVERIFIED pid=$pidValue"
        }
        Write-Output ("ORDAX_UPGRADE_STOP pid={0} name={1} path={2}" -f $pidValue, $process.Name, $process.ExecutablePath)
        try {
            Stop-Process -InputObject $handle -Force -ErrorAction Stop
        } catch {
            if ($null -eq (Get-ExactProcessOrNull -TargetPid $pidValue)) { continue }
            # Preserve a stable fail-closed category even when Windows emits
            # localized Stop-Process errors. The lifecycle records only it.
            throw ("ORDAX_UPGRADE_PROCESS_STOP_FAILED pid={0}" -f $pidValue)
        }
    }
    Start-Sleep -Milliseconds 250
} while ([DateTime]::UtcNow -lt $deadline)

$remaining = @(Get-OrdaxPackagedProcesses)
if ($remaining.Count) {
    $details = ($remaining | ForEach-Object { "$($_.ProcessId):$($_.ExecutablePath)" }) -join '; '
    throw "ORDAX packaged processes are still running after upgrade quiesce: $details"
}
Write-Output "ORDAX_UPGRADE_QUIESCED"
