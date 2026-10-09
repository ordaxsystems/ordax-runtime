# Cross-repository presentation build proof. This is an isolated Windows
# candidate, NOT the ORDAX Windows release or a second installer.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$AppSourceRoot,
    [Parameter(Mandatory = $true)][string]$OutputDirectory
)

$ErrorActionPreference = "Stop"

function Require-Success([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE" }
}
function Read-Json([string]$File) {
    if (-not (Test-Path -LiteralPath $File -PathType Leaf)) {
        throw "Missing canonical source/artifact: $File"
    }
    return Get-Content -LiteralPath $File -Encoding UTF8 -Raw | ConvertFrom-Json
}
function Normalize-FullPath([string]$Value) {
    return [System.IO.Path]::GetFullPath($Value).TrimEnd('\', '/')
}

$source = (Resolve-Path -LiteralPath $AppSourceRoot -ErrorAction Stop).Path
if (-not [System.IO.Path]::IsPathRooted($OutputDirectory)) {
    throw "Presentation output must be an absolute path"
}
$destination = Normalize-FullPath $OutputDirectory
if (Test-Path -LiteralPath $destination) {
    throw "Presentation candidate destination must be empty and not pre-existing"
}
$checkout = (& git -C $source rev-parse --show-toplevel)
Require-Success "Resolve Studio source checkout"
if ((Normalize-FullPath $checkout.Trim()) -ne (Normalize-FullPath $source)) {
    throw "Presentation source must be the canonical repository checkout root"
}
$commit = (& git -C $source rev-parse HEAD).Trim()
Require-Success "Resolve immutable Studio source commit"
if ($commit -notmatch '^[0-9a-f]{40}$') {
    throw "Presentation source commit is not an immutable Git SHA"
}
$dirty = @(& git -C $source status --porcelain -- apps/studio tools/assistant-host)
Require-Success "Check canonical Studio source state"
if ($dirty.Count -gt 0) {
    throw "Presentation source is dirty or has untracked Studio/host files"
}

$appManifest = Read-Json (Join-Path $source "apps\studio\app.json")
$hostRoot = Join-Path $source "tools\assistant-host"
$hostPackage = Read-Json (Join-Path $hostRoot "package.json")
if ($appManifest.schema -ne "ordax.component-manifest/1" -or
    $appManifest.id -ne "studio" -or
    $appManifest.owner -ne "ordaxsystems/ordax-apps" -or
    $appManifest.version -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
    $appManifest.version -ne $hostPackage.version -or
    $hostPackage.dependencies.electron -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$') {
    throw "Studio version/ownership is not aligned with canonical Apps manifests"
}
$ai = Read-Json (Join-Path $source "apps\studio\ai\manifest.json")
$actions = Read-Json (Join-Path $source "apps\studio\actions\manifest.json")
if ($ai.appVersion -ne $appManifest.version -or
    $actions.appVersion -ne $appManifest.version -or
    $ai.authority -ne "none" -or $actions.authority -ne "none") {
    throw "Studio AI/Application Action manifests diverge from the app authority"
}
if (-not (Test-Path -LiteralPath (Join-Path $source "apps\studio\conversation\src\index.html") -PathType Leaf)) {
    throw "Studio Conversation canonical entrypoint is absent"
}
if (-not (Test-Path -LiteralPath (Join-Path $source "tools\assistant-host\verify-windows-candidate.mjs") -PathType Leaf)) {
    throw "Studio candidate independent SHA verifier is absent"
}
Write-Host "ORDAX_STUDIO_CANONICAL_SOURCE_SHA=$commit"
Write-Host "ORDAX_STUDIO_PRESENTATION_VERSION=$($appManifest.version)"

& npm.cmd --prefix $hostRoot ci --no-audit --no-fund
Require-Success "Pinned Studio Electron npm ci"
& npm.cmd --prefix $hostRoot run setup:native
Require-Success "Pinned Studio Electron setup:native"
if (-not (Test-Path -LiteralPath (Join-Path $hostRoot "node_modules\electron\dist\electron.exe") -PathType Leaf)) {
    throw "Pinned Electron.exe was not materialized"
}
& npm.cmd --prefix $hostRoot run build:windows
Require-Success "Canonical Studio Windows folder build"
& node.exe (Join-Path $hostRoot "verify-windows-candidate.mjs")
Require-Success "Independent Studio Windows candidate verification"

$pointer = Read-Json (Join-Path $hostRoot ".data\portable\latest-build.json")
if ([string]::IsNullOrWhiteSpace([string]$pointer.directory) -or
    [string]::IsNullOrWhiteSpace([string]$pointer.executable)) {
    throw "Canonical builder did not produce an immutable folder receipt"
}
$built = Normalize-FullPath ([string]$pointer.directory)
$portableBase = Normalize-FullPath (Join-Path $hostRoot ".data\portable")
if ((Normalize-FullPath (Split-Path -Parent $built)) -ne $portableBase -or
    (Normalize-FullPath ([string]$pointer.executable)) -ne
        (Normalize-FullPath (Join-Path $built "ORDAX Studio.exe"))) {
    throw "Canonical builder returned an unexpected source/output path"
}
$manifest = Read-Json (Join-Path $built "build-manifest.json")
$deps = Read-Json (Join-Path $built "candidate-dependencies.json")
if ($manifest.sourceRepository -ne "ordaxsystems/ordax-apps" -or
    $manifest.sourcePath -ne "apps/studio" -or
    $manifest.entrypoint -ne "apps/studio/conversation/src/index.html" -or
    $manifest.version -ne $appManifest.version -or $manifest.candidate -ne $true -or
    $deps.candidate -ne $true -or
    $deps.installation.artifact -ne "unsigned-portable-folder" -or
    $deps.installation.officialWindowsInstallerVerified -ne $false -or
    $deps.installation.ordaxOSAdapterVerified -ne $false -or
    $deps.installation.bundlesSecondRuntimeIntoOrdaxOS -ne $false -or
    $deps.projectRuntime.bundled -ne $false) {
    throw "Presentation candidate must not self-declare production authority"
}
New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
Copy-Item -LiteralPath $built -Destination $destination -Recurse -Force
if (-not (Test-Path -LiteralPath (Join-Path $destination "ORDAX Studio.exe") -PathType Leaf)) {
    throw "Electron presentation binary not present in isolated stage"
}

$expected = @{}
foreach ($file in @($manifest.files)) {
    $relative = [string]$file.path
    if ($relative -notmatch '^[^/\\][^\\]*$' -or
        $relative.Split('/') -contains '..' -or
        $relative.Split('/') -contains '.' -or
        $expected.ContainsKey($relative) -or
        [string]$file.sha256 -cnotmatch '^[a-f0-9]{64}$') {
        throw "Invalid path, duplicate or digest in canonical presentation inventory"
    }
    $expected[$relative] = [string]$file.sha256
}
if ($expected.Count -lt 100) { throw "Presentation artifact inventory is unexpectedly small" }
$copied = @(Get-ChildItem -LiteralPath $destination -File -Recurse -Force)
$checked = 0
foreach ($file in $copied) {
    if (($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Symlink/reparse point in staged presentation"
    }
    $relative = $file.FullName.Substring($destination.Length).TrimStart('\', '/').Replace('\','/')
    if ($relative -eq "build-manifest.json") { continue }
    if (-not $expected.ContainsKey($relative)) { throw "Unlisted file in staged presentation: $relative" }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant()
    if ($actual -cne $expected[$relative]) { throw "Copied presentation byte mismatch: $relative" }
    $checked++
}
if ($checked -ne $expected.Count) {
    throw "Copied presentation file inventory differs from canonical source"
}
$main = Read-Json (Join-Path $destination "resources\app\package.json")
if ($main.main -ne "tools/assistant-host/native/main.cjs" -or
    $main.version -ne $appManifest.version) {
    throw "Staged Electron entrypoint or version is incompatible"
}
if (Test-Path -LiteralPath (Join-Path $destination "workbench")) {
    throw "Unexpected second Studio UI host in isolated presentation candidate"
}
Write-Host "ORDAX_STUDIO_PRESENTATION_HANDOFF=PASS"
Write-Host "ORDAX_STUDIO_PRESENTATION_FILES_VERIFIED=$checked"
Write-Host "ORDAX_STUDIO_PRESENTATION_RELEASE=NOT_PROMOTED"
