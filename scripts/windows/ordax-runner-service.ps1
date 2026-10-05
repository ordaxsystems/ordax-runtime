$ErrorActionPreference = "Stop"

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Administrator privileges are required to harden the GitHub Actions runner Windows service."
    }
}

Assert-Administrator

$service = Get-CimInstance Win32_Service |
    Where-Object { $_.Name -like "actions.runner.*" } |
    Select-Object -First 1

if (-not $service) {
    throw @"
No GitHub Actions runner Windows service is installed.

On Windows, GitHub requires a runner that was configured without service mode
to be removed/reconfigured with config.cmd and service mode enabled. This
script intentionally does not bypass the official registration flow.

Reconfigure the existing runner once using GitHub Settings > Actions > Runners
from an elevated terminal, choose service mode, then run this hardening script.
The OrdaX Agent itself does not depend on this runner service for normal uptime.
"@
}

$serviceName = $service.Name
Write-Host "Runner service: $serviceName"

Set-Service -Name $serviceName -StartupType Automatic

& sc.exe failure $serviceName reset= 86400 actions= restart/5000/restart/15000/restart/30000 | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Could not configure Service Control Manager recovery for $serviceName"
}
& sc.exe failureflag $serviceName 1 | Out-Null

$current = Get-Service -Name $serviceName
if ($current.Status -ne "Running") {
    Start-Service -Name $serviceName
    $current.WaitForStatus("Running", (New-TimeSpan -Seconds 30))
}

$service = Get-CimInstance Win32_Service -Filter ("Name='" + $serviceName.Replace("'","''") + "'")
Write-Host "GitHub Actions runner Windows service hardened."
Write-Host ("State: " + $service.State)
Write-Host ("StartMode: " + $service.StartMode)
Write-Host "Recovery: restart after 5s / 15s / 30s"
