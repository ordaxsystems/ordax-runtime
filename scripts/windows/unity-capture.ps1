param(
    [Parameter(Mandatory=$true)]
    [string]$ProjectPath,
    [string]$OutputPath = "",
    [int]$Width = 1280,
    [int]$Height = 720,
    [int]$WarmupFrames = 120,
    [int]$TimeoutSeconds = 900,
    [string]$LogFile = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "unity-discovery.ps1")

$ProjectPath = (Resolve-Path $ProjectPath).Path
if (-not (Test-Path (Join-Path $ProjectPath "Assets"))) { throw "ProjectPath does not contain Assets/: $ProjectPath" }

$requiredVersion = Get-RequiredUnityVersion -Root $ProjectPath
$unityResolution = Get-UnityResolution -ProjectRoot $ProjectPath -RequiredVersion $requiredVersion
$unity = $unityResolution.SelectedPath
if (-not $unity -or -not (Test-Path $unity)) {
    if ($unityResolution.ExplicitInvalid) { throw $unityResolution.ExplicitError }
    throw "Unity $requiredVersion executable not found."
}
if (-not $unityResolution.InstallationHealthy) { throw ("Unity installation is incomplete: " + ($unityResolution.SelectedDiagnostics.MissingComponents -join "; ")) }

if (-not $OutputPath) {
    $captureDir = Join-Path $ProjectPath "Artifacts\UnityCaptures"
    New-Item -ItemType Directory -Force -Path $captureDir | Out-Null
    $OutputPath = Join-Path $captureDir "prototype-latest.png"
}
$outputParent = Split-Path -Parent $OutputPath
if ($outputParent) { New-Item -ItemType Directory -Force -Path $outputParent | Out-Null }

if (-not $LogFile) {
    $logs = Join-Path $ProjectPath "Logs"
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    $LogFile = Join-Path $logs "unity-visual-capture.log"
}
if (Test-Path $OutputPath) { Remove-Item $OutputPath -Force }

$argsList = @(
    "-batchmode",
    "-projectPath", $ProjectPath,
    "-logFile", $LogFile,
    "-executeMethod", "HORDAX.EditorTools.AutomationCapture.CapturePrototype",
    "-hordaxCapturePath", $OutputPath,
    "-hordaxCaptureWidth", $Width,
    "-hordaxCaptureHeight", $Height,
    "-hordaxCaptureWarmupFrames", $WarmupFrames
)

Write-Host "Unity:            $unity"
Write-Host "Project:          $ProjectPath"
Write-Host "Capture:          $OutputPath"
Write-Host "Resolution:       $Width x $Height"
Write-Host "Warmup frames:    $WarmupFrames"

$process = Start-Process -FilePath $unity -ArgumentList $argsList -PassThru -NoNewWindow
if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
    try { $process.Kill() } catch {}
    throw "Unity visual capture timed out after $TimeoutSeconds seconds."
}

$exitCode = $process.ExitCode
$exists = Test-Path $OutputPath
$size = if ($exists) { (Get-Item $OutputPath).Length } else { 0 }
if (Test-Path $LogFile) {
    Write-Host ""
    Write-Host "----- Unity capture log tail -----"
    Get-Content $LogFile -Tail 220
}
Write-Host "Unity exit code: $exitCode"
Write-Host "Capture exists:  $exists"
Write-Host "Capture bytes:   $size"
if ($exitCode -ne 0) { exit $exitCode }
if (-not $exists -or $size -le 0) { exit 21 }
exit 0
