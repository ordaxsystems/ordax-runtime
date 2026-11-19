# Canonical Windows lifetime and verified-upgrade handoff for the ONE OrdaX MCP Runtime.
# Scheduled Tasks is the Windows supervisor, not another agent or identity.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Install', 'Launch', 'Uninstall', 'Upgrade', 'ApplyUpgrade')]
    [string]$Operation,
    [string]$PackagePath = '',
    [string]$PackageSha256 = '',
    [string]$ExpectedCommit = '',
    [string]$TransactionId = ''
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$installRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs\OrdaX MCP')).TrimEnd('\')
$launcher = Join-Path $installRoot 'ORDAX Runtime.exe'
$lifecycle = Join-Path $installRoot 'ordax-mcp-lifecycle.ps1'
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

function Invoke-BoundedSchtasks {
    param([string[]]$Arguments)
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = Join-Path $env:SystemRoot 'System32\schtasks.exe'
    $start.Arguments = ($Arguments | ForEach-Object { '"' + $_.Replace('"','\"') + '"' }) -join ' '
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $enc = [Text.Encoding]::GetEncoding([Globalization.CultureInfo]::CurrentCulture.TextInfo.OEMCodePage)
    $start.StandardOutputEncoding = $enc
    $start.StandardErrorEncoding = $enc
    $process = [Diagnostics.Process]::Start($start)
    if ($null -eq $process) { throw 'Scheduler query could not start' }
    try {
        if (!$process.WaitForExit(10000)) {
            $process.Kill()
            [void]$process.WaitForExit(2000)
            throw 'Scheduler query exceeded its 10-second deadline'
        }
        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            Stdout = $process.StandardOutput.ReadToEnd()
            Stderr = $process.StandardError.ReadToEnd()
        }
    } finally { $process.Dispose() }
}
function Get-CanonicalScheduledTask {
    param([string]$Name)
    $xmlResult = Invoke-BoundedSchtasks -Arguments @('/Query','/TN',$Name,'/XML')
    if ($xmlResult.ExitCode -ne 0) {
        if ($xmlResult.ExitCode -eq 1 -and $xmlResult.Stderr -match '(?i)(cannot find|encontrar|does not exist)') {
            return $null
        }
        throw "Scheduler task metadata unavailable: $Name"
    }
    [xml]$doc = $xmlResult.Stdout
    $ns = [Xml.XmlNamespaceManager]::new($doc.NameTable)
    $ns.AddNamespace('t','http://schemas.microsoft.com/windows/2004/02/mit/task')
    $execNodes = $doc.SelectNodes('/t:Task/t:Actions/t:Exec',$ns)
    if ($execNodes.Count -ne 1) { throw "Unexpected scheduler task actions: $Name" }
    $commands = @($execNodes | ForEach-Object {
        [pscustomobject]@{
            Execute = $_.SelectSingleNode('t:Command',$ns).InnerText
            Arguments = $_.SelectSingleNode('t:Arguments',$ns).InnerText
        }
    })
    $principal = $doc.SelectSingleNode('/t:Task/t:Principals/t:Principal/t:UserId',$ns)
    $selfSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    if ($null -eq $principal -or $principal.InnerText -cne $selfSid) {
        throw "Scheduler task owner mismatch: $Name"
    }
    $stateResult = Invoke-BoundedSchtasks -Arguments @('/Query','/TN',$Name,'/FO','CSV','/V')
    if ($stateResult.ExitCode -ne 0) { throw "Scheduler task state unavailable: $Name" }
    $rows = @($stateResult.Stdout | ConvertFrom-Csv)
    if ($rows.Count -lt 1) { throw "Scheduler task state result is empty: $Name" }
    $stateValues = @()
    foreach ($row in $rows) {
        $fields = @($row.PSObject.Properties)
        if ($fields.Count -lt 4 -or [string]$fields[1].Value -cne ('\' + $Name)) {
            throw "Scheduler task state includes another task: $Name"
        }
        $stateValues += [string]$fields[3].Value
    }
    $uniqueStates = @($stateValues | Select-Object -Unique)
    if ($uniqueStates.Count -ne 1) { throw "Inconsistent scheduler task states: $Name" }
    $stateText = $uniqueStates[0]
    $state = switch -Regex ($stateText) {
        '^(Ready|Pronto)$' { 'Ready'; break }
        '^(Running|Em execu)' { 'Running'; break }
        '^(Disabled|Desativado)$' { 'Disabled'; break }
        '^(Queued|Na fila)' { 'Queued'; break }
        default { throw "Unknown scheduler task state: $Name" }
    }
    return [pscustomobject]@{
        TaskName = $Name
        State = $state
        Actions = $commands
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


# A single portable DEV package holds the exact installed Runtime stage.
# Validate archive paths and the in-archive source manifest before ever
# quiescing the installed MCP. This is not a second updater or identity owner.
function Assert-DevArchive {
    param([string]$ArchivePath, [string]$Commit, [string]$Destination = '')
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($ArchivePath)
    $total = [long]0
    $files = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    try {
        if ($zip.Entries.Count -lt 10 -or $zip.Entries.Count -gt 30000) {
            throw 'Portable DEV archive entry count invalid'
        }
        foreach ($entry in $zip.Entries) {
            $relative = [string]$entry.FullName.Replace('/', '\')
            if (!$relative -or $relative.StartsWith('\') -or
                $relative -match '^[A-Za-z]:' -or $relative.Contains(':')) {
                throw 'Portable DEV archive contains an absolute or invalid path'
            }
            $segments = $relative.TrimEnd('\').Split('\')
            if ($segments -contains '..' -or $segments -contains '.') {
                throw 'Portable DEV archive contains path traversal'
            }
            $type = ([int64]$entry.ExternalAttributes -shr 16) -band 0xF000
            if ($type -eq 0xA000) { throw 'Portable DEV archive contains a symlink' }
            if ($entry.Length -lt 0 -or $entry.Length -gt 200MB) {
                throw 'Portable DEV archive contains an oversized entry'
            }
            $total += [long]$entry.Length
            if ($total -gt 1024MB) { throw 'Portable DEV archive expansion exceeds 1 GiB' }
            if (!$files.Add($relative)) { throw 'Portable DEV archive contains duplicate paths' }
            if ($Destination) {
                $full = [IO.Path]::GetFullPath((Join-Path $Destination $relative))
                $prefix = [IO.Path]::GetFullPath($Destination).TrimEnd('\') + '\'
                if (!$full.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) {
                    throw 'Portable DEV archive destination escaped staging root'
                }
            }
        }
        foreach ($required in @(
            'ORDAX Runtime.exe', 'product-manifest.json',
            'ordax-mcp-lifecycle.ps1', 'ordax-upgrade-quiesce.ps1',
            'runtime\python.exe', 'runtime\pythonw.exe'
        )) {
            if (!$files.Contains($required)) {
                throw "Portable DEV archive required file absent: $required"
            }
        }
        $manifestEntry = $zip.GetEntry('product-manifest.json')
        if (!$manifestEntry -or $manifestEntry.Length -gt 65536) {
            throw 'Portable DEV manifest unavailable'
        }
        $reader = [IO.StreamReader]::new($manifestEntry.Open(),[Text.Encoding]::UTF8)
        try { $manifest = $reader.ReadToEnd() | ConvertFrom-Json -ErrorAction Stop }
        finally { $reader.Dispose() }
        if ($manifest.schema -cne 'ordax.mcp-windows-candidate/1' -or
            $manifest.product -cne 'OrdaX MCP' -or
            $manifest.runtime -cne 'ORDAX Runtime.exe' -or
            $manifest.runtime_source_commit -cne $Commit -or
            $manifest.studio_required -ne $false -or
            $manifest.signed_public_release -ne $false) {
            throw 'Portable DEV archive source/provenance does not match pinned commit'
        }
    } finally { $zip.Dispose() }
    if ($Destination) {
        if (Test-Path -LiteralPath $Destination) {
            throw 'Portable DEV staging directory already exists'
        }
        [IO.Compression.ZipFile]::ExtractToDirectory($ArchivePath,$Destination)
        foreach ($required in @(
            'ORDAX Runtime.exe', 'product-manifest.json',
            'ordax-mcp-lifecycle.ps1', 'ordax-upgrade-quiesce.ps1',
            'runtime\python.exe', 'runtime\pythonw.exe'
        )) {
            if (!(Test-Path -LiteralPath (Join-Path $Destination $required) -PathType Leaf)) {
                throw "Portable DEV extraction missing file: $required"
            }
        }
    }
}

function Invoke-QuiesceRoot {
    param([string]$Root)
    $script = Join-Path $Root 'ordax-upgrade-quiesce.ps1'
    if (!(Test-Path -LiteralPath $script -PathType Leaf)) {
        throw 'Installed Runtime quiesce controller missing'
    }
    & $powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $script -InstallRoot $Root -TimeoutSeconds 25
    if ($LASTEXITCODE -ne 0) { throw "Runtime quiesce failed for owner root: $Root" }
}

function Invoke-RegisterAndLaunch {
    param([string]$Root)
    $script = Join-Path $Root 'ordax-mcp-lifecycle.ps1'
    & $powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $script -Operation Install
    if ($LASTEXITCODE -ne 0) { throw 'Canonical MCP scheduled recovery installation failed' }
    Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
}

switch ($Operation) {
    'Install' {
        Assert-InstalledSource
        $existing = Get-CanonicalScheduledTask -Name $taskName
        Assert-OurTask $existing $lifecycle 'Launch'
        $actionArgs = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Operation Launch' -f $lifecycle
        $action = New-ScheduledTaskAction -Execute $powershell -Argument $actionArgs -WorkingDirectory $installRoot
        $triggers = @(
            (New-ScheduledTaskTrigger -AtLogOn -User $owner),
            (New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1)) -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650))
        )
        $task = New-ScheduledTask -Action $action -Trigger $triggers -Settings (New-OwnerSettings) -Principal (New-OwnerPrincipal) -Description 'Single OrdaX MCP Runtime: automatic launcher recovery without a second agent'
        Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
        $registered = Get-CanonicalScheduledTask -Name $taskName
        if ($null -eq $registered) { throw 'Canonical owner task not registered' }
        Assert-OurTask $registered $lifecycle 'Launch'
        # The scheduled task is now the only autostart owner. Remove old logon-only fallback.
        Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'OrdaX MCP' -ErrorAction SilentlyContinue
        Write-Output 'ORDAX_MCP_LIFECYCLE_REGISTERED'
    }
    'Launch' {
        Assert-InstalledSource
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
        $runtime.WaitForExit()
        if ($runtime.ExitCode -ne 0) {
            throw "OrdaX MCP supervisor ended with exit code $($runtime.ExitCode)"
        }
    }
    'Uninstall' {
        $existing = Get-CanonicalScheduledTask -Name $taskName
        Assert-OurTask $existing $lifecycle 'Launch'
        if ($null -ne $existing) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction Stop
        }
        Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'OrdaX MCP' -ErrorAction SilentlyContinue
        Write-Output 'ORDAX_MCP_LIFECYCLE_UNREGISTERED'
    }
    'Upgrade' {
        Assert-InstalledSource
        if ($PackageSha256 -cnotmatch '^[0-9a-fA-F]{64}$' -or
            $ExpectedCommit -cnotmatch '^[0-9a-f]{40}$') {
            throw 'Exact portable DEV package SHA-256 and Runtime commit are mandatory'
        }
        if (![IO.Path]::IsPathRooted($PackagePath) -or
            !(Test-Path -LiteralPath $PackagePath -PathType Leaf) -or
            (Split-Path $PackagePath -Leaf) -cne ("OrdaX-MCP-Dev-{0}.zip" -f $ExpectedCommit)) {
            throw 'Portable DEV candidate filename/source is not canonical'
        }
        $source = Get-Item -LiteralPath $PackagePath -ErrorAction Stop
        if (($source.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            $source.Length -lt 1000000 -or $source.Length -gt 300000000) {
            throw 'Portable DEV archive path or size invalid'
        }
        if ((Get-FileHash -LiteralPath $source.FullName -Algorithm SHA256).Hash -ine $PackageSha256) {
            throw 'Portable DEV archive differs from verified CI candidate'
        }
        Assert-DevArchive -ArchivePath $source.FullName -Commit $ExpectedCommit
        # The previous Inno DEV attempts remain historical receipts only.
        # A live journal in either schema still forbids overlapping upgrades.
        if (Test-Path -LiteralPath $updatesRoot -PathType Container) {
            foreach ($previous in @(Get-ChildItem -LiteralPath $updatesRoot -Directory -ErrorAction Stop)) {
                $previousJournal = Join-Path $previous.FullName 'journal.json'
                if (!(Test-Path -LiteralPath $previousJournal -PathType Leaf)) { continue }
                $prior = Get-Content -LiteralPath $previousJournal -Raw | ConvertFrom-Json -ErrorAction Stop
                if ($prior.schema -notin @('ordax.mcp-upgrade/1','ordax.mcp-dev-upgrade/2')) {
                    throw "Unknown MCP update journal schema: $($previous.Name)"
                }
                if ($prior.phase -in @('staged','applying')) {
                    throw "Unresolved MCP update must be reconciled: $($previous.Name)"
                }
            }
        }
        $existing = Get-CanonicalScheduledTask -Name $upgradeTaskName
        if ($existing -and $existing.State -eq 'Running') {
            throw 'Canonical MCP upgrade already running'
        }
        if ($existing) {
            $actions = @($existing.Actions)
            if ($actions.Count -ne 1 -or [string]$actions[0].Arguments -notmatch ' -Operation ApplyUpgrade ') {
                throw 'Upgrade task name belongs to another program'
            }
        }
        New-Item -Path $updatesRoot -ItemType Directory -Force | Out-Null
        $id = [guid]::NewGuid().ToString('N')
        $tx = Join-Path $updatesRoot $id
        New-Item -Path $tx -ItemType Directory -ErrorAction Stop | Out-Null
        $staged = Join-Path $tx $source.Name
        Copy-Item -LiteralPath $source.FullName -Destination $staged -ErrorAction Stop
        if ((Get-FileHash -LiteralPath $staged -Algorithm SHA256).Hash -ine $PackageSha256) {
            throw 'Portable DEV archive changed during staging'
        }
        $applyScript = Join-Path $tx 'apply.ps1'
        Copy-Item -LiteralPath $lifecycle -Destination $applyScript -ErrorAction Stop
        $journal = [pscustomobject]@{
            schema = 'ordax.mcp-dev-upgrade/2'; transactionId = $id; phase = 'staged'
            packageName = $source.Name; sha256 = $PackageSha256.ToUpperInvariant()
            targetCommit = $ExpectedCommit; updatedAt = ''
        }
        Save-Journal (Join-Path $tx 'journal.json') $journal
        $args = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Operation ApplyUpgrade -TransactionId {1}' -f $applyScript, $id
        $action = New-ScheduledTaskAction -Execute $powershell -Argument $args -WorkingDirectory $tx
        $trigger = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(1))
        $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 40)
        $task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal (New-OwnerPrincipal)
        Register-ScheduledTask -TaskName $upgradeTaskName -InputObject $task -Force | Out-Null
        Start-ScheduledTask -TaskName $upgradeTaskName -ErrorAction Stop
        Write-Output ("ORDAX_MCP_DEV_UPGRADE_QUEUED transaction={0} journal={1}" -f $id, (Join-Path $tx 'journal.json'))
    }
    'ApplyUpgrade' {
        if ($TransactionId -cnotmatch '^[0-9a-f]{32}$') { throw 'Invalid upgrade transaction ID' }
        $tx = Join-Path $updatesRoot $TransactionId
        if ([IO.Path]::GetFullPath($PSCommandPath) -ine (Join-Path $tx 'apply.ps1')) {
            throw 'DEV upgrade executor did not originate from staged transaction'
        }
        $journalPath = Join-Path $tx 'journal.json'
        $journal = Get-Content -LiteralPath $journalPath -Raw -ErrorAction Stop | ConvertFrom-Json
        if ($journal.schema -cne 'ordax.mcp-dev-upgrade/2' -or
            $journal.transactionId -cne $TransactionId -or
            $journal.phase -cne 'staged' -or
            $journal.sha256 -cnotmatch '^[0-9A-F]{64}$' -or
            $journal.targetCommit -cnotmatch '^[0-9a-f]{40}$' -or
            $journal.packageName -cne ("OrdaX-MCP-Dev-{0}.zip" -f $journal.targetCommit)) {
            throw 'DEV upgrade journal identity, phase or package name invalid'
        }
        $package = Join-Path $tx $journal.packageName
        if ((Get-FileHash -LiteralPath $package -Algorithm SHA256).Hash -cne $journal.sha256) {
            throw 'Portable DEV staged package integrity mismatch'
        }
        $journal.phase = 'applying'
        Save-Journal $journalPath $journal
        $expanded = Join-Path $tx 'expanded-runtime'
        $previous = $installRoot + '.previous-dev'
        $failed = Join-Path $tx 'failed-candidate'
        $ownerDisabled = $false
        $oldMoved = $false
        $newMoved = $false
        try {
            # No installed files or tasks are touched until the entire ZIP
            # and its exact source commit have passed preflight.
            Assert-DevArchive -ArchivePath $package -Commit $journal.targetCommit -Destination $expanded
            if (Test-Path -LiteralPath $previous) {
                throw 'Unresolved previous DEV Runtime directory blocks activation'
            }
            if ([IO.Path]::GetPathRoot($expanded) -ine [IO.Path]::GetPathRoot($installRoot)) {
                throw 'DEV Runtime atomic activation requires same volume'
            }
            $existing = Get-CanonicalScheduledTask -Name $taskName
            Assert-OurTask $existing $lifecycle 'Launch'
            if ($existing) {
                Disable-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null
                $ownerDisabled = $true
            }
            Invoke-QuiesceRoot -Root $installRoot
            [IO.Directory]::Move($installRoot,$previous)
            $oldMoved = $true
            [IO.Directory]::Move($expanded,$installRoot)
            $newMoved = $true
            Invoke-RegisterAndLaunch -Root $installRoot
            $healthy = $false
            $launchedAt = [DateTime]::UtcNow
            for ($i = 0; $i -lt 60; $i++) {
                try {
                    $status = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/readiness' -TimeoutSec 2
                    $manifest = Get-Content -LiteralPath (Join-Path $installRoot 'product-manifest.json') -Raw | ConvertFrom-Json
                    $ownedLaunchers = @(Get-Process -Name 'ORDAX Runtime' -ErrorAction SilentlyContinue | Where-Object {
                        try { [IO.Path]::GetFullPath([string]$_.Path) -ieq $launcher -and
                              $_.StartTime.ToUniversalTime() -ge $launchedAt.AddSeconds(-5) }
                        catch { $false }
                    })
                    if ($manifest.runtime_source_commit -ceq $journal.targetCommit -and
                        $ownedLaunchers.Count -eq 1 -and
                        $status.local_dispatch_ready -eq $true -and
                        $status.runtime.state -in @('ready','busy') -and
                        $status.runtime.transport_state -eq 'connected' -and
                        $status.runtime.product_dispatch_ready -eq $true) {
                        $healthy = $true
                        break
                    }
                } catch {}
                Start-Sleep -Seconds 2
            }
            if (!$healthy) { throw 'New portable DEV MCP failed local and remote readiness' }
            $journal.phase = 'committed'
            Save-Journal $journalPath $journal
            # DEV contains no user data under the package root; the identity
            # and grants remain in the existing persistent Owner state.
            try { Remove-Item -LiteralPath $previous -Recurse -Force -ErrorAction Stop }
            catch { Write-Warning "DEV cleanup after committed activation failed: $($_.Exception.Message)" }
            Write-Output "ORDAX_MCP_DEV_RUNTIME_ACTIVATED commit=$($journal.targetCommit)"
        } catch {
            $failure = $_.Exception.Message
            if ($oldMoved) {
                try {
                    if ($newMoved) {
                        $running = Get-CanonicalScheduledTask -Name $taskName
                        if ($running) { Disable-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null }
                        Invoke-QuiesceRoot -Root $installRoot
                        if (Test-Path -LiteralPath $failed) {
                            throw 'Previously failed DEV candidate directory exists'
                        }
                        [IO.Directory]::Move($installRoot,$failed)
                    }
                    if (!(Test-Path -LiteralPath $previous -PathType Container)) {
                        throw 'Verified previous DEV Runtime root unavailable for rollback'
                    }
                    [IO.Directory]::Move($previous,$installRoot)
                    Invoke-RegisterAndLaunch -Root $installRoot
                } catch {
                    $failure += "; DEV rollback failed: $($_.Exception.Message)"
                }
            } elseif ($ownerDisabled) {
                try {
                    Enable-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null
                    Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
                } catch { $failure += "; DEV owner resume failed: $($_.Exception.Message)" }
            }
            $journal.phase = 'recovery-required'
            $journal | Add-Member -NotePropertyName error -NotePropertyValue $failure -Force
            Save-Journal $journalPath $journal
            throw $failure
        }
    }
}
