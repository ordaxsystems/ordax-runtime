param(
    [Parameter(Mandatory=$true)]
    [string]$ProjectPath,

    [string]$ExecuteMethod = "",

    [string]$LogFile = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "unity-discovery.ps1")

$ProjectPath = (Resolve-Path $ProjectPath).Path

if (-not (Test-Path (Join-Path $ProjectPath "Assets"))) {
    throw "ProjectPath does not contain Assets/: $ProjectPath"
}

$requiredVersion = Get-RequiredUnityVersion -Root $ProjectPath
$unityResolution = Get-UnityResolution -ProjectRoot $ProjectPath -RequiredVersion $requiredVersion
$unity = $unityResolution.SelectedPath

if (-not $unity -or -not (Test-Path $unity)) {
    if ($unityResolution.ExplicitInvalid) {
        throw $unityResolution.ExplicitError
    }
    throw "Unity $requiredVersion executable not found. Install the project editor version or set UNITY_EXE explicitly."
}

if (-not $unityResolution.InstallationHealthy) {
    if ($unityResolution.ExplicitInvalid) {
        throw $unityResolution.ExplicitError
    }
    throw ("Unity installation is incomplete: " + ($unityResolution.SelectedDiagnostics.MissingComponents -join "; "))
}

if (-not $LogFile) {
    $logs = Join-Path $ProjectPath "Logs"
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    $LogFile = Join-Path $logs "unity-cli.log"
}
else {
    $logParent = Split-Path -Parent $LogFile
    if ($logParent) {
        New-Item -ItemType Directory -Force -Path $logParent | Out-Null
    }
}

$argsList = @(
    "-batchmode",
    "-quit",
    "-projectPath", $ProjectPath,
    "-logFile", $LogFile
)

if ($ExecuteMethod) {
    $argsList += @("-executeMethod", $ExecuteMethod)
}

Write-Host "Unity:            $unity"
Write-Host "Required version: $requiredVersion"
Write-Host "Project:          $ProjectPath"
Write-Host "Method:           $ExecuteMethod"
Write-Host "Log:              $LogFile"

if (Test-Path $LogFile) {
    Remove-Item $LogFile -Force -ErrorAction SilentlyContinue
}

& $unity @argsList
$exitCode = $LASTEXITCODE
if ($null -eq $exitCode) {
    $exitCode = 1
}

$compilerErrors = $false
$errorMatches = @()

if (Test-Path $LogFile) {
    $logText = Get-Content $LogFile -Raw
    $patterns = @(
        "error CS\d{4}",
        "Scripts have compiler errors",
        "Compilation failed",
        "Aborting batchmode due to failure",
        "Aborting batchmode due to fatal error",
        "another Unity instance is running with this project open",
        "Multiple Unity instances cannot open the same project",
        "executeMethod.*could not be found"
    )

    foreach ($pattern in $patterns) {
        if ($logText -match $pattern) {
            $compilerErrors = $true
            $errorMatches += $pattern
        }
    }

    Write-Host ""
    Write-Host "----- Unity log tail -----"
    Get-Content $LogFile -Tail 250
}

if ($compilerErrors) {
    Write-Host ("Unity log contains blocking error patterns: " + ($errorMatches -join ", "))
    if ($exitCode -eq 0) {
        $exitCode = 20
    }
}

Write-Host "Unity exit code: $exitCode"
exit $exitCode
