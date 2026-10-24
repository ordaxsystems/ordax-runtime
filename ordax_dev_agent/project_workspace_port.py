"""Runtime-owned, project-grant-backed workspace observation (proposed port v1).

This module does not issue grants and cannot be called with a raw path as authority.
A trusted Runtime/Platform adapter must supply a *live*, project-scoped grant
and local-policy check for EVERY request. No MCP route is registered here until
those owners expose the resource chooser and grant transport.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
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
# Bound directories as well as regular files before retaining DirEntry objects
# for deterministic ordering. A tree can contain many empty directories or
# reparse points while remaining below the regular-file limit.
_MAX_ENTRIES = 40000
_MAX_DEPTH = 24
_SCANDIR_SUPPORTS_FD = os.scandir in os.supports_fd
_WIN_FILE_LIST_DIRECTORY = 0x0001
_WIN_FILE_READ_ATTRIBUTES = 0x0080
_WIN_FILE_SHARE_READ = 0x0001
_WIN_FILE_SHARE_WRITE = 0x0002
_WIN_FILE_ATTRIBUTE_DIRECTORY = 0x0010
_WIN_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_WIN_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WIN_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_WIN_OPEN_EXISTING = 3
_WIN_FILE_ATTRIBUTE_TAG_INFO = 9


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
    return stat.S_ISLNK(data.st_mode) or bool(
        getattr(data, "st_file_attributes", 0) & _WIN_FILE_ATTRIBUTE_REPARSE_POINT
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


def _git(
    root: Path,
    *args: str,
    cwd_fd: int | None = None,
    git_dir_fd: int | None = None,
) -> bytes:
    try:
        # Inherited GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, config injection,
        # alternate object stores, etc. must never redirect a project snapshot.
        # Preserve the ordinary process environment, not Git's ambient state.
        env = readonly_git_environment()
        command = ["git"]
        options = {}
        if cwd_fd is None:
            command.extend(["-C", str(root)])
        else:
            if git_dir_fd is None:
                raise ProjectWorkspaceDenied("workspace_git_boundary_unavailable")
            env["GIT_WORK_TREE"] = _inherited_directory_fd_path(cwd_fd)
            env["GIT_DIR"] = _inherited_directory_fd_path(git_dir_fd)
            options["cwd"] = cwd_fd
            options["pass_fds"] = (cwd_fd, git_dir_fd)
        response = subprocess.run(
            [*command, "--no-optional-locks", "-c", "core.fsmonitor=false", *args],
            shell=False, capture_output=True, timeout=8, check=False, env=env,
            **options,
        )
    except ProjectWorkspaceDenied:
        raise
    except (OSError, subprocess.SubprocessError) as error:
        raise ProjectWorkspaceDenied("workspace_git_unavailable") from error
    if response.returncode or len(response.stdout) > 1024 * 1024:
        raise ProjectWorkspaceDenied("workspace_git_unavailable")
    return response.stdout


def _inherited_directory_fd_path(descriptor: int) -> str:
    for directory in ("/proc/self/fd", "/dev/fd"):
        if os.path.isdir(directory):
            return f"{directory}/{descriptor}"
    raise ProjectWorkspaceDenied("workspace_git_boundary_unavailable")


def _is_reparse_info(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & _WIN_FILE_ATTRIBUTE_REPARSE_POINT
    )


class _WorkspaceSnapshotAccumulator:
    def __init__(self) -> None:
        self.file_count = 0
        self.entry_count = 0
        self.digest = hashlib.sha256()

    def count_entry(self) -> None:
        self.entry_count += 1
        if self.entry_count > _MAX_ENTRIES:
            raise ProjectWorkspaceDenied("workspace_scan_limit")

    def inspect(self, name: str, info: os.stat_result, relative: str) -> bool:
        if name in _EXCLUDED or _is_reparse_info(info):
            return False
        if stat.S_ISDIR(info.st_mode):
            return True
        if stat.S_ISREG(info.st_mode):
            self.file_count += 1
            if self.file_count > _MAX_FILES:
                raise ProjectWorkspaceDenied("workspace_scan_limit")
            self.digest.update(relative.encode("utf-8", "surrogateescape"))
            self.digest.update(str((info.st_size, info.st_mtime_ns)).encode("ascii"))
        return False


def _bounded_entries(iterator, state: _WorkspaceSnapshotAccumulator):
    entries = []
    for entry in iterator:
        state.count_entry()
        entries.append(entry)
    entries.sort(key=lambda entry: entry.name)
    return entries


def _directory_fd_flags() -> int:
    if (not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW")
            or os.open not in os.supports_dir_fd or not _SCANDIR_SUPPORTS_FD):
        raise ProjectWorkspaceDenied("workspace_secure_scan_unavailable")
    return (os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0))


def _open_posix_root(root: Path, expected_identity: tuple[int, int]) -> int:
    flags = _directory_fd_flags()
    descriptor: int | None = None
    try:
        descriptor = os.open(root.anchor or os.path.sep, flags)
        for component in root.parts[1:]:
            if component in ("", "."):
                continue
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode):
            raise ProjectWorkspaceDenied("workspace_root_not_directory")
        if (info.st_dev, info.st_ino) != expected_identity:
            raise ProjectWorkspaceDenied("workspace_root_changed")
        return descriptor
    except ProjectWorkspaceDenied:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except (OSError, ValueError) as error:
        if descriptor is not None:
            os.close(descriptor)
        raise ProjectWorkspaceDenied("workspace_root_unavailable") from error


def _open_posix_directory_at(parent: int, name: str) -> int:
    try:
        descriptor = os.open(name, _directory_fd_flags(), dir_fd=parent)
    except OSError as error:
        raise ProjectWorkspaceDenied("workspace_scan_boundary_changed") from error
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ProjectWorkspaceDenied("workspace_scan_boundary_changed")
        return descriptor
    except ProjectWorkspaceDenied:
        os.close(descriptor)
        raise
    except OSError as error:
        os.close(descriptor)
        raise ProjectWorkspaceDenied("workspace_scan_unavailable") from error


def _scan_posix_directory(
    descriptor: int,
    depth: int,
    relative_prefix: str,
    state: _WorkspaceSnapshotAccumulator,
) -> None:
    if depth > _MAX_DEPTH:
        raise ProjectWorkspaceDenied("workspace_scan_limit")
    try:
        with os.scandir(descriptor) as iterator:
            entries = _bounded_entries(iterator, state)
        directories: list[tuple[str, str]] = []
        for entry in entries:
            name = os.fsdecode(entry.name)
            if name in _EXCLUDED:
                continue
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            relative = f"{relative_prefix}/{name}" if relative_prefix else name
            if state.inspect(name, info, relative):
                directories.append((name, relative))
        # Match the former LIFO traversal order so existing revisions remain
        # stable while retaining each parent descriptor during recursion.
        for name, relative in reversed(directories):
            child = _open_posix_directory_at(descriptor, name)
            try:
                _scan_posix_directory(child, depth + 1, relative, state)
            finally:
                os.close(child)
    except ProjectWorkspaceDenied:
        raise
    except (OSError, ValueError) as error:
        raise ProjectWorkspaceDenied("workspace_scan_unavailable") from error


@lru_cache(maxsize=1)
def _windows_api():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class _FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]

    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    return ctypes, kernel, _FileAttributeTagInfo


def _open_windows_directory(path: Path, error_code: str) -> int:
    try:
        ctypes, kernel, file_attribute_tag_info = _windows_api()
        # Omitting FILE_SHARE_DELETE makes a concurrent directory rename fail.
        handle = kernel.CreateFileW(
            str(path), _WIN_FILE_LIST_DIRECTORY | _WIN_FILE_READ_ATTRIBUTES,
            _WIN_FILE_SHARE_READ | _WIN_FILE_SHARE_WRITE, None, _WIN_OPEN_EXISTING,
            _WIN_FILE_FLAG_BACKUP_SEMANTICS | _WIN_FILE_FLAG_OPEN_REPARSE_POINT, None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if handle == invalid_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = file_attribute_tag_info()
        if not kernel.GetFileInformationByHandleEx(
            handle, _WIN_FILE_ATTRIBUTE_TAG_INFO, ctypes.byref(info), ctypes.sizeof(info)
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            kernel.CloseHandle(handle)
            raise error
        if info.FileAttributes & _WIN_FILE_ATTRIBUTE_REPARSE_POINT:
            kernel.CloseHandle(handle)
            raise ProjectWorkspaceDenied(error_code)
        if not info.FileAttributes & _WIN_FILE_ATTRIBUTE_DIRECTORY:
            kernel.CloseHandle(handle)
            raise ProjectWorkspaceDenied("workspace_scan_boundary_changed")
        return int(handle)
    except ProjectWorkspaceDenied:
        raise
    except (AttributeError, OSError, TypeError, ValueError) as error:
        raise ProjectWorkspaceDenied("workspace_scan_unavailable") from error


def _close_windows_handle(handle: int) -> None:
    _, kernel, _ = _windows_api()
    if not kernel.CloseHandle(handle):
        raise ProjectWorkspaceDenied("workspace_scan_unavailable")


def _open_windows_root(root: Path, expected_identity: tuple[int, int]) -> int:
    handle: int | None = None
    try:
        # Pin the attested directory without DELETE sharing while path-based
        # enumeration uses its identity.
        handle = _open_windows_directory(root, "workspace_root_reparse_point")
        info = root.stat()
        if (info.st_dev, info.st_ino) != expected_identity:
            raise ProjectWorkspaceDenied("workspace_root_changed")
        return handle
    except ProjectWorkspaceDenied:
        if handle is not None:
            _close_windows_handle(handle)
        raise
    except (OSError, ValueError) as error:
        if handle is not None:
            _close_windows_handle(handle)
        raise ProjectWorkspaceDenied("workspace_root_unavailable") from error


def _scan_windows_directory(
    directory: Path,
    depth: int,
    relative_prefix: str,
    state: _WorkspaceSnapshotAccumulator,
) -> None:
    if depth > _MAX_DEPTH:
        raise ProjectWorkspaceDenied("workspace_scan_limit")
    try:
        with os.scandir(directory) as iterator:
            entries = _bounded_entries(iterator, state)
        directories: list[tuple[Path, str]] = []
        for entry in entries:
            name = entry.name
            if name in _EXCLUDED:
                continue
            candidate = Path(entry.path)
            info = candidate.lstat()
            relative = f"{relative_prefix}/{name}" if relative_prefix else name
            if state.inspect(name, info, relative):
                directories.append((candidate, relative))
        for candidate, relative in reversed(directories):
            # OPEN_REPARSE_POINT inspects the directory entry itself. The
            # handle also denies concurrent rename while os.scandir uses path.
            handle = _open_windows_directory(
                candidate, "workspace_scan_boundary_changed"
            )
            try:
                _scan_windows_directory(candidate, depth + 1, relative, state)
            finally:
                _close_windows_handle(handle)
    except ProjectWorkspaceDenied:
        raise
    except (OSError, ValueError) as error:
        raise ProjectWorkspaceDenied("workspace_scan_unavailable") from error


def _git_metadata(root: Path, root_fd: int | None) -> tuple[dict, bytes, int]:
    git_marker = root / ".git"
    git_fd: int | None = None
    git_handle: int | None = None
    if root_fd is None:
        try:
            marker_info = git_marker.lstat()
        except FileNotFoundError:
            return {"isRepository": False}, b"", 0
        except OSError as error:
            raise ProjectWorkspaceDenied("workspace_git_boundary_unavailable") from error
        if not stat.S_ISDIR(marker_info.st_mode) or _reparse(git_marker):
            raise ProjectWorkspaceDenied("workspace_git_boundary_unverified")
        git_handle = _open_windows_directory(
            git_marker, "workspace_git_boundary_unverified"
        )
    else:
        try:
            marker_info = os.stat(".git", dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return {"isRepository": False}, b"", 0
        except OSError as error:
            raise ProjectWorkspaceDenied("workspace_git_boundary_unavailable") from error
        if not stat.S_ISDIR(marker_info.st_mode) or _is_reparse_info(marker_info):
            raise ProjectWorkspaceDenied("workspace_git_boundary_unverified")
        git_fd = _open_posix_directory_at(root_fd, ".git")
        try:
            opened_info = os.fstat(git_fd)
        except OSError as error:
            os.close(git_fd)
            raise ProjectWorkspaceDenied("workspace_git_boundary_unavailable") from error
        if (opened_info.st_dev, opened_info.st_ino) != (
            marker_info.st_dev, marker_info.st_ino
        ):
            os.close(git_fd)
            raise ProjectWorkspaceDenied("workspace_git_boundary_changed")

    def run(*args: str) -> bytes:
        return _git(root, *args, cwd_fd=root_fd, git_dir_fd=git_fd)

    try:
        if root_fd is None:
            git_root = Path(os.fsdecode(run("rev-parse", "--show-toplevel")).strip())
            if git_root.resolve() != root.resolve():
                raise ProjectWorkspaceDenied("workspace_git_root_mismatch")
        elif os.fsdecode(run("rev-parse", "--show-prefix")).strip():
            raise ProjectWorkspaceDenied("workspace_git_root_mismatch")
        branch = os.fsdecode(run("rev-parse", "--abbrev-ref", "HEAD")).strip()
        head = os.fsdecode(run("rev-parse", "--verify", "HEAD")).strip()
        if not re.fullmatch(r"[a-fA-F0-9]{40,64}", head) or not re.fullmatch(
            r"[A-Za-z0-9_.\\/-]{1,140}", branch
        ):
            raise ProjectWorkspaceDenied("workspace_git_metadata_invalid")
        porcelain = run("status", "--porcelain=v1", "-z", "--untracked-files=normal")
        parts = [item for item in porcelain.split(b"\x00") if item]
        changed = 0
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
        return {"isRepository": True, "branch": branch, "head": head}, porcelain, changed
    finally:
        if git_fd is not None:
            os.close(git_fd)
        if git_handle is not None:
            _close_windows_handle(git_handle)


def _snapshot(root: Path, expected_identity: tuple[int, int]) -> dict:
    # Count metadata from pinned directory identities; never recurse through a
    # path name after a concurrent rename can redirect it to a reparse target.
    state = _WorkspaceSnapshotAccumulator()
    if os.name == "nt":
        root_handle = _open_windows_root(root, expected_identity)
        try:
            _scan_windows_directory(root, 0, "", state)
            git, porcelain, changed = _git_metadata(root, None)
        finally:
            _close_windows_handle(root_handle)
    else:
        root_fd = _open_posix_root(root, expected_identity)
        try:
            _scan_posix_directory(root_fd, 0, "", state)
            git, porcelain, changed = _git_metadata(root, root_fd)
        finally:
            os.close(root_fd)
    if git["isRepository"]:
        state.digest.update(git["head"].encode("ascii"))
        state.digest.update(git["branch"].encode("ascii"))
        state.digest.update(porcelain)
    revision = "rev_" + state.digest.hexdigest()[:32]
    return {
        "state": "connected", "revision": revision,
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "fileCount": state.file_count,
        "changedCount": changed,
        "git": git,
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
        data = _snapshot(grant.root, (grant.root_device, grant.root_inode))
        # Re-check owner grant/policy after filesystem work; no stale positive
        # summary if revocation occurred mid-scan.
        self._authorized(actor, device_id, resource_id)
        return {"authorized": True, "deviceId": device_id,
                "resourceId": resource_id, **data}
