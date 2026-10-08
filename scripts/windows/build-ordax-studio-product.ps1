param(
    [string]$Version = "",
    [string]$OutputDirectory = "",
    [string]$PythonVersion = "3.12.10"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $repoRoot "dist\windows"
}

$requestedCanonicalVersion = ([string]$env:ORDAX_STUDIO_CANONICAL_VERSION).Trim()
$studioAppSource = ([string]$env:ORDAX_STUDIO_APP_SOURCE).Trim()
if (-not $studioAppSource) {
    throw "ORDAX_STUDIO_APP_SOURCE is required: the Windows installer must use the pinned ordax-apps Studio package, never the Runtime's historical UI copy"
}
$studioAppSource = (Resolve-Path -LiteralPath $studioAppSource -ErrorAction Stop).Path
if ($studioAppSource) {
    $sourceLockPath = Join-Path $repoRoot "studio-source.lock.json"
    if (-not (Test-Path $sourceLockPath)) { throw "Studio source lock is missing" }
    $sourceLock = Get-Content $sourceLockPath -Raw | ConvertFrom-Json
    if ($sourceLock.schema -ne "ordax.studio-source-lock/1") { throw "Studio source lock schema is invalid" }
    if ($sourceLock.repository -ne "ordaxsystems/ordax-apps") { throw "Studio source repository is not canonical" }
    if ($sourceLock.path -ne "apps/studio") { throw "Studio source path is not canonical" }
    if ($sourceLock.authority -ne "none") { throw "Studio source lock must not carry authority" }
    if ([string]$sourceLock.commit -notmatch '^[0-9a-f]{40}$') { throw "Studio source lock commit must be immutable" }

    # The source cannot be a same-version folder or a dirty local copy.
    # Git checkout HEAD and the portable path must match the locked revision.
    $checkoutRoot = (& git -C $studioAppSource rev-parse --show-toplevel)
    if ($LASTEXITCODE -ne 0 -or -not $checkoutRoot) { throw "Studio source must be a Git checkout" }
    $checkoutRoot = (Resolve-Path -LiteralPath $checkoutRoot.Trim() -ErrorAction Stop).Path
    $expectedStudioPath = [System.IO.Path]::GetFullPath(
        (Join-Path $checkoutRoot ($sourceLock.path.Replace("/", [System.IO.Path]::DirectorySeparatorChar)))
    )
    if ([System.IO.Path]::GetFullPath($studioAppSource) -ne $expectedStudioPath) {
        throw "Studio source path does not match the canonical lock"
    }
    $checkedOutCommit = (& git -C $checkoutRoot rev-parse HEAD)
    if ($LASTEXITCODE -ne 0 -or $checkedOutCommit.Trim() -ne [string]$sourceLock.commit) {
        throw "Studio source checkout must match the exact pinned Git commit"
    }
    $sourceChanges = @(& git -C $checkoutRoot status --porcelain -- $sourceLock.path)
    if ($LASTEXITCODE -ne 0 -or $sourceChanges.Count -ne 0) {
        throw "Studio source checkout contains local changes or untracked files"
    }

    $studioAppManifestPath = Join-Path $studioAppSource "app.json"
    if (-not (Test-Path $studioAppManifestPath)) { throw "Canonical Studio app manifest is missing: $studioAppManifestPath" }
    $studioAppManifest = Get-Content $studioAppManifestPath -Raw | ConvertFrom-Json
    if ($studioAppManifest.schema -ne "ordax.component-manifest/1" -or $studioAppManifest.id -ne "studio") {
        throw "Canonical Studio app manifest is incompatible"
    }
    if ([string]$studioAppManifest.version -ne [string]$sourceLock.version) {
        throw "Canonical Studio version does not match studio-source.lock.json"
    }
    if ($requestedCanonicalVersion -and $requestedCanonicalVersion -ne [string]$sourceLock.version) {
        throw "ORDAX Studio canonical environment version conflicts with the locked app manifest"
    }

    $studioAiManifestPath = Join-Path $studioAppSource "ai\\manifest.json"
    if (-not (Test-Path $studioAiManifestPath)) {
        throw "Canonical Studio App Intelligence manifest is missing: $studioAiManifestPath"
    }
    $studioAiManifest = Get-Content $studioAiManifestPath -Raw | ConvertFrom-Json
    if (
        $studioAiManifest.schema -ne "ordax.app-intelligence-manifest/1" -or
        $studioAiManifest.appId -ne "studio" -or
        [string]$studioAiManifest.appVersion -ne [string]$studioAppManifest.version -or
        $studioAiManifest.authority -ne "none" -or
        $studioAiManifest.execution -ne "declarative-only"
    ) {
        throw "Canonical Studio App Intelligence manifest is incompatible"
    }

    $studioActionManifestPath = Join-Path $studioAppSource "actions\\manifest.json"
    if (-not (Test-Path $studioActionManifestPath)) {
        throw "Canonical Studio Application Action manifest is missing: $studioActionManifestPath"
    }
    $studioActionManifest = Get-Content $studioActionManifestPath -Raw | ConvertFrom-Json
    if (
        $studioActionManifest.schema -ne "ordax.application-action-manifest/1" -or
        $studioActionManifest.appId -ne "studio" -or
        [string]$studioActionManifest.appVersion -ne [string]$studioAppManifest.version -or
        $studioActionManifest.authority -ne "none" -or
        $studioActionManifest.execution -ne "proposal-only"
    ) {
        throw "Canonical Studio Application Action manifest is incompatible"
    }

    # Produce only immutable app metadata here. Runtime Git sources are NEVER
    # rewritten by a Studio installer build; inject the portable bundle below,
    # into private Python site-packages after pip has installed the host.
    $appIntelligenceRegistry = [ordered]@{
        schema = "ordax.app-intelligence-registry/1"
        authority = "none"
        source = [ordered]@{
            repository = [string]$sourceLock.repository
            commit = [string]$sourceLock.commit
        }
        apps = @(
            [ordered]@{
                id = [string]$studioAppManifest.id
                title = [string]$studioAppManifest.title
                version = [string]$studioAppManifest.version
                manifest = $studioAiManifest
            }
        )
    }
    $appIntelligenceRegistryJson = $appIntelligenceRegistry | ConvertTo-Json -Depth 20
    $portableHtml = Get-Content (Join-Path $studioAppSource "src\index.html") -Raw
    $portableHtml = $portableHtml.Replace("../assets/", "assets/")
    $portableHtml = $portableHtml.Replace(
        '<script src="host_contract.js"></script>',
        '<script src="host_bridge.js"></script>' + [Environment]::NewLine + '<script src="host_contract.js"></script>'
    )
    $canonicalVersion = [string]$studioAppManifest.version
    $canonicalVersionSource = "ordax-apps-lock"
    Write-Output "ORDAX_STUDIO_PORTABLE_SOURCE=$studioAppSource"
    Write-Output "ORDAX_STUDIO_PORTABLE_SOURCE_COMMIT=$($sourceLock.commit)"
}


