param(
    [switch]$RequireBlender,
    [switch]$RequireUnity,
    [string]$ProjectPath = ""
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "unity-discovery.ps1")

function Find-Blender {
    if ($env:BLENDER_EXE -and (Test-Path $env:BLENDER_EXE)) {
        return $env:BLENDER_EXE
    }

    $items = Get-ChildItem "C:\Program Files\Blender Foundation\Blender *\blender.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending

    if ($items) { return $items[0].FullName }
    return $null
}

if ($ProjectPath) {
    $ProjectPath = (Resolve-Path $ProjectPath).Path
}

$requiredVersion = Get-RequiredUnityVersion -Root $ProjectPath
$blender = Find-Blender
$unityResolution = Get-UnityResolution -ProjectRoot $ProjectPath -RequiredVersion $requiredVersion
$unity = $unityResolution.SelectedPath
$python = Get-Command python -ErrorAction SilentlyContinue
$git = Get-Command git -ErrorAction SilentlyContinue

$unityDiagnostics = $unityResolution.SelectedDiagnostics

Write-Host "Blender:          $blender"
Write-Host "Unity:            $unity"
Write-Host "Required Unity:   $requiredVersion"
if ($unity) {
    Write-Host "API compatibility: $($unityDiagnostics.ApiCompatibilityLevel)"
    Write-Host "Unity refs required: $($unityDiagnostics.ReferenceAssembliesRequired)"
    Write-Host "Unity refs:       $($unityDiagnostics.ReferenceAssembliesAvailable)"
    Write-Host "UPM:              $($unityDiagnostics.PackageManagerAvailable)"
    if ($unityDiagnostics.MissingComponents.Count -gt 0) {
        Write-Host "Missing components:"
        $unityDiagnostics.MissingComponents | ForEach-Object { Write-Host "  $_" }
    }
}
Write-Host "Python:           $($python.Source)"
Write-Host "Git:              $($git.Source)"

$failed = $false

if (-not $blender) {
    Write-Warning "Blender was not found."
    if ($RequireBlender) { $failed = $true }
}

if (-not $unity) {
    if ($requiredVersion) {
        Write-Warning "Unity $requiredVersion required by project was not found."
    }
    else {
        Write-Warning "Unity was not found."
    }

    if ($RequireUnity) { $failed = $true }
}
elseif ($unityDiagnostics -and -not $unityDiagnostics.InstallationHealthy) {
    if ($unityResolution.ExplicitInvalid) {
        Write-Warning $unityResolution.ExplicitError
    }
    else {
        Write-Warning "Unity Editor installation is incomplete: required files are missing."
    }
    if ($RequireUnity) { $failed = $true }
}

if (-not $python) {
    Write-Warning "Python was not found."
}

if (-not $git) {
    Write-Warning "Git was not found."
}

if ($failed) {
    exit 1
}

exit 0
