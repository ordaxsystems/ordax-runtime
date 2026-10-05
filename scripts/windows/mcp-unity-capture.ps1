param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectPath,

    [string]$OutputPath = "",

    [int]$Width = 1280,

    [int]$Height = 720,

    [int]$WarmupFrames = 120,

    [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$script = Join-Path $repoRoot "scripts\mcp_unity_capture.py"

if (-not (Test-Path $python)) {
    throw ".venv Python not found. Run .\scripts\windows\mcp-start.ps1 first."
}

$resolvedProject = (Resolve-Path $ProjectPath).Path
$arguments = @(
    $script,
    $resolvedProject,
    "--width", $Width,
    "--height", $Height,
    "--warmup-frames", $WarmupFrames,
    "--timeout-seconds", $TimeoutSeconds
)

if ($OutputPath) {
    $arguments += @("--output-path", $OutputPath)
}

& $python @arguments
exit $LASTEXITCODE
