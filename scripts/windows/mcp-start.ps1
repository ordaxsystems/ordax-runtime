param(
    [switch]$Reinstall
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$venv = Join-Path $repoRoot ".venv"
$python = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "Creating virtual environment..."
    python -m venv $venv
}

$needsInstall = $Reinstall

if (-not $needsInstall) {
    & $python -c "from mcp.server.fastmcp import FastMCP; import ordax_studio, ordax_dev_agent; print('ORDAX Studio MCP dependencies OK')" 2>$null
    if ($LASTEXITCODE -ne 0) {
        $needsInstall = $true
    }
}

if ($needsInstall) {
    Write-Host "Installing ORDAX Studio MCP dependencies..."
    & $python -m pip install --upgrade pip
    & $python -m pip install -e $repoRoot --upgrade

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install ORDAX Studio MCP dependencies."
    }

    & $python -c "from mcp.server.fastmcp import FastMCP; import ordax_studio, ordax_dev_agent; print('ORDAX Studio MCP dependencies OK')"
    if ($LASTEXITCODE -ne 0) {
        throw "ORDAX Studio MCP dependency validation failed after installation."
    }
}

Write-Host "Starting ORDAX Studio MCP (historical connector alias: mcp-blender)..."
& $python -m ordax_studio.mcp_server
exit $LASTEXITCODE
