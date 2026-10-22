"""Runtime Device/Product live workspace proof adapter, no separate grant system.

Trusted host composition only. The Owner native selection and canonical
Project resolution remain local; a signed Device transport proves a current
grant against the existing PostgreSQL verifier on EVERY call. This module
does not create MCP actions, issue grants, store device tokens, or permit path
payloads. The endpoint is default-off until the gated Platform rollout.
"""
from __future__ import annotations

from collections.abc import Callable

from .product_remote_client import ProductRemoteClient
from .project_workspace_port import WorkspaceActor
from .workspace_resource_selection import (
    LiveWorkspaceAuthority, OwnerCanonicalProjectScope, _uuid,
)
from .project_workspace_live_proof import project_workspace_live_proof


class DeviceWorkspaceLiveAuthority:
    """Turn a fresh trusted Device reply into the existing Runtime grant type.

    The supplied callbacks MUST come from the current trusted Device host,
    NOT the renderer/model/MCP. `scope_for_resource` should be
    WorkspaceResourceSelection.trusted_scope_for_resource. Its in-memory UUIDs
    are lookup constraints only, not authorization. PostgreSQL must approve
    the exact identity and unrevoked resource for every verification.
    """

    __slots__ = ("_remote", "_device_id", "_token", "_scope")

    def __init__(
        self,
        *,
        remote: ProductRemoteClient,
        device_id: str,
        device_token: Callable[[], str],
        scope_for_resource: Callable[[str], OwnerCanonicalProjectScope | None],
    ) -> None:
        if (not isinstance(remote, ProductRemoteClient) or not _uuid(device_id)
                or not callable(device_token)
                or not callable(scope_for_resource)):
            raise ValueError("workspace_live_device_host_required")
        self._remote = remote
        self._device_id = device_id
        self._token = device_token
        self._scope = scope_for_resource

    def __call__(
        self,
        actor: WorkspaceActor,
        device_id: str,
        resource_id: str,
    ) -> LiveWorkspaceAuthority | None:
        if (not isinstance(actor, WorkspaceActor)
                or device_id != self._device_id
                or not _uuid(actor.actor_id)
                or not _uuid(actor.client_id)
                or not isinstance(resource_id, str)):
            return None
        try:
            scope = self._scope(resource_id)
            if (not isinstance(scope, OwnerCanonicalProjectScope)
                    or scope.device_id != device_id
                    or scope.client_id != actor.client_id):
                return None
            device_token = self._token()
            response = self._remote.device_workspace_proof(
                device_id=device_id, device_token=device_token,
                owner_user_id=scope.owner_user_id,
                actor_id=actor.actor_id, client_id=scope.client_id,
                lab_project_id=actor.project_id,
                workspace_session_id=actor.workspace_session_id,
                resource_id=resource_id,
            )
            # Raw JSON is NEVER authority. The exact server-issued proof is
            # checked against local Owner + actor identities, grant scope,
            # current expiration and readonly actions before the existing port
            # revalidates the filesystem root.
            return project_workspace_live_proof(
                response, scope=scope, actor=actor,
                device_id=device_id, resource_id=resource_id,
            )
        except Exception:
            # Network outage, unenrolled device, no migration, disabled gate,
            # revoked consent/grant, malformed response: all deny. Do not leak
            # device credentials, paths, DB diagnostics, or exception strings.
            return None
