"""Reusable OrdaX device connector primitives.

This package is UI-agnostic: Windows hosts, OrdaX OS, Studio and other apps
consume the same connector contracts instead of owning MCP transport.
"""
from .capabilities import canonical_capabilities, capability_digest
from .owner_grants import (
    OwnerMcpClientSelectionError,
    grant_consented_owner_device_profile,
    select_consented_owner_mcp_client,
)

__all__ = [
    "canonical_capabilities",
    "capability_digest",
    "OwnerMcpClientSelectionError",
    "grant_consented_owner_device_profile",
    "select_consented_owner_mcp_client",
]
