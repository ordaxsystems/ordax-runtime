param(
    [Parameter(Mandatory=$true)]
    [string]$PythonScript,

    [string]$BlendFile = ""
)

$ErrorActionPreference = "Stop"

$blender = $env:BLENDER_EXE
if (-not $blender -or -not (Test-Path $blender)) {
    $candidate = Get-ChildItem "C:\Program Files\Blender Foundation\Blender *\blender.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -First 1

    if ($candidate) { $blender = $candidate.FullName }
}

if (-not $blender -or -not (Test-Path $blender)) {
    throw "Blender executable not found. Set BLENDER_EXE."
}

if (-not (Test-Path $PythonScript)) {
    throw "Python script not found: $PythonScript"
}

$argsList = @("--background")

if ($BlendFile) {
    if (-not (Test-Path $BlendFile)) {
        throw "Blend file not found: $BlendFile"
    }
    $argsList += $BlendFile
}

$argsList += @("--python", $PythonScript)

& $blender @argsList
exit $LASTEXITCODE
