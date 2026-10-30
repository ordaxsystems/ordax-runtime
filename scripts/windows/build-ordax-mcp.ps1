# Independent OrdaX MCP Windows candidate. Does not consume ordax-apps/Studio.
[CmdletBinding()]
param(
  [string]$OutputDirectory = "",
  [string]$PlatformSource = "",
  [string]$PythonVersion = "3.12.10",
  [ValidateSet("validation-candidate","production")]
  [string]$ControlPlaneEnvironment = "validation-candidate"
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if ($ControlPlaneEnvironment -ne "validation-candidate") {
  throw "Standalone OrdaX MCP production requires a separate signed release gate"
}
if (-not $PlatformSource) { throw "Pinned ORDAX_PLATFORM_SOURCE checkout is required" }
$platform = (Resolve-Path -LiteralPath $PlatformSource -ErrorAction Stop).Path
$sourceCommit = (& git -C $root rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $sourceCommit -cnotmatch '^[0-9a-f]{40}$') {
  throw "Canonical Runtime commit required"
}
if ($env:GITHUB_ACTIONS -eq "true" -and $env:GITHUB_SHA -and $env:GITHUB_SHA -cne $sourceCommit) {
  throw "GitHub build must use exact Runtime commit"
}
$dirty = @(& git -C $root status --porcelain --untracked-files=no)
if ($LASTEXITCODE -ne 0 -or ($env:GITHUB_ACTIONS -eq "true" -and $dirty.Count -ne 0)) {
  throw "Runtime source must be clean for candidate packaging"
}
$lock = Get-Content (Join-Path $root "control-plane-source.lock.json") -Raw | ConvertFrom-Json
if ($lock.schema -ne "ordax.control-plane-source-lock/1" -or
    $lock.authority -ne "none" -or
    $lock.source.repository -ne "ordaxsystems/ordax-platform" -or
    [string]$lock.source.commit -cnotmatch '^[0-9a-f]{40}$') {
  throw "Canonical Platform source lock invalid"
}
$actualPlatform = (& git -C $platform rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $actualPlatform -cne [string]$lock.source.commit) {
  throw "Platform checkout differs from the immutable Runtime pin"
}
$endpointJson = & python (Join-Path $root "scripts\windows\resolve-control-plane-provenance.py") --lock (Join-Path $root "control-plane-source.lock.json") --platform-root $platform --environment validation-candidate
if ($LASTEXITCODE -ne 0) { throw "Canonical Product MCP provenance validation failed" }
$endpoint = ($endpointJson -join "`n") | ConvertFrom-Json
if ($endpoint.schema -ne "ordax.control-plane-endpoint-provenance/1") { throw "Unexpected provenance schema" }
$packageVersion = (& python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])").Trim()
if ($LASTEXITCODE -ne 0 -or $packageVersion -notmatch '^\d+\.\d+\.\d+$') {
  throw "Invalid canonical Runtime distribution version"
}
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $root "dist\mcp-windows" }
$build = Join-Path $root "build\mcp-windows"
$stage = Join-Path $build "stage"
$runtime = Join-Path $stage "runtime"
$cache = Join-Path $build "cache"
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $runtime,$cache,$OutputDirectory | Out-Null
$zip = Join-Path $cache "python-$PythonVersion-embed-amd64.zip"
if (-not (Test-Path -LiteralPath $zip)) {
  Invoke-WebRequest -Uri "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" -OutFile $zip
}
Expand-Archive -LiteralPath $zip -DestinationPath $runtime -Force
$stdlib = Get-ChildItem $runtime -Filter "python*.zip" | Select-Object -First 1
$pth = Get-ChildItem $runtime -Filter "python*._pth" | Select-Object -First 1
if (-not $stdlib -or -not $pth) { throw "Private CPython embedded runtime incomplete" }
@($stdlib.Name, ".", "Lib", "Lib\site-packages", "import site") | Set-Content $pth.FullName -Encoding ascii
$site = Join-Path $runtime "Lib\site-packages"
New-Item $site -ItemType Directory -Force | Out-Null
& python -m pip install --disable-pip-version-check --no-compile --upgrade --target $site $root
if ($LASTEXITCODE -ne 0) { throw "Runtime dependency installation failed" }
$privatePython = Join-Path $runtime "python.exe"
& (Join-Path $root "scripts\windows\assert-runtime-package-capabilities.ps1") -PrivatePython $privatePython -RuntimeRoot $runtime -Phase staged
if ($LASTEXITCODE -ne 0) { throw "Private Runtime package capability gate failed" }
& $privatePython -m ordax_dev_agent.owner_grants_cli --help | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Owner OAuth CLI missing" }
& $privatePython -c "import importlib.metadata as m; assert m.version('ordax-runtime') == '$packageVersion'"
if ($LASTEXITCODE -ne 0) { throw "Private Runtime version mismatch" }
# Reuse the ONE official native Runtime launcher source and OS mutex.
$resource = Join-Path $build "ordax-mcp-version.rc"
$compiled = Join-Path $build "ordax-mcp-version.res"
$quad = "$($packageVersion.Replace('.',',')),0"
$rc = @"
#include <windows.h>
VS_VERSION_INFO VERSIONINFO
 FILEVERSION $quad
 PRODUCTVERSION $quad
 FILEFLAGSMASK 0x3fL
 FILEFLAGS 0x0L
 FILEOS VOS_NT_WINDOWS32
 FILETYPE VFT_APP
 FILESUBTYPE 0x0L
