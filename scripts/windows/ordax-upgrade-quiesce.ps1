param(
    [Parameter(Mandatory = $true)]
    [string]$InstallRoot,
    [int]$TimeoutSeconds = 15
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
            throw
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
