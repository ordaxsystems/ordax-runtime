# Verify the canonical source/version, installer identity, and uploaded bytes
# before allowing a tagged Windows release to reach Authenticode verification.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Tag,
    [Parameter(Mandatory = $true)][string]$SourceLock,
    [Parameter(Mandatory = $true)][string]$ArtifactDirectory
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $SourceLock -PathType Leaf)) {
    throw "Canonical Studio source lock is missing"
}
if (-not (Test-Path -LiteralPath $ArtifactDirectory -PathType Container)) {
    throw "Validated Windows artifact directory is missing"
}

$lock = Get-Content -LiteralPath $SourceLock -Raw | ConvertFrom-Json
if ($lock.schema -cne "ordax.studio-source-lock/1" -or
    $lock.repository -cne "ordaxsystems/ordax-apps" -or
    $lock.path -cne "apps/studio" -or
    $lock.authority -cne "none" -or
    [string]$lock.commit -cnotmatch '^[a-f0-9]{40}$') {
    throw "Canonical Studio source lock identity/provenance is invalid"
}
$version = [string]$lock.version
if ($version -cnotmatch '^\d+\.\d+\.\d+$') {
    throw "Canonical Studio version must be a stable three-part version"
}
$expectedTag = "v$version"
if ($Tag -cne $expectedTag) {
    throw "Release tag does not match canonical Studio version ($expectedTag)"
}

$expectedName = "ORDAX-Studio-Setup-$version-x64.exe"
$executables = @(Get-ChildItem -LiteralPath $ArtifactDirectory -File -Force | Where-Object {
    $_.Extension -ieq ".exe"
})
if ($executables.Count -ne 1 -or $executables[0].Name -cne $expectedName) {
    throw "Release artifact must contain exactly the canonical Studio installer ($expectedName)"
}

$checksumPath = Join-Path $ArtifactDirectory "SHA256SUMS.txt"
if (-not (Test-Path -LiteralPath $checksumPath -PathType Leaf)) {
    throw "Release artifact is missing SHA256SUMS.txt"
}
$checksumLines = @(Get-Content -LiteralPath $checksumPath | Where-Object {
    -not [string]::IsNullOrWhiteSpace($_)
})
if ($checksumLines.Count -ne 1) {
    throw "Release checksum manifest must have one strict SHA-256 entry"
}
$checksumMatch = [regex]::Match($checksumLines[0], '^([a-f0-9]{64})  ([A-Za-z0-9._-]+)$')
if (-not $checksumMatch.Success) {
    throw "Release checksum manifest must have one strict SHA-256 entry"
}
$expectedHash = $checksumMatch.Groups[1].Value
$checksumFileName = $checksumMatch.Groups[2].Value
if ($checksumFileName -cne $expectedName) {
    throw "Release checksum does not refer to canonical installer"
}
$installerStream = [System.IO.File]::OpenRead($executables[0].FullName)
try {
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $actualHash = ([System.BitConverter]::ToString($sha256.ComputeHash($installerStream))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha256.Dispose()
    }
} finally {
    $installerStream.Dispose()
}
if ($actualHash -cne $expectedHash) {
    throw "Release artifact SHA-256 does not match verified checksum manifest"
}

Write-Host "ORDAX_RELEASE_PROVENANCE_VALID"
Write-Host "ORDAX_STUDIO_RELEASE_VERSION=$version"
Write-Host "ORDAX_STUDIO_RELEASE_SOURCE_COMMIT=$($lock.commit)"