BEGIN
 BLOCK "StringFileInfo"
 BEGIN
  BLOCK "040904B0"
  BEGIN
   VALUE "CompanyName", "ORDAX\0"
   VALUE "FileDescription", "OrdaX MCP device Runtime\0"
   VALUE "FileVersion", "$packageVersion\0"
   VALUE "OriginalFilename", "ORDAX Runtime.exe\0"
   VALUE "ProductName", "OrdaX MCP\0"
   VALUE "ProductVersion", "$packageVersion\0"
  END
 END
 BLOCK "VarFileInfo"
 BEGIN
  VALUE "Translation", 0x0409, 1200
 END
END
"@
[IO.File]::WriteAllText($resource,$rc,[Text.Encoding]::ASCII)
$launcher = Join-Path $stage "ORDAX Runtime.exe"
Push-Location $build
try {
  & rc.exe /nologo "/fo$compiled" $resource
  if ($LASTEXITCODE -ne 0) { throw "Runtime VERSIONINFO compilation failed" }
  & cl.exe /nologo /O2 /W4 /DUNICODE /D_UNICODE /DORDAX_RUNTIME_LAUNCHER=1 "/Fe:$launcher" (Join-Path $root "packaging\windows\ordax_launcher.c") $compiled /link /SUBSYSTEM:WINDOWS user32.lib shell32.lib
  if ($LASTEXITCODE -ne 0) { throw "Canonical Runtime launcher compilation failed" }
} finally { Pop-Location }
if (-not (Test-Path $launcher)) { throw "Runtime launcher missing" }
Copy-Item (Join-Path $root "scripts\windows\ordax-upgrade-quiesce.ps1") (Join-Path $stage "ordax-upgrade-quiesce.ps1")
$manifest = [ordered]@{
  schema = "ordax.mcp-windows-candidate/1"
  product = "OrdaX MCP"
  version = $packageVersion
  runtime_source_commit = $sourceCommit
  runtime = "ORDAX Runtime.exe"
  account_authority = "ordax-platform"
  control_plane_provenance = $endpoint
  studio_required = $false
  signed_public_release = $false
}
$manifest | ConvertTo-Json -Depth 9 | Set-Content (Join-Path $stage "product-manifest.json") -Encoding UTF8
if (Test-Path (Join-Path $stage "presentation")) { throw "Studio presentation leaked into independent MCP distribution" }
$inno = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
if (-not $inno) {
  $inno = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
}
if (-not (Test-Path $inno)) { throw "Inno Setup 6 required" }
& $inno "/DStageDir=$stage" "/DAppVersion=$packageVersion" "/DOutputDir=$OutputDirectory" (Join-Path $root "packaging\windows\ordax-mcp.iss")
if ($LASTEXITCODE -ne 0) { throw "OrdaX MCP candidate Inno packaging failed" }
$installer = Join-Path $OutputDirectory "OrdaX-MCP-Setup-$packageVersion-x64.exe"
if (-not (Test-Path $installer)) { throw "Canonical standalone MCP installer missing" }
$hash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Output "ORDAX_MCP_CANDIDATE=$installer"
Write-Output "ORDAX_MCP_CANDIDATE_SHA256=$hash"
Write-Output "ORDAX_MCP_STUDIO_DEPENDENCY=NONE"
