[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$TransactionRoot,
    [Parameter(Mandatory = $true)][string]$TargetInstaller,
    [Parameter(Mandatory = $true)][string]$TargetSha256,
    [Parameter(Mandatory = $true)][string]$TargetVersion,
    [Parameter(Mandatory = $true)][string]$RollbackInstaller,
    [Parameter(Mandatory = $true)][string]$RollbackSha256,
    [Parameter(Mandatory = $true)][string]$RollbackVersion,
    [Parameter(Mandatory = $true)][string]$InstallRoot,
    [Parameter(Mandatory = $true)][string]$TrustedSignerThumbprints,
    [Parameter(Mandatory = $true)][string]$VerifierScript
)

$ErrorActionPreference = 'Stop'
$transactionRootPath = [IO.Path]::GetFullPath($TransactionRoot).TrimEnd('\')
$transactionPrefix = ($transactionRootPath + '\').ToLowerInvariant()
$journalPath = Join-Path $transactionRootPath 'transaction.json'
$installRootPath = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$canonicalInstallRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs\ORDAX')).TrimEnd('\')
if ($installRootPath -ine $canonicalInstallRoot) { throw 'Production updater refuses a non-canonical install root' }
if ($TargetVersion -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$' -or
    $RollbackVersion -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$' -or
    ([version]$TargetVersion -le [version]$RollbackVersion)) {
    throw 'Production update version transition is invalid'
}

function Assert-TransactionHandoff {
    # Rebind every handoff parameter to the single durable journal, including
    # staged file identities. A terminal transaction cannot be replayed.
    $updatesRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'OrdaX\updates')).TrimEnd('\') + '\'
    if (-not $transactionPrefix.StartsWith($updatesRoot.ToLowerInvariant())) {
        throw 'Transaction root escaped canonical ORDAX update storage'
    }
    if (-not (Test-Path -LiteralPath $journalPath -PathType Leaf)) {
        throw 'Update transaction journal is missing'
    }
    $journal = Get-Content -LiteralPath $journalPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($journal.schema -cne 'ordax.windows-update-transaction/1' -or
        $journal.authority -cne 'local-privileged-updater' -or
        $journal.phase -cne 'handoff-launching' -or
        [string]$journal.transactionId -cnotmatch '^[0-9a-f]{32}$' -or
        $journal.transactionId -cne (Split-Path -Path $transactionRootPath -Leaf)) {
        throw 'Update handoff authority, journal identity or phase is invalid'
    }
    if ([string]$journal.installedVersion -cne $RollbackVersion -or
        [string]$journal.target.version -cne $TargetVersion -or
        [string]$journal.rollback.version -cne $RollbackVersion -or
        [string]$journal.target.tag -cne "v$TargetVersion" -or
        [string]$journal.rollback.tag -cne "v$RollbackVersion") {
        throw 'Update handoff versions differ from the durable journal'
    }
    foreach ($role in @('target', 'rollback')) {
        $version = if ($role -eq 'target') { $TargetVersion } else { $RollbackVersion }
        $path = if ($role -eq 'target') { $TargetInstaller } else { $RollbackInstaller }
        $sha = if ($role -eq 'target') { $TargetSha256 } else { $RollbackSha256 }
        $record = if ($role -eq 'target') { $journal.target } else { $journal.rollback }
        $filename = "ORDAX-Studio-Setup-$version-x64.exe"
        $expectedPath = [IO.Path]::GetFullPath((Join-Path (Join-Path $transactionRootPath $role) $filename))
        if ([IO.Path]::GetFullPath($path) -ine $expectedPath -or
            [string]$record.installer -cne $filename -or
            [string]$record.checksums -cne 'SHA256SUMS.txt' -or
            [string]$record.installerSha256 -cne $sha -or
            $sha -cnotmatch '^[0-9a-f]{64}$') {
            throw "Update $role handoff path or SHA-256 differs from the durable journal"
        }
    }
    if ((Read-InstalledVersion) -cne $RollbackVersion) {
        throw 'Installed product changed before updater handoff'
    }
}

function Write-TransactionPhase {
    param([string]$Phase, [string]$Detail = '')
    if (-not (Test-Path -LiteralPath $journalPath -PathType Leaf)) {
        throw "Update transaction journal is missing"
    }
    $journal = Get-Content -LiteralPath $journalPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($journal.schema -ne 'ordax.windows-update-transaction/1') {
        throw "Update transaction schema is invalid"
    }
    $journal.phase = $Phase
    $journal | Add-Member -NotePropertyName updatedAtUtc -NotePropertyValue ([DateTime]::UtcNow.ToString('o')) -Force
    if ($Detail) {
        $safeDetail = if ($Detail.Length -gt 2000) { $Detail.Substring(0, 2000) } else { $Detail }
        $journal | Add-Member -NotePropertyName detail -NotePropertyValue $safeDetail -Force
    } else {
        $journal.PSObject.Properties.Remove('detail')
    }
    $pending = "$journalPath.next"
    [IO.File]::WriteAllText($pending, ($journal | ConvertTo-Json -Depth 10 -Compress), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $pending -Destination $journalPath -Force
}

function Get-Sha256 {
    param([string]$Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '').ToLowerInvariant()
    } finally {
        $stream.Dispose()
        $sha.Dispose()
    }
}

function Assert-StagedInstaller {
    param([string]$Path, [string]$ExpectedSha256)
    $full = [IO.Path]::GetFullPath($Path)
    if (-not $full.ToLowerInvariant().StartsWith($transactionPrefix)) {
        throw "Installer escaped the private update staging root"
    }
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) {
        throw "Staged installer is missing: $full"
    }
    if ($ExpectedSha256 -notmatch '^[0-9a-f]{64}$') {
        throw "Expected installer SHA-256 is invalid"
    }
    $actual = Get-Sha256 -Path $full
    if ($actual -ne $ExpectedSha256) {
        throw "Staged installer SHA-256 changed after handoff"
    }
    $verification = @(& $VerifierScript -Path $full -TrustedSignerThumbprints $TrustedSignerThumbprints)
    if (-not ($verification -contains 'ORDAX_AUTHENTICODE_VALID')) {
        throw "Staged installer Authenticode verification did not emit the required proof"
    }
}

