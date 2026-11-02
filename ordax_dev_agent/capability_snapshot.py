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
    # The v1 OS reader accepts at most 64 entries. Preserve the complete
    # computer-control subset first when the Product catalog is larger,
    # and mark the snapshot degraded rather than failing HTTP or claiming
    # complete discovery. This grants no additional remote capabilities.
    overflow = len(capabilities) > MAX_DEVICE_CAPABILITIES
    if overflow:
        capabilities = sorted(
            capabilities,
            key=lambda item: (not item["id"].startswith("computer."), item["id"]),
        )[:MAX_DEVICE_CAPABILITIES]
        capabilities.sort(key=lambda item: item["id"])

    return {
        "schema": DEVICE_AGENT_CAPABILITIES_SCHEMA,
        "state": "degraded" if overflow or not capabilities else "ready",
        "capabilities": capabilities,
    }
