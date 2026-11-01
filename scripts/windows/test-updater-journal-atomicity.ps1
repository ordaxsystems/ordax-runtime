# Isolated Windows proof of the single canonical updater journal writer.
# This test reads the REAL function from the bootstrap AST; it never executes
# the updater's top-level code, installers, or the user's actual local state.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'

$bootstrap = Join-Path $PSScriptRoot 'apply-ordax-production-update.ps1'
$source = Get-Content -LiteralPath $bootstrap -Raw
if ($source -match 'Move-Item -LiteralPath \$pending -Destination \$journalPath') {
    throw 'Non-atomic journal replacement has returned'
}
foreach ($expected in @(
    '[IO.File]::Replace($pending, $journalPath, [NullString]::Value)',
    '$handle.Flush($true)',
    '[IO.FileMode]::CreateNew',
    '[IO.FileOptions]::WriteThrough'
)) {
    if (-not $source.Contains($expected)) { throw "Missing durability gate $expected" }
}

$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $bootstrap, [ref]$tokens, [ref]$errors
)
if (@($errors).Count -ne 0) { throw 'Canonical update bootstrap has PowerShell parse errors' }
$functions = @($ast.FindAll({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -ceq 'Write-TransactionPhase'
}, $true))
if ($functions.Count -ne 1) { throw 'Exactly one canonical journal writer is required' }
. ([ScriptBlock]::Create($functions[0].Extent.Text))

$root = Join-Path ([IO.Path]::GetTempPath()) (
    'ordax-journal-proof-' + [guid]::NewGuid().ToString('N')
)
$transactionRootPath = Join-Path $root ([guid]::NewGuid().ToString('N'))
$journalPath = Join-Path $transactionRootPath 'transaction.json'
New-Item -ItemType Directory -Path $transactionRootPath -Force | Out-Null

function New-InitialJournal([string]$phase) {
    $journal = [ordered]@{
        schema = 'ordax.windows-update-transaction/1'
        authority = 'local-privileged-updater'
        transactionId = (Split-Path -Path $transactionRootPath -Leaf)
        phase = $phase
        installedVersion = '0.15.6'
        target = @{ version = '0.15.9'; installerSha256 = ('a' * 64) }
        rollback = @{ version = '0.15.6'; installerSha256 = ('b' * 64) }
    }
    [IO.File]::WriteAllText(
        $journalPath, ($journal | ConvertTo-Json -Depth 8 -Compress),
        [Text.UTF8Encoding]::new($false)
    )
}

function Assert-Phase([string]$phase) {
    $actual = Get-Content -LiteralPath $journalPath -Raw | ConvertFrom-Json
    if ($actual.phase -cne $phase -or
        $actual.target.installerSha256 -cne ('a' * 64) -or
        $actual.rollback.installerSha256 -cne ('b' * 64) -or
        -not $actual.updatedAtUtc) {
        throw "Journal transition to $phase lost original identity or evidence"
    }
    return $actual
}

function Assert-Rejected([string]$phase) {
    $old = [IO.File]::ReadAllBytes($journalPath)
    $rejected = $false
    try {
        Write-TransactionPhase -Phase $phase
    } catch {
        if ($_.Exception.Message -notlike '*phase transition is invalid*') {
            throw
        }
        $rejected = $true
    }
    if (-not $rejected) { throw "Unsafe transition to $phase was allowed" }
    $current = [IO.File]::ReadAllBytes($journalPath)
    if ([Convert]::ToBase64String($old) -cne [Convert]::ToBase64String($current)) {
        throw 'Rejected transition modified the durable journal'
    }
}

try {
    New-InitialJournal 'handoff-launching'
    # Interrupted prior temp files are not an alternate SSOT.
    $orphan = "$journalPath.next"
    [IO.File]::WriteAllText($orphan, '{"phase":"committed","authority":"attacker"}')
    Write-TransactionPhase -Phase 'bootstrap-verifying' -Detail 'preflight passed'
    $snapshot = Assert-Phase 'bootstrap-verifying'
    if ($snapshot.detail -cne 'preflight passed') { throw 'Bounded diagnostic missing' }
    if ((Get-Content -LiteralPath $orphan -Raw) -notlike '*"authority":"attacker"*') {
        throw 'Stale journal fragment was inappropriately trusted or rewritten'
    }
    Write-TransactionPhase -Phase 'bootstrap-verified'
    $snapshot = Assert-Phase 'bootstrap-verified'
    if ($snapshot.PSObject.Properties.Name -contains 'detail') { throw 'Stale diagnostics retained' }
    Write-TransactionPhase -Phase 'installing-target'
    Assert-Phase 'installing-target' | Out-Null
    Write-TransactionPhase -Phase 'committed'
    Assert-Phase 'committed' | Out-Null
    Assert-Rejected 'bootstrap-verifying'
    Assert-Rejected 'installing-target'

    New-InitialJournal 'installing-target'
    Write-TransactionPhase -Phase 'rolling-back'
    Assert-Phase 'rolling-back' | Out-Null
    Write-TransactionPhase -Phase 'rolled-back'
    Assert-Phase 'rolled-back' | Out-Null
    Assert-Rejected 'committed'

    New-InitialJournal 'installing-target'
    Write-TransactionPhase -Phase 'recovery-required'
    Assert-Phase 'recovery-required' | Out-Null
    Assert-Rejected 'rolling-back'

    New-InitialJournal 'bootstrap-verifying'
    Write-TransactionPhase -Phase 'blocked'
    Assert-Phase 'blocked' | Out-Null
    Assert-Rejected 'bootstrap-verified'

    New-InitialJournal 'handoff-launching'
    [IO.File]::WriteAllText($journalPath, (' ' * 65537))
    $oversized = $false
    try { Write-TransactionPhase -Phase 'bootstrap-verifying' } catch {
        if ($_.Exception.Message -like '*journal is unsafe*') { $oversized = $true } else { throw }
    }
    if (-not $oversized) { throw 'Oversized journal was accepted' }
    if ((Get-Item -LiteralPath $journalPath).Length -ne 65537) {
        throw 'Oversized journal was silently modified'
    }
    # The intentionally planted transaction.json.next fragment is expected
    # to remain untouched; only fresh GUID-named write artifacts are forbidden.
    $temps = @(Get-ChildItem -LiteralPath $transactionRootPath -Filter 'transaction.*.next' |
        Where-Object { $_.Name -match '^transaction\\.[0-9a-f]{32}\\.next
    Write-Output 'ORDAX_UPDATE_DURABLE_JOURNAL_CONTRACT=PASS'
} finally {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
 })
    if ($temps.Count -ne 0) { throw 'New journal writes left unverified temporary files' }
    Write-Output 'ORDAX_UPDATE_DURABLE_JOURNAL_CONTRACT=PASS'
} finally {
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
