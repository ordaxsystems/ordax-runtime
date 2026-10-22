"""Runtime-owned, project-grant-backed workspace observation (proposed port v1).

This module does not issue grants and cannot be called with a raw path as authority.
A trusted Runtime/Platform adapter must supply a *live*, project-scoped grant
and local-policy check for EVERY request. No MCP route is registered here until
those owners expose the resource chooser and grant transport.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .git_readonly import readonly_git_environment

_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,180}$")
_RESOURCE = re.compile(r"^resource_[A-Za-z0-9_-]{8,160}$")
_EXCLUDED = frozenset({".git", "node_modules", ".venv", "__pycache__", ".next",
                       "dist", "build", ".cache", ".pytest_cache"})
_MAX_FILES = 20000
_MAX_DEPTH = 24


class ProjectWorkspaceDenied(RuntimeError):
    """Fail-closed without disclosing the path or grant record."""


@dataclass(frozen=True)
class WorkspaceActor:
    actor_id: str
    client_id: str
    workspace_session_id: str
    project_id: str


@dataclass(frozen=True)
class VerifiedProjectResource:
    """Created by a trusted, authenticated grant resolver, NOT an MCP caller."""

    actor_id: str
    client_id: str
    workspace_session_id: str
    project_id: str
    device_id: str
    resource_id: str
    root: Path
    root_device: int
    root_inode: int
    expires_at: datetime
    allowed_actions: frozenset[str]


def _valid_identifier(value: object, resource: bool = False) -> bool:
    return isinstance(value, str) and bool(
        (_RESOURCE if resource else _IDENTIFIER).fullmatch(value)
    )


def _reparse(path: Path) -> bool:
    data = path.lstat()
    reparse_bit = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(data.st_mode) or bool(
        getattr(data, "st_file_attributes", 0) & reparse_bit
    ) or bool(getattr(path, "is_junction", lambda: False)())


def capture_workspace_root_identity(root: Path) -> tuple[int, int]:
    """Attest one local directory using the same boundary as the read port.

    Called only from trusted host composition, never with model-selected paths.
    No symlink, junction or reparse-point ancestor may be trusted.
    """
    if not isinstance(root, Path) or not root.is_absolute():
        raise ProjectWorkspaceDenied("workspace_root_invalid")
    try:
        for segment in (root, *root.parents):
            if _reparse(segment):
                raise ProjectWorkspaceDenied("workspace_root_reparse_point")
        info = root.stat()
    except (OSError, ValueError) as error:
        raise ProjectWorkspaceDenied("workspace_root_unavailable") from error
    if not stat.S_ISDIR(info.st_mode):
        raise ProjectWorkspaceDenied("workspace_root_not_directory")
    return (info.st_dev, info.st_ino)


def _secure_root(root: Path, grant: VerifiedProjectResource) -> Path:
    if capture_workspace_root_identity(root) != (grant.root_device, grant.root_inode):
        raise ProjectWorkspaceDenied("workspace_root_changed")
    return root


def _git(root: Path, *args: str) -> bytes:
    try:
        # Inherited GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, config injection,
        # alternate object stores, etc. must never redirect a project snapshot.
        # Preserve the ordinary process environment, not Git's ambient state.
        env = readonly_git_environment()
        response = subprocess.run(
            ["git", "-C", str(root), "--no-optional-locks",
             "-c", "core.fsmonitor=false", *args],
            shell=False, capture_output=True, timeout=8, check=False, env=env,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ProjectWorkspaceDenied("workspace_git_unavailable") from error
    if response.returncode or len(response.stdout) > 1024 * 1024:
        raise ProjectWorkspaceDenied("workspace_git_unavailable")
    return response.stdout


def _snapshot(root: Path) -> dict:
    # Walk is bounded; never follow symlinks, junctions, or reparse points.
    # No names, file contents, absolute paths, or raw Git output leave Runtime.
    count = 0
    digest = hashlib.sha256()
    pending: list[tuple[Path, int]] = [(root, 0)]
    while pending:
        directory, depth = pending.pop()
        if depth > _MAX_DEPTH:
            raise ProjectWorkspaceDenied("workspace_scan_limit")
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
            for entry in entries:
                if entry.name in _EXCLUDED:
                    continue
                candidate = Path(entry.path)
                info = candidate.lstat()
                if (stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
                    continue
                if stat.S_ISDIR(info.st_mode):
                    pending.append((candidate, depth + 1))
                elif stat.S_ISREG(info.st_mode):
                    count += 1
                    if count > _MAX_FILES:
                        raise ProjectWorkspaceDenied("workspace_scan_limit")
                    relative = candidate.relative_to(root).as_posix()
                    digest.update(relative.encode("utf-8", "surrogateescape"))
                    digest.update(str((info.st_size, info.st_mtime_ns)).encode("ascii"))
        except (OSError, ValueError) as error:
            raise ProjectWorkspaceDenied("workspace_scan_unavailable") from error

    # Git may follow a .git file (linked worktree) or a directory junction
    # outside the selected resource. Until Runtime owns a separately verified
    # Git-dir grant, such repositories must fail closed rather than probing
    # paths beyond the root. A normal local .git directory is supported.
    git_marker = root / ".git"
    try:
        marker_info = git_marker.lstat()
    except FileNotFoundError:
        is_repo = False
    except OSError as error:
        raise ProjectWorkspaceDenied("workspace_git_boundary_unavailable") from error
    else:
        if not stat.S_ISDIR(marker_info.st_mode) or _reparse(git_marker):
            raise ProjectWorkspaceDenied("workspace_git_boundary_unverified")
        is_repo = True
    changed = 0
    git: dict = {"isRepository": False}
    if is_repo:
        git_root = Path(os.fsdecode(_git(root, "rev-parse", "--show-toplevel")).strip())
        if git_root.resolve() != root.resolve():
            raise ProjectWorkspaceDenied("workspace_git_root_mismatch")
        branch = os.fsdecode(_git(root, "rev-parse", "--abbrev-ref", "HEAD")).strip()
        head = os.fsdecode(_git(root, "rev-parse", "--verify", "HEAD")).strip()
        if not re.fullmatch(r"[a-fA-F0-9]{40,64}", head) or not re.fullmatch(
            r"[A-Za-z0-9_.\\/-]{1,140}", branch
        ):
            raise ProjectWorkspaceDenied("workspace_git_metadata_invalid")
        porcelain = _git(root, "status", "--porcelain=v1", "-z",
                         "--untracked-files=normal")
        parts = [item for item in porcelain.split(b"\x00") if item]
        position = 0
        while position < len(parts):
            entry = parts[position]
            if len(entry) < 3 or entry[2:3] != b" ":
                raise ProjectWorkspaceDenied("workspace_git_status_invalid")
            changed += 1
            position += 1
            # Porcelain -z records the extra original path after a rename/copy.
            if b"R" in entry[:2] or b"C" in entry[:2]:
                position += 1
        if changed > _MAX_FILES:
            raise ProjectWorkspaceDenied("workspace_scan_limit")
        git = {"isRepository": True, "branch": branch, "head": head}
        digest.update(head.encode("ascii"))
        digest.update(branch.encode("ascii"))
        digest.update(porcelain)
    revision = "rev_" + digest.hexdigest()[:32]
    return {
        "state": "connected", "revision": revision,
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "fileCount": count, "changedCount": changed, "git": git,
    }


class ProjectWorkspacePort:
    """Consumer port with live authority supplied by Runtime/Platform.

    The grants callback is invoked once for verification and *again* when
    performing a snapshot, so persisted binding IDs cannot become credentials.
    """

    def __init__(
        self,
        *,
        device_id: str,
        grants: Callable[[WorkspaceActor, str, str], VerifiedProjectResource | None],
        local_policy: Callable[[Path], bool],
    ) -> None:
        if not _valid_identifier(device_id) or not callable(grants) or not callable(local_policy):
            raise ValueError("workspace_port_authority_required")
        self.device_id = device_id
        self._grants = grants
        self._policy = local_policy

    def _authorized(
        self, actor: WorkspaceActor, device_id: str, resource_id: str
    ) -> VerifiedProjectResource:
        if not isinstance(actor, WorkspaceActor) or not all(
            _valid_identifier(value) for value in
            (actor.actor_id, actor.client_id, actor.workspace_session_id, actor.project_id)
        ) or device_id != self.device_id or not _valid_identifier(resource_id, True):
            raise ProjectWorkspaceDenied("workspace_invalid_identity")
        grant = self._grants(actor, device_id, resource_id)
        if not isinstance(grant, VerifiedProjectResource):
            raise ProjectWorkspaceDenied("workspace_grant_missing")
        if (grant.actor_id, grant.client_id, grant.workspace_session_id,
            grant.project_id, grant.device_id, grant.resource_id) != (
            actor.actor_id, actor.client_id, actor.workspace_session_id,
            actor.project_id, device_id, resource_id,
        ) or grant.allowed_actions != frozenset({"read"}):
            raise ProjectWorkspaceDenied("workspace_grant_scope_mismatch")
        if grant.expires_at.tzinfo is None or grant.expires_at <= datetime.now(timezone.utc):
            raise ProjectWorkspaceDenied("workspace_grant_expired")
        root = _secure_root(grant.root, grant)
        if self._policy(root) is not True:
            raise ProjectWorkspaceDenied("workspace_local_policy_denied")
        return grant

    def verify_binding(
        self, actor: WorkspaceActor, device_id: str, resource_id: str
    ) -> dict:
        self._authorized(actor, device_id, resource_id)
        return {
            "authorized": True, "projectId": actor.project_id,
            "actorId": actor.actor_id, "clientId": actor.client_id,
            "workspaceSessionId": actor.workspace_session_id,
            "deviceId": device_id, "resourceId": resource_id,
        }

    def snapshot(
        self, actor: WorkspaceActor, device_id: str, resource_id: str
    ) -> dict:
        grant = self._authorized(actor, device_id, resource_id)
        data = _snapshot(grant.root)
        # Re-check owner grant/policy after filesystem work; no stale positive
        # summary if revocation occurred mid-scan.
        self._authorized(actor, device_id, resource_id)
        return {"authorized": True, "deviceId": device_id,
                "resourceId": resource_id, **data}
