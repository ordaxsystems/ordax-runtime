# Gate public releases on the actual canonical Runtime Git history and GitHub main
# governance, not merely a version-looking tag. A tag created on an unrelated
# branch or a release from an unprotected main must fail closed.
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

# GitHub Actions always exposes GITHUB_REPOSITORY. Local Git-history contract tests
# intentionally remove it so they do not depend on live repository administration.
if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_REPOSITORY)) {
    if ($env:GITHUB_REPOSITORY -cnotmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
        throw "GitHub repository identity is missing or invalid"
    }

    $headers = @{
        Accept = "application/vnd.github+json"
        "X-GitHub-Api-Version" = "2022-11-28"
        "User-Agent" = "ORDAX-Release-Governance-Gate"
    }
    $repositoryUri = "https://api.github.com/repos/$($env:GITHUB_REPOSITORY)"
    $repositoryState = Invoke-RestMethod -Method Get -Uri $repositoryUri -Headers $headers
    if (-not $repositoryState -or [string]$repositoryState.default_branch -ne "main") {
        throw "Public ORDAX releases require main to be the canonical GitHub default branch"
    }

    $branchUri = "https://api.github.com/repos/$($env:GITHUB_REPOSITORY)/branches/main"
    $githubMain = Invoke-RestMethod -Method Get -Uri $branchUri -Headers $headers
    if (-not $githubMain -or $githubMain.name -ne "main") {
        throw "Canonical GitHub main branch metadata could not be verified"
    }
    if (-not [bool]$githubMain.protected) {
        throw "Blocked public release: canonical main is not protected by GitHub branch protection or rulesets"
    }
    if (-not $githubMain.commit -or [string]$githubMain.commit.sha -notmatch '^[0-9a-f]{40}$') {
        throw "Canonical GitHub main branch commit identity is invalid"
    }
    if ([string]$githubMain.commit.sha -cne $mainCommit) {
        throw "Canonical GitHub main moved after the trusted release checkout was resolved"
    }

    Write-Host "ORDAX_RELEASE_REPOSITORY_GOVERNANCE=PASS"
    Write-Host "ORDAX_RELEASE_GITHUB_MAIN_COMMIT=$($githubMain.commit.sha)"
}

Write-Host "ORDAX_RELEASE_MAIN_ANCESTRY_VALID"
Write-Host "ORDAX_RELEASE_RUNTIME_COMMIT=$ExpectedCommit"
Write-Host "ORDAX_RELEASE_MAIN_COMMIT=$mainCommit"
