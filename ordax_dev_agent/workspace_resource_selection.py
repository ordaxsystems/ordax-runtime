"""Runtime-only Owner folder-selection seam; deliberately NOT an issuance API.

The trusted Windows host MUST supply an OS-native picker and a separate
affirmative Owner confirmation. No model, MCP payload, App SQLite record or
remote tool may invoke these callbacks as the selected-path authority.

All roots reuse the canonical registered-project allow-list and the workspace
port's root-identity policy. A resource ID is an opaque, NON-BEARER lookup key.
It confers no read permission until a trusted, freshly authenticated Platform
verifier returns exact-scoped live authority on every request.
"""
from __future__ import annotations

import secrets
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .project_workspace_port import (
    ProjectWorkspaceDenied, ProjectWorkspacePort, VerifiedProjectResource,
    WorkspaceActor, capture_workspace_root_identity,
)
from .projects import Project, canonical_github_repository_full_name, resolve_project_for_repository

_PENDING_SECONDS = 300
_MAX_REFERENCE_SECONDS = 24 * 60 * 60
_MAX_SELECTIONS = 64
_MAX_LIFESPAN = timedelta(hours=24)


def _uuid(value: object) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


@dataclass(frozen=True)
class OwnerCanonicalProjectScope:
    """Owner-authenticated project scope from the existing Product resolver.

    The trusted GUI composition must derive this from an authenticated Owner
    session plus ProductRemoteClient.owner_project_scope(), and separately pin
    the intended Product client UUID. Never from Lab/model/MCP JSON.
    """

    owner_user_id: str
    client_id: str
    space_id: str
    project_id: str
    device_id: str
    repository_full_name: str


@dataclass(frozen=True)
class LiveWorkspaceAuthority:
    """Typed result of the server-authorized Product verifier, not user JSON.

    Trusted composition must construct this ONLY from the exact authenticated,
    current PostgreSQL/Runtime channel, after validating schema and scope.
    A dataclass instance alone is not authentication.
    """

    owner_user_id: str
    space_id: str
    canonical_project_id: str
    lab_project_id: str
    actor_id: str
    client_id: str
    workspace_session_id: str
    device_id: str
    resource_id: str
    grant_id: str
    issued_at: datetime
    expires_at: datetime
    allowed_actions: frozenset[str]


@dataclass(frozen=True)
class _LocalSelection:
    repository_full_name: str
    project_scope: OwnerCanonicalProjectScope
    root: Path
    root_device: int
    root_inode: int
    prepared_at: float
    actor: WorkspaceActor | None = None
    bound: bool = False


