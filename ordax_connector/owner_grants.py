"""Canonical, UI-independent selection of a consented Product MCP client.

Owner operations stay in the Product Control Plane. This module determines
*which* previously consented client may receive a grant; it never issues,
fabricates or persists grants. It fails closed on ambiguous identity.
"""
from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any


class OwnerMcpClientSelectionError(ValueError):
    """A bounded, machine-readable authorization selection failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _uuid(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        normalized = str(uuid.UUID(value))
    except (ValueError, AttributeError):
        return None
    return normalized if value.lower() == normalized else None


def select_consented_owner_mcp_client(
    authority: Mapping[str, Any],
    *,
    device_id: str,
    requested_client_id: str | None = None,
) -> str:
    """Resolve one client from canonical owner_remote_authority response.

    One consented OAuth client can be selected without an explicit argument.
    With multiple clients, require the exact Owner-selected client_id: existing
    device grants are NOT proof of which ChatGPT connection the Owner intended.
    Never infer a new grant recipient from previous profile assignments.
    """
    normalized_device = _uuid(device_id)
    if not normalized_device or _uuid(authority.get("device_id")) != normalized_device:
        raise OwnerMcpClientSelectionError("owner_mcp_device_mismatch")

    clients = authority.get("clients")
    groups = authority.get("grant_groups")
    if not isinstance(clients, list) or not isinstance(groups, list):
        raise OwnerMcpClientSelectionError("owner_mcp_authority_invalid")

    consented: set[str] = set()
    for client in clients:
        if not isinstance(client, Mapping):
            raise OwnerMcpClientSelectionError("owner_mcp_authority_invalid")
        client_id = _uuid(client.get("client_id"))
        if client_id is None:
            raise OwnerMcpClientSelectionError("owner_mcp_authority_invalid")
        consented.add(client_id)

    if not consented:
        raise OwnerMcpClientSelectionError("owner_mcp_no_consented_client")

    if requested_client_id is not None:
        requested = _uuid(requested_client_id)
        if requested is None:
            raise OwnerMcpClientSelectionError("owner_mcp_client_id_invalid")
        if requested not in consented:
            raise OwnerMcpClientSelectionError("owner_mcp_client_not_consented")
        return requested

    if len(consented) == 1:
        return next(iter(consented))

    # Ambiguity must be resolved by the authenticated Owner, not by a
    # previous grant on another OAuth connection (or on another device).
    raise OwnerMcpClientSelectionError("owner_mcp_client_ambiguous")


def grant_consented_owner_device_profile(
    remote: Any,
    access_token: str,
    *,
    device_id: str,
    profile_key: str,
    requested_client_id: str | None = None,
    valid_until: str | None = None,
) -> dict[str, Any]:
    """Use canonical Product remote endpoints with a verified owner token.

    Only the bounded observer and filesystem profiles are exposed by this
    reusable Connector entrypoint. The caller must explicitly authenticate
    the device owner before invoking it.
    """
    if profile_key not in {"computer-observe", "computer-filesystem"}:
        raise OwnerMcpClientSelectionError("owner_mcp_profile_not_allowed")
    authority = remote.owner_remote_authority(access_token, device_id=device_id)
    client_id = select_consented_owner_mcp_client(
        authority, device_id=device_id, requested_client_id=requested_client_id,
    )
    return remote.replace_owner_remote_grant_group(
        access_token,
        client_id=client_id,
        device_id=device_id,
        profile_key=profile_key,
        space_id=None,
        project_id=None,
        valid_until=valid_until,
    )
