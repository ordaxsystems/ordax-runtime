from __future__ import annotations

from .cloudflare_control_plane import CloudflareControlPlane
from .config import AgentConfig
from .product_central_session import ProductCentralSessionClient
from .product_presence_bridge import ProductPresenceBridge


class ProductAwareCloudflareControlPlane(CloudflareControlPlane):
    """Cloudflare command transport with canonical Product projections.

    The existing command heartbeat remains the single cadence owner. Product
    presence and Central-session attestation are observations/bindings only;
    neither can mint grants or execute a model-selected command.
    """

    def __init__(self, config: AgentConfig):
        super().__init__(config)
        self.product_presence = ProductPresenceBridge(
            self.base_http_url,
            self.device_id,
            lambda: self.device_token,
        )
        self.product_central_session = ProductCentralSessionClient(
            str(config.product_mcp_url),
            self.device_id,
            lambda: self.device_token,
        ) if config.product_mcp_url else None

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
        presence = self.product_presence.report(
            actions,
            agent_version=agent_version,
            online=True,
        )

        # Session attestation is driven by the SAME heartbeat and runs only
        # after Product identity has been verified. It is deliberately
        # non-authoritative for command-channel health: even an unexpected
        # attestation bug must not make a healthy Runtime look offline.
        if (
            presence.verified
            and presence.online is True
            and self.product_central_session is not None
        ):
            try:
                self.product_central_session.tick()
            except Exception:
                pass


def build_control_plane(config: AgentConfig) -> CloudflareControlPlane:
    """Build the only supported remote transport: Cloudflare v3."""

    protocol = str(config.control_plane_protocol or "").strip().lower()
    if protocol != "cloudflare-v3":
        raise ValueError(f"Unsupported control-plane protocol: {protocol}")
    return ProductAwareCloudflareControlPlane(config)
