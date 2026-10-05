from __future__ import annotations

from .cloudflare_control_plane import CloudflareControlPlane
from .config import AgentConfig


def build_control_plane(config: AgentConfig) -> CloudflareControlPlane:
    """Build the only supported remote transport: Cloudflare v3."""

    protocol = str(config.control_plane_protocol or "").strip().lower()
    if protocol != "cloudflare-v3":
        raise ValueError(f"Unsupported control-plane protocol: {protocol}")
    return CloudflareControlPlane(config)
