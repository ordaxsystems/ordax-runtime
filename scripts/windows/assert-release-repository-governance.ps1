# Fail closed when the canonical GitHub repository is not protected for public release.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Repository,
    [Parameter(Mandatory = $false)][string]$Token = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($Repository -cnotmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    throw "GitHub repository identity is missing or invalid"
}

$headers = @{
    Accept = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
    "User-Agent" = "ORDAX-Release-Governance-Gate"
}
if (-not [string]::IsNullOrWhiteSpace($Token)) {
    $headers.Authorization = "Bearer $Token"
}

$repositoryUri = "https://api.github.com/repos/$Repository"
$repositoryState = Invoke-RestMethod -Method Get -Uri $repositoryUri -Headers $headers
if (-not $repositoryState -or [string]$repositoryState.default_branch -ne "main") {
    throw "Public ORDAX releases require main to be the canonical GitHub default branch"
}

$branchUri = "https://api.github.com/repos/$Repository/branches/main"
$main = Invoke-RestMethod -Method Get -Uri $branchUri -Headers $headers
if (-not $main) {
    throw "Canonical main branch metadata could not be read"
}
if ($main.name -ne "main") {
    throw "Canonical main branch identity mismatch"
}
if (-not [bool]$main.protected) {
    throw "Blocked public release: canonical main is not protected by GitHub branch protection or rulesets"
}
if (-not $main.commit -or [string]$main.commit.sha -notmatch '^[0-9a-f]{40}$') {
    throw "Canonical main branch commit identity is invalid"
}

Write-Host "ORDAX_RELEASE_REPOSITORY_GOVERNANCE=PASS"
Write-Host ("ORDAX_RELEASE_CANONICAL_MAIN_SHA=" + [string]$main.commit.sha)
