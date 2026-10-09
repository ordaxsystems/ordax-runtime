"""Independent component version inventory for the OrdaX toolchain."""
from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from mcp_blender_unity import __version__ as bridge_package_version

from . import __version__ as dev_agent_version
from .blender_live_bridge import BUNDLE_FORMAT_VERSION, EXPECTED_PROTOCOL_VERSION
from .references import MANIFEST_VERSION as REFERENCE_CONTRACT_VERSION


def runtime_package_version() -> str | None:
    # A source checkout uses its distribution manifest; an installed Runtime uses
    # that same manifest's built distribution metadata. Never alias the independent
    # Blender bridge or device-agent component version as the Runtime package.
    source_manifest = Path(__file__).resolve().parents[1] / "pyproject.toml"
    if source_manifest.is_file():
        with source_manifest.open("rb") as handle:
            project = tomllib.load(handle).get("project", {})
        if project.get("name") == "ordax-runtime":
            return project.get("version")
    try:
        return version("ordax-runtime")
    except PackageNotFoundError:
        return None


def component_versions() -> dict[str, object]:
    return {
        "runtime_package": runtime_package_version(),
        "bridge_package": bridge_package_version,
        "device_agent": dev_agent_version,
        "dev_agent": dev_agent_version,
        "blender_live_protocol": EXPECTED_PROTOCOL_VERSION,
        "blender_companion_bundle_format": BUNDLE_FORMAT_VERSION,
        "reference_contract": REFERENCE_CONTRACT_VERSION,
    }
