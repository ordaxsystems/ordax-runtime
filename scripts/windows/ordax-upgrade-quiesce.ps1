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

function Get-OrdaxPackagedProcesses {
    Get-CimInstance Win32_Process |
        Where-Object {
            $path = [string]$_.ExecutablePath
            if (-not $path) { return $false }
            try {
                $full = [IO.Path]::GetFullPath($path)
            } catch {
                return $false
            }
            if (-not $full.ToLowerInvariant().StartsWith($rootPrefix)) {
                return $false
            }
            return $ownedExecutables -contains [IO.Path]::GetFileName($full)
        } |
        Select-Object ProcessId, ParentProcessId, Name, ExecutablePath
}

$deadline = [DateTime]::UtcNow.AddSeconds([Math]::Max(1, $TimeoutSeconds))
$attempt = 0
do {
    $attempt++
    $processes = @(Get-OrdaxPackagedProcesses)
    if (-not $processes.Count) {
        Write-Output "ORDAX_UPGRADE_QUIESCED"
        exit 0
    }

    foreach ($process in $processes) {
        $pidValue = [int]$process.ProcessId
        if ($pidValue -le 0 -or $pidValue -eq $PID) { continue }

        Write-Output ("ORDAX_UPGRADE_STOP pid={0} name={1} path={2}" -f $pidValue, $process.Name, $process.ExecutablePath)
        & "$env:SystemRoot\System32\taskkill.exe" /PID $pidValue /T /F 2>$null | Out-Null
    }

    Start-Sleep -Milliseconds 250
} while ([DateTime]::UtcNow -lt $deadline)

$remaining = @(Get-OrdaxPackagedProcesses)
if ($remaining.Count) {
    $details = ($remaining | ForEach-Object { "$($_.ProcessId):$($_.ExecutablePath)" }) -join '; '
    throw "ORDAX packaged processes are still running after upgrade quiesce: $details"
}

Write-Output "ORDAX_UPGRADE_QUIESCED"
