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
from .projects import Project, resolve_project_for_repository

_PENDING_SECONDS = 300
_MAX_SELECTIONS = 64
_MAX_LIFESPAN = timedelta(hours=24)


def _uuid(value: object) -> bool:
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        return False


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
    expires_at: datetime
    allowed_actions: frozenset[str]


@dataclass(frozen=True)
class _LocalSelection:
    repository_full_name: str
    root: Path
    root_device: int
    root_inode: int
    prepared_at: float
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
        live_authority: Callable[[WorkspaceActor, str, str], LiveWorkspaceAuthority | None],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not (_uuid(device_id) and callable(choose_directory)
                and callable(confirm_owner) and callable(live_authority)
                and callable(clock)):
            raise ValueError("workspace_selection_trusted_host_required")
        self._config = config
        self._device_id = device_id
        self._choose = choose_directory
        self._confirm = confirm_owner
        self._live = live_authority
        self._clock = clock
        self._selections: dict[str, _LocalSelection] = {}
        self._lock = threading.RLock()

    def _registered_project(self, repository_full_name: str) -> Project:
        try:
            return resolve_project_for_repository(self._config, repository_full_name)
        except (ValueError, OSError, RuntimeError):
            raise ProjectWorkspaceDenied("workspace_registered_project_unavailable") from None

    def choose_for_owner(self, canonical_repository_full_name: str) -> str:
        """Trusted user-session GUI entrypoint; returns ONLY an opaque ID.

        This method must never be exposed via MCP/LLM, HTTP or renderer-supplied
        arbitrary paths. The project comes from an authenticated Product resolver.
        """
        project = self._registered_project(canonical_repository_full_name)
        original = capture_workspace_root_identity(project.root)
        try:
            selected = self._choose(project)
        except Exception:
            raise ProjectWorkspaceDenied("workspace_picker_unavailable") from None
        if selected is None:
            raise ProjectWorkspaceDenied("workspace_selection_cancelled")
        if not isinstance(selected, Path) or selected != project.root:
            raise ProjectWorkspaceDenied("workspace_selection_outside_registered_project")
        if capture_workspace_root_identity(selected) != original:
            raise ProjectWorkspaceDenied("workspace_root_changed")
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

        # Project paths remain private to Runtime. No cloud request or grant is
        # created here. Until Platform issuance succeeds the ID cannot be used.
        with self._lock:
            if len(self._selections) >= _MAX_SELECTIONS:
                raise ProjectWorkspaceDenied("workspace_selection_limit")
            resource_id = "resource_" + secrets.token_urlsafe(24)
            self._selections[resource_id] = _LocalSelection(
                canonical_repository_full_name, selected, *original,
                self._clock(),
            )
        return resource_id

    def discard_local_selection(self, resource_id: str) -> None:
        """Invalidate Runtime's reference; does NOT revoke the Product grant."""
        with self._lock:
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
            if not selected.bound and self._clock() - selected.prepared_at > _PENDING_SECONDS:
                self._selections.pop(resource_id, None)
                return None

        # Never use the picker record as authorization. Re-consult live Product
        # authority on EVERY port verification and snapshot (including postscan).
        try:
            proof = self._live(actor, device_id, resource_id)
        except Exception:
            return None
        now = datetime.now(timezone.utc)
        if not isinstance(proof, LiveWorkspaceAuthority):
            return None
        if not all(_uuid(v) for v in (
            proof.owner_user_id, proof.space_id, proof.canonical_project_id,
            proof.client_id, proof.device_id, proof.grant_id,
        )):
            return None
        if (proof.lab_project_id, proof.actor_id, proof.client_id,
            proof.workspace_session_id, proof.device_id, proof.resource_id) != (
            actor.project_id, actor.actor_id, actor.client_id,
            actor.workspace_session_id, device_id, resource_id,
        ):
            return None
        if (not isinstance(proof.expires_at, datetime)
                or proof.expires_at.tzinfo is None
                or not now < proof.expires_at <= now + _MAX_LIFESPAN
                or proof.allowed_actions != frozenset({"read"})):
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