function Read-InstalledVersion {
    $manifestPath = Join-Path $installRootPath 'product-manifest.json'
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "Installed ORDAX product manifest is missing"
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.schema -ne 'ordax.windows-product/1' -or $manifest.product -ne 'ORDAX Studio') {
        throw "Installed ORDAX product manifest is incompatible"
    }
    return [string]$manifest.version
}

function Invoke-Installer {
    param([string]$Path, [string]$LogName)
    $logPath = Join-Path $transactionRootPath $LogName
    $process = Start-Process -FilePath $Path -ArgumentList @(
        '/VERYSILENT',
        '/SUPPRESSMSGBOXES',
        '/NORESTART',
        "/DIR=$installRootPath",
        "/LOG=$logPath"
    ) -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        $tail = ''
        if (Test-Path -LiteralPath $logPath -PathType Leaf) {
            $tail = (Get-Content -LiteralPath $logPath -Tail 80) -join "`n"
        }
        throw "ORDAX installer exited with code $($process.ExitCode). $tail"
    }
}

function Start-RuntimeAndWaitHealthy {
    param([string]$ExpectedVersion)
    if ((Read-InstalledVersion) -ne $ExpectedVersion) {
        throw "Installed version does not match expected version $ExpectedVersion"
    }
    $runtimeExe = Join-Path $installRootPath 'ORDAX Runtime.exe'
    if (-not (Test-Path -LiteralPath $runtimeExe -PathType Leaf)) {
        throw "Installed ORDAX Runtime executable is missing"
    }
    $existing = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        try { $_.Path -and ([IO.Path]::GetFullPath($_.Path) -ieq $runtimeExe) } catch { $false }
    })
    if (-not $existing.Count) {
        Start-Process -FilePath $runtimeExe -WorkingDirectory $installRootPath | Out-Null
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(75)
    do {
        try {
            $status = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/status' -TimeoutSec 2
            $state = if ($status.runtime) { [string]$status.runtime.state } else { '' }
            $actions = @($status.actions).Count
            if ($state -in @('local-ready', 'pairing', 'ready') -and $actions -gt 0) {
                return @{ state = $state; actionCount = $actions }
            }
        } catch {
        }
        Start-Sleep -Milliseconds 750
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "ORDAX Runtime did not reach an action-ready state after install"
}

$verifierFullPath = [IO.Path]::GetFullPath($VerifierScript)
if (-not $verifierFullPath.ToLowerInvariant().StartsWith($transactionPrefix) -or
    -not (Test-Path -LiteralPath $verifierFullPath -PathType Leaf)) {
    throw "Staged Authenticode verifier is missing or escaped the transaction root"
}
$VerifierScript = $verifierFullPath

try {
    Assert-TransactionHandoff
    Write-TransactionPhase -Phase 'bootstrap-verifying'
    Assert-StagedInstaller -Path $TargetInstaller -ExpectedSha256 $TargetSha256
    Assert-StagedInstaller -Path $RollbackInstaller -ExpectedSha256 $RollbackSha256
    Write-TransactionPhase -Phase 'bootstrap-verified'

    $targetAttempted = $false
    try {
        Write-TransactionPhase -Phase 'installing-target'
        Assert-StagedInstaller -Path $TargetInstaller -ExpectedSha256 $TargetSha256
        $targetAttempted = $true
        Invoke-Installer -Path $TargetInstaller -LogName 'target-install.log'
        $health = Start-RuntimeAndWaitHealthy -ExpectedVersion $TargetVersion
        Write-TransactionPhase -Phase 'committed' -Detail ("state={0}; actions={1}" -f $health.state, $health.actionCount)
        exit 0
    } catch {
        if (-not $targetAttempted) {
            # Verification failed before any installer ran; preserve the original
            # installation instead of unnecessarily executing a rollback.
            throw
        }
        $targetError = $_.Exception.Message
        Write-TransactionPhase -Phase 'rolling-back' -Detail $targetError
        try {
            Assert-StagedInstaller -Path $RollbackInstaller -ExpectedSha256 $RollbackSha256
            Invoke-Installer -Path $RollbackInstaller -LogName 'rollback-install.log'
            $health = Start-RuntimeAndWaitHealthy -ExpectedVersion $RollbackVersion
            Write-TransactionPhase -Phase 'rolled-back' -Detail ("targetFailure={0}; rollbackState={1}; actions={2}" -f $targetError, $health.state, $health.actionCount)
            exit 10
        } catch {
            $rollbackError = $_.Exception.Message
            Write-TransactionPhase -Phase 'recovery-required' -Detail ("targetFailure={0}; rollbackFailure={1}" -f $targetError, $rollbackError)
            exit 20
        }
    }
} catch {
    try {
        # Replay attempts must not overwrite the proof of a previously
        # committed, rolled-back or recovery-required transaction.
        if (Test-Path -LiteralPath $journalPath -PathType Leaf) {
            $lastPhase = (Get-Content -LiteralPath $journalPath -Raw -Encoding UTF8 | ConvertFrom-Json).phase
            if ($lastPhase -notin @('committed', 'rolled-back', 'recovery-required', 'blocked', 'handoff-failed')) {
                Write-TransactionPhase -Phase 'blocked' -Detail $_.Exception.Message
            }
        }
    } catch {
    }
    exit 30
}
