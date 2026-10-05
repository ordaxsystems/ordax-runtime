param(
    [string]$Branch = "main"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$installer = Join-Path $repoRoot "scripts\windows\ordax-agent-install.ps1"

if (-not (Test-Path (Join-Path $repoRoot ".git"))) {
    throw "This recovery script must run from the managed mcp-blender Git checkout."
}

$dirty = git -C $repoRoot status --porcelain --untracked-files=no
if ($LASTEXITCODE -ne 0) { throw "Could not read Git status." }
if ($dirty) {
    throw "Tracked local changes exist. Recovery refuses to overwrite them."
}

$previous = (git -C $repoRoot rev-parse HEAD).Trim()
if (-not $previous) { throw "Could not resolve current commit." }

$remoteRef = "refs/remotes/origin/$Branch"
$fetchRefspec = "refs/heads/${Branch}:$remoteRef"
git -c core.fsmonitor=false -C $repoRoot fetch origin $fetchRefspec
if ($LASTEXITCODE -ne 0) { throw "Could not fetch $Branch into $remoteRef." }

$currentBranch = (git -C $repoRoot rev-parse --abbrev-ref HEAD).Trim()
if ($currentBranch -ne $Branch) {
    git -C $repoRoot checkout $Branch
    if ($LASTEXITCODE -ne 0) { throw "Could not checkout $Branch." }
}

git -c core.fsmonitor=false -C $repoRoot merge --ff-only $remoteRef
if ($LASTEXITCODE -ne 0) {
    throw "Recovery branch is not fast-forwardable. No force/reset was performed."
}

if (-not (Test-Path $python)) {
    throw "Managed .venv is missing: $python"
}

& $python -m compileall -q (Join-Path $repoRoot "mcp_blender_unity") (Join-Path $repoRoot "ordax_dev_agent") (Join-Path $repoRoot "ordax_device_agent")
if ($LASTEXITCODE -ne 0) {
    Write-Warning "Updated code failed compile verification. Restoring previous commit $previous."
    git -C $repoRoot reset --hard $previous
    throw "Remote update failed compile verification and was rolled back."
}

$taskName = "OrdaX Dev Agent"
try {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
} catch {
    Write-Warning "Could not stop previous scheduled task cleanly: $($_.Exception.Message)"
}

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer -StartNow
if ($LASTEXITCODE -ne 0) {
    throw "Scheduled-task installation/start failed."
}

$deadline = [DateTime]::UtcNow.AddSeconds(60)
while ([DateTime]::UtcNow -lt $deadline) {
    try {
        $status = Invoke-RestMethod -Uri "http://127.0.0.1:8765/status" -TimeoutSec 2
        Write-Host "OrdaX Dev Agent recovered."
        $status | ConvertTo-Json -Depth 8
        exit 0
    } catch {
        Start-Sleep -Seconds 1
    }
}

throw "Scheduled task started but the OrdaX health endpoint did not recover within 60 seconds."
