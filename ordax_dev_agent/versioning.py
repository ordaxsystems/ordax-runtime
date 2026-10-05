"""Independent component version inventory for the OrdaX toolchain."""
from __future__ import annotations

from mcp_blender_unity import __version__ as bridge_package_version

from . import __version__ as dev_agent_version
from .blender_live_bridge import BUNDLE_FORMAT_VERSION, EXPECTED_PROTOCOL_VERSION
from .references import MANIFEST_VERSION as REFERENCE_CONTRACT_VERSION


def component_versions() -> dict[str, object]:
    return {
        "bridge_package": bridge_package_version,
        "device_agent": dev_agent_version,
        "dev_agent": dev_agent_version,
        "blender_live_protocol": EXPECTED_PROTOCOL_VERSION,
        "blender_companion_bundle_format": BUNDLE_FORMAT_VERSION,
        "reference_contract": REFERENCE_CONTRACT_VERSION,
    }
