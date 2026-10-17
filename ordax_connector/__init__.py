"""Reusable OrdaX device connector primitives.

This package is UI-agnostic: Windows hosts, OrdaX OS, Studio and other apps
consume the same connector contracts instead of owning MCP transport.
"""
from .capabilities import canonical_capabilities, capability_digest

__all__ = ["canonical_capabilities", "capability_digest"]
