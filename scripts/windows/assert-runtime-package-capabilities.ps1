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
from ordax_dev_agent.owner_dev_update_preflight import stage_owner_dev_candidates, verify_owner_dev_preflight

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
from tempfile import TemporaryDirectory

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.product_gateway import PRODUCT_ACTIONS
from ordax_dev_agent.product_mcp import PRODUCT_MCP_TOOLS
from ordax_dev_agent.main import product_idle_claim_delay_seconds

# Offline proof executed by the *shipped private Python*, not the developer
# checkout. Older installed Runtime builds polled an empty Product queue every
# ~1.8 seconds, consuming the shared Hyperdrive SQL quota and eventually
# disabling discovery and heartbeat for every account device. Reject those
# installations before publication, silent upgrade or repair.
if product_idle_claim_delay_seconds(1, 0.25) != 0.25:
    raise SystemExit("Packaged Runtime changed interactive Product claim burst unexpectedly")
for configured_ceiling in (0.25, 1.0, 10.0, 30.0):
    if product_idle_claim_delay_seconds(40, configured_ceiling) < 30.0:
        raise SystemExit("Packaged Runtime can exhaust shared Hyperdrive idle budget")
if product_idle_claim_delay_seconds(40, 300.0) > 60.0:
    raise SystemExit("Packaged Runtime unbounded idle Product claim delay")
print("ORDAX_PACKAGED_PRODUCT_IDLE_BUDGET=PASS")

# The actual shipped registry, transport-independent MCP catalog and Product
# gateway must all agree about device actions. A cloud-only catalog does not
# prove this installed executable can execute a capability.
required_device_actions = (
    "computer.terminal_exec",
    "computer.process_start",
    "computer.process_status",
    "computer.process_list",
    "computer.process_logs",
    "computer.process_write_stdin",
    "computer.process_stop",
)
with TemporaryDirectory(prefix="ordax-package-capability-") as scratch:
    root = Path(scratch)
    config = AgentConfig(
        agent_name="packaged-runtime-probe",
        poll_seconds=0.25,
        state_dir=root / "state",
        agent_repo_path=root / "agent",
        hordax_path=root / "hordax",
        bridge_path=root / "bridge",
        workspace_root=root / "workspace",
        projects={},
        default_project="none",
    )
    installed_registry = ActionRegistry(config)
    installed_names = set(installed_registry.names)
    exported_tools = {item.action: item for item in PRODUCT_MCP_TOOLS}
    for action in required_device_actions:
        spec = PRODUCT_ACTIONS.get(action)
        tool = exported_tools.get(action)
        if action not in installed_names or spec is None or tool is None:
            raise SystemExit(f"Packaged Runtime/MCP device capability is incomplete: {action}")
        if spec.project_required or spec.local_action != action or tool.name != action.replace(".", "_"):
            raise SystemExit(f"Packaged Runtime project-free capability contract mismatches: {action}")
        if spec.effect != tool.effect:
            raise SystemExit(f"Packaged Runtime/MCP effect contract mismatches: {action}")
print("ORDAX_PACKAGED_DEVICE_CAPABILITIES=PASS")

if not callable(stage_owner_dev_candidates) or not callable(verify_owner_dev_preflight):
    raise SystemExit("Packaged Owner/Dev provenance preflight is missing")
print("ORDAX_OWNER_DEV_PREFLIGHT_PACKAGED=PASS")
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
    # One executor probe, two deliberately distinct distribution identities.
    # The standalone MCP candidate may not claim to be a signed public release
    # or silently adopt the legacy Studio installer/product identity.
    $isStudio = ($manifest.schema -eq "ordax.windows-product/1" -and
                 $manifest.product -eq "ORDAX Studio")
    $isMcpCandidate = ($manifest.schema -eq "ordax.mcp-windows-candidate/1" -and
                       $manifest.product -eq "OrdaX MCP" -and
                       $manifest.studio_required -eq $false -and
                       $manifest.signed_public_release -eq $false -and
                       [string]$manifest.runtime_source_commit -cmatch '^[0-9a-f]{40}$' -and
                       $manifest.runtime -eq "ORDAX Runtime.exe" -and
                       $manifest.control_plane_provenance.schema -eq "ordax.control-plane-endpoint-provenance/1")
    if (-not ($isStudio -or $isMcpCandidate)) {
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
 -and
                       $manifest.runtime -eq "ORDAX Runtime.exe" -and
                       $manifest.control_plane_provenance.schema -eq "ordax.control-plane-endpoint-provenance/1")
    if (-not ($isStudio -or $isMcpCandidate)) {
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
