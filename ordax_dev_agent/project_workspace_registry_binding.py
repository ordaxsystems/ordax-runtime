"""Bind an already authenticated workspace grant to the canonical Runtime project.

No new registry, filesystem API, grant writer, Product route or raw-path input.
The caller must provide a *live* Platform/Runtime VerifiedProjectResource and
a canonical repository identity resolved by the authenticated Product owner.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .project_workspace_port import (
    ProjectWorkspaceDenied, VerifiedProjectResource, WorkspaceActor,
)
from .projects import resolve_project_for_repository


def bind_workspace_to_registered_project(
    *,
    config,
    repository_full_name: str,
    actor: WorkspaceActor,
    device_id: str,
    resource_id: str,
    live_grant: VerifiedProjectResource | None,
) -> VerifiedProjectResource:
    """Return a grant confined to the *existing* unique local project root.

    This helper never issues a grant. Its inputs MUST be supplied by a trusted
    host composition, never directly deserialized from model or MCP arguments.
    A local project slug or path does not establish Product authority.
    """
    if not isinstance(actor, WorkspaceActor) or not isinstance(
        live_grant, VerifiedProjectResource
    ):
        raise ProjectWorkspaceDenied("workspace_grant_missing")
    if (
        live_grant.actor_id != actor.actor_id
        or live_grant.client_id != actor.client_id
        or live_grant.workspace_session_id != actor.workspace_session_id
        or live_grant.project_id != actor.project_id
        or live_grant.device_id != device_id
        or live_grant.resource_id != resource_id
    ):
        raise ProjectWorkspaceDenied("workspace_grant_scope_mismatch")
    try:
        project = resolve_project_for_repository(config, repository_full_name)
    except (OSError, ValueError, RuntimeError):
        raise ProjectWorkspaceDenied("workspace_registered_project_unavailable") from None
    # The existing local project allow-list is the *only* registry of roots.
    # Do not broaden a grant to a parent or different worktree/repository.
    if not isinstance(live_grant.root, Path):
        raise ProjectWorkspaceDenied("workspace_root_invalid")
    try:
        if live_grant.root != project.root:
            raise ProjectWorkspaceDenied("workspace_project_root_mismatch")
        info = project.root.stat()
    except OSError:
        raise ProjectWorkspaceDenied("workspace_registered_project_unavailable") from None
    if (info.st_dev, info.st_ino) != (
        live_grant.root_device, live_grant.root_inode
    ):
        raise ProjectWorkspaceDenied("workspace_root_changed")
    # Same resource and root; the returned grant is not enlarged or renewed.
    return replace(live_grant, root=project.root)
