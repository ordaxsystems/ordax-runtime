# Canonical Windows lifetime and verified-upgrade handoff for the ONE OrdaX MCP Runtime.
# Scheduled Tasks is the Windows supervisor, not another agent or identity.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Install', 'Launch', 'Uninstall', 'Upgrade', 'ApplyUpgrade', 'ReconcileUpgrade')]
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

function Read-OwnerJsonDocument {
    param([string]$Path)
    # On the Windows DEV host, the FileSystem provider's Get-Content
    # stalls on small transaction journals. Direct .NET I/O has equivalent
    # exception behavior and does not change the canonical journal format.
    $raw = [IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8)
    return ($raw | ConvertFrom-Json -ErrorAction Stop)
}

function Get-OwnerArchiveSha256 {
    param([string]$Path)
    # The Get-FileHash cmdlet can stall for minutes on the Windows DEV host.
    # Preserve full SHA-256 verification with a single bounded file stream.
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $stream = [IO.File]::Open(
            $Path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
            [IO.FileShare]::Read
        )
        try {
            return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '')
        } finally {
            $stream.Dispose()
        }
    } finally {
        $sha.Dispose()
    }
}

function Assert-InstalledSource {
    if ([IO.Path]::GetFullPath($PSCommandPath) -ine $lifecycle) {
        throw 'Lifecycle registration must originate from the canonical OrdaX MCP installation'
    }
    if (!(Test-Path -LiteralPath $launcher -PathType Leaf)) {
        throw 'Canonical OrdaX MCP launcher is missing'
    }
    $manifestPath = Join-Path $installRoot 'product-manifest.json'
    $manifest = Read-OwnerJsonDocument -Path $manifestPath
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
        '^(Disabled|Desativado|Desabilitado)$' { 'Disabled'; break }
        '^(Queued|Na fila)' { 'Queued'; break }
        default { throw "Unknown scheduler task state: $Name" }
    }
    return [pscustomobject]@{
        TaskName = $Name
        State = $state
        Actions = $commands
    }
}

# The same two canonical Windows tasks are owned through schtasks.exe.
# PowerShell ScheduledTasks CIM cmdlets can hang indefinitely on the DEV host.
function Invoke-CanonicalTaskAction {
    param([string]$Name,[ValidateSet('Run','Enable','Disable','Delete','End')][string]$Action)
    if ($Name -cnotin @($taskName,$upgradeTaskName)) { throw 'Unknown canonical scheduler task' }
    if ($Action -eq 'End' -and $Name -cne $taskName) { throw 'Only the verified permanent owner task may be stopped' }
    $args = switch ($Action) {
        'Run' { @('/Run','/TN',$Name) }
        'Enable' { @('/Change','/TN',$Name,'/ENABLE') }
        'Disable' { @('/Change','/TN',$Name,'/DISABLE') }
        'End' { @('/End','/TN',$Name) }
        'Delete' { @('/Delete','/TN',$Name,'/F') }
    }
    $out = Invoke-BoundedSchtasks -Arguments $args
    if ($out.ExitCode -ne 0) { throw "Canonical scheduler $Action failed for task: $Name" }
}

