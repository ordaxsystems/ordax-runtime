"""Developer-grade project and terminal capabilities for ORDAX.

These actions are intentionally transport-neutral. They are shared by the local
Studio, the remote Product gateway and the ChatGPT app. Filesystem actions are
strictly project-scoped. Terminal execution is a separate privileged capability:
it runs with the local OS user's permissions and therefore must never be implied
by ordinary file/project grants.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

from .models import ActionResult
from .process_runner import run_command as _run


_MAX_SOURCE_BYTES = 16 * 1024 * 1024
_MAX_INLINE_BYTES = 2 * 1024 * 1024
_MAX_WRITE_BYTES = 8 * 1024 * 1024
_MAX_LIST_ENTRIES = 5000
_BLOCKED_PARTS = {".git"}
_ALLOWED_GIT_SUBCOMMANDS = frozenset({
    "add",
    "branch",
    "cat-file",
    "checkout",
    "cherry-pick",
    "commit",
    "describe",
    "diff",
    "fetch",
    "log",
    "ls-files",
    "ls-tree",
    "merge",
    "notes",
    "pull",
    "push",
    "rebase",
    "reflog",
    "remote",
    "reset",
    "restore",
    "revert",
    "rev-parse",
    "show",
    "stash",
    "status",
    "switch",
    "tag",
})
_GIT_URL_USERINFO_RE = re.compile(r"(?P<scheme>https?://)[^\s/@]+@", re.IGNORECASE)
_GIT_URL_PASSWORD_RE = re.compile(r"(?P<scheme>https?://)[^\s/:@]+:[^\s/@]+@", re.IGNORECASE)
_GIT_TOKEN_QUERY_RE = re.compile(
    r"([?&](?:access_token|token|auth|password)=)[^&#\s]+",
    re.IGNORECASE,
)


def _redact_git_sensitive_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    value = _GIT_URL_PASSWORD_RE.sub(r"\g<scheme>***@", value)
    value = _GIT_URL_USERINFO_RE.sub(r"\g<scheme>***@", value)
    return _GIT_TOKEN_QUERY_RE.sub(r"\1***", value)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_project_path(
    project,
    raw: str,
    *,
    must_exist: bool,
    allow_root: bool = False,
) -> tuple[Path, Path]:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("path is required")
    path = project.path(raw.strip(), must_exist=must_exist)
    relative = path.relative_to(project.root.resolve())
    if not allow_root and not relative.parts:
        raise ValueError("operation on project root is not allowed")
    if any(part in _BLOCKED_PARTS for part in relative.parts):
        raise ValueError("direct access to .git internals is not allowed")
    return path, relative


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int, name: str) -> int:
    try:
        parsed = int(default if value is None else value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


class DeveloperActions:
    """General development capabilities independent from Blender/Unity adapters."""

    def workspace_file_stat(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        path, relative = _safe_project_path(
            project,
            str(payload.get("path") or "."),
            must_exist=True,
            allow_root=True,
        )
        stat = path.stat()
        data: dict[str, Any] = {
            "project": project.slug,
            "relative_path": "." if not relative.parts else relative.as_posix(),
            "kind": "directory" if path.is_dir() else "file" if path.is_file() else "other",
            "size_bytes": stat.st_size,
            "modified_at_ns": stat.st_mtime_ns,
        }
        if path.is_file() and stat.st_size <= _MAX_SOURCE_BYTES:
            data["sha256"] = _sha256(path.read_bytes())
        return ActionResult(True, "workspace path inspected", data)

    def workspace_directory_list(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        path, relative = _safe_project_path(
            project,
            str(payload.get("path") or "."),
            must_exist=True,
            allow_root=True,
        )
        if not path.is_dir():
            return ActionResult(False, "path is not a directory")

        max_depth = _bounded_int(
            payload.get("max_depth"), default=2, minimum=1, maximum=12, name="max_depth"
        )
        max_entries = _bounded_int(
            payload.get("max_entries"),
            default=500,
            minimum=1,
            maximum=_MAX_LIST_ENTRIES,
            name="max_entries",
        )
        include_hidden = bool(payload.get("include_hidden", False))
        base = path.resolve()
        entries: list[dict[str, Any]] = []
        queue: list[tuple[Path, int]] = [(base, 0)]

        while queue and len(entries) < max_entries:
            directory, depth = queue.pop(0)
            if depth >= max_depth:
                continue
            try:
                children = sorted(
                    directory.iterdir(),
                    key=lambda item: (not item.is_dir(), item.name.casefold()),
                )
            except OSError as error:
                return ActionResult(False, f"directory listing failed: {error}")

            for child in children:
                if child.name == ".git":
                    continue
                if not include_hidden and child.name.startswith("."):
                    continue
                try:
                    rel = child.resolve().relative_to(project.root.resolve())
                except (OSError, ValueError):
                    continue
                try:
                    child_stat = child.stat()
                except OSError:
                    child_stat = None
                entry = {
                    "relative_path": rel.as_posix(),
                    "kind": "directory" if child.is_dir() else "file" if child.is_file() else "other",
                    "depth": depth + 1,
                    "size_bytes": child_stat.st_size if child_stat else None,
                }
                entries.append(entry)
                if child.is_dir() and depth + 1 < max_depth:
                    queue.append((child, depth + 1))
                if len(entries) >= max_entries:
                    break

        return ActionResult(
            True,
            "workspace directory listed",
            {
                "project": project.slug,
                "relative_path": "." if not relative.parts else relative.as_posix(),
                "entry_count": len(entries),
                "truncated": len(entries) >= max_entries,
                "entries": entries,
            },
        )

    def workspace_text_read(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        path, relative = _safe_project_path(
            project,
            str(payload.get("path") or ""),
            must_exist=True,
        )
        if not path.is_file():
            return ActionResult(False, "path is not a file")
        size = path.stat().st_size
        if size > _MAX_SOURCE_BYTES:
            return ActionResult(
                False,
                f"file is too large for text inspection: {size} > {_MAX_SOURCE_BYTES}",
                {"relative_path": relative.as_posix(), "size_bytes": size},
            )
        raw = path.read_bytes()
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            return ActionResult(False, f"file is not valid UTF-8 text: {error}")

        start_line = _bounded_int(
            payload.get("start_line"), default=1, minimum=1, maximum=2_000_000, name="start_line"
        )
        end_value = payload.get("end_line")
        end_line = None
        if end_value is not None:
            end_line = _bounded_int(
                end_value,
                default=start_line,
                minimum=start_line,
                maximum=2_000_000,
                name="end_line",
            )
        lines = content.splitlines(keepends=True)
        selected = "".join(lines[start_line - 1:end_line])
        encoded = selected.encode("utf-8")
        if len(encoded) > _MAX_INLINE_BYTES:
            return ActionResult(
                False,
                "selected text range is too large; request a smaller line range",
                {
                    "relative_path": relative.as_posix(),
                    "selected_bytes": len(encoded),
                    "max_inline_bytes": _MAX_INLINE_BYTES,
                    "line_count": len(lines),
                    "sha256": _sha256(raw),
                },
            )

        return ActionResult(
            True,
            "workspace text ready",
            {
                "project": project.slug,
                "relative_path": relative.as_posix(),
                "size_bytes": len(raw),
                "sha256": _sha256(raw),
                "line_count": len(lines),
                "start_line": start_line,
                "end_line": min(end_line or len(lines), len(lines)),
                "content": selected,
            },
        )

    def workspace_text_write(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        path, relative = _safe_project_path(
            project,
            str(payload.get("path") or ""),
            must_exist=False,
        )
        content = payload.get("content")
        if not isinstance(content, str):
            return ActionResult(False, "content must be a string")
        encoded = content.encode("utf-8")
        if len(encoded) > _MAX_WRITE_BYTES:
            return ActionResult(False, f"content exceeds {_MAX_WRITE_BYTES} bytes")

        if path.exists() and not path.is_file():
            return ActionResult(False, "path exists and is not a file")

        exists = path.is_file()
        before = path.read_bytes() if exists else None
        before_sha = _sha256(before) if before is not None else None
        expected = str(payload.get("expected_sha256") or "").strip().lower()
        create = bool(payload.get("create", False))

        if exists:
            if not expected:
                return ActionResult(
                    False,
                    "expected_sha256 is required when replacing an existing file",
                    {"relative_path": relative.as_posix(), "current_sha256": before_sha},
                )
            if expected != before_sha:
                return ActionResult(
                    False,
                    "file changed since it was read; refusing stale overwrite",
                    {
                        "relative_path": relative.as_posix(),
                        "expected_sha256": expected,
                        "current_sha256": before_sha,
                    },
                )
        elif not create:
            return ActionResult(
                False,
                "file does not exist; set create=true to create it",
                {"relative_path": relative.as_posix()},
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.ordax-{uuid.uuid4().hex}.tmp")
        try:
            temp.write_bytes(encoded)
            if temp.read_bytes() != encoded:
                raise OSError("temporary write verification failed")
            os.replace(temp, path)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

        after = path.read_bytes()
        return ActionResult(
            True,
            "workspace text written",
            {
                "project": project.slug,
                "relative_path": relative.as_posix(),
                "created": not exists,
                "before_sha256": before_sha,
                "sha256": _sha256(after),
                "size_bytes": len(after),
            },
        )

    def workspace_text_patch(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        path, relative = _safe_project_path(
            project,
            str(payload.get("path") or ""),
            must_exist=True,
        )
        if not path.is_file():
            return ActionResult(False, "path is not a file")
        before = path.read_bytes()
        if len(before) > _MAX_SOURCE_BYTES:
            return ActionResult(False, "file is too large to patch")
        before_sha = _sha256(before)
        expected = str(payload.get("expected_sha256") or "").strip().lower()
        if not expected or expected != before_sha:
            return ActionResult(
                False,
                "expected_sha256 does not match current file",
                {"relative_path": relative.as_posix(), "current_sha256": before_sha},
            )
        try:
            text = before.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            return ActionResult(False, f"file is not valid UTF-8 text: {error}")

        replacements = payload.get("replacements")
        if not isinstance(replacements, list) or not replacements or len(replacements) > 100:
            return ActionResult(False, "replacements must contain 1..100 items")

        patched = text
        applied: list[dict[str, int]] = []
        for index, item in enumerate(replacements):
            if not isinstance(item, dict):
                return ActionResult(False, f"replacement {index} must be an object")
            old = item.get("old")
            new = item.get("new")
            if not isinstance(old, str) or not old:
                return ActionResult(False, f"replacement {index}.old must be a non-empty string")
            if not isinstance(new, str):
                return ActionResult(False, f"replacement {index}.new must be a string")
            expected_count = _bounded_int(
                item.get("expected_count"),
                default=1,
                minimum=1,
                maximum=10000,
                name=f"replacement {index}.expected_count",
            )
            actual = patched.count(old)
            if actual != expected_count:
                return ActionResult(
                    False,
                    f"replacement {index} matched {actual}, expected {expected_count}",
                    {
                        "relative_path": relative.as_posix(),
                        "replacement_index": index,
                        "actual_count": actual,
                        "expected_count": expected_count,
                        "current_sha256": before_sha,
                    },
                )
            patched = patched.replace(old, new)
            applied.append({"index": index, "count": actual})

        encoded = patched.encode("utf-8")
        if len(encoded) > _MAX_WRITE_BYTES:
            return ActionResult(False, "patched content exceeds write limit")

        temp = path.with_name(f".{path.name}.ordax-{uuid.uuid4().hex}.tmp")
        try:
            temp.write_bytes(encoded)
            os.replace(temp, path)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass

        return ActionResult(
            True,
            "workspace text patched",
            {
                "project": project.slug,
                "relative_path": relative.as_posix(),
                "before_sha256": before_sha,
                "sha256": _sha256(path.read_bytes()),
                "replacement_count": len(applied),
                "replacements": applied,
            },
        )

    def workspace_directory_create(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        path, relative = _safe_project_path(
            project,
            str(payload.get("path") or ""),
            must_exist=False,
        )
        existed = path.exists()
        if existed and not path.is_dir():
            return ActionResult(False, "path exists and is not a directory")
        path.mkdir(parents=bool(payload.get("parents", True)), exist_ok=True)
        return ActionResult(
            True,
            "workspace directory ready",
            {
                "project": project.slug,
                "relative_path": relative.as_posix(),
                "created": not existed,
            },
        )

    def workspace_path_remove(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        path, relative = _safe_project_path(
            project,
            str(payload.get("path") or ""),
            must_exist=True,
        )
        if path.is_symlink():
            path.unlink()
            kind = "symlink"
        elif path.is_file():
            current = path.read_bytes()
            expected = str(payload.get("expected_sha256") or "").strip().lower()
            if expected and expected != _sha256(current):
                return ActionResult(
                    False,
                    "expected_sha256 does not match current file",
                    {"relative_path": relative.as_posix(), "current_sha256": _sha256(current)},
                )
            path.unlink()
            kind = "file"
        elif path.is_dir():
            if not bool(payload.get("recursive", False)):
                try:
                    path.rmdir()
                except OSError:
                    return ActionResult(False, "directory is not empty; set recursive=true")
            else:
                shutil.rmtree(path)
            kind = "directory"
        else:
            return ActionResult(False, "unsupported path type")

        return ActionResult(
            True,
            "workspace path removed",
            {"project": project.slug, "relative_path": relative.as_posix(), "kind": kind},
        )

    def workspace_path_move(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        source, source_rel = _safe_project_path(
            project,
            str(payload.get("source") or ""),
            must_exist=True,
        )
        destination, destination_rel = _safe_project_path(
            project,
            str(payload.get("destination") or ""),
            must_exist=False,
        )
        if destination.exists() and not bool(payload.get("overwrite", False)):
            return ActionResult(False, "destination exists; set overwrite=true to replace it")
        if source.is_file():
            expected = str(payload.get("expected_sha256") or "").strip().lower()
            if expected:
                current_sha = _sha256(source.read_bytes())
                if expected != current_sha:
                    return ActionResult(
                        False,
                        "expected_sha256 does not match source file",
                        {"relative_path": source_rel.as_posix(), "current_sha256": current_sha},
                    )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()
        shutil.move(str(source), str(destination))
        return ActionResult(
            True,
            "workspace path moved",
            {
                "project": project.slug,
                "source": source_rel.as_posix(),
                "destination": destination_rel.as_posix(),
            },
        )

    def terminal_exec(self, payload: dict[str, Any]) -> ActionResult:
        """Execute a foreground command with the local OS user's permissions.

        This is deliberately a privileged capability. The project only supplies
        the working directory; arbitrary commands can still access resources the
        OS user can access. Remote callers therefore require an explicit
        terminal.exec grant and every invocation remains audited.
        """
        project = self._project(payload)
        cwd_raw = str(payload.get("cwd") or ".")
        cwd, cwd_rel = _safe_project_path(
            project, cwd_raw, must_exist=True, allow_root=True
        )
        if not cwd.is_dir():
            return ActionResult(False, "cwd is not a directory")

        timeout = _bounded_int(
            payload.get("timeout_seconds"),
            default=900,
            minimum=1,
            maximum=1800,
            name="timeout_seconds",
        )
        env_raw = payload.get("env")
        env: dict[str, str] | None = None
        if env_raw is not None:
            if not isinstance(env_raw, dict) or len(env_raw) > 64:
                return ActionResult(False, "env must be an object with at most 64 entries")
            env = {}
            for key, value in env_raw.items():
                if (
                    not isinstance(key, str)
                    or not key
                    or len(key) > 128
                    or "\x00" in key
                    or not isinstance(value, (str, int, float, bool))
                ):
                    return ActionResult(False, "env contains an invalid key or value")
                env[key] = str(value)

        argv_raw = payload.get("argv")
        command_raw = payload.get("command")
        shell = bool(payload.get("shell", False))
        if argv_raw is not None and command_raw is not None:
            return ActionResult(False, "provide either argv or command, not both")

        if argv_raw is not None:
            if shell:
                return ActionResult(False, "shell=true is only valid with command")
            if (
                not isinstance(argv_raw, list)
                or not argv_raw
                or len(argv_raw) > 256
                or not all(
                    isinstance(item, str)
                    and item
                    and len(item) <= 8192
                    and "\x00" not in item
                    for item in argv_raw
                )
            ):
                return ActionResult(False, "argv must contain 1..256 valid strings")
            command = list(argv_raw)
        elif isinstance(command_raw, str) and command_raw.strip():
            if not shell:
                return ActionResult(False, "command requires shell=true; otherwise use argv")
            if len(command_raw) > 32768 or "\x00" in command_raw:
                return ActionResult(False, "command is too long or invalid")
            if sys.platform == "win32":
                command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command_raw]
            else:
                command = ["/bin/bash", "-lc", command_raw]
        else:
            return ActionResult(False, "argv or command is required")

        result = _run(command, cwd=cwd, timeout=timeout, env=env)
        data = dict(result.data)
        data.pop("command", None)
        data["project"] = project.slug
        data["cwd"] = "." if not cwd_rel.parts else cwd_rel.as_posix()
        data["shell"] = shell
        return ActionResult(result.ok, result.summary, data)

    def git_command(self, payload: dict[str, Any]) -> ActionResult:
        """Run a Git subcommand against one registered project.

        Git itself may execute repository hooks. This capability is therefore
        write/execute privileged and is never implied by read-only Git grants.
        """
        project = self._project(payload)
        args = payload.get("args")
        if (
            not isinstance(args, list)
            or not args
            or len(args) > 128
            or not all(
                isinstance(item, str)
                and item
                and len(item) <= 8192
                and "\x00" not in item
                for item in args
            )
        ):
            return ActionResult(False, "args must contain 1..128 valid Git arguments")

        forbidden = {"-C", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
        for index, arg in enumerate(args):
            if arg in forbidden or any(arg.startswith(value + "=") for value in forbidden if value.startswith("--")):
                return ActionResult(False, f"Git argument may escape project scope: {arg}")
            if arg == "-c":
                return ActionResult(False, "Git config overrides are not allowed through the remote boundary")

        subcommand = args[0].strip().lower()
        if subcommand not in _ALLOWED_GIT_SUBCOMMANDS:
            return ActionResult(
                False,
                f"Git subcommand is not allowed through the remote boundary: {subcommand}",
            )

        timeout = _bounded_int(
            payload.get("timeout_seconds"),
            default=300,
            minimum=1,
            maximum=1800,
            name="timeout_seconds",
        )
        result = _run(["git", "-C", str(project.root), *args], timeout=timeout)
        data = dict(result.data)
        data.pop("command", None)
        for field in ("stdout", "stderr"):
            if field in data:
                data[field] = _redact_git_sensitive_text(data[field])
        data["project"] = project.slug
        data["git_args"] = args
        return ActionResult(
            result.ok,
            _redact_git_sensitive_text(result.summary),
            data,
        )
