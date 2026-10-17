# Canonical packaging proof for capabilities that must exist in the shipped private Runtime.
# Keep this probe side-effect free: it verifies package identity/import wiring without
# pairing a device, reading credentials, or contacting the control plane.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PrivatePython,
    [Parameter(Mandatory = $true)][string]$RuntimeRoot,
    [Parameter(Mandatory = $true)]
    [ValidateSet("staged", "installed", "upgraded", "repaired")]
    [string]$Phase
)

$ErrorActionPreference = "Stop"

$privatePythonPath = (Resolve-Path -LiteralPath $PrivatePython -ErrorAction Stop).Path
$runtimeRootPath = (Resolve-Path -LiteralPath $RuntimeRoot -ErrorAction Stop).Path
$expectedModule = Join-Path $runtimeRootPath "Lib\site-packages\ordax_dev_agent\product_device_control_plane.py"

if (-not (Test-Path -LiteralPath $expectedModule -PathType Leaf)) {
    throw "Packaged Product device control-plane module is missing: $expectedModule"
}

$probe = @'
from importlib import metadata
from pathlib import Path
import sys

import ordax_dev_agent
import ordax_dev_agent.product_device_control_plane as transport
from ordax_dev_agent.control_plane import build_control_plane
from ordax_dev_agent.product_device_control_plane import ProductDeviceControlPlane

expected = Path(sys.argv[1]).resolve()
actual = Path(transport.__file__).resolve()
if actual != expected:
    raise SystemExit(f"Product device control-plane resolved outside packaged Runtime: {actual} != {expected}")
if ProductDeviceControlPlane is not transport.ProductDeviceControlPlane:
    raise SystemExit("Product device control-plane class identity is inconsistent")
if not callable(build_control_plane):
    raise SystemExit("Canonical control-plane factory is not callable")
if ProductDeviceControlPlane.runtime_kind != "desktop-agent":
    raise SystemExit("Product device control-plane runtime kind is not canonical")
if not callable(getattr(ProductDeviceControlPlane, "_runtime_provenance", None)):
    raise SystemExit("Product device control-plane runtime provenance sender is missing")
if transport.DEVICE_AGENT_VERSION != ordax_dev_agent.__version__:
    raise SystemExit("Product device control-plane agent version authority is inconsistent")
if not isinstance(ordax_dev_agent.__version__, str) or not ordax_dev_agent.__version__.strip():
    raise SystemExit("ORDAX device-agent version authority is empty")
runtime_package_version = metadata.version("ordax-runtime").strip()
if not runtime_package_version:
    raise SystemExit("ORDAX Runtime distribution version authority is empty")
print(f"ORDAX_DEVICE_AGENT_VERSION={ordax_dev_agent.__version__.strip()}")
print(f"ORDAX_RUNTIME_PACKAGE_VERSION={runtime_package_version}")
'@

$probeOutput = @($probe | & $privatePythonPath -I - $expectedModule)
if ($LASTEXITCODE -ne 0) {
    throw "Packaged Product device control-plane import proof failed for phase '$Phase'"
}
$versionLine = @($probeOutput | Where-Object { $_ -is [string] -and $_.StartsWith("ORDAX_RUNTIME_PACKAGE_VERSION=") })
if ($versionLine.Count -ne 1) {
    throw "Packaged ORDAX Runtime version proof is missing or ambiguous for phase '$Phase'"
}
$runtimePackageVersion = $versionLine[0].Substring("ORDAX_RUNTIME_PACKAGE_VERSION=".Length).Trim()
if ($runtimePackageVersion -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$') {
    throw "Packaged ORDAX Runtime version is invalid for phase '$Phase': $runtimePackageVersion"
}

# The staged builder verifies its just-linked launchers before product-manifest.json exists.
# Installed lifecycle phases must additionally prove that the immutable distribution authority,
# installed manifest, and native launcher VERSIONINFO all describe the same Runtime.
if ($Phase -ne "staged") {
    $productRoot = Split-Path -Parent $runtimeRootPath
    $manifestPath = Join-Path $productRoot "product-manifest.json"
    $runtimeExe = Join-Path $productRoot "ORDAX Runtime.exe"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "Installed ORDAX product manifest is missing for phase '$Phase'"
    }
    if (-not (Test-Path -LiteralPath $runtimeExe -PathType Leaf)) {
        throw "Installed ORDAX Runtime executable is missing for phase '$Phase'"
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($manifest.schema -ne "ordax.windows-product/1" -or $manifest.product -ne "ORDAX Studio") {
        throw "Installed ORDAX product manifest is incompatible for phase '$Phase'"
    }
    if ([string]$manifest.runtime_version -ne $runtimePackageVersion) {
        throw "Installed manifest Runtime version does not match packaged Runtime for phase '$Phase'"
    }
    $versionInfo = (Get-Item -LiteralPath $runtimeExe).VersionInfo
    if ($versionInfo.FileVersion -ne $runtimePackageVersion -or $versionInfo.ProductVersion -ne $runtimePackageVersion) {
        throw "Installed ORDAX Runtime VERSIONINFO does not match packaged Runtime for phase '$Phase'"
    }
}

Write-Output ("ORDAX_PRODUCT_DEVICE_CONTROL_PLANE_{0}=PASS" -f $Phase.ToUpperInvariant())
Write-Output ("ORDAX_RUNTIME_VERSION_PROVENANCE_{0}=PASS" -f $Phase.ToUpperInvariant())
