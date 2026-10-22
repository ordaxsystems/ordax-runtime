"""Read-only Owner/Project scope adapter for the existing workspace selector.

This composes the existing ProductRemoteClient endpoints; it does not issue
grants, retain tokens, create an HTTP/MCP action, register a filesystem path,
or authenticate the caller by itself. Construct only in the already trusted
interactive Owner host using its private account-session token provider and
verified device/client identities. Never hand the object to an untrusted UI.
"""
from __future__ import annotations

import time
import uuid
from collections.abc import Callable

from .product_remote_client import ProductRemoteClient
from .project_workspace_live_proof import project_workspace_live_proof
from .project_workspace_port import ProjectWorkspaceDenied, WorkspaceActor
from .projects import canonical_github_repository_full_name
from .workspace_resource_selection import (
    LiveWorkspaceAuthority, OwnerCanonicalProjectScope,
)


def _canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (AttributeError, TypeError, ValueError):
        return False


class AuthenticatedOwnerProjectScopeResolver:
    """Proof of same Owner, device, OAuth client consent and repository scope.

    The existing Platform account-session validates the signed bearer token,
    owner-remote-authority checks device ownership and consent, and the
    owner-project-scope endpoint verifies the canonical Product project/Space.
    We recheck account and client consent AFTER project resolution to make
    mid-request revocation fail closed. The broker rechecks this resolver again
    after the native human folder-confirmation dialog.
    """

    __slots__ = (
        "_remote", "_token", "_device_token", "_owner", "_client", "_device", "_clock",
    )

    def __init__(
        self,
        *,
        remote: ProductRemoteClient,
        owner_user_id: str,
        client_id: str,
        device_id: str,
        access_token: Callable[[], str],
        device_token: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if (not isinstance(remote, ProductRemoteClient)
                or not all(_canonical_uuid(x) for x in (
                    owner_user_id, client_id, device_id,
                ))
                or not callable(access_token)
                or (device_token is not None and not callable(device_token))
                or not callable(clock)):
            raise ValueError("workspace_owner_host_identity_required")
        self._remote = remote
        self._owner = owner_user_id
        self._client = client_id
        self._device = device_id
        self._token = access_token
        self._device_token = device_token
        self._clock = clock

    def _owner_access_token(self) -> str:
        token = self._token()
        if (not isinstance(token, str) or not 1 <= len(token) <= 16000
                or "\r" in token or "\n" in token
                or not self._session_valid(token) or not self._client_consented(token)):
            raise ProjectWorkspaceDenied("workspace_owner_session_unavailable")
        return token

    def _device_access_token(self) -> str:
        if self._device_token is None:
            raise ProjectWorkspaceDenied("workspace_device_credential_unavailable")
        token = self._device_token()
        if (not isinstance(token, str) or not 32 <= len(token) <= 512
                or any(ord(char) < 33 or ord(char) > 126 for char in token)):
            raise ProjectWorkspaceDenied("workspace_device_credential_unavailable")
        return token

    def _validate_workspace_actor(
        self, scope: OwnerCanonicalProjectScope, actor: WorkspaceActor,
    ) -> None:
        if (not isinstance(scope, OwnerCanonicalProjectScope)
                or not isinstance(actor, WorkspaceActor)
                or scope.owner_user_id != self._owner
                or scope.client_id != self._client
                or scope.device_id != self._device
                or actor.actor_id != self._owner
                or actor.client_id != self._client):
            raise ProjectWorkspaceDenied("workspace_actor_scope_unavailable")

    def _session_valid(self, token: str) -> bool:
        session = self._remote.session(token)
        expiry = session.get("expires_at_unix")
        return (session.get("subject_id") == self._owner
                and type(expiry) is int and expiry > self._clock())

    def _client_consented(self, token: str) -> bool:
        authority = self._remote.owner_remote_authority(
            token, device_id=self._device,
        )
        clients = authority.get("clients")
        if not isinstance(clients, list):
            return False
        matches = [
            client for client in clients
            if isinstance(client, dict) and client.get("client_id") == self._client
        ]
        # Every Client must have exactly one current, non-revoked consent.
        # Never equate an existing remote-grant-group to OAuth consent.
        return len(matches) == 1

    def __call__(self, repository_full_name: str, device_id: str) -> OwnerCanonicalProjectScope:
        if (device_id != self._device
                or not isinstance(repository_full_name, str)
                or canonical_github_repository_full_name(repository_full_name)
                != repository_full_name):
            raise ProjectWorkspaceDenied("workspace_project_scope_unavailable")
        try:
            token = self._owner_access_token()
            resolved = self._remote.owner_project_scope(
                token, device_id=self._device,
                repository_full_name=repository_full_name,
            )
            if (not isinstance(resolved, dict)
                    or canonical_github_repository_full_name(
                        resolved.get("repository_full_name"),
                    ) != repository_full_name
                    or not _canonical_uuid(resolved.get("space_id"))
                    or not _canonical_uuid(resolved.get("project_id"))):
                raise ValueError("workspace_project_scope_mismatch")
            # A signed token remains the Owner identity on both sides of the
            # project lookup; consent can be revoked while the lookup runs.
            if not self._session_valid(token) or not self._client_consented(token):
                raise ValueError("workspace_owner_session_changed")
            return OwnerCanonicalProjectScope(
                owner_user_id=self._owner, client_id=self._client,
                space_id=resolved["space_id"], project_id=resolved["project_id"],
                device_id=self._device,
                repository_full_name=repository_full_name,
            )
        except Exception:
            # Do not let JWTs, URLs, SQL diagnostics, device paths or internal
            # exceptions cross the GUI/Runtime boundary.
            raise ProjectWorkspaceDenied("workspace_project_scope_unavailable") from None

    def register_workspace_resource(
        self,
        scope: OwnerCanonicalProjectScope,
        actor: WorkspaceActor,
        resource_id: str,
    ) -> LiveWorkspaceAuthority:
        """Register the Runtime's opaque selection under the existing Product grant."""
        try:
            self._validate_workspace_actor(scope, actor)
            token = self._owner_access_token()
            current = self(scope.repository_full_name, self._device)
            if current != scope:
                raise ValueError("workspace_project_scope_changed")
            device_token = self._device_access_token()
            response = self._remote.register_owner_project_workspace_resource(
                token,
                device_id=self._device,
                device_token=device_token,
                actor_id=actor.actor_id,
                client_id=self._client,
                lab_project_id=actor.project_id,
                repository_full_name=scope.repository_full_name,
                resource_id=resource_id,
                workspace_session_id=actor.workspace_session_id,
            )
            proof = project_workspace_live_proof(
                response, scope=scope, actor=actor, device_id=self._device,
                resource_id=resource_id,
            )
            if proof is None:
                raise ValueError("workspace_grant_unverified")
            if not self._session_valid(token) or not self._client_consented(token):
                raise ValueError("workspace_owner_session_changed")
            return proof
        except Exception:
            raise ProjectWorkspaceDenied("workspace_resource_registration_unavailable") from None

    def live_workspace_authority(
        self,
        actor: WorkspaceActor,
        device_id: str,
        resource_id: str,
        scope: OwnerCanonicalProjectScope,
    ) -> LiveWorkspaceAuthority | None:
        """Verify one resource afresh over the authenticated Product Device transport."""
        try:
            self._validate_workspace_actor(scope, actor)
            if device_id != self._device:
                return None
            token = self._owner_access_token()
            if self(scope.repository_full_name, self._device) != scope:
                return None
            response = self._remote.verify_device_project_workspace_resource(
                owner_user_id=self._owner,
                actor_id=actor.actor_id,
                client_id=self._client,
                device_id=self._device,
                device_token=self._device_access_token(),
                lab_project_id=actor.project_id,
                resource_id=resource_id,
                workspace_session_id=actor.workspace_session_id,
            )
            proof = project_workspace_live_proof(
                response, scope=scope, actor=actor, device_id=self._device,
                resource_id=resource_id,
            )
            if proof is None or not self._session_valid(token) or not self._client_consented(token):
                return None
            return proof
        except Exception:
            return None

    def revoke_workspace_resource(
        self,
        scope: OwnerCanonicalProjectScope,
        actor: WorkspaceActor,
        resource_id: str,
    ) -> None:
        """Revoke only this resource; the existing parent grant remains owned by Platform."""
        try:
            self._validate_workspace_actor(scope, actor)
            token = self._owner_access_token()
            if self(scope.repository_full_name, self._device) != scope:
                raise ValueError("workspace_project_scope_changed")
            result = self._remote.revoke_owner_project_workspace_resource(
                token,
                client_id=self._client,
                device_id=self._device,
                device_token=self._device_access_token(),
                resource_id=resource_id,
            )
            if result.get("revoked") is not True:
                raise ValueError("workspace_resource_revoke_unconfirmed")
            if not self._session_valid(token) or not self._client_consented(token):
                raise ValueError("workspace_owner_session_changed")
        except Exception:
            raise ProjectWorkspaceDenied("workspace_resource_revoke_unavailable") from None
