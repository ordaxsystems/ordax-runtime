param(
    [switch]$NonInteractive,
    [int]$ReadyTimeoutSeconds = 120,
    [string]$ControlPlaneUrl = 'https://ordax-control-plane-v3.ordax-ac1ca1b50d09.workers.dev'
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($ControlPlaneUrl)) {
    throw 'CLOUDFLARE_CONTROL_PLANE_URL_REQUIRED'
}
$ControlPlaneUrl = $ControlPlaneUrl.TrimEnd('/')
$stateDir = Join-Path $env:LOCALAPPDATA 'OrdaX\DevAgent'
$repo = Join-Path $stateDir 'src'
$remote = 'https://github.com/washingtonmsdj/mcp-blender.git'
$taskName = 'OrdaX Dev Agent'
$mutex = New-Object System.Threading.Mutex($false, 'Local\OrdaXDeviceSetup')
$restartExisting = $false
$changedLocation = $false
if (-not $mutex.WaitOne(0)) { throw 'SETUP_ALREADY_RUNNING' }
try {
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
    foreach ($dependency in @(
        @{Command='git'; Package='Git.Git'},
        @{Command='python'; Package='Python.Python.3.13'},
        @{Command='gh'; Package='GitHub.cli'}
    )) {
        if (-not (Get-Command $dependency.Command -ErrorAction SilentlyContinue)) {
            if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw "PREREQUISITE_REQUIRED: $($dependency.Package)" }
            & winget install --id $dependency.Package --exact --source winget --silent --accept-package-agreements --accept-source-agreements
            if ($LASTEXITCODE -ne 0) { throw "PREREQUISITE_INSTALL_FAILED: $($dependency.Package)" }
            $env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
        }
    }
    if (-not (Test-Path (Join-Path $repo '.git'))) {
        if (Test-Path $repo) { throw 'MANAGED_DIRECTORY_NOT_A_CHECKOUT' }
        & git clone --branch main --single-branch $remote $repo
        if ($LASTEXITCODE -ne 0) { throw 'CHECKOUT_UNAVAILABLE_RETRY_SETUP' }
    }
    $origin = (& git -C $repo remote get-url origin).Trim()
    if ($LASTEXITCODE -ne 0 -or $origin -notin @($remote, 'https://github.com/washingtonmsdj/mcp-blender', 'git@github.com:washingtonmsdj/mcp-blender.git')) { throw 'MANAGED_REMOTE_MISMATCH' }
    $dirty = & git -c core.fsmonitor=false -C $repo status --porcelain --untracked-files=no
    if ($LASTEXITCODE -ne 0 -or $dirty) { throw 'MANAGED_CHECKOUT_DIRTY_PRESERVED' }
    & git -C $repo fetch --quiet origin 'refs/heads/main:refs/remotes/origin/main'
    if ($LASTEXITCODE -ne 0) { throw 'UPDATE_OFFLINE_RETRY_SETUP' }
    & git -C $repo merge-base --is-ancestor HEAD origin/main
    if ($LASTEXITCODE -ne 0) { throw 'MANAGED_CHECKOUT_DIVERGED_PRESERVED' }

    # Do not interrupt a modeling job for an installation update.
    try { $health = Invoke-RestMethod 'http://127.0.0.1:8765/status' -TimeoutSec 3 } catch { $health = $null }
    if ($health -and $health.runtime.state -eq 'busy') { throw 'AGENT_BUSY_RETRY_SETUP' }
    $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($existingTask) {
        Disable-ScheduledTask -TaskName $taskName | Out-Null
        Stop-ScheduledTask -TaskName $taskName
        $restartExisting = $true
    }
    # Old task_entry/pythonw children can survive a task action replacement.
    # Match the managed executable and exact Agent modules; never stop Blender.
    $managedPython = Join-Path $repo '.venv\Scripts\python'
    $oldAgents = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'"
    foreach ($oldAgent in $oldAgents) {
        if ($oldAgent.CommandLine -and
            $oldAgent.CommandLine.Contains($managedPython) -and
            $oldAgent.CommandLine -match '-m ordax_dev_agent\.(task_entry|main)(\s|$)') {
            Stop-Process -Id $oldAgent.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
    & git -C $repo merge --ff-only origin/main
    if ($LASTEXITCODE -ne 0) { throw 'UPDATE_FAILED' }
    Push-Location -LiteralPath $repo
    $changedLocation = $true

    $python = Join-Path $repo '.venv\Scripts\python.exe'
    $healthy = $false
    if (Test-Path $python) {
        & $python -c "import sys; assert sys.version_info >= (3,11); import httpx, mcp, websockets, ordax_dev_agent.device_setup" 2>$null
        $healthy = $LASTEXITCODE -eq 0
    }
    if (-not $healthy) {
        $basePython = Get-Command python -ErrorAction Stop
        & $basePython.Source -c "import sys; assert sys.version_info >= (3,11)"
        if ($LASTEXITCODE -ne 0) { throw 'PYTHON_311_REQUIRED' }
        & $basePython.Source -m venv (Join-Path $repo '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'VENV_REPAIR_FAILED' }
    }
    $contract = (Get-FileHash (Join-Path $repo 'pyproject.toml') -Algorithm SHA256).Hash
    $contractPath = Join-Path $stateDir 'setup-install-contract.txt'
    $previousContract = if (Test-Path $contractPath) { (Get-Content $contractPath -Raw).Trim() } else { '' }
    if (-not $healthy -or $previousContract -ne $contract) {
        & $python -m pip install --disable-pip-version-check -e $repo
        if ($LASTEXITCODE -ne 0) { throw 'EDITABLE_INSTALL_FAILED_RETRY_SETUP' }
        [System.IO.File]::WriteAllText("$contractPath.next", $contract)
        Move-Item -LiteralPath "$contractPath.next" -Destination $contractPath -Force
    }

    # Install the external supervisor even if pairing/network needs a retry.
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo 'scripts\windows\ordax-agent-install.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'TASK_INSTALL_FAILED' }
    $setupArgs = @(
        '-m', 'ordax_dev_agent.device_setup',
        '--control-plane-url', $ControlPlaneUrl
    )
    if (-not $NonInteractive) { $setupArgs += '--interactive' }
    & $python @setupArgs
    $pairingCode = $LASTEXITCODE
    Enable-ScheduledTask -TaskName $taskName | Out-Null
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Start-ScheduledTask -TaskName $taskName
    $restartExisting = $false
    if ($pairingCode -ne 0) { throw 'SETUP_PENDING_AUTH_OR_NETWORK_RETRY_SETUP' }

    $deadline = [DateTime]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $health = Invoke-RestMethod 'http://127.0.0.1:8765/status' -TimeoutSec 3
            $config = Get-Content (Join-Path $stateDir 'agent-settings.json') -Raw | ConvertFrom-Json
            $task = Get-ScheduledTask -TaskName $taskName
            $registered = @($health.projects | ForEach-Object { $_.slug })
            $cercoExists = Test-Path (Join-Path $env:USERPROFILE 'Documents\github\cerco-no-interior-mvp')
            $configuredUrl = if ($config.control_plane_url) { ([string]$config.control_plane_url).TrimEnd('/') } else { '' }
            $controlPlaneMatches = $configuredUrl -eq $ControlPlaneUrl
            if ($health.control_plane_protocol -eq 'cloudflare-v3' -and
                $controlPlaneMatches -and
                $health.device_id -eq $config.device_id -and
                $health.runtime.last_heartbeat_at -and
                $health.runtime.supervisor_pid -gt 0 -and
                ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - $health.runtime.last_heartbeat_at) -lt 60 -and
                $task.Actions.Arguments -match 'bootstrap\\ordax-agent-bootstrap.ps1' -and
                (-not $cercoExists -or $registered -contains 'cerco-no-interior-mvp')) {
                Write-Output 'ORDAX_DEVICE_AGENT=READY'
                exit 0
            }
        } catch { }
        Start-Sleep -Seconds 2
    }
    throw 'AGENT_NOT_READY_CHECK_LOCAL_STATUS'
} finally {
    if ($changedLocation) { Pop-Location }
    if ($restartExisting) {
        Enable-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue | Out-Null
        Start-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    }
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
