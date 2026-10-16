from __future__ import annotations

from .config import AgentConfig
from .product_device_control_plane import ProductDeviceControlPlane


def build_control_plane(config: AgentConfig) -> ProductDeviceControlPlane:
    """Build the single canonical Product device transport.

    The legacy Cloudflare WebSocket/D1 control plane is intentionally not a
    fallback. Product MCP + PostgreSQL is the sole remote device authority.
    """
    protocol = str(config.control_plane_protocol or "").strip().lower()
    if protocol != "cloudflare-v3":
        raise ValueError(f"Unsupported control-plane protocol: {protocol}")
    return ProductDeviceControlPlane(config)
