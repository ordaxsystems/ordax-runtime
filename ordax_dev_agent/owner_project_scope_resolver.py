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
from .project_workspace_port import ProjectWorkspaceDenied
from .projects import canonical_github_repository_full_name
from .workspace_resource_selection import OwnerCanonicalProjectScope


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

    __slots__ = ("_remote", "_token", "_owner", "_client", "_device", "_clock")

    def __init__(
        self,
        *,
        remote: ProductRemoteClient,
        owner_user_id: str,
        client_id: str,
        device_id: str,
        access_token: Callable[[], str],
        clock: Callable[[], float] = time.time,
    ) -> None:
        if (not isinstance(remote, ProductRemoteClient)
                or not all(_canonical_uuid(x) for x in (
                    owner_user_id, client_id, device_id,
                ))
                or not callable(access_token) or not callable(clock)):
            raise ValueError("workspace_owner_host_identity_required")
        self._remote = remote
        self._owner = owner_user_id
        self._client = client_id
        self._device = device_id
        self._token = access_token
        self._clock = clock

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
            token = self._token()
            if not isinstance(token, str) or not 1 <= len(token) <= 16000:
                raise ValueError("workspace_owner_token_invalid")
            if "\r" in token or "\n" in token:
                raise ValueError("workspace_owner_token_invalid")
            if not self._session_valid(token) or not self._client_consented(token):
                raise ValueError("workspace_owner_session_unavailable")
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