# The application manifest in the pinned checkout is the only release-version
# authority. A tag, pyproject or explicit argument cannot silently replace it.
$Version = $Version.Trim()
if ($Version -and $Version -ne $canonicalVersion) {
    throw "ORDAX Studio version mismatch: explicit version '$Version' differs from canonical version '$canonicalVersion'"
}
$Version = $canonicalVersion
$versionSource = "ordax-apps-lock"


if ($Version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$') {
    throw "ORDAX Studio version is not valid semantic version syntax: $Version"
}

$hostVersion = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$targetMinor = ($PythonVersion -split '\.')[0..1] -join '.'
if ($hostVersion.Trim() -ne $targetMinor) {
    throw "Build Python must be $targetMinor.x to match the embedded runtime; found $hostVersion"
}

$buildRoot = Join-Path $repoRoot "build\windows-product"
$stageRoot = Join-Path $buildRoot "stage"
$runtimeRoot = Join-Path $stageRoot "runtime"
$redistRoot = Join-Path $stageRoot "redist"
$cacheRoot = Join-Path $buildRoot "cache"
Remove-Item $stageRoot -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $runtimeRoot, $redistRoot, $cacheRoot, $OutputDirectory | Out-Null

$pythonZip = Join-Path $cacheRoot "python-$PythonVersion-embed-amd64.zip"
if (-not (Test-Path $pythonZip)) {
    $pythonUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
    Write-Host "Downloading private CPython runtime: $pythonUrl"
    Invoke-WebRequest -Uri $pythonUrl -OutFile $pythonZip
}
Expand-Archive -LiteralPath $pythonZip -DestinationPath $runtimeRoot -Force

$stdlibZip = Get-ChildItem $runtimeRoot -Filter "python*.zip" | Select-Object -First 1
$pth = Get-ChildItem $runtimeRoot -Filter "python*._pth" | Select-Object -First 1
if (-not $stdlibZip -or -not $pth) {
    throw "Embedded Python runtime is incomplete"
}
@(
    $stdlibZip.Name,
    ".",
    "Lib",
    "Lib\site-packages",
    "import site"
) | Set-Content -LiteralPath $pth.FullName -Encoding ASCII