function Register-CanonicalTask {
    param(
        [string]$Name, [string]$Script, [string]$Operation, [string]$WorkingDirectory,
        [string]$TransactionId = ''
    )
    if ($Name -cnotin @($taskName,$upgradeTaskName)) { throw 'Unknown canonical scheduler registration' }
    $isUpgrade = $Name -ceq $upgradeTaskName
    if (($isUpgrade -and ($Operation -cne 'ApplyUpgrade' -or $TransactionId -cnotmatch '^[0-9a-f]{32}$')) -or
        (!$isUpgrade -and ($Operation -cne 'Launch' -or $TransactionId))) {
        throw 'Scheduler operation and transaction mismatch'
    }
    if (![IO.Path]::IsPathRooted($Script) -or ![IO.Path]::IsPathRooted($WorkingDirectory)) {
        throw 'Canonical scheduler action paths must be absolute'
    }
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    $xmlEscape = { param([string]$value) [Security.SecurityElement]::Escape($value) }
    $safeName = & $xmlEscape $Name
    $safeUser = & $xmlEscape $owner
    $safeSid = & $xmlEscape $sid
    $safeCommand = & $xmlEscape $powershell
    $args = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Operation {1}' -f $Script, $Operation
    if ($isUpgrade) { $args += ' -TransactionId ' + $TransactionId }
    $safeArgs = & $xmlEscape $args
    $safeDirectory = & $xmlEscape $WorkingDirectory
    $startBoundary = [DateTimeOffset]::Now.AddMinutes(1).ToString('yyyy-MM-ddTHH:mm:sszzz')
    if ($isUpgrade) {
        $trigger = "<TimeTrigger><StartBoundary>$startBoundary</StartBoundary></TimeTrigger>"
        $limit = 'PT40M'
        $restart = ''
        $description = ''
        $priority = ''
    } else {
        $trigger = "<LogonTrigger><UserId>$safeUser</UserId></LogonTrigger><TimeTrigger><StartBoundary>$startBoundary</StartBoundary><Repetition><Interval>PT1M</Interval><Duration>P3650D</Duration><StopAtDurationEnd>true</StopAtDurationEnd></Repetition></TimeTrigger>"
        $limit = 'PT0S'
        $restart = '<RestartOnFailure><Count>999</Count><Interval>PT1M</Interval></RestartOnFailure>'
        $description = '<Description>Single OrdaX MCP Runtime: automatic launcher recovery without a second agent</Description>'
        # MCP serves interactive requests. The scheduler default (7) starts
        # its launcher and Python below normal, delaying bootstrap under I/O load.
        $priority = '<Priority>4</Priority>'
    }
    $xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>$description<URI>\$safeName</URI></RegistrationInfo>
  <Principals><Principal id="Author"><UserId>$safeSid</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>$limit</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    $restart
    <StartWhenAvailable>true</StartWhenAvailable>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
    $priority
  </Settings>
  <Triggers>$trigger</Triggers>
  <Actions Context="Author"><Exec><Command>$safeCommand</Command><Arguments>$safeArgs</Arguments><WorkingDirectory>$safeDirectory</WorkingDirectory></Exec></Actions>
</Task>
"@
    [xml]$parsed = $xml
    $ns = [Xml.XmlNamespaceManager]::new($parsed.NameTable)
    $ns.AddNamespace('t','http://schemas.microsoft.com/windows/2004/02/mit/task')
    $exec = $parsed.SelectNodes('/t:Task/t:Actions/t:Exec',$ns)
    if ($exec.Count -ne 1) { throw 'Canonical scheduler XML must have exactly one action' }
    New-Item -ItemType Directory -Path $updatesRoot -Force | Out-Null
    $temp = Join-Path $updatesRoot ('task.' + [guid]::NewGuid().ToString('N') + '.xml')
    [IO.File]::WriteAllText($temp,$xml,[Text.Encoding]::Unicode)
    try {
        $result = Invoke-BoundedSchtasks -Arguments @('/Create','/F','/TN',$Name,'/XML',$temp)
        if ($result.ExitCode -ne 0) { throw "Failed to register canonical scheduler task: $Name" }
    } finally {
        Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
    }
    $actual = Get-CanonicalScheduledTask -Name $Name
    if ($null -eq $actual) { throw "New canonical scheduler task is missing: $Name" }
    Assert-OurTask $actual $Script $Operation
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
        # The Task Scheduler background .NET extraction can advance only
        # one copy buffer per several seconds on the DEV workstation. Reuse
        # the already installed, trusted embedded Python in a *bounded child*
        # rather than creating a second updater or changing any project data.
        # The entire ZIP was validated above, before invoking this extractor.
        $privatePython = Join-Path $installRoot 'runtime\python.exe'
        if (!(Test-Path -LiteralPath $privatePython -PathType Leaf)) {
            throw 'Installed private Runtime Python unavailable for archive extraction'
        }
        # A scheduled background task receives slower per-file I/O than an
        # interactive extraction on the DEV host. Populate directories once
        # and extract independent, prevalidated ZIP members concurrently.
        # This remains one bounded, child-owned extraction in the same updater.
        $extractCode = @'
import os
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
with zipfile.ZipFile(sys.argv[1]) as archive:
    members = archive.infolist()
    destination = sys.argv[2]
    for member in members:
        parent = os.path.dirname(os.path.join(destination, member.filename))
        if parent:
            os.makedirs(parent, exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for _ in pool.map(lambda member: archive.extract(member, destination), members):
            pass
'@
        $start = [Diagnostics.ProcessStartInfo]::new()
        $start.FileName = $privatePython
        $start.Arguments = '-I -c "' + $extractCode + '" "' + $ArchivePath + '" "' + $Destination + '"'
        $start.WorkingDirectory = $installRoot
        $start.UseShellExecute = $false
        $start.CreateNoWindow = $true
        $child = [Diagnostics.Process]::Start($start)
        if ($null -eq $child) { throw 'Portable DEV archive extractor did not start' }
        try {
            # Time-bound even when Windows throttles the scheduler's I/O;
            # the ordinary path is accelerated by bounded file concurrency.
            if (!$child.WaitForExit(480000)) {
                $child.Kill()
                [void]$child.WaitForExit(5000)
                throw 'Portable DEV archive extraction exceeded its 480-second deadline'
            }
            if ($child.ExitCode -ne 0) {
                throw "Portable DEV archive extraction failed with exit code $($child.ExitCode)"
            }
        } finally { $child.Dispose() }
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


# Stage integrity is a digest of paths, sizes and bytes, verified again before
# any owner-task or installation mutation by the scheduled executor.
function Get-PreparedTreeDigest {
    param([string]$Root)
    if (!(Test-Path -LiteralPath $Root -PathType Container)) { throw 'Prepared DEV tree missing' }
    $privatePython = Join-Path $installRoot 'runtime\python.exe'
    if (!(Test-Path -LiteralPath $privatePython -PathType Leaf)) {
        throw 'Private Runtime Python unavailable for staging integrity'
    }
    $digestCode = @'
import hashlib, os, sys
root = os.path.abspath(sys.argv[1])
entries = []
for base, dirs, files in os.walk(root):
    for name in dirs + files:
        if os.path.islink(os.path.join(base, name)):
            raise ValueError('Prepared DEV tree contains symlink')
    for name in files:
        path = os.path.join(base, name)
        entries.append((os.path.relpath(path, root).replace(os.sep, '/'), path))
if not 10 <= len(entries) <= 30000:
    raise ValueError('Prepared DEV tree count invalid')
digest = hashlib.sha256()
for name, path in sorted(entries):
    name_bytes = name.encode('utf-8')
    size = os.path.getsize(path)
    if len(name_bytes) > 4096 or size < 0 or size > 200 * 1024 * 1024:
        raise ValueError('Prepared DEV file invalid')
    digest.update(len(name_bytes).to_bytes(4, 'big'))
    digest.update(name_bytes)
    digest.update(size.to_bytes(8, 'big'))
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1048576), b''):
            digest.update(chunk)
print(digest.hexdigest().upper())
'@
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $privatePython
    $start.Arguments = '-I -c "' + $digestCode + '" "' + $Root + '"'
    $start.WorkingDirectory = $installRoot
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $child = [Diagnostics.Process]::Start($start)
    if ($null -eq $child) { throw 'Prepared DEV digest process unavailable' }
    try {
        if (!$child.WaitForExit(480000)) {
            $child.Kill()
            [void]$child.WaitForExit(5000)
            throw 'Prepared DEV digest exceeded bounded deadline'
        }
        $digest = $child.StandardOutput.ReadToEnd().Trim()
        if ($child.ExitCode -ne 0 -or $digest -cnotmatch '^[0-9A-F]{64}$') {
            throw 'Prepared DEV tree integrity mismatch'
        }
        return $digest
    } finally { $child.Dispose() }
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
    Invoke-CanonicalTaskAction -Name $taskName -Action Run
}

