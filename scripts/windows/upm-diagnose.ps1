param(
    [string]$UnityVersion = "6000.6.1f1",
    [int]$Minutes = 30
)

$ErrorActionPreference = "Continue"
. (Join-Path $PSScriptRoot "unity-discovery.ps1")

$unityResolution = Get-UnityResolution -RequiredVersion $UnityVersion
$unity = $unityResolution.SelectedPath
$unityRoot = if ($unity) { Split-Path -Parent $unity } else { $null }
$upm = if ($unityRoot) {
    Join-Path $unityRoot "Data\Resources\PackageManager\Server\UnityPackageManager.exe"
} else {
    $null
}

Write-Host "=== Unity Package Manager local diagnostics ==="
Write-Host "Unity version: $UnityVersion"
Write-Host "Unity:         $unity"
Write-Host "UPM path:      $upm"
Write-Host ""

if (-not (Test-Path $upm)) {
    Write-Host "FAIL: UnityPackageManager.exe was not found."
    exit 2
}

$item = Get-Item $upm
$sig = Get-AuthenticodeSignature $upm

Write-Host "Executable:     OK"
Write-Host "File version:   $($item.VersionInfo.FileVersion)"
Write-Host "Signature:      $($sig.Status)"
if ($sig.StatusMessage) {
    Write-Host "Signature msg:  $($sig.StatusMessage)"
}
Write-Host ""

$proc = Get-Process UnityPackageManager -ErrorAction SilentlyContinue
if ($proc) {
    Write-Host "Running process:"
    $proc | Select-Object Id,ProcessName,StartTime,Path | Format-Table -AutoSize
} else {
    Write-Host "Running process: none"
}
Write-Host ""

Write-Host "DNS checks:"
$dnsNames = @(
    "packages.unity.com",
    "packages-v2.unity.com",
    "api.hub-proxy.unity3d.com",
    "config.hub-proxy.unity3d.com"
)
foreach ($name in $dnsNames) {
    try {
        $answer = Resolve-DnsName $name -Type A -ErrorAction Stop |
            Where-Object { $_.IPAddress } |
            Select-Object -First 2 -ExpandProperty IPAddress
        $answerText = $answer -join ", "
        if ([string]::IsNullOrWhiteSpace($answerText)) {
            $answerText = "no A record"
        }
        Write-Host ("  {0,-36} {1}" -f $name, $answerText)
    } catch {
        Write-Host ("  {0,-36} FAIL: {1}" -f $name, $_.Exception.Message)
    }
}
Write-Host ""

$start = (Get-Date).AddMinutes(-[Math]::Abs($Minutes))

Write-Host "Recent Application events mentioning UnityPackageManager:"
try {
    $events = Get-WinEvent -FilterHashtable @{LogName="Application"; StartTime=$start} -ErrorAction Stop |
        Where-Object {
            $_.Message -match "UnityPackageManager|UnityPackageManager\.exe|Package Manager"
        } |
        Select-Object -First 20 TimeCreated,Id,ProviderName,LevelDisplayName,Message

    if ($events) {
        $events | Format-List
    } else {
        Write-Host "  none"
    }
} catch {
    Write-Host "  unable to read Application log: $($_.Exception.Message)"
}
Write-Host ""

Write-Host "Recent Windows Defender events mentioning Unity:"
try {
    $defender = Get-WinEvent -FilterHashtable @{
        LogName="Microsoft-Windows-Windows Defender/Operational"
        StartTime=$start
    } -ErrorAction Stop |
        Where-Object {
            $_.Message -match "UnityPackageManager|Unity\.exe|Unity Hub"
        } |
        Select-Object -First 20 TimeCreated,Id,LevelDisplayName,Message

    if ($defender) {
        $defender | Format-List
    } else {
        Write-Host "  none"
    }
} catch {
    Write-Host "  unable to read Defender log: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "=== End diagnostics ==="
