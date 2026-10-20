# Gate public stable releases on canonical Runtime Git history. GitHub main
# governance is delegated to the shared publication SSOT so every public
# publication path evaluates the same protection/ruleset policy.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Tag,
    [Parameter(Mandatory = $true)][string]$ExpectedCommit,
    [Parameter(Mandatory = $true)][string]$RepositoryRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($Tag -cnotmatch '^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') {
    throw "Release Git tag must be an exact stable version"
}
if ($ExpectedCommit -cnotmatch '^[0-9a-f]{40}$') {
    throw "Expected tagged Runtime commit SHA is malformed"
}
if (-not (Test-Path -LiteralPath $RepositoryRoot -PathType Container)) {
    throw "Runtime checkout directory is missing"
}

function Git-Output {
    param([string[]]$Arguments, [string]$Description)
    $output = & git -C $RepositoryRoot @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Git release provenance failed: $Description"
    }
    return ([string]($output | Select-Object -Last 1)).Trim()
}

$shallow = Git-Output -Arguments @("rev-parse", "--is-shallow-repository") -Description "determine Git history depth"
if ($shallow -cne "false") {
    throw "Release provenance requires full Git history; shallow checkout is not accepted"
}

$head = Git-Output -Arguments @("rev-parse", "--verify", "HEAD^{commit}") -Description "resolve checked-out Runtime commit"
$tagCommit = Git-Output -Arguments @("rev-parse", "--verify", "refs/tags/$Tag^{commit}") -Description "resolve exact release tag"
$mainCommit = Git-Output -Arguments @("rev-parse", "--verify", "refs/remotes/origin/main^{commit}") -Description "resolve canonical origin/main"

if ($head -cne $ExpectedCommit -or $tagCommit -cne $ExpectedCommit) {
    throw "Release tag, checked-out Runtime HEAD, and trusted GitHub event SHA disagree"
}

# Exit code 1 means not an ancestor; other errors also block release.
& git -C $RepositoryRoot merge-base --is-ancestor $ExpectedCommit $mainCommit
if ($LASTEXITCODE -ne 0) {
    throw "Release Runtime commit is not an ancestor of canonical origin/main"
}

# GitHub Actions always exposes GITHUB_REPOSITORY. Local Git-history contract
# tests intentionally remove it so they exercise only the ancestry half of this
# gate. Production delegates the administrative policy to the shared SSOT.
if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_REPOSITORY)) {
    $governanceGate = Join-Path $PSScriptRoot "assert-github-main-governance.ps1"
    if (-not (Test-Path -LiteralPath $governanceGate -PathType Leaf)) {
        throw "Shared GitHub main governance gate is missing"
    }
    & $governanceGate -ExpectedMainCommit $mainCommit

    # Preserve stable-release-specific evidence markers for existing release
    # diagnostics while the policy itself remains centralized in the helper.
    Write-Host "ORDAX_RELEASE_REPOSITORY_GOVERNANCE=PASS"
    Write-Host "ORDAX_RELEASE_GITHUB_MAIN_COMMIT=$mainCommit"
    Write-Host "ORDAX_RELEASE_MAIN_RULESET_POLICY=PASS"
}

Write-Host "ORDAX_RELEASE_MAIN_ANCESTRY_VALID"
Write-Host "ORDAX_RELEASE_RUNTIME_COMMIT=$ExpectedCommit"
Write-Host "ORDAX_RELEASE_MAIN_COMMIT=$mainCommit"