$sitePackages = Join-Path $runtimeRoot "Lib\site-packages"
New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null
$desktopPackage = ('{0}[desktop]' -f $repoRoot)
& python -m pip install --disable-pip-version-check --no-compile --upgrade --target $sitePackages $desktopPackage
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install ORDAX desktop runtime into the private Python distribution"
}

# The installer package is the only materialization destination for the
# portable Studio UI. Do not create or overwrite ordax_studio/assets in Git.
$packagedStudio = Join-Path $sitePackages "ordax_studio"
if (-not (Test-Path -LiteralPath (Join-Path $packagedStudio "host_bridge.js"))) {
    throw "Installed Runtime is missing its Windows Studio host bridge"
}
$targetAssets = Join-Path $packagedStudio "assets"
if (Test-Path -LiteralPath $targetAssets) {
    Remove-Item -LiteralPath $targetAssets -Recurse -Force
}
New-Item -ItemType Directory -Path $targetAssets -Force | Out-Null
Copy-Item (Join-Path $studioAppSource "assets\*") $targetAssets -Recurse -Force
Copy-Item (Join-Path $studioAppSource "src\host_contract.js") (Join-Path $packagedStudio "host_contract.js") -Force
[System.IO.File]::WriteAllText(
    (Join-Path $packagedStudio "studio_product.html"),
    $portableHtml,
    [System.Text.UTF8Encoding]::new($false)
)
[System.IO.File]::WriteAllText(
    (Join-Path $packagedStudio "app_intelligence_registry.json"),
    $appIntelligenceRegistryJson,
    [System.Text.UTF8Encoding]::new($false)
)

