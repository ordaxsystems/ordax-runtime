param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectPath
)

$ErrorActionPreference = "Stop"
$resolved = (Resolve-Path $ProjectPath).Path
$projectName = Split-Path $resolved -Leaf

$candidates = Get-CimInstance Win32_Process -Filter "Name='Unity.exe'" |
    Where-Object {
        $_.CommandLine -and
        ($_.CommandLine -like "*$resolved*" -or $_.CommandLine -like "*$projectName*")
    }

if (-not $candidates) {
    $candidates = Get-Process Unity -ErrorAction SilentlyContinue |
        Where-Object {
            $_.MainWindowHandle -ne 0 -and
            $_.MainWindowTitle -like "*$projectName*"
        } |
        ForEach-Object {
            Get-CimInstance Win32_Process -Filter ("ProcessId=" + $_.Id)
        }
}

$candidate = $candidates | Select-Object -First 1
if (-not $candidate) {
    throw "No running Unity Editor process was found for $resolved"
}

$process = Get-Process -Id $candidate.ProcessId -ErrorAction Stop
if ($process.MainWindowHandle -eq 0) {
    throw "Unity process has no main window."
}

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class OrdaXPlayWindow {
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);

    [DllImport("user32.dll")]
    public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
}
"@

[OrdaXPlayWindow]::ShowWindowAsync($process.MainWindowHandle, 9) | Out-Null
Start-Sleep -Milliseconds 250
[OrdaXPlayWindow]::SetForegroundWindow($process.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 350

$shell = New-Object -ComObject WScript.Shell
if (-not $shell.AppActivate($process.Id)) {
    throw "Could not activate Unity Editor window."
}

Start-Sleep -Milliseconds 350
$shell.SendKeys("^p")
Write-Host "Play toggle sent to Unity Editor PID $($process.Id) for $resolved"
