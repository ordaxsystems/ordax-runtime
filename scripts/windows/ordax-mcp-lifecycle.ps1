# Canonical Windows lifetime and verified-upgrade handoff for the ONE OrdaX MCP Runtime.
# Scheduled Tasks is the Windows supervisor, not another agent or identity.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Install', 'Launch', 'Uninstall', 'Upgrade', 'ApplyUpgrade')]
    [string]$Operation,
    [string]$InstallerPath = '',
    [string]$InstallerSha256 = '',
    [string]$ExpectedCommit = '',
    [string]$TransactionId = ''
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$installRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs\OrdaX MCP')).TrimEnd('\')
$launcher = Join-Path $installRoot 'ORDAX Runtime.exe'
$lifecycle = Join-Path $installRoot 'ordax-mcp-lifecycle.ps1'
$watchdog = Join-Path $installRoot 'ordax-agent-watchdog.ps1'
$updatesRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'OrdaX\updates\mcp')).TrimEnd('\')
$taskName = 'OrdaX MCP'
$upgradeTaskName = 'OrdaX MCP Verified Upgrade'
$powershell = Join-Path $PSHOME 'powershell.exe'
$owner = [Security.Principal.WindowsIdentity]::GetCurrent().Name

function Assert-InstalledSource {
    if ([IO.Path]::GetFullPath($PSCommandPath) -ine $lifecycle) {
        throw 'Lifecycle registration must originate from the canonical OrdaX MCP installation'
    }
    if (!(Test-Path -LiteralPath $launcher -PathType Leaf)) {
        throw 'Canonical OrdaX MCP launcher is missing'
    }
    if (!(Test-Path -LiteralPath $watchdog -PathType Leaf)) {
        throw 'Canonical OrdaX MCP watchdog is missing'
    }
    $manifestPath = Join-Path $installRoot 'product-manifest.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.product -cne 'OrdaX MCP' -or $manifest.runtime -cne 'ORDAX Runtime.exe') {
        throw 'Installed product provenance is not OrdaX MCP'
    }
}

function Assert-OurTask {
    param($Task, [string]$ExpectedScript, [string]$ExpectedOperation)
    if ($null -eq $Task) { return }
    $actions = @($Task.Actions)
    if ($actions.Count -ne 1 -or
        [IO.Path]::GetFullPath([string]$actions[0].Execute) -ine [IO.Path]::GetFullPath($powershell) -or
        [string]$actions[0].Arguments -notlike ('*-File "' + $ExpectedScript + '" -Operation ' + $ExpectedOperation + '*')) {
        throw "Scheduled task name already belongs to another action: $($Task.TaskName)"
    }
}

function New-OwnerSettings {
    return New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
}

function New-OwnerPrincipal {
    return New-ScheduledTaskPrincipal -UserId $owner -LogonType Interactive -RunLevel Limited
}

function Save-Journal {
    param([string]$Path, $Journal)
    $Journal.updatedAt = [DateTime]::UtcNow.ToString('o')
    $tmp = Join-Path (Split-Path $Path -Parent) ('journal.' + [guid]::NewGuid().ToString('N') + '.next')
    $encoded = $Journal | ConvertTo-Json -Depth 6 -Compress
    [IO.File]::WriteAllText($tmp, $encoded, [Text.UTF8Encoding]::new($false))
    if (Test-Path -LiteralPath $Path) {
        # Windows PowerShell 5.1 / .NET Framework rejects a null backup path.
        # Use a unique sibling backup so the destination replacement remains
        # atomic even if the updater is interrupted between journal phases.
        $backup = Join-Path (Split-Path $Path -Parent) ('journal.' + [guid]::NewGuid().ToString('N') + '.previous')
        try {
            [IO.File]::Replace($tmp, $Path, $backup)
        } finally {
            if (Test-Path -LiteralPath $backup) {
                Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
            }
        }
    } else {
        [IO.File]::Move($tmp, $Path)
    }
}

