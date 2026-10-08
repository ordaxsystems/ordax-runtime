# Launch only the installed ORDAX Studio application at its authoritative Inno Setup location.
# Never start a Runtime-checkout HTML snapshot or a Python source-shell fallback.
param([switch]$Foreground)

$ErrorActionPreference = "Stop"

$uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{0D31F22D-8451-4CF4-9E34-F0D4D857F55F}_is1'
$product = Get-ItemProperty -LiteralPath $uninstallKey -ErrorAction SilentlyContinue
$installLocation = [string]$product.InstallLocation
if ([string]::IsNullOrWhiteSpace($installLocation) -or
    -not [System.IO.Path]::IsPathRooted($installLocation)) {
    throw "ORDAX Studio is not registered for the current Windows user. Install the signed product release; Runtime source is not a Studio UI."
}

$studioExe = Join-Path $installLocation "ORDAX Studio.exe"
if (-not (Test-Path -LiteralPath $studioExe -PathType Leaf)) {
    throw "The registered ORDAX Studio installation has no executable: $studioExe. Repair the installed product instead of starting source-tree UI."
}
if ($Foreground) {
    & $studioExe
    exit $LASTEXITCODE
}
Start-Process -FilePath $studioExe -WorkingDirectory (Split-Path -Parent $studioExe)
