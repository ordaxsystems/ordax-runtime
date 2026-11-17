"""Consume the OS-owned Project Catalog projection over the Native bridge.

Cloud authority never contains a filesystem path. The Native Host projects only
an already-existing local Project Catalog id plus its logical File Space path.
The Runtime resolves that path inside the private Desktop File Space and
independently verifies the GitHub origin before binding or executing a Project
action. No project, grant, queue or parallel registry is created here.
"""
from __future__ import annotations

import json
import os
import re
import stat
import uuid
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .projects import (
    Project,
    canonical_github_repository_full_name,
    project_repository_full_name,
)

_SURFACE_URL = re.compile(
    r"^http://127\.0\.0\.1:([1-9][0-9]{0,4})"
    r"/__ordax/native/desktop-surface-state$"
)
_TOKEN = re.compile(r"^[a-f0-9]{64}$")
_LOCAL_PROJECT = re.compile(r"^project-[1-9][0-9]*$")
_SCHEMA = "ordax.desktop-project-links/1"
_MAX_RESPONSE_BYTES = 24 * 1024
_MAX_LINKS = 32
_MAX_LOGICAL_PATH = 2048
_REPARSE_POINT = 0x400


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (AttributeError, TypeError, ValueError):
        return False


def _logical_project_path(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or not 2 <= len(value) <= _MAX_LOGICAL_PATH
        or not value.startswith("/")
        or value.endswith("/")
        or "\\" in value
        or "\x00" in value
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
    ):
        return None
    path = PurePosixPath(value)
    if not path.is_absolute() or path == PurePosixPath("/"):
        return None
    parts = path.parts[1:]
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    # PurePosixPath normalizes repeated separators and "."; require an exact
    # canonical representation so an authenticated-but-corrupt snapshot cannot
    # create two names for the same local authority.
    canonical = "/" + "/".join(parts)
    return canonical if canonical == value else None


def _native_endpoint() -> tuple[str, str] | None:
    if os.environ.get("ORDAX_RUNTIME_HOST_PROFILE") != "ordax-desktop":
        return None
    source = os.environ.get("ORDAX_DESKTOP_SURFACE_BRIDGE_URL", "")
    token = os.environ.get("ORDAX_DESKTOP_SURFACE_BRIDGE_TOKEN", "")
    match = _SURFACE_URL.fullmatch(source)
    if not match or int(match.group(1)) > 65535 or not _TOKEN.fullmatch(token):
        return None
    return (
        source.removesuffix("/desktop-surface-state") + "/desktop-project-links",
        token,
    )


def _validate_snapshot(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "schema", "host", "persistence", "links",
    }:
        raise ValueError("Invalid Native Project link snapshot")
    if (
        value.get("schema") != _SCHEMA
        or value.get("host") != "ordax-desktop"
        or value.get("persistence") != "device"
        or not isinstance(value.get("links"), list)
        or len(value["links"]) > _MAX_LINKS
    ):
        raise ValueError("Invalid Native Project link snapshot")
    local_seen: set[str] = set()
    cloud_seen: set[str] = set()
    normalized = []
    for raw in value["links"]:
        if not isinstance(raw, dict) or set(raw) != {
            "localProjectId", "localProjectPath", "cloudProjectId", "spaceId",
        }:
            raise ValueError("Invalid Native Project link")
        local_id = raw.get("localProjectId")
        local_path = _logical_project_path(raw.get("localProjectPath"))
        cloud_id = raw.get("cloudProjectId")
        space_id = raw.get("spaceId")
        if (
            not isinstance(local_id, str)
            or _LOCAL_PROJECT.fullmatch(local_id) is None
            or local_path is None
            or not _canonical_uuid(cloud_id)
            or not _canonical_uuid(space_id)
            or local_id in local_seen
            or cloud_id in cloud_seen
        ):
            raise ValueError("Invalid Native Project link")
        local_seen.add(local_id)
        cloud_seen.add(cloud_id)
        normalized.append({
            "localProjectId": local_id,
            "localProjectPath": local_path,
            "cloudProjectId": cloud_id,
            "spaceId": space_id,
        })
    return {
        "schema": _SCHEMA,
        "host": "ordax-desktop",
        "persistence": "device",
        "links": normalized,
    }


def read_desktop_project_links() -> dict | None:
    """Return only a durable, bounded OS Project Catalog link snapshot."""
    endpoint = _native_endpoint()
    if endpoint is None:
        return None
    url, token = endpoint
    request = Request(
        url,
        method="GET",
        headers={
            "X-OrdaX-Product-Bridge-Token": token,
            "Accept": "application/json",
        },
    )
    try:
        with build_opener(ProxyHandler({}), _RejectRedirects()).open(
            request, timeout=2.0,
        ) as reply:
            if reply.status != 200:
                return None
            raw = reply.read(_MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, OSError):
        return None
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("Oversized Native Project link snapshot")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError("Invalid Native Project link snapshot") from error
    return _validate_snapshot(decoded)


def _has_reparse_component(root: Path, candidate: Path) -> bool:
    relative = candidate.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            return True
        if getattr(info, "st_file_attributes", 0) & _REPARSE_POINT:
            return True
    return False


def resolve_desktop_project_workspace(
    link: dict,
    repository_full_name: str,
) -> Project:
    """Resolve one OS Project Catalog link to a verified private Git workspace.

    The logical path is local-only input from the authenticated Native bridge.
    It is never accepted from Platform/model input and never returned upstream.
    """
    repository = canonical_github_repository_full_name(repository_full_name)
    local_id = link.get("localProjectId") if isinstance(link, dict) else None
    logical = _logical_project_path(
        link.get("localProjectPath") if isinstance(link, dict) else None
    )
    if (
        repository is None
        or not isinstance(local_id, str)
        or _LOCAL_PROJECT.fullmatch(local_id) is None
        or logical is None
        or os.environ.get("ORDAX_RUNTIME_HOST_PROFILE") != "ordax-desktop"
    ):
        raise ValueError("Desktop Project workspace authority is invalid")
    user_data_raw = os.environ.get("ORDAX_DESKTOP_USER_DATA", "")
    if not user_data_raw:
        raise ValueError("Desktop File Space root is unavailable")
    user_data = Path(user_data_raw)
    if not user_data.is_absolute():
        raise ValueError("Desktop File Space root is invalid")
    files_root = (user_data / "Files").resolve(strict=True)
    if not files_root.is_dir():
        raise ValueError("Desktop File Space root is unavailable")
    candidate = files_root.joinpath(*PurePosixPath(logical).parts[1:])
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(files_root)
    except ValueError as error:
        raise ValueError("Project workspace escaped Desktop File Space") from error
    if not resolved.is_dir() or _has_reparse_component(files_root, candidate):
        raise ValueError("Project workspace is not a regular File Space directory")
    project = Project(slug=local_id, root=resolved)
    observed_repository = project_repository_full_name(project)
    if observed_repository != repository:
        raise ValueError("Project workspace GitHub origin does not match authority")
    return project
