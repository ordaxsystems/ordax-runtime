param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectPath
)

$ErrorActionPreference = "Stop"
$resolved = (Resolve-Path $ProjectPath).Path
$projectName = Split-Path $resolved -Leaf

$allUnity = @(Get-Process Unity -ErrorAction SilentlyContinue)
$candidates = @(
    $allUnity |
        Where-Object {
            $_.MainWindowHandle -ne 0 -and
            $_.MainWindowTitle -like "*$projectName*"
        }
)

if ($candidates.Count -eq 0 -and $allUnity.Count -eq 1) {
    $lockPath = Join-Path $resolved "Temp\UnityLockfile"
    if (Test-Path $lockPath) {
        # A single Unity process plus this project's active lock is a safe
        # recovery target even when a modal dialog replaced the normal title.
        $candidates = @($allUnity[0])
    }
}

$process = $candidates | Select-Object -First 1
if (-not $process) {
    throw "No uniquely identifiable running Unity Editor process was found for $resolved"
}
if ($process.MainWindowHandle -eq 0) {
    throw "Unity process has no main window."
}

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class OrdaXWindow {
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
}
"@

[OrdaXWindow]::ShowWindowAsync($process.MainWindowHandle, 9) | Out-Null
Start-Sleep -Milliseconds 250
[OrdaXWindow]::SetForegroundWindow($process.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 350

$shell = New-Object -ComObject WScript.Shell
if (-not $shell.AppActivate($process.Id)) {
    throw "Could not activate Unity Editor window."
}

Start-Sleep -Milliseconds 350

# Unity Editor: Assets > Refresh
$shell.SendKeys("^r")

Write-Host "Refresh shortcut sent to Unity Editor PID $($process.Id) for $resolved"