switch ($Operation) {
    'Install' {
        Assert-InstalledSource
        $existing = Get-CanonicalScheduledTask -Name $taskName
        Assert-OurTask $existing $lifecycle 'Launch'
        Register-CanonicalTask -Name $taskName -Script $lifecycle -Operation Launch -WorkingDirectory $updatesRoot
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
            Invoke-CanonicalTaskAction -Name $taskName -Action Delete
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
        if ((Get-OwnerArchiveSha256 -Path $source.FullName) -ine $PackageSha256) {
            throw 'Portable DEV archive differs from verified CI candidate'
        }
        Assert-DevArchive -ArchivePath $source.FullName -Commit $ExpectedCommit
        # The previous Inno DEV attempts remain historical receipts only.
        # A live journal in either schema still forbids overlapping upgrades.
        if (Test-Path -LiteralPath $updatesRoot -PathType Container) {
            foreach ($previous in @(Get-ChildItem -LiteralPath $updatesRoot -Directory -ErrorAction Stop)) {
                $previousJournal = Join-Path $previous.FullName 'journal.json'
                if (!(Test-Path -LiteralPath $previousJournal -PathType Leaf)) { continue }
                $prior = Read-OwnerJsonDocument -Path $previousJournal
                if ($prior.schema -notin @('ordax.mcp-upgrade/1','ordax.mcp-dev-upgrade/2','ordax.mcp-dev-upgrade/3')) {
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
        if ((Get-OwnerArchiveSha256 -Path $staged) -ine $PackageSha256) {
            throw 'Portable DEV archive changed during staging'
        }
        # User-initiated Upgrade prepares all files before the slower scheduled
        # task. The task must only verify their content and activate atomically.
        $expanded = Join-Path $tx 'expanded-runtime'
        try {
            Assert-DevArchive -ArchivePath $staged -Commit $ExpectedCommit -Destination $expanded
            $preparedDigest = Get-PreparedTreeDigest -Root $expanded
        } catch {
            # No published journal or task exists yet for this failed stage.
            Remove-Item -LiteralPath $tx -Recurse -Force -ErrorAction SilentlyContinue
            throw
        }
        $applyScript = Join-Path $tx 'apply.ps1'
        Copy-Item -LiteralPath $lifecycle -Destination $applyScript -ErrorAction Stop
        $journal = [pscustomobject]@{
            schema = 'ordax.mcp-dev-upgrade/3'; transactionId = $id; phase = 'staged'
            packageName = $source.Name; sha256 = $PackageSha256.ToUpperInvariant()
            targetCommit = $ExpectedCommit; preparedTreeSha256 = $preparedDigest
            updatedAt = ''
        }
        Save-Journal (Join-Path $tx 'journal.json') $journal
        Register-CanonicalTask -Name $upgradeTaskName -Script $applyScript -Operation ApplyUpgrade -WorkingDirectory $tx -TransactionId $id
        Invoke-CanonicalTaskAction -Name $upgradeTaskName -Action Run
        Write-Output ("ORDAX_MCP_DEV_UPGRADE_QUEUED transaction={0} journal={1}" -f $id, (Join-Path $tx 'journal.json'))
    }
    'ReconcileUpgrade' {
        # Only reconcile a stopped pre-activation transaction with the
        # original owner installation still intact; never forge a success.
        Assert-InstalledSource
        if ($TransactionId -cnotmatch '^[0-9a-f]{32}$') {
            throw 'Invalid interrupted upgrade transaction ID'
        }
        $tx = Join-Path $updatesRoot $TransactionId
        $journalPath = Join-Path $tx 'journal.json'
        $journal = Read-OwnerJsonDocument -Path $journalPath
        if ($journal.schema -notin @('ordax.mcp-dev-upgrade/2','ordax.mcp-dev-upgrade/3') -or
            $journal.transactionId -cne $TransactionId -or
            $journal.phase -cne 'applying') {
            throw 'Reconcile requires an exact interrupted DEV transaction'
        }
        $upgrade = Get-CanonicalScheduledTask -Name $upgradeTaskName
        if ($upgrade -and $upgrade.State -in @('Running','Queued')) {
            throw 'Cannot reconcile an active canonical upgrade'
        }
        if ((Test-Path -LiteralPath ($installRoot + '.previous-dev')) -or
            (Test-Path -LiteralPath (Join-Path $tx 'failed-candidate'))) {
            throw 'Interrupted upgrade has moved Runtime directories; inspect first'
        }
        $installed = Read-OwnerJsonDocument -Path (Join-Path $installRoot 'product-manifest.json')
        if ($installed.runtime_source_commit -ceq $journal.targetCommit) {
            throw 'Installed candidate cannot be marked rolled back'
        }
        $journal.phase = 'recovery-required'
        $journal | Add-Member -NotePropertyName error -NotePropertyValue 'Interrupted before atomic activation; original Runtime intact' -Force
        Save-Journal $journalPath $journal
        Write-Output ("ORDAX_MCP_DEV_RECOVERY_RECORDED transaction={0}" -f $TransactionId)
    }
    'ApplyUpgrade' {
        if ($TransactionId -cnotmatch '^[0-9a-f]{32}$') { throw 'Invalid upgrade transaction ID' }
        $tx = Join-Path $updatesRoot $TransactionId
        if ([IO.Path]::GetFullPath($PSCommandPath) -ine (Join-Path $tx 'apply.ps1')) {
            throw 'DEV upgrade executor did not originate from staged transaction'
        }
        $journalPath = Join-Path $tx 'journal.json'
        $journal = Read-OwnerJsonDocument -Path $journalPath
        if ($journal.schema -cne 'ordax.mcp-dev-upgrade/3' -or
            $journal.transactionId -cne $TransactionId -or
            $journal.phase -cne 'staged' -or
            $journal.sha256 -cnotmatch '^[0-9A-F]{64}$' -or
            $journal.preparedTreeSha256 -cnotmatch '^[0-9A-F]{64}$' -or
            $journal.targetCommit -cnotmatch '^[0-9a-f]{40}$' -or
            $journal.packageName -cne ("OrdaX-MCP-Dev-{0}.zip" -f $journal.targetCommit)) {
            throw 'DEV upgrade journal identity, phase or package name invalid'
        }
        $package = Join-Path $tx $journal.packageName
        if ((Get-OwnerArchiveSha256 -Path $package) -cne $journal.sha256) {
            throw 'Portable DEV staged package integrity mismatch'
        }
        # Revalidate archive and every prepared file before changing the
        # installed Runtime or its singleton scheduled owner.
        Assert-DevArchive -ArchivePath $package -Commit $journal.targetCommit
        $expanded = Join-Path $tx 'expanded-runtime'
        if ((Get-PreparedTreeDigest -Root $expanded) -cne $journal.preparedTreeSha256) {
            throw 'Prepared DEV stage changed before activation'
        }
        $journal.phase = 'applying'
        Save-Journal $journalPath $journal
        $previous = $installRoot + '.previous-dev'
        $failed = Join-Path $tx 'failed-candidate'
        $ownerDisabled = $false
        $oldMoved = $false
        $newMoved = $false
        try {
            # No installed files or tasks are touched until the entire ZIP
            # and its exact source commit have passed preflight.
            # Verified prepared tree replaces scheduled-task archive extraction.
            if (Test-Path -LiteralPath $previous) {
                throw 'Unresolved previous DEV Runtime directory blocks activation'
            }
            if ([IO.Path]::GetPathRoot($expanded) -ine [IO.Path]::GetPathRoot($installRoot)) {
                throw 'DEV Runtime atomic activation requires same volume'
            }
            $existing = Get-CanonicalScheduledTask -Name $taskName
            Assert-OurTask $existing $lifecycle 'Launch'
            if ($existing) {
                # The previous task registered PowerShell with its current
                # directory INSIDE $installRoot. Even after the launcher PID
                # exits, that task process can still hold a directory handle
                # and make the atomic directory move fail with ERROR_SHARING_VIOLATION.
                # End only our identity-verified canonical owner task before
                # switching the installation; never terminate unrelated tasks.
                if ($existing.State -eq 'Running') {
                    Invoke-CanonicalTaskAction -Name $taskName -Action End
                    $released = $false
                    for ($attempt = 0; $attempt -lt 6; $attempt++) {
                        $observed = Get-CanonicalScheduledTask -Name $taskName
                        if ($null -eq $observed -or $observed.State -ne 'Running') {
                            $released = $true
                            break
                        }
                        Start-Sleep -Milliseconds 300
                    }
                    if (!$released) {
                        throw 'Canonical MCP owner task did not release its running process'
                    }
                }
                Invoke-CanonicalTaskAction -Name $taskName -Action Disable
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
            # Native bootstrap may require up to 10 minutes on a degraded
            # Windows host. Retain another two minutes for Product transport,
            # without accepting local-only readiness as upgrade success.
            $activationClock = [Diagnostics.Stopwatch]::StartNew()
            while ($activationClock.Elapsed.TotalMinutes -lt 12) {
                try {
                    $status = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/readiness' -TimeoutSec 2
                    $manifest = Read-OwnerJsonDocument -Path (Join-Path $installRoot 'product-manifest.json')
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
                        if ($running) { Invoke-CanonicalTaskAction -Name $taskName -Action Disable }
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
                    Invoke-CanonicalTaskAction -Name $taskName -Action Enable
                    Invoke-CanonicalTaskAction -Name $taskName -Action Run
                } catch { $failure += "; DEV owner resume failed: $($_.Exception.Message)" }
            }
            $journal.phase = 'recovery-required'
            $journal | Add-Member -NotePropertyName error -NotePropertyValue $failure -Force
            Save-Journal $journalPath $journal
            throw $failure
        }
    }
}
