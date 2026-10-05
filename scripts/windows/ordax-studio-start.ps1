param(
    [string]$PythonPath = "",
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

if (-not $PythonPath) {
    $repoPythonw = Join-Path $RepoRoot ".venv\Scripts\pythonw.exe"
    $repoPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $repoPythonw) { $PythonPath = $repoPythonw }
    elseif (Test-Path $repoPython) { $PythonPath = $repoPython }
    else {
        $command = Get-Command pythonw.exe -ErrorAction SilentlyContinue
        if (-not $command) { $command = Get-Command python.exe -ErrorAction Stop }
        $PythonPath = $command.Source
    }
}

if (-not (Test-Path $PythonPath)) {
    throw "Python executable not found: $PythonPath"
}

$env:PYTHONPATH = $RepoRoot
$arguments = @("-m", "ordax_studio.web_desktop")
if ($Foreground) {
    & $PythonPath @arguments
    exit $LASTEXITCODE
}

Start-Process -FilePath $PythonPath -ArgumentList $arguments -WorkingDirectory $RepoRoot