class WorkspaceResourceSelection:
    """In-memory local root reference; never a second Product grant ledger."""

    def __init__(
        self,
        *,
        config,
        device_id: str,
        choose_directory: Callable[[Project], Path | None],
        confirm_owner: Callable[[Project, Path], bool],
        canonical_project_scope: Callable[[str, str], OwnerCanonicalProjectScope | None],
        live_authority: Callable[[WorkspaceActor, str, str, OwnerCanonicalProjectScope], LiveWorkspaceAuthority | None],
        register_resource: Callable[[OwnerCanonicalProjectScope, WorkspaceActor, str], LiveWorkspaceAuthority] | None = None,
        revoke_resource: Callable[[OwnerCanonicalProjectScope, WorkspaceActor, str], object] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not (_uuid(device_id) and callable(choose_directory)
                and callable(confirm_owner) and callable(canonical_project_scope)
                and callable(live_authority)
                and (register_resource is None or callable(register_resource))
                and (revoke_resource is None or callable(revoke_resource))
                and ((register_resource is None) == (revoke_resource is None))
                and callable(clock)):
            raise ValueError("workspace_selection_trusted_host_required")
        self._config = config
        self._device_id = device_id
        self._choose = choose_directory
        self._confirm = confirm_owner
        self._canonical_scope = canonical_project_scope
        self._live = live_authority
        self._register = register_resource
        self._revoke = revoke_resource
        self._clock = clock
        self._selections: dict[str, _LocalSelection] = {}
        self._lock = threading.RLock()

    def _registered_project(self, repository_full_name: str) -> Project:
        try:
            return resolve_project_for_repository(self._config, repository_full_name)
        except (ValueError, OSError, RuntimeError):
            raise ProjectWorkspaceDenied("workspace_registered_project_unavailable") from None

    def _owner_project_scope(self, repository_full_name: str) -> OwnerCanonicalProjectScope:
        canonical = canonical_github_repository_full_name(repository_full_name)
        if canonical is None or canonical != repository_full_name:
            raise ProjectWorkspaceDenied("workspace_project_scope_unavailable")
        try:
            scope = self._canonical_scope(repository_full_name, self._device_id)
        except Exception:
            raise ProjectWorkspaceDenied("workspace_project_scope_unavailable") from None
        if (not isinstance(scope, OwnerCanonicalProjectScope)
                or not all(_uuid(value) for value in (
                    scope.owner_user_id, scope.client_id, scope.space_id,
                    scope.project_id, scope.device_id,
                ))
                or scope.device_id != self._device_id
                or scope.repository_full_name != repository_full_name):
            raise ProjectWorkspaceDenied("workspace_project_scope_unavailable")
        return scope

    @staticmethod
    def _matches_live_authority(
        proof: object,
        actor: WorkspaceActor,
        device_id: str,
        resource_id: str,
        scope: OwnerCanonicalProjectScope,
    ) -> bool:
        if not isinstance(proof, LiveWorkspaceAuthority):
            return False
        if not all(_uuid(v) for v in (
            proof.owner_user_id, proof.space_id, proof.canonical_project_id,
            proof.client_id, proof.device_id, proof.grant_id,
        )):
            return False
        if (proof.owner_user_id, proof.space_id, proof.canonical_project_id,
            proof.client_id, proof.device_id) != (
            scope.owner_user_id, scope.space_id, scope.project_id,
            scope.client_id, scope.device_id,
        ):
            return False
        if (proof.lab_project_id, proof.actor_id, proof.client_id,
            proof.workspace_session_id, proof.device_id, proof.resource_id) != (
            actor.project_id, actor.actor_id, actor.client_id,
            actor.workspace_session_id, device_id, resource_id,
        ):
            return False
        now = datetime.now(timezone.utc)
        return (
            isinstance(proof.issued_at, datetime)
            and proof.issued_at.tzinfo is not None
            and isinstance(proof.expires_at, datetime)
            and proof.expires_at.tzinfo is not None
            and proof.issued_at <= now < proof.expires_at
            and proof.expires_at <= proof.issued_at + _MAX_LIFESPAN
            and proof.allowed_actions == frozenset({"read"})
        )

    def _revoke_unconfirmed(self, scope, actor, resource_id) -> None:
        if self._revoke is None:
            return
        try:
            self._revoke(scope, actor, resource_id)
        except Exception:
            # No usable local reference is retained. A remote orphan is bounded
            # by the Platform's short resource lifetime and cannot authorize a
            # different actor or local root.
            pass

    def choose_for_owner(
        self,
        canonical_repository_full_name: str,
        actor: WorkspaceActor | None = None,
    ) -> str:
        """Trusted user-session GUI entrypoint; returns ONLY an opaque ID.

        This method must never be exposed via MCP/LLM, HTTP or renderer-supplied
        arbitrary paths. The project comes from an authenticated Product resolver.
        """
        # Authenticate the Product project/Space binding before opening a
        # native dialog. A repository label is not an authorization by itself.
        scope = self._owner_project_scope(canonical_repository_full_name)
        if self._register is not None and (
            not isinstance(actor, WorkspaceActor)
            or actor.actor_id != scope.owner_user_id
            or actor.client_id != scope.client_id
        ):
            raise ProjectWorkspaceDenied("workspace_actor_scope_unavailable")
        project = self._registered_project(canonical_repository_full_name)
        original = capture_workspace_root_identity(project.root)
        try:
            selected = self._choose(project)
        except Exception:
            raise ProjectWorkspaceDenied("workspace_picker_unavailable") from None
        if selected is None:
            raise ProjectWorkspaceDenied("workspace_selection_cancelled")
        if (not isinstance(selected, Path) or not selected.is_absolute()
                or ".." in selected.parts):
            raise ProjectWorkspaceDenied("workspace_selection_outside_registered_project")
        # Verify the spelling supplied by the native dialog BEFORE resolving
        # aliases. On Windows the same directory may have an 8.3 short name;
        # a symlink or junction must not become acceptable via resolve().
        selected_identity = capture_workspace_root_identity(selected)
        try:
            canonical_selected = selected.resolve(strict=True)
        except (OSError, ValueError, RuntimeError):
            raise ProjectWorkspaceDenied("workspace_selection_outside_registered_project") from None
        if canonical_selected != project.root:
            raise ProjectWorkspaceDenied("workspace_selection_outside_registered_project")
        if selected_identity != original:
            raise ProjectWorkspaceDenied("workspace_root_changed")
        selected = canonical_selected
        try:
            accepted = self._confirm(project, selected)
        except Exception:
            raise ProjectWorkspaceDenied("workspace_confirmation_unavailable") from None
        if accepted is not True:
            raise ProjectWorkspaceDenied("workspace_selection_not_confirmed")
        if capture_workspace_root_identity(selected) != original:
            raise ProjectWorkspaceDenied("workspace_root_changed")
        if self._registered_project(canonical_repository_full_name).root != selected:
            raise ProjectWorkspaceDenied("workspace_project_root_mismatch")
        # The Owner scope may have been reassigned while the dialog was open.
        if self._owner_project_scope(canonical_repository_full_name) != scope:
            raise ProjectWorkspaceDenied("workspace_project_scope_changed")

        # The path remains private to Runtime. The optional production path
        # registers the opaque reference under the existing canonical read
        # grant; the App never receives the path or any grant credentials.
        resource_id = "resource_" + secrets.token_urlsafe(24)
        with self._lock:
            now = self._clock()
            for key, item in tuple(self._selections.items()):
                elapsed = now - item.prepared_at
                if elapsed > _MAX_REFERENCE_SECONDS or (
                    not item.bound and elapsed > _PENDING_SECONDS
                ):
                    self._selections.pop(key, None)
            if len(self._selections) >= _MAX_SELECTIONS:
                raise ProjectWorkspaceDenied("workspace_selection_limit")
            if resource_id in self._selections:
                raise ProjectWorkspaceDenied("workspace_selection_collision")

        if self._register is not None:
            try:
                proof = self._register(scope, actor, resource_id)
            except Exception:
                self._revoke_unconfirmed(scope, actor, resource_id)
                raise ProjectWorkspaceDenied("workspace_resource_registration_unavailable") from None
            if not self._matches_live_authority(proof, actor, self._device_id, resource_id, scope):
                self._revoke_unconfirmed(scope, actor, resource_id)
                raise ProjectWorkspaceDenied("workspace_grant_unverified")
            try:
                unchanged = (
                    capture_workspace_root_identity(selected) == original
                    and self._registered_project(canonical_repository_full_name).root == selected
                    and self._owner_project_scope(canonical_repository_full_name) == scope
                )
            except Exception:
                unchanged = False
            if not unchanged:
                self._revoke_unconfirmed(scope, actor, resource_id)
                raise ProjectWorkspaceDenied("workspace_selection_changed")

        stored = False
        with self._lock:
            now = self._clock()
            for key, item in tuple(self._selections.items()):
                elapsed = now - item.prepared_at
                if elapsed > _MAX_REFERENCE_SECONDS or (
                    not item.bound and elapsed > _PENDING_SECONDS
                ):
                    self._selections.pop(key, None)
            if len(self._selections) < _MAX_SELECTIONS and resource_id not in self._selections:
                self._selections[resource_id] = _LocalSelection(
                    canonical_repository_full_name, scope, selected, *original, now,
                    actor=actor,
                    bound=self._register is not None,
                )
                stored = True
        if not stored:
            self._revoke_unconfirmed(scope, actor, resource_id)
            raise ProjectWorkspaceDenied("workspace_selection_limit")
        return resource_id

    def discard_local_selection(self, resource_id: str) -> None:
        """Invalidate Runtime's reference; does NOT revoke the Product grant."""
        with self._lock:
            self._selections.pop(resource_id, None)

    def revoke_for_owner(self, actor: WorkspaceActor, resource_id: str) -> None:
        """Revoke one resource through Platform, then discard its local root."""
        if not isinstance(actor, WorkspaceActor) or self._revoke is None:
            raise ProjectWorkspaceDenied("workspace_resource_revoke_unavailable")
        with self._lock:
            selected = self._selections.get(resource_id)
        if (selected is None or selected.actor != actor
                or actor.actor_id != selected.project_scope.owner_user_id
                or actor.client_id != selected.project_scope.client_id):
            raise ProjectWorkspaceDenied("workspace_resource_revoke_unavailable")
        try:
            self._revoke(selected.project_scope, actor, resource_id)
        except Exception:
            raise ProjectWorkspaceDenied("workspace_resource_revoke_unavailable") from None
        with self._lock:
            if self._selections.get(resource_id) is selected:
                self._selections.pop(resource_id, None)

    def resolve_live(
        self, actor: WorkspaceActor, device_id: str, resource_id: str,
    ) -> VerifiedProjectResource | None:
        """Grant callback for existing ProjectWorkspacePort; deny by default."""
        if not isinstance(actor, WorkspaceActor) or device_id != self._device_id:
            return None
        with self._lock:
            selected = self._selections.get(resource_id)
            if selected is None:
                return None
            if selected.actor is not None and selected.actor != actor:
                return None
            elapsed = self._clock() - selected.prepared_at
            if elapsed > _MAX_REFERENCE_SECONDS or (
                not selected.bound and elapsed > _PENDING_SECONDS
            ):
                self._selections.pop(resource_id, None)
                return None

        # Never use the picker record as authorization. Re-consult live Product
        # authority on EVERY port verification and snapshot (including postscan).
        try:
            proof = self._live(actor, device_id, resource_id, selected.project_scope)
        except Exception:
            return None
        if not self._matches_live_authority(proof, actor, device_id, resource_id,
                                            selected.project_scope):
            return None
        try:
            current = self._registered_project(selected.repository_full_name)
            if current.root != selected.root or (
                capture_workspace_root_identity(selected.root)
                != (selected.root_device, selected.root_inode)
            ):
                return None
        except ProjectWorkspaceDenied:
            return None
        with self._lock:
            # No resurrection if Owner discarded selection during remote lookup.
            if self._selections.get(resource_id) is not selected:
                return None
            elapsed = self._clock() - selected.prepared_at
            if elapsed > _MAX_REFERENCE_SECONDS or (
                not selected.bound and elapsed > _PENDING_SECONDS
            ):
                self._selections.pop(resource_id, None)
                return None
            if not selected.bound:
                self._selections[resource_id] = replace(selected, bound=True)
        return VerifiedProjectResource(
            actor_id=proof.actor_id,
            client_id=proof.client_id,
            workspace_session_id=proof.workspace_session_id,
            project_id=proof.lab_project_id,
            device_id=device_id,
            resource_id=resource_id,
            root=selected.root,
            root_device=selected.root_device,
            root_inode=selected.root_inode,
            expires_at=proof.expires_at,
            allowed_actions=proof.allowed_actions,
        )

    def as_port(self, *, local_policy: Callable[[Path], bool]) -> ProjectWorkspacePort:
        """Compose the EXISTING port with fresh Product proof and local policy."""
        return ProjectWorkspacePort(
            device_id=self._device_id, grants=self.resolve_live,
            local_policy=local_policy,
        )
