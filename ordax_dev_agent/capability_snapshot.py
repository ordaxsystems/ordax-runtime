from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .product_gateway import PRODUCT_ACTIONS
from .computer_transfer_actions import ComputerTransferActions


# Derive native MCP transfer names from the implemented handlers, never from a
# second manually maintained grant/tool catalog. Presence in the live registry
# is required before any local capability is advertised.
NATIVE_FILE_TRANSFER_ACTION_IDS = frozenset(
    "computer." + method.removeprefix("computer_")
    for method in vars(ComputerTransferActions)
    if method.startswith("computer_transfer_")
    and callable(getattr(ComputerTransferActions, method))
)


DEVICE_AGENT_CAPABILITIES_SCHEMA = "ordax.device-agent-capabilities/1"
# Bounded for local clients; all approved Product actions and native file
# transfers must fit. Growth beyond this contract needs explicit review.
MAX_DEVICE_CAPABILITIES = 128


def device_capability_snapshot(action_names: Iterable[str]) -> dict[str, Any]:
    """Build the client-neutral, read-only Device Agent capability snapshot.

    Only Product-approved actions and the implemented native transfer handlers
    registered in this Runtime are surfaced. This metadata grants no execution
    authority; Owner consent and local policy remain independent.
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
                "modes": ["read" if spec.effect == "read" else "write"],
            }
        )

    for action in sorted(available & NATIVE_FILE_TRANSFER_ACTION_IDS):
        if action not in PRODUCT_ACTIONS:
            capabilities.append({
                "id": action,
                "modes": ["read" if action.endswith("_status") else "write"],
            })

    capabilities.sort(key=lambda item: item["id"])
    if len(capabilities) > MAX_DEVICE_CAPABILITIES:
        raise RuntimeError("Device Agent capability snapshot exceeds contract bound")

    return {
        "schema": DEVICE_AGENT_CAPABILITIES_SCHEMA,
        "state": "ready" if capabilities else "degraded",
        "capabilities": capabilities,
    }
