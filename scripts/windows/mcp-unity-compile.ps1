param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectPath,

    [int]$TimeoutSeconds = 1800
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$script = Join-Path $repoRoot "scripts\mcp_unity_compile.py"

if (-not (Test-Path $python)) {
    throw ".venv Python not found. Run .\scripts\windows\mcp-start.ps1 first."
}

$resolvedProject = (Resolve-Path $ProjectPath).Path

& $python $script $resolvedProject --timeout-seconds $TimeoutSeconds
exit $LASTEXITCODE
