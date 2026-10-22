"""Decode only the canonical Product PostgreSQL workspace proof projection.

PARSER, NOT AUTHORITY. Only a trusted device-authenticated Product transport
may supply the raw response. Untrusted App/MCP/model JSON is never a grant;
the caller must use WorkspaceResourceSelection.resolve_live, which checks a
fresh proof and the local root on every verify/snapshot and after the scan.

Canonical response: Platform project_workspace_resource_authority_v1.sql
(schema=ordax.project-workspace-resource/1). Never deserialize a path, local
token, arbitrary capability or an owner/client from the request.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from .project_workspace_port import WorkspaceActor
from .workspace_resource_selection import (
    LiveWorkspaceAuthority, OwnerCanonicalProjectScope, _uuid,
)

_RESOURCE = re.compile(r"resource_[A-Za-z0-9_-]{8,160}\Z")
_LOCAL_ID = re.compile(r"[A-Za-z0-9_-]{1,180}\Z")
_OUTER = frozenset({"ok", "schema", "grant"})
_GRANT = frozenset({
    "grant_id", "owner_user_id", "space_id", "project_id",
    "lab_project_id", "actor_id", "client_id", "workspace_session_id",
    "device_id", "resource_id", "allowed_actions", "issued_by",
    "issued_at", "valid_until", "revoked_at", "active",
})
_MAX_LIFETIME = timedelta(hours=24)


def _instant(value: object) -> datetime | None:
    if (not isinstance(value, str)
            or not 20 <= len(value) <= 40
            or not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})",
                value,
            )):
        return None
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return instant.astimezone(timezone.utc) if instant.utcoffset() is not None else None


def project_workspace_live_proof(
    response: object,
    *,
    scope: OwnerCanonicalProjectScope,
    actor: WorkspaceActor,
    device_id: str,
    resource_id: str,
    now: datetime | None = None,
) -> LiveWorkspaceAuthority | None:
    """Strict projection of a fresh attested Product verifier reply.

    Calling this function with forged JSON does NOT establish authorization.
    The origin of `response` is an independent transport trust boundary.
    """
    if (not isinstance(response, dict) or set(response) != _OUTER
            or response.get("ok") is not True
            or response.get("schema") != "ordax.project-workspace-resource/1"
            or not isinstance(scope, OwnerCanonicalProjectScope)
            or not isinstance(actor, WorkspaceActor)
            or not all(_uuid(value) for value in (
                scope.owner_user_id, scope.space_id, scope.project_id,
                scope.client_id, scope.device_id, device_id,
            ))
            or not isinstance(resource_id, str)
            or not _RESOURCE.fullmatch(resource_id)
            or not isinstance(actor.project_id, str)
            or not _LOCAL_ID.fullmatch(actor.project_id)
            or not isinstance(actor.workspace_session_id, str)
            or not _LOCAL_ID.fullmatch(actor.workspace_session_id)
            or not _uuid(actor.client_id)
            or not _uuid(actor.actor_id)):
        return None
    received = response["grant"]
    if not isinstance(received, dict) or set(received) != _GRANT:
        return None
    if (received.get("active") is not True
            or received.get("revoked_at") is not None
            or received.get("issued_by") != "trusted-runtime-picker"
            or received.get("allowed_actions") != ["read"]
            or not _uuid(received.get("grant_id"))):
        return None
    expected = {
        "owner_user_id": scope.owner_user_id,
        "space_id": scope.space_id,
        "project_id": scope.project_id,
        "client_id": scope.client_id,
        "lab_project_id": actor.project_id,
        "actor_id": actor.actor_id,
        "workspace_session_id": actor.workspace_session_id,
        "device_id": device_id,
        "resource_id": resource_id,
    }
    if scope.device_id != device_id:
        return None
    if any(received.get(key) != value for key, value in expected.items()):
        return None
    issued = _instant(received.get("issued_at"))
    expires = _instant(received.get("valid_until"))
    current = datetime.now(timezone.utc) if now is None else now
    if (not isinstance(current, datetime) or current.utcoffset() is None
            or issued is None or expires is None):
        return None
    current = current.astimezone(timezone.utc)
    if not issued <= current < expires <= issued + _MAX_LIFETIME:
        return None
    return LiveWorkspaceAuthority(
        owner_user_id=scope.owner_user_id,
        space_id=scope.space_id,
        canonical_project_id=scope.project_id,
        lab_project_id=actor.project_id,
        actor_id=actor.actor_id,
        client_id=scope.client_id,
        workspace_session_id=actor.workspace_session_id,
        device_id=device_id,
        resource_id=resource_id,
        grant_id=received["grant_id"],
        issued_at=issued,
        expires_at=expires,
        allowed_actions=frozenset({"read"}),
    )
