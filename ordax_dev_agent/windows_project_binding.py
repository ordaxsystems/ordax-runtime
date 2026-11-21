"""Host-owned Project Catalog adapter for the standalone Windows MCP agent.

Reuse the existing AgentConfig.projects allowlist. A user must explicitly map
a local project-N + absolute checkout root to cloud Project and Space UUIDs.
No local path, repository name, grant or capability is sent as authority.
The Platform Owner creates the signed intent; the enrolled Device confirms it.
"""
from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID

from .config import AgentConfig
from .projects import Project, canonical_github_repository_full_name, load_projects, project_repository_full_name

_SCHEMA = "ordax.windows-project-links/1"
_LOCAL_ID = re.compile(r"^project-[1-9][0-9]*$")


def _uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value
    except (TypeError, ValueError, AttributeError):
        return False


def validate_windows_project_links(snapshot: object) -> list[dict[str, str]]:
    """Reject unknown fields, ambiguous links and noncanonical identities."""
    if not isinstance(snapshot, dict) or set(snapshot) != {
        "schema", "host", "persistence", "links",
    }:
        raise RuntimeError("Windows Project Catalog snapshot is invalid")
    links = snapshot["links"]
    if (snapshot["schema"] != _SCHEMA or snapshot["host"] != "ordax-windows-agent"
            or snapshot["persistence"] != "device" or type(links) is not list
            or len(links) > 32):
        raise RuntimeError("Windows Project Catalog snapshot is invalid")
    local_ids: set[str] = set()
    cloud_ids: set[str] = set()
    normalized: list[dict[str, str]] = []
    for link in links:
        if not isinstance(link, dict) or set(link) != {
            "localProjectId", "cloudProjectId", "spaceId",
        }:
            raise RuntimeError("Windows Project Catalog link is invalid")
        local = link["localProjectId"]
        cloud = link["cloudProjectId"]
        space = link["spaceId"]
        if (not isinstance(local, str) or not _LOCAL_ID.fullmatch(local)
                or not _uuid(cloud) or not _uuid(space)
                or local in local_ids or cloud in cloud_ids):
            raise RuntimeError("Windows Project Catalog link is invalid")
        local_ids.add(local)
        cloud_ids.add(cloud)
        normalized.append({
            "localProjectId": local, "cloudProjectId": cloud, "spaceId": space,
        })
    return normalized


def windows_project_links(config: AgentConfig) -> dict:
    """Project only explicit config entries. Never invent cloud IDs from Git."""
    if config.computer_access_source != "windows-local":
        raise RuntimeError("Windows Project Catalog host authority is unavailable")
    links: list[dict[str, str]] = []
    if config.projects is not None:
        # The same canonical project allowlist authorizes normal local actions.
        registered = load_projects(config)
        for local, entry in config.projects.items():
            if not isinstance(entry, dict):
                raise ValueError("Invalid registered Windows project")
            cloud = entry.get("cloud_project_id")
            space = entry.get("space_id")
            if cloud is None and space is None:
                continue
            if (local not in registered or not _LOCAL_ID.fullmatch(local)
                    or not _uuid(cloud) or not _uuid(space)):
                raise ValueError("Explicit Windows Project/Space link is invalid")
            links.append({
                "localProjectId": local, "cloudProjectId": cloud, "spaceId": space,
            })
    snapshot = {
        "schema": _SCHEMA, "host": "ordax-windows-agent",
        "persistence": "device", "links": links,
    }
    validate_windows_project_links(snapshot)
    return snapshot


def resolve_windows_project_workspace(
    config: AgentConfig, link: dict[str, str], repository: str,
) -> Project:
    """Rebind the cloud scope to the registered Windows checkout, fail-closed.

    An explicit local UUID mapping alone is insufficient: the checkout must
    still exist, not be a junction/symlink, and have the matching Git origin.
    """
    canonical = canonical_github_repository_full_name(repository)
    if canonical != repository or config.computer_access_source != "windows-local":
        raise ValueError("Windows Project repository authority is invalid")
    links = validate_windows_project_links(windows_project_links(config))
    matching = [item for item in links if item == link]
    if len(matching) != 1:
        raise ValueError("Windows Project link is missing or has changed")
    projects = load_projects(config)
    project = projects.get(link["localProjectId"])
    if project is None or not project.root.is_dir():
        raise ValueError("Registered Windows Project workspace is unavailable")
    raw = Path(config.projects[project.slug]["path"])
    if not raw.is_absolute() or raw.resolve() != project.root:
        raise ValueError("Windows Project workspace path is not canonical")
    # Reject reparse/symlink components above the checkout too. A path that
    # resolves into a different tree is never local Project Catalog authority.
    for component in (raw, *raw.parents):
        if component.is_symlink() or (
                hasattr(component, "is_junction") and component.is_junction()):
            raise ValueError("Windows Project workspace path is not canonical")
    if project_repository_full_name(project) != canonical:
        raise ValueError("Windows Project Git origin does not match Owner intent")
    return project
