from __future__ import annotations

from .cloudflare_control_plane import CloudflareControlPlane
from .config import AgentConfig
from .product_presence_bridge import ProductPresenceBridge


class ProductAwareCloudflareControlPlane(CloudflareControlPlane):
    """Cloudflare command transport with canonical Product presence projection.

    The existing command heartbeat remains the single cadence owner. Product
    presence is an observation for the ChatGPT/Product surface and never gains
    authority to execute commands or mint grants.
    """

    def __init__(self, config: AgentConfig):
        super().__init__(config)
        self.product_presence = ProductPresenceBridge(
            self.base_http_url,
            self.device_id,
            lambda: self.device_token,
        )

    def heartbeat(
        self,
        actions: list[str],
        *,
        agent_version: str,
        metadata: dict | None = None,
        status: str = "online",
        last_error: str | None = None,
    ) -> None:
        if status == "offline":
            command_error: Exception | None = None
            try:
                super().heartbeat(
                    actions,
                    agent_version=agent_version,
                    metadata=metadata,
                    status=status,
                    last_error=last_error,
                )
            except Exception as exc:
                command_error = exc
            self.product_presence.report(
                actions,
                agent_version=agent_version,
                online=False,
            )
            if command_error is not None:
                raise command_error
            return

        # Never advertise Product presence before the execution channel itself
        # has accepted the heartbeat for this same device identity.
        super().heartbeat(
            actions,
            agent_version=agent_version,
            metadata=metadata,
            status=status,
            last_error=last_error,
        )
        self.product_presence.report(
            actions,
            agent_version=agent_version,
            online=True,
        )


def build_control_plane(config: AgentConfig) -> CloudflareControlPlane:
    """Build the only supported remote transport: Cloudflare v3."""

    protocol = str(config.control_plane_protocol or "").strip().lower()
    if protocol != "cloudflare-v3":
        raise ValueError(f"Unsupported control-plane protocol: {protocol}")
    return ProductAwareCloudflareControlPlane(config)