# Fail closed if the installed assets diverge from the exact pinned source.
$sourceAssets = @(Get-ChildItem -LiteralPath (Join-Path $studioAppSource "assets") -File -Recurse)
$packagedAssets = @(Get-ChildItem -LiteralPath $targetAssets -File -Recurse)
if ($sourceAssets.Count -ne $packagedAssets.Count) {
    throw "Packaged Studio asset inventory differs from canonical source"
}
foreach ($sourceAsset in $sourceAssets) {
    $relative = $sourceAsset.FullName.Substring((Join-Path $studioAppSource "assets").Length).TrimStart('\', '/')
    $packagedAsset = Join-Path $targetAssets $relative
    if (-not (Test-Path -LiteralPath $packagedAsset) -or
        (Get-FileHash -Algorithm SHA256 -LiteralPath $sourceAsset.FullName).Hash -ne
        (Get-FileHash -Algorithm SHA256 -LiteralPath $packagedAsset).Hash) {
        throw "Packaged Studio asset does not match pinned canonical source: $relative"
    }
}
Write-Output "ORDAX_STUDIO_PACKAGE_SOURCE_VERIFIED=$($sourceLock.commit)"

Copy-Item (Join-Path $repoRoot "scripts") (Join-Path $stageRoot "scripts") -Recurse -Force

$workbenchRoot = Join-Path $stageRoot "workbench"
$workbenchProject = Join-Path $repoRoot "native\ordax-workbench\Ordax.Workbench.csproj"
Write-Host "Publishing native ORDAX Workbench"
& dotnet publish $workbenchProject `
    --configuration Release `
    --runtime win-x64 `
    --self-contained true `
    --output $workbenchRoot `
    /p:PublishSingleFile=false `
    /p:DebugType=None `
    /p:DebugSymbols=false
if ($LASTEXITCODE -ne 0) {
    throw "Native ORDAX Workbench publish failed"
}
$workbenchExe = Join-Path $workbenchRoot "ORDAX Workbench.exe"
if (-not (Test-Path $workbenchExe)) {
    throw "Native ORDAX Workbench executable was not produced"
}

$privatePython = Join-Path $runtimeRoot "python.exe"
& $privatePython -c "import ordax_studio, ordax_dev_agent, ordax_device_agent, webview; print('ORDAX_PRIVATE_RUNTIME_OK')"
if ($LASTEXITCODE -ne 0) {
    throw "Private ORDAX Python runtime import smoke failed"
}
& $privatePython -c "from ordax_dev_agent.application_intelligence_actions import load_app_intelligence_registry; r = load_app_intelligence_registry(); assert r['available'] and r['by_id']['studio']['version'] == '$Version'; print('ORDAX_APP_INTELLIGENCE_REGISTRY_OK')"
if ($LASTEXITCODE -ne 0) {
    throw "Packaged App Intelligence registry validation failed"
}

$webViewBootstrapper = Join-Path $redistRoot "MicrosoftEdgeWebview2Setup.exe"
Write-Host "Downloading Microsoft Edge WebView2 Evergreen bootstrapper"
Invoke-WebRequest -Uri "https://go.microsoft.com/fwlink/p/?LinkId=2124703" -OutFile $webViewBootstrapper
if ((Get-Item $webViewBootstrapper).Length -lt 100000) {
    throw "WebView2 bootstrapper download is unexpectedly small"
}

$cl = Get-Command cl.exe -ErrorAction SilentlyContinue
if (-not $cl) {
    throw "cl.exe was not found. Run from a Visual Studio developer environment."
}
$launcherSource = Join-Path $repoRoot "packaging\windows\ordax_launcher.c"
$studioExe = Join-Path $stageRoot "ORDAX Studio.exe"
$legacyStudioExe = Join-Path $stageRoot "ORDAX Dev.exe"
$runtimeExe = Join-Path $stageRoot "ORDAX Runtime.exe"

Push-Location $buildRoot
try {
    & cl.exe /nologo /O2 /W4 /DUNICODE /D_UNICODE /DORDAX_RUNTIME_LAUNCHER=0 "/Fe:$studioExe" $launcherSource /link /SUBSYSTEM:WINDOWS user32.lib
    if ($LASTEXITCODE -ne 0) { throw "ORDAX Studio launcher compilation failed" }
    Remove-Item "ordax_launcher.obj" -Force -ErrorAction SilentlyContinue

    & cl.exe /nologo /O2 /W4 /DUNICODE /D_UNICODE /DORDAX_RUNTIME_LAUNCHER=1 "/Fe:$runtimeExe" $launcherSource /link /SUBSYSTEM:WINDOWS user32.lib
    if ($LASTEXITCODE -ne 0) { throw "ORDAX Runtime launcher compilation failed" }
    Remove-Item "ordax_launcher.obj" -Force -ErrorAction SilentlyContinue
} finally {
    Pop-Location
}

if (-not (Test-Path $studioExe) -or -not (Test-Path $runtimeExe)) {
    throw "Native ORDAX launchers were not produced"
}

# Compatibility only: old shortcuts/automation may still point at ORDAX Dev.exe.
# Keep one implementation by copying the exact Studio launcher bytes rather than
# compiling or maintaining a second launcher path.
Copy-Item -LiteralPath $studioExe -Destination $legacyStudioExe -Force
$studioHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $studioExe).Hash
$legacyHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $legacyStudioExe).Hash
if ($studioHash -ne $legacyHash) {
    throw "Legacy ORDAX Dev launcher alias is not byte-identical to ORDAX Studio.exe"
}

$manifest = [ordered]@{
    schema = "ordax.windows-product/1"
    product = "ORDAX Studio"
    version = $Version
    version_provenance = @{
        source = $versionSource
        canonical_version_asserted = [bool]$canonicalVersion
    }
    architecture = "x64"
    python = $PythonVersion
    entrypoints = @{
        studio = "ORDAX Studio.exe"
        studio_legacy_alias = "ORDAX Dev.exe"
        workbench = "workbench\\ORDAX Workbench.exe"
        runtime = "ORDAX Runtime.exe"
    }
    compatibility = @{
        legacy_studio_alias = $true
        legacy_studio_alias_byte_identical = $true
    }
    control_plane = "https://ordax-control-plane-v3.ordax-ac1ca1b50d09.workers.dev"
    built_at = [DateTimeOffset]::UtcNow.ToString("o")
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $stageRoot "product-manifest.json") -Encoding UTF8

$isccPath = $null
$isccCommand = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if ($isccCommand) {
    $isccPath = $isccCommand.Source
}
if (-not $isccPath) {
    $candidate = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    if (Test-Path $candidate) {
        $isccPath = $candidate
    }
}
if (-not $isccPath) {
    throw "Inno Setup 6 (ISCC.exe) was not found"
}

$iss = Join-Path $repoRoot "packaging\windows\ordax-studio.iss"
& $isccPath "/DStageDir=$stageRoot" "/DAppVersion=$Version" "/DOutputDir=$OutputDirectory" $iss
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup compilation failed"
}

$setup = Get-ChildItem $OutputDirectory -Filter "ORDAX-Studio-Setup-*.exe" | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
if (-not $setup) {
    throw "Installer output was not produced"
}
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $setup.FullName).Hash.ToLowerInvariant()
Write-Output "ORDAX_STUDIO_VERSION=$Version"
Write-Output "ORDAX_STUDIO_VERSION_SOURCE=$versionSource"
Write-Output "ORDAX_STUDIO_SETUP=$($setup.FullName)"
Write-Output "ORDAX_STUDIO_SETUP_SHA256=$hash"