switch ($Operation) {
    'Install' {
        Assert-InstalledSource
        $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Assert-OurTask $existing $lifecycle 'Launch'
        $actionArgs = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Operation Launch' -f $lifecycle
        $action = New-ScheduledTaskAction -Execute $powershell -Argument $actionArgs -WorkingDirectory $installRoot
        $triggers = @(
            (New-ScheduledTaskTrigger -AtLogOn -User $owner),
            (New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650))
        )
        $task = New-ScheduledTask -Action $action -Trigger $triggers -Settings (New-OwnerSettings) -Principal (New-OwnerPrincipal) -Description 'Single OrdaX MCP Runtime: automatic launcher recovery without a second agent'
        Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
        $registered = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
        Assert-OurTask $registered $lifecycle 'Launch'
        # The scheduled task is now the only autostart owner. Remove old logon-only fallback.
        Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'OrdaX MCP' -ErrorAction SilentlyContinue
        Write-Output 'ORDAX_MCP_LIFECYCLE_REGISTERED'
    }
    'Launch' {
        Assert-InstalledSource
        function Start-McpWatchdog {
            param([System.Diagnostics.Process]$RuntimeProcess)
            $arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -AgentPid {1} -RestartTaskName "{2}" -RequireProductDispatchReadiness -StartupGraceSeconds 60 -ProbeIntervalSeconds 10 -MaxConsecutiveFailures 6' -f $watchdog, $RuntimeProcess.Id, $taskName
            Start-Process -FilePath $powershell -ArgumentList $arguments -WindowStyle Hidden | Out-Null
        }
        # Never execute files while the trusted Inno installer is replacing them.
        if (@(Get-Process -Name 'OrdaX-MCP-Setup-*' -ErrorAction SilentlyContinue).Count -gt 0) {
            Write-Output 'ORDAX_MCP_UPGRADE_IN_PROGRESS'
            return
        }
        # During an upgrade, the installer may have started this launcher
        # before the scheduled task. Adopt its process as the single owner:
        # do not repeatedly spawn duplicate launcher attempts each minute.
        $existingLaunchers = @(Get-Process -Name 'ORDAX Runtime' -ErrorAction SilentlyContinue | Where-Object {
            try { [IO.Path]::GetFullPath($_.Path) -ieq $launcher } catch { $false }
        })
        if ($existingLaunchers.Count -gt 0) {
            Start-McpWatchdog -RuntimeProcess $existingLaunchers[0]
            $existingLaunchers[0].WaitForExit()
            # Quiesce intentionally stopped it. Do not race the file replacement.
            if (@(Get-Process -Name 'OrdaX-MCP-Setup-*' -ErrorAction SilentlyContinue).Count -gt 0) {
                Write-Output 'ORDAX_MCP_UPGRADE_IN_PROGRESS'
                return
            }
        }
        # The launcher owns the singleton mutex and Python restart loop.
        # Waiting on its exact PID keeps the Task Scheduler action alive.
        $runtime = Start-Process -FilePath $launcher -WorkingDirectory $installRoot -PassThru -ErrorAction Stop
        Start-McpWatchdog -RuntimeProcess $runtime
        $runtime.WaitForExit()
        if ($runtime.ExitCode -ne 0) {
            throw "OrdaX MCP supervisor ended with exit code $($runtime.ExitCode)"
        }
    }
    'Uninstall' {
        $existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        Assert-OurTask $existing $lifecycle 'Launch'
        if ($null -ne $existing) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
        }
        Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'OrdaX MCP' -ErrorAction SilentlyContinue
        Write-Output 'ORDAX_MCP_LIFECYCLE_UNREGISTERED'
    }
    'Upgrade' {
        Assert-InstalledSource
        if ($InstallerSha256 -cnotmatch '^[0-9a-fA-F]{64}$' -or
            $ExpectedCommit -cnotmatch '^[0-9a-f]{40}$') {
            throw 'An independently verified SHA-256 and exact target commit are mandatory'
        }
        if (![IO.Path]::IsPathRooted($InstallerPath) -or
            !(Test-Path -LiteralPath $InstallerPath -PathType Leaf) -or
            (Split-Path $InstallerPath -Leaf) -notmatch '^OrdaX-MCP-Setup-[0-9]+\.[0-9]+\.[0-9]+-x64\.exe$') {
            throw 'Installer file is not a complete canonical MCP candidate'
        }
        $source = Get-Item -LiteralPath $InstallerPath -ErrorAction Stop
        if (($source.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            $source.Length -lt 1000000 -or $source.Length -gt 500000000) {
            throw 'Installer path or size is unsafe'
        }
        # Inno Setup pads VERSIONINFO string values with spaces on Windows.
        # Compare normalized human-readable metadata, never accept a different
        # product, and still bind the exact installer bytes through SHA-256.
        if ([string]::IsNullOrWhiteSpace($source.VersionInfo.CompanyName) -or
            [string]::IsNullOrWhiteSpace($source.VersionInfo.ProductName) -or
            $source.VersionInfo.CompanyName.Trim() -cne 'ORDAX' -or
            $source.VersionInfo.ProductName.Trim() -cne 'OrdaX MCP') {
            throw 'Installer product metadata does not identify OrdaX MCP'
        }
        if ((Get-FileHash -LiteralPath $source.FullName -Algorithm SHA256).Hash -ine $InstallerSha256) {
            throw 'Installer SHA-256 differs from verified candidate'
        }
        $existing = Get-ScheduledTask -TaskName $upgradeTaskName -ErrorAction SilentlyContinue
        if ($existing -and $existing.State -eq 'Running') {
            throw 'Another canonical MCP upgrade is already active'
        }
        if ($existing) {
            $actions = @($existing.Actions)
            if ($actions.Count -ne 1 -or [string]$actions[0].Arguments -notmatch ' -Operation ApplyUpgrade ') {
                throw 'Upgrade task name is already owned by another program'
            }
        }
        New-Item -Path $updatesRoot -ItemType Directory -Force | Out-Null
        $id = [guid]::NewGuid().ToString('N')
        $tx = Join-Path $updatesRoot $id
        New-Item -Path $tx -ItemType Directory -ErrorAction Stop | Out-Null
        $staged = Join-Path $tx $source.Name
        Copy-Item -LiteralPath $source.FullName -Destination $staged -ErrorAction Stop
        if ((Get-FileHash -LiteralPath $staged -Algorithm SHA256).Hash -ine $InstallerSha256) {
            throw 'Installer changed during staging'
        }
        $applyScript = Join-Path $tx 'apply.ps1'
        Copy-Item -LiteralPath $lifecycle -Destination $applyScript -ErrorAction Stop
        $journal = [pscustomobject]@{
            schema = 'ordax.mcp-upgrade/1'; transactionId = $id; phase = 'staged'
            installerName = $source.Name; sha256 = $InstallerSha256.ToUpperInvariant()
            targetCommit = $ExpectedCommit; updatedAt = ''
        }
        Save-Journal (Join-Path $tx 'journal.json') $journal
        $args = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Operation ApplyUpgrade -TransactionId {1}' -f $applyScript, $id
        $action = New-ScheduledTaskAction -Execute $powershell -Argument $args -WorkingDirectory $tx
        $trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1))
        $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
        $task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal (New-OwnerPrincipal)
        Register-ScheduledTask -TaskName $upgradeTaskName -InputObject $task -Force | Out-Null
        Start-ScheduledTask -TaskName $upgradeTaskName -ErrorAction Stop
        Write-Output ("ORDAX_MCP_UPGRADE_QUEUED transaction={0} journal={1}" -f $id, (Join-Path $tx 'journal.json'))
    }
    'ApplyUpgrade' {
        if ($TransactionId -cnotmatch '^[0-9a-f]{32}$') { throw 'Invalid upgrade transaction ID' }
        $tx = Join-Path $updatesRoot $TransactionId
        if ([IO.Path]::GetFullPath($PSCommandPath) -ine (Join-Path $tx 'apply.ps1')) {
            throw 'Upgrade executor did not originate from its staged transaction'
        }
        $journalPath = Join-Path $tx 'journal.json'
        $journal = Get-Content -LiteralPath $journalPath -Raw -ErrorAction Stop | ConvertFrom-Json
        if ($journal.schema -cne 'ordax.mcp-upgrade/1' -or
            $journal.transactionId -cne $TransactionId -or
            $journal.phase -cne 'staged' -or
            $journal.sha256 -cnotmatch '^[0-9A-F]{64}$' -or
            $journal.targetCommit -cnotmatch '^[0-9a-f]{40}$' -or
            $journal.installerName -cnotmatch '^OrdaX-MCP-Setup-[0-9]+\.[0-9]+\.[0-9]+-x64\.exe$') {
            throw 'Upgrade transaction identity, integrity or phase is invalid'
        }
        $installer = Join-Path $tx $journal.installerName
        if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash -cne $journal.sha256) {
            throw 'Staged update failed integrity verification'
        }
        $journal.phase = 'applying'
        Save-Journal $journalPath $journal
        try {
            $log = Join-Path $tx 'installer.log'
            # Start-Process -Wait waits for the entire process tree, including the
            # intentionally persistent Runtime launched by Inno [Run]. Wait for
            # only the installer PID so successful upgrades can actually commit.
            $process = Start-Process -FilePath $installer -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/LOG=' + $log)) -PassThru -ErrorAction Stop
            if (!$process.WaitForExit(900000)) { throw 'Installer exceeded the 15-minute bound' }
            $process.Refresh()
            if ($process.ExitCode -ne 0) {
                throw "Inno installer failed exit code $($process.ExitCode)"
            }
            $manifest = Get-Content -LiteralPath (Join-Path $installRoot 'product-manifest.json') -Raw | ConvertFrom-Json
            if ($manifest.product -cne 'OrdaX MCP' -or $manifest.runtime_source_commit -cne $journal.targetCommit) {
                throw 'Installed runtime commit does not match the staged candidate'
            }
            $healthy = $false
            for ($i = 0; $i -lt 60; $i++) {
                try {
                    # O(1) Portable readiness, not the optional app/dashboard
                    # status provider which may block on filesystem or adapters.
                    $status = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/readiness' -TimeoutSec 2
                    if ($status.local_dispatch_ready -eq $true -and
                        $status.runtime.state -in @('ready','busy') -and
                        $status.runtime.transport_state -eq 'connected' -and
                        $status.runtime.product_dispatch_ready -eq $true) {
                        $healthy = $true
                        break
                    }
                } catch {}
                Start-Sleep -Seconds 2
            }
            if (!$healthy) { throw 'New Runtime failed remote heartbeat readiness verification' }
            $journal.phase = 'committed'
            Save-Journal $journalPath $journal
        } catch {
            $journal.phase = 'recovery-required'
            $journal | Add-Member -NotePropertyName error -NotePropertyValue $_.Exception.Message -Force
            Save-Journal $journalPath $journal
            throw
        }
    }
}