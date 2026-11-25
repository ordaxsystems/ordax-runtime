param(
    [Parameter(Mandatory = $true)]
    [string]$InstallRoot,
    [int]$TimeoutSeconds = 15,
    [switch]$PreflightOnly,
    [switch]$AssessWorkerContinuity,
    [switch]$PreserveVerifiedWorkers,
    [string]$ContinuityJournalPath = ''
)

$ErrorActionPreference = "Stop"
if ($AssessWorkerContinuity -and !$PreflightOnly) {
    throw 'ORDAX_UPGRADE_WORKER_ASSESSMENT_REQUIRES_PREFLIGHT'
}
if ($ContinuityJournalPath -and !$PreserveVerifiedWorkers) {
    throw 'ORDAX_UPGRADE_CONTINUITY_JOURNAL_REQUIRES_PRESERVATION'
}
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

function Test-VerifiedDetachedWorker {
    param($Record, $Manager, [string]$ExpectedWorker)
    # Eligibility only, not authority to activate. A real upgrade must
    # additionally protect the rollback window and verify continued ownership.
    if ($Record.state -ne 'running' -or $Record.launcher_job_breakaway_verified -isnot [bool] -or
        !$Record.launcher_job_breakaway_verified -or
        [string]$Record.launcher_job_name -cnotmatch '^Local\\OrdaXRuntimeJob_[0-9]+_[0-9]+_[0-9]+$') {
        return $false
    }
    try {
        if ([IO.Path]::GetFullPath([string]$Manager.Path) -ine $ExpectedWorker) { return $false }
        $managerBirth = ([double]$Manager.StartTime.ToUniversalTime().Ticks - 621355968000000000) / 10000000
        $recordedManager = [double]$Record.manager_created_at_unix
        if ($recordedManager -le 0 -or [double]::IsNaN($recordedManager) -or
            [double]::IsInfinity($recordedManager) -or
            [Math]::Abs($managerBirth - $recordedManager) -gt 0.01) { return $false }
        $childPid = [int]$Record.child_pid
        if ($childPid -le 0 -or $childPid -eq $Manager.Id) { return $false }
        $child = Get-ExactProcessOrNull -TargetPid $childPid
        if ($null -eq $child) { return $false }
        $childPath = [IO.Path]::GetFullPath([string]$child.Path)
        $recordedChild = [double]$Record.child_created_at_unix
        $childBirth = ([double]$child.StartTime.ToUniversalTime().Ticks - 621355968000000000) / 10000000
        if ($recordedChild -le 0 -or [double]::IsNaN($recordedChild) -or
            [double]::IsInfinity($recordedChild) -or
            [Math]::Abs($childBirth - $recordedChild) -gt 0.01 -or
            $childPath -ine [string]$Record.child_executable_path) { return $false }
        # Do not classify tasks tied to the mutable installation as detached.
        foreach ($path in @($childPath, $Record.worker_manager_cwd, $Record.worker_child_cwd)) {
            if (!($path -is [string]) -or ![IO.Path]::IsPathRooted($path)) { return $false }
            $full = [IO.Path]::GetFullPath($path).TrimEnd('\')
            if ($full -ieq $root -or $full.ToLowerInvariant().StartsWith($rootPrefix)) { return $false }
        }
        foreach ($argument in @($Record.argv)) {
            if ($argument -isnot [string] -or
                $argument.Replace('/','\').ToLowerInvariant().Contains($root.ToLowerInvariant())) { return $false }
        }
        # Loaded modules must not come from the installation being replaced.
        # This is a present-time proof, not a promise about arbitrary future
        # file accesses by the external program.
        foreach ($process in @($Manager, $child)) {
            foreach ($module in @($process.Modules)) {
                if (!$module.FileName) { return $false }
                $modulePath = [IO.Path]::GetFullPath($module.FileName)
                if ($modulePath.ToLowerInvariant().StartsWith($rootPrefix)) { return $false }
            }
        }
        return $true
    } catch {
        return $false
    }
}

function Get-ReceiptBirthIdentity {
    param($Value)
    # Windows PowerShell ConvertTo-Json may round a double to 15 digits.
    # Keep the exact parsed value as an invariant round-trip string in the
    # journal; equality never depends on a lossy JSON numeric round trip.
    $birth = [double]$Value
    if ($birth -le 0 -or [double]::IsNaN($birth) -or [double]::IsInfinity($birth)) {
        throw 'ORDAX_UPGRADE_SUPERVISOR_BIRTH_IDENTITY_INVALID'
    }
    return $birth.ToString('R',[Globalization.CultureInfo]::InvariantCulture)
}

function Assert-PreservedWorkerReceipts {
    param($ObservedWorkers)
    if (!$ContinuityJournalPath) { return }
    $journal = [IO.File]::ReadAllText($ContinuityJournalPath,[Text.Encoding]::UTF8) | ConvertFrom-Json
    if ($journal.schema -cne 'ordax.mcp-dev-upgrade/4' -or
        $journal.phase -cne 'applying' -or
        $null -eq $journal.PSObject.Properties['preservedWorkers']) { throw 'ORDAX_UPGRADE_CONTINUITY_JOURNAL_INVALID' }
    $store = Join-Path $env:LOCALAPPDATA 'OrdaX\DevAgent'
    foreach ($expected in @($journal.preservedWorkers)) {
        if ($null -eq $expected) { continue }
        $reason = 'receipt_locator'
        try {
            # The canonical key is namespace/scope/process, not an absolute
            # pathname that may change spelling (e.g. Windows 8.3 aliases).
            $scopeName = [string]$expected.scope
            if ($expected.kind -cnotin @('device-processes','processes') -or
                [string]$expected.processId -cnotmatch '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' -or
                !$scopeName -or $scopeName -in @('.','..') -or
                $scopeName.IndexOfAny([IO.Path]::GetInvalidFileNameChars()) -ge 0 -or
                $scopeName.TrimEnd(' ','.') -cne $scopeName) {
                throw 'invalid receipt locator'
            }
            $namespace = Join-Path $store ([string]$expected.kind)
            $scopeRoot = Join-Path $namespace $scopeName
            $path = Join-Path $scopeRoot ([string]$expected.processId + '.json')
            foreach ($item in @($store,$namespace,$scopeRoot,$path)) {
                if (([IO.File]::GetAttributes($item) -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw 'receipt locator contains reparse point'
                }
            }
            $reason = 'receipt_read'
            $current = [IO.File]::ReadAllText($path,[Text.Encoding]::UTF8) | ConvertFrom-Json
            $reason = 'receipt_identity'
            if ($current.process_id -cne $expected.processId -or
                $current.worker_generation -cne $expected.generation -or
                $current.manager_pid -ne $expected.managerPid -or
                $current.child_pid -ne $expected.childPid -or
                (Get-ReceiptBirthIdentity $current.manager_created_at_unix) -cne [string]$expected.managerBirth -or
                (Get-ReceiptBirthIdentity $current.child_created_at_unix) -cne [string]$expected.childBirth) { throw 'identity changed' }
            $reason = 'terminal_receipt'
            if ($current.state -in @('exited','failed','stopped')) {
                # The same supervisor may naturally finish its work during
                # a switch; only its durable terminal receipt settles it.
                if ($null -eq $current.returncode -or [double]$current.ended_at_unix -le 0) {
                    throw 'terminal receipt incomplete'
                }
                continue
            }
            $reason = 'live_worker_identity'
            # Consume the exact same verified observation used by quiesce.
            # Never reconstruct a second manager path or query a second PID
            # between the scan and the continuity comparison.
            $matches = @($ObservedWorkers | Where-Object {
                $_.kind -ceq $expected.kind -and $_.scope -ceq $expected.scope -and
                $_.processId -ceq $expected.processId -and $_.generation -ceq $expected.generation -and
                $_.managerPid -eq $expected.managerPid -and $_.childPid -eq $expected.childPid -and
                $_.managerBirth -ceq $expected.managerBirth -and $_.childBirth -ceq $expected.childBirth
            })
            if ($matches.Count -ne 1) { throw 'live worker identity unavailable' }
        } catch {
            throw ("ORDAX_UPGRADE_PRESERVED_WORKER_CONTINUITY_LOST process_id={0} reason={1}" -f $expected.processId,$reason)
        }
    }
}

function Assert-NoActiveSupervisedManagers {
    param($ObservedWorkers)
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
                    if ($record.worker_generation -and $record.state -eq 'starting') {
                        throw 'ORDAX_UPGRADE_WORKER_LAUNCH_IN_PROGRESS'
                    }
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
                # Default callers (including uninstall and Inno) still block.
                # The verified portable lifecycle may preserve eligible workers.
                if ($record.worker_generation) {
                    $generation = [string]$record.worker_generation
                    if ($generation -cnotmatch '^[0-9a-f]{64}$') {
                        throw 'ORDAX_UPGRADE_SUPERVISOR_MANAGER_IDENTITY_UNVERIFIED'
                    }
                    $expectedWorker = Join-Path $env:LOCALAPPDATA ("OrdaX\worker-generations\$generation\python.exe")
                    if ($actualPath -ieq [IO.Path]::GetFullPath($expectedWorker)) {
                        if (($AssessWorkerContinuity -or $PreserveVerifiedWorkers) -and
                            (Test-VerifiedDetachedWorker -Record $record -Manager $handle -ExpectedWorker ([IO.Path]::GetFullPath($expectedWorker)))) {
                            Write-Output ("ORDAX_UPGRADE_WORKER_CONTINUITY_ELIGIBLE process_id={0} manager_pid={1}" -f
                                $record.process_id, $manager)
                            if ($PreserveVerifiedWorkers) {
                                $snapshot = [ordered]@{
                                    kind = $kind; scope = $scope.Name; processId = $record.process_id
                                    generation = $generation; managerPid = $manager; childPid = $record.child_pid
                                    managerBirth = (Get-ReceiptBirthIdentity $record.manager_created_at_unix)
                                    childBirth = (Get-ReceiptBirthIdentity $record.child_created_at_unix)
                                }
                                [void]$ObservedWorkers.Add([pscustomobject]$snapshot)
                                Write-Output ('ORDAX_UPGRADE_PRESERVED_WORKER|' + ($snapshot | ConvertTo-Json -Compress))
                                continue
                            }
                        }
                        throw ("ORDAX_UPGRADE_BLOCKED_SUPERVISED_PROCESS process_id={0} manager_pid={1}" -f
                            $record.process_id, $manager)
                    }
                    # A declared generation never authorizes a different manager.
                    # Only a proven recycled PID may be ignored.
                    $birth = [double]$record.manager_created_at_unix
                    $actualBirth = ([double]$handle.StartTime.ToUniversalTime().Ticks - 621355968000000000) / 10000000
                    if ($birth -le 0 -or [double]::IsNaN($birth) -or [double]::IsInfinity($birth) -or
                        [Math]::Abs($actualBirth - $birth) -le 0.01) {
                        throw 'ORDAX_UPGRADE_SUPERVISOR_MANAGER_IDENTITY_UNVERIFIED'
                    }
                    Assert-NoLiveOrphanSupervisedChild -Record $record
                    continue
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
# The canonical generation launch path takes a shared handle on this file.
# Hold the exclusive handle across the supervisor scan and actual quiesce.
# A preflight-only call releases it on exit; activation repeats the scan.
$private = Join-Path $env:LOCALAPPDATA 'OrdaX'
[void][IO.Directory]::CreateDirectory($private)
if (([IO.File]::GetAttributes($private) -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw 'ORDAX_UPGRADE_WORKER_LAUNCH_FENCE_UNVERIFIED'
}
$fencePath = Join-Path $private 'worker-launch.lock'
$fence = $null
$fenceDeadline = [DateTime]::UtcNow.AddSeconds([Math]::Min(10, [Math]::Max(1, $TimeoutSeconds)))
do {
    try {
        if ([IO.File]::Exists($fencePath) -and
            (([IO.File]::GetAttributes($fencePath) -band [IO.FileAttributes]::ReparsePoint) -ne 0)) {
            throw 'ORDAX_UPGRADE_WORKER_LAUNCH_FENCE_UNVERIFIED'
        }
        $fence = [IO.File]::Open($fencePath, [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch {
        $cause = $_.Exception.GetBaseException()
        $code = $cause.HResult -band 0xffff
        if ($code -notin @(32, 33)) {
            throw 'ORDAX_UPGRADE_WORKER_LAUNCH_FENCE_UNVERIFIED'
        }
        Start-Sleep -Milliseconds 100
    }
} while ($null -eq $fence -and [DateTime]::UtcNow -lt $fenceDeadline)
if ($null -eq $fence) {
    throw 'ORDAX_UPGRADE_WORKER_LAUNCH_IN_PROGRESS'
}
try {
$observedWorkers = [Collections.Generic.List[object]]::new()
Assert-NoActiveSupervisedManagers -ObservedWorkers $observedWorkers
Assert-PreservedWorkerReceipts -ObservedWorkers $observedWorkers
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
} finally {
    $fence.Dispose()
}
