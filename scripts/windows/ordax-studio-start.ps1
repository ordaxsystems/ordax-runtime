# Start the installed, canonical ORDAX Studio product. No Runtime-source UI fallback.
param([switch]$Foreground)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw "LOCALAPPDATA unavailable; ORDAX Studio must be installed for the current user."
}
$studioExe = Join-Path $env:LOCALAPPDATA "Programs\ORDAX\ORDAX Studio.exe"
if (-not (Test-Path -LiteralPath $studioExe -PathType Leaf)) {
    throw "ORDAX Studio is not installed at its canonical product path: $studioExe. Install the signed product release; the Runtime source does not include a UI."
}
if ($Foreground) {
    & $studioExe
    exit $LASTEXITCODE
}
Start-Process -FilePath $studioExe -WorkingDirectory (Split-Path -Parent $studioExe)
