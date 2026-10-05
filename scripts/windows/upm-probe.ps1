param(
    [string]$UnityVersion = "6000.6.1f1",
    [int]$WaitSeconds = 6
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "unity-discovery.ps1")

$unityResolution = Get-UnityResolution -RequiredVersion $UnityVersion
$unity = $unityResolution.SelectedPath
$unityRoot = if ($unity) { Split-Path -Parent $unity } else { $null }
$upm = if ($unityRoot) {
    Join-Path $unityRoot "Data\Resources\PackageManager\Server\UnityPackageManager.exe"
} else {
    $null
}

if (-not (Test-Path $upm)) {
    Write-Host "FAIL: UnityPackageManager.exe not found:"
    Write-Host $upm
    exit 2
}

$probeRoot = Join-Path $env:TEMP "mcp-blender-upm-probe"
New-Item -ItemType Directory -Force -Path $probeRoot | Out-Null

function Invoke-UpmProbe {
    param(
        [string]$Name,
        [string[]]$Arguments
    )

    $stdout = Join-Path $probeRoot ($Name + "-stdout.txt")
    $stderr = Join-Path $probeRoot ($Name + "-stderr.txt")
    Remove-Item $stdout,$stderr -Force -ErrorAction SilentlyContinue

    Write-Host ""
    Write-Host "=== $Name ==="
    Write-Host "Executable: $upm"
    if ($Arguments -and $Arguments.Count -gt 0) {
        Write-Host ("Arguments:  " + ($Arguments -join " "))
    } else {
        Write-Host "Arguments:  <none>"
    }

    try {
        $startArgs = @{
            FilePath = $upm
            PassThru = $true
            WindowStyle = "Hidden"
            RedirectStandardOutput = $stdout
            RedirectStandardError = $stderr
        }

        if ($Arguments -and $Arguments.Count -gt 0) {
            $startArgs["ArgumentList"] = $Arguments
        }

        $p = Start-Process @startArgs
        Write-Host "PID:        $($p.Id)"

        $deadline = (Get-Date).AddSeconds([Math]::Max(1, $WaitSeconds))
        while (-not $p.HasExited -and (Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 250
            $p.Refresh()
        }

        if ($p.HasExited) {
            $p.WaitForExit()
            Write-Host "Result:     exited"
            Write-Host "Exit code:  $($p.ExitCode)"
        } else {
            Write-Host "Result:     process stayed alive for $WaitSeconds seconds"
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
            Write-Host "Cleanup:    probe process stopped"
        }
    } catch {
        Write-Host "Result:     failed to launch"
        Write-Host "Error:      $($_.Exception.Message)"
    }

    if (Test-Path $stdout) {
        $text = Get-Content $stdout -Raw -ErrorAction SilentlyContinue
        if (-not [string]::IsNullOrWhiteSpace($text)) {
            Write-Host "--- stdout ---"
            Write-Host $text
        }
    }

    if (Test-Path $stderr) {
        $text = Get-Content $stderr -Raw -ErrorAction SilentlyContinue
        if (-not [string]::IsNullOrWhiteSpace($text)) {
            Write-Host "--- stderr ---"
            Write-Host $text
        }
    }
}

Invoke-UpmProbe -Name "version" -Arguments @("--version")
Invoke-UpmProbe -Name "help" -Arguments @("--help")
Invoke-UpmProbe -Name "plain-launch" -Arguments @()

Write-Host ""
Write-Host "Probe files: $probeRoot"
