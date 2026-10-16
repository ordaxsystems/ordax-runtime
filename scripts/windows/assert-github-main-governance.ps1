# Canonical GitHub main governance gate shared by every ORDAX publication path.
# Publication must fail closed unless GitHub itself reports the required branch
# protection/ruleset policy on the exact main commit that was evaluated.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ExpectedMainCommit
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($ExpectedMainCommit -cnotmatch '^[0-9a-f]{40}$') {
    throw "Expected canonical main commit SHA is malformed"
}
if ([string]::IsNullOrWhiteSpace($env:GITHUB_REPOSITORY) -or $env:GITHUB_REPOSITORY -cnotmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    throw "GitHub repository identity is missing or invalid"
}

$headers = @{
    Accept = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
    "User-Agent" = "ORDAX-Main-Governance-Gate"
}
$repositoryUri = "https://api.github.com/repos/$($env:GITHUB_REPOSITORY)"
$repositoryState = Invoke-RestMethod -Method Get -Uri $repositoryUri -Headers $headers
if (-not $repositoryState -or [string]$repositoryState.default_branch -ne "main") {
    throw "ORDAX publication requires main to be the canonical GitHub default branch"
}

$branchUri = "https://api.github.com/repos/$($env:GITHUB_REPOSITORY)/branches/main"
$githubMain = Invoke-RestMethod -Method Get -Uri $branchUri -Headers $headers
if (-not $githubMain -or [string]$githubMain.name -ne "main") {
    throw "Canonical GitHub main branch metadata could not be verified"
}
if (-not [bool]$githubMain.protected) {
    throw "Blocked publication: canonical main is not protected by GitHub branch protection or rulesets"
}
if (-not $githubMain.commit -or [string]$githubMain.commit.sha -notmatch '^[0-9a-f]{40}$') {
    throw "Canonical GitHub main branch commit identity is invalid"
}
if ([string]$githubMain.commit.sha -cne $ExpectedMainCommit) {
    throw "Canonical GitHub main moved after the trusted publication commit was resolved"
}

# `protected` alone can represent a weak policy. Require the concrete active
# rules that define the ORDAX publication trust boundary. The public endpoint
# includes repository- and organization-level rules that apply to main.
$rulesUri = "https://api.github.com/repos/$($env:GITHUB_REPOSITORY)/rules/branches/main?per_page=100"
$mainRules = @(Invoke-RestMethod -Method Get -Uri $rulesUri -Headers $headers)
if ($mainRules.Count -eq 0) {
    throw "Blocked publication: canonical main has no active inspectable GitHub ruleset policy"
}

$ruleTypes = @($mainRules | ForEach-Object { [string]$_.type })
foreach ($requiredRuleType in @("deletion", "non_fast_forward", "pull_request", "required_status_checks")) {
    if ($ruleTypes -cnotcontains $requiredRuleType) {
        throw "Blocked publication: canonical main ruleset is missing required rule '$requiredRuleType'"
    }
}

$pullRequestRules = @($mainRules | Where-Object { [string]$_.type -ceq "pull_request" })
$requiresResolvedThreads = $false
foreach ($rule in $pullRequestRules) {
    if ([bool]$rule.parameters.required_review_thread_resolution) {
        $requiresResolvedThreads = $true
        break
    }
}
if (-not $requiresResolvedThreads) {
    throw "Blocked publication: canonical main must require pull-request conversation resolution"
}

$requiredStatusRules = @($mainRules | Where-Object { [string]$_.type -ceq "required_status_checks" })
$requiredContexts = @(
    "packaging-contracts",
    "build-installer",
    "install-smoke",
    "product-mcp-minimum-sdk",
    "runtime-contracts"
)
$configuredContexts = @(
    foreach ($rule in $requiredStatusRules) {
        foreach ($check in @($rule.parameters.required_status_checks)) {
            if (-not [string]::IsNullOrWhiteSpace([string]$check.context)) {
                [string]$check.context
            }
        }
    }
)
foreach ($context in $requiredContexts) {
    if ($configuredContexts -cnotcontains $context) {
        throw "Blocked publication: canonical main does not require status check '$context'"
    }
}

$strictStatusChecks = $false
foreach ($rule in $requiredStatusRules) {
    if ([bool]$rule.parameters.strict_required_status_checks_policy) {
        $strictStatusChecks = $true
        break
    }
}
if (-not $strictStatusChecks) {
    throw "Blocked publication: canonical main required status checks must be strict/up-to-date"
}

Write-Host "ORDAX_REPOSITORY_GOVERNANCE=PASS"
Write-Host "ORDAX_GITHUB_MAIN_COMMIT=$($githubMain.commit.sha)"
Write-Host "ORDAX_MAIN_RULESET_POLICY=PASS"
