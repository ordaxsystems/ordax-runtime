param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectPath,

    [string]$UnityVersion = "6000.6.1f1",

    [int]$TimeoutSeconds = 75
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "unity-discovery.ps1")

$project = (Resolve-Path $ProjectPath).Path
$unityResolution = Get-UnityResolution -ProjectRoot $project -RequiredVersion $UnityVersion
$unity = $unityResolution.SelectedPath

if (-not (Test-Path $unity)) {
    throw "Unity.exe not found: $unity"
}

if (-not $unityResolution.InstallationHealthy) {
    throw ("Unity installation is incomplete: " + ($unityResolution.SelectedDiagnostics.MissingComponents -join "; "))
}

$logs = Join-Path $project "Logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null

$unityLog = Join-Path $logs "unity-upm-watch-editor.log"
$upmLog = Join-Path $logs "unity-upm-watch-upm.log"

Remove-Item $unityLog,$upmLog -Force -ErrorAction SilentlyContinue

Write-Host "Launching Unity and watching UnityPackageManager.exe..."
Write-Host "Unity:   $unity"
Write-Host "Project: $project"
Write-Host ""

$unityArgs = @(
    "-batchmode",
    "-quit",
    "-projectPath", $project,
    "-logFile", $unityLog,
    "-upmLogFile", $upmLog
)

$p = Start-Process -FilePath $unity -ArgumentList $unityArgs -PassThru

$seen = @{}
$deadline = (Get-Date).AddSeconds([Math]::Max(10, $TimeoutSeconds))

while ((Get-Date) -lt $deadline) {
    try {
        $processes = Get-CimInstance Win32_Process -Filter "Name='UnityPackageManager.exe'" -ErrorAction SilentlyContinue

        foreach ($proc in $processes) {
            if (-not $seen.ContainsKey($proc.ProcessId)) {
                $seen[$proc.ProcessId] = $true
                Write-Host "UPM process detected"
                Write-Host "PID:         $($proc.ProcessId)"
                Write-Host "Parent PID:  $($proc.ParentProcessId)"
                Write-Host "CommandLine: $($proc.CommandLine)"
                Write-Host ""
            }
        }
    } catch {
        Write-Host "Watcher warning: $($_.Exception.Message)"
    }

    if ($p.HasExited) {
        break
    }

    Start-Sleep -Milliseconds 100
    $p.Refresh()
}

if (-not $p.HasExited) {
    Write-Host "Unity did not exit within $TimeoutSeconds seconds; stopping probe Unity process."
    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
} else {
    $p.WaitForExit()
    Write-Host "Unity exit code: $($p.ExitCode)"
}

Write-Host ""
Write-Host "Detected UPM process count: $($seen.Count)"
Write-Host "Unity log: $unityLog"
Write-Host "UPM log:   $upmLog"

if (Test-Path $unityLog) {
    Write-Host ""
    Write-Host "--- Unity log tail ---"
    Get-Content $unityLog -Tail 80
}

if (Test-Path $upmLog) {
    Write-Host ""
    Write-Host "--- UPM log tail ---"
    Get-Content $upmLog -Tail 120
} else {
    Write-Host ""
    Write-Host "UPM log was not created."
}
