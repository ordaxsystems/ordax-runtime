from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .product_gateway import PRODUCT_ACTIONS


DEVICE_AGENT_CAPABILITIES_SCHEMA = "ordax.device-agent-capabilities/1"
MAX_DEVICE_CAPABILITIES = 64


def device_capability_snapshot(action_names: Iterable[str]) -> dict[str, Any]:
    """Build the client-neutral, read-only Device Agent capability snapshot.

    Only actions already approved by the Product gateway are surfaced. Internal
    actions, arbitrary shell capabilities and implementation-specific helpers do
    not cross this boundary. The snapshot deliberately carries capability
    metadata only; it grants no execution authority.
    """

    available = {
        action
        for action in action_names
        if isinstance(action, str) and action
    }
    capabilities: list[dict[str, Any]] = []

    for capability_id, spec in PRODUCT_ACTIONS.items():
        if spec.local_action not in available:
            continue
        capabilities.append(
            {
                "id": capability_id,
                "modes": ["write" if spec.effect == "write" else "read"],
            }
        )

    capabilities.sort(key=lambda item: item["id"])
    if len(capabilities) > MAX_DEVICE_CAPABILITIES:
        raise RuntimeError("Device Agent capability snapshot exceeds contract bound")

    return {
        "schema": DEVICE_AGENT_CAPABILITIES_SCHEMA,
        "state": "ready" if capabilities else "degraded",
        "capabilities": capabilities,
    }
