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
# Bounded by the OS v1 Device Agent reader, not by the execution catalog.
# More locally available actions remain executable under their existing grants.
MAX_DEVICE_CAPABILITIES = 64


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
    overflow = len(capabilities) > MAX_DEVICE_CAPABILITIES
    if overflow:
        # The OS v1 reader accepts at most 64 descriptors. Keep Computer
        # Control and implemented file-transfer operations first. This only
        # selects read-only metadata; execution/grants remain independent.
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
