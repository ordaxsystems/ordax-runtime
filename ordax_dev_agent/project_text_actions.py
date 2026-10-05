"""Project-scoped UTF-8 text inspection and optimistic-concurrency writes.

These actions deliberately do not provide shell access. Reads/writes stay inside
an already registered local project, reject generated/cache directories, bound
file sizes, and require an exact SHA-256 precondition before replacing an
existing file.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

from .models import ActionResult


_READABLE_SUFFIXES = {
    ".asmdef", ".asmref", ".cginc", ".compute", ".cs", ".css", ".glsl",
    ".hlsl", ".html", ".js", ".jsx", ".json", ".md", ".mjs", ".mod", ".py",
    ".shader", ".toml", ".ts", ".tsx", ".txt", ".uss", ".uxml", ".xml",
    ".yaml", ".yml",
    # Serialized Unity text may be inspected, but is intentionally not writable
    # through this generic action.
    ".unity", ".prefab", ".meta",
}
_WRITABLE_SUFFIXES = _READABLE_SUFFIXES - {".unity", ".prefab", ".meta"}
_ALLOWED_TOP_LEVEL = {
    ".cursor", ".github", "Assets", "Packages", "ProjectSettings", "automation",
    "docs", "blender", "src", "ordax_core", "ordax_studio", "ordax_dev_agent",
    "ordax_device_agent", "mcp_blender_unity", "tests", "scripts",
}
_ALLOWED_ROOT_FILES = {
    ".editorconfig", "AGENTS.md", "CLAUDE.md", "README.md", "Cargo.toml",
    "composer.json", "go.mod", "index.html", "next.config.js", "next.config.mjs",
    "package.json", "pnpm-lock.yaml", "pyproject.toml", "tsconfig.json",
    "vite.config.js", "vite.config.ts",
}
_BLOCKED_PARTS = {"Library", "Temp", "Logs", "Builds", "obj", ".git"}
_MAX_READ_BYTES = 2 * 1024 * 1024
_MAX_WRITE_BYTES = 1024 * 1024


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _relative_project_path(project, raw: str, *, must_exist: bool) -> tuple[Path, Path]:
    if not raw.strip():
        raise ValueError("path is required")
    path = project.path(raw.strip(), must_exist=must_exist)
    relative = path.relative_to(project.root.resolve())
    if not relative.parts:
        raise ValueError("path must identify a file inside the project")
    root_file_allowed = len(relative.parts) == 1 and relative.as_posix() in _ALLOWED_ROOT_FILES
    if relative.parts[0] not in _ALLOWED_TOP_LEVEL and not root_file_allowed:
        raise ValueError(
            "text actions are limited to approved project source/document paths"
        )
    if any(part in _BLOCKED_PARTS for part in relative.parts):
        raise ValueError("path is inside a generated, build, cache, or repository-control directory")
    return path, relative


class ProjectTextActions:
    def project_inventory(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        allowed = {"project", "max_depth", "max_entries"}
        unsupported = sorted(set(payload) - allowed)
        if unsupported:
            return ActionResult(False, "unsupported field(s): " + ", ".join(unsupported))

        try:
            max_depth = int(payload.get("max_depth", 4))
            max_entries = int(payload.get("max_entries", 500))
        except (TypeError, ValueError):
            return ActionResult(False, "max_depth and max_entries must be integers")
        if not 1 <= max_depth <= 8:
            return ActionResult(False, "max_depth must be between 1 and 8")
        if not 1 <= max_entries <= 1000:
            return ActionResult(False, "max_entries must be between 1 and 1000")

        blocked = {".git", ".venv", "venv", "node_modules", "Library", "Temp", "Logs", "Build", "Builds", "dist", "obj", "__pycache__", ".cache"}
        root = project.root.resolve()
        entries: list[dict[str, Any]] = []
        blend_files: list[str] = []
        documents: list[str] = []
        scripts: list[str] = []
        models: list[str] = []
        queue: list[tuple[Path, int]] = [(root, 0)]

        while queue and len(entries) < max_entries:
            directory, depth = queue.pop(0)
            if depth >= max_depth:
                continue
            try:
                children = sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
            except OSError:
                continue
            for child in children:
                if child.name in blocked or (child.name.startswith(".") and child.name not in {".cursor", ".github"}):
                    continue
                try:
                    relative = child.relative_to(root).as_posix()
                except ValueError:
                    continue
                if child.is_dir():
                    entries.append({"path": relative, "kind": "directory", "depth": depth + 1})
                    if depth + 1 < max_depth:
                        queue.append((child, depth + 1))
                elif child.is_file():
                    try:
                        size = child.stat().st_size
                    except OSError:
                        size = None
                    suffix = child.suffix.lower()
                    entries.append({"path": relative, "kind": "file", "suffix": suffix, "size_bytes": size, "depth": depth + 1})
                    if suffix == ".blend":
                        blend_files.append(relative)
                    if suffix in {".md", ".txt", ".json", ".yaml", ".yml"}:
                        documents.append(relative)
                    if suffix in {".py", ".ps1", ".js", ".ts", ".cs"}:
                        scripts.append(relative)
                    if suffix in {".blend", ".fbx", ".obj", ".glb", ".gltf", ".stl"}:
                        models.append(relative)
                if len(entries) >= max_entries:
                    break

        return ActionResult(True, "project inventory ready", {
            "project": project.slug,
            "project_root": str(root),
            "max_depth": max_depth,
            "max_entries": max_entries,
            "entry_count": len(entries),
            "truncated": len(entries) >= max_entries,
            "blend_files": blend_files[:100],
            "documents": documents[:200],
            "scripts": scripts[:200],
            "models": models[:200],
            "entries": entries,
        })

    def project_text_read(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        raw = str(payload.get("path") or "")
        path, relative = _relative_project_path(project, raw, must_exist=True)

        root_file = len(relative.parts) == 1 and relative.as_posix() in _ALLOWED_ROOT_FILES
        if path.suffix.lower() not in _READABLE_SUFFIXES and not root_file:
            return ActionResult(False, f"text file extension is not readable: {path.suffix or '<none>'}")
        if not path.is_file():
            return ActionResult(False, f"path is not a file: {relative.as_posix()}")

        data = path.read_bytes()
        if len(data) > _MAX_READ_BYTES:
            return ActionResult(
                False,
                f"text file is too large to read inline: {len(data)} > {_MAX_READ_BYTES}",
                {"path": relative.as_posix(), "size_bytes": len(data)},
            )

        has_bom = data.startswith(b"\xef\xbb\xbf")
        try:
            content = data.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            return ActionResult(False, f"file is not valid UTF-8 text: {error}")

        return ActionResult(
            True,
            "project text file ready",
            {
                "project": project.slug,
                "path": relative.as_posix(),
                "size_bytes": len(data),
                "sha256": _sha256(data),
                "encoding": "utf-8-sig" if has_bom else "utf-8",
                "content": content,
            },
        )

    def project_search_text(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        query = str(payload.get("query") or "").strip()
        if not query or len(query) > 200:
            return ActionResult(False, "query must contain between 1 and 200 characters")
        try:
            max_results = int(payload.get("max_results", 40))
            max_files = int(payload.get("max_files", 1500))
        except (TypeError, ValueError):
            return ActionResult(False, "max_results and max_files must be integers")
        if not 1 <= max_results <= 100 or not 50 <= max_files <= 5000:
            return ActionResult(False, "max_results must be 1..100 and max_files 50..5000")
        case_sensitive = bool(payload.get("case_sensitive", False))
        needle = query if case_sensitive else query.casefold()
        root = project.root.resolve()
        matches: list[dict[str, Any]] = []
        scanned = 0

        candidates: list[Path] = []
        for name in sorted(_ALLOWED_TOP_LEVEL):
            base = root / name
            if base.is_dir():
                candidates.append(base)
        for name in sorted(_ALLOWED_ROOT_FILES):
            candidate = root / name
            if candidate.is_file():
                candidates.append(candidate)

        def inspect(path: Path) -> None:
            nonlocal scanned
            if scanned >= max_files or len(matches) >= max_results:
                return
            if path.is_dir():
                for child in sorted(path.iterdir(), key=lambda item: item.name.lower()):
                    if child.name.startswith(".") or child.name in _BLOCKED_PARTS:
                        continue
                    inspect(child)
                    if scanned >= max_files or len(matches) >= max_results:
                        break
                return
            relative = path.relative_to(root).as_posix()
            root_file = "/" not in relative and relative in _ALLOWED_ROOT_FILES
            if path.suffix.lower() not in _READABLE_SUFFIXES and not root_file:
                return
            scanned += 1
            try:
                if path.stat().st_size > _MAX_READ_BYTES:
                    return
                text = path.read_text(encoding="utf-8-sig")
            except (OSError, UnicodeDecodeError):
                return
            relative = path.relative_to(root).as_posix()
            for line_number, line in enumerate(text.splitlines(), start=1):
                haystack = line if case_sensitive else line.casefold()
                if needle in haystack:
                    matches.append({
                        "path": relative,
                        "line": line_number,
                        "text": line.strip()[:500],
                    })
                    if len(matches) >= max_results:
                        break

        for candidate in candidates:
            inspect(candidate)
            if scanned >= max_files or len(matches) >= max_results:
                break
        return ActionResult(True, "project text search ready", {
            "project": project.slug,
            "query": query,
            "case_sensitive": case_sensitive,
            "scanned_files": scanned,
            "match_count": len(matches),
            "truncated": len(matches) >= max_results or scanned >= max_files,
            "matches": matches,
        })

    def project_text_read_batch(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        paths = payload.get("paths")
        if not isinstance(paths, list) or not paths or len(paths) > 16:
            return ActionResult(False, "paths must be a non-empty list with at most 16 entries")
        try:
            max_total_bytes = int(payload.get("max_total_bytes", 393216))
        except (TypeError, ValueError):
            return ActionResult(False, "max_total_bytes must be an integer")
        if not 65536 <= max_total_bytes <= 786432:
            return ActionResult(False, "max_total_bytes must be between 65536 and 786432")

        files: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        total_bytes = 0
        for raw in paths:
            if not isinstance(raw, str) or not raw.strip():
                errors.append({"path": str(raw), "error": "path must be a non-empty string"})
                continue
            result = self.project_text_read({"project": project.slug, "path": raw})
            if not result.ok:
                errors.append({"path": raw, "error": result.summary})
                continue
            size = int(result.data.get("size_bytes") or 0)
            if total_bytes + size > max_total_bytes:
                errors.append({"path": raw, "error": "batch context byte limit reached"})
                break
            files.append(result.data)
            total_bytes += size
        return ActionResult(True, "project text batch ready", {
            "project": project.slug,
            "file_count": len(files),
            "total_bytes": total_bytes,
            "max_total_bytes": max_total_bytes,
            "files": files,
            "errors": errors,
        })

    def project_text_write(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        raw = str(payload.get("path") or "")
        content = payload.get("content")
        if not isinstance(content, str):
            return ActionResult(False, "content must be a string")

        path, relative = _relative_project_path(project, raw, must_exist=False)
        if path.suffix.lower() not in _WRITABLE_SUFFIXES:
            return ActionResult(
                False,
                f"text file extension is not writable: {path.suffix or '<none>'}",
            )

        existing = path.is_file()
        if path.exists() and not existing:
            return ActionResult(False, f"path exists and is not a file: {relative.as_posix()}")

        before_bytes = path.read_bytes() if existing else None
        before_sha = _sha256(before_bytes) if before_bytes is not None else None
        expected = str(payload.get("expected_sha256") or "").strip().lower()
        create = bool(payload.get("create", False))

        if existing:
            if not expected:
                return ActionResult(
                    False,
                    "expected_sha256 is required when replacing an existing file",
                    {"path": relative.as_posix(), "current_sha256": before_sha},
                )
            if expected != before_sha:
                return ActionResult(
                    False,
                    "text file changed since it was read; refusing stale overwrite",
                    {"path": relative.as_posix(), "expected_sha256": expected, "current_sha256": before_sha},
                )
        elif not create:
            return ActionResult(
                False,
                "file does not exist; set create=true to create a new text file",
                {"path": relative.as_posix()},
            )

        keep_bom = bool(before_bytes and before_bytes.startswith(b"\xef\xbb\xbf"))
        encoded = content.encode("utf-8-sig" if keep_bom else "utf-8")
        if len(encoded) > _MAX_WRITE_BYTES:
            return ActionResult(
                False,
                f"text content is too large to write: {len(encoded)} > {_MAX_WRITE_BYTES}",
                {"path": relative.as_posix(), "size_bytes": len(encoded)},
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + f".ordax-{uuid.uuid4().hex}.tmp")
        try:
            temp.write_bytes(encoded)
            if temp.read_bytes() != encoded:
                raise IOError("temporary file verification failed")
            temp.replace(path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

        after_bytes = path.read_bytes()
        return ActionResult(
            True,
            "project text file written",
            {
                "project": project.slug,
                "path": relative.as_posix(),
                "created": not existing,
                "before_sha256": before_sha,
                "sha256": _sha256(after_bytes),
                "size_bytes": len(after_bytes),
                "encoding": "utf-8-sig" if keep_bom else "utf-8",
            },
        )

    def project_text_patch(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        raw = str(payload.get("path") or "")
        path, relative = _relative_project_path(project, raw, must_exist=True)

        if path.suffix.lower() not in _WRITABLE_SUFFIXES:
            return ActionResult(
                False,
                f"text file extension is not writable: {path.suffix or '<none>'}",
            )
        if not path.is_file():
            return ActionResult(False, f"path is not a file: {relative.as_posix()}")

        before_bytes = path.read_bytes()
        if len(before_bytes) > _MAX_READ_BYTES:
            return ActionResult(
                False,
                f"text file is too large to patch: {len(before_bytes)} > {_MAX_READ_BYTES}",
                {"path": relative.as_posix(), "size_bytes": len(before_bytes)},
            )

        before_sha = _sha256(before_bytes)
        expected = str(payload.get("expected_sha256") or "").strip().lower()
        if not expected:
            return ActionResult(
                False,
                "expected_sha256 is required when patching an existing file",
                {"path": relative.as_posix(), "current_sha256": before_sha},
            )
        if expected != before_sha:
            return ActionResult(
                False,
                "text file changed since it was read; refusing stale patch",
                {
                    "path": relative.as_posix(),
                    "expected_sha256": expected,
                    "current_sha256": before_sha,
                },
            )

        try:
            content = before_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            return ActionResult(False, f"file is not valid UTF-8 text: {error}")

        replacements = payload.get("replacements")
        if not isinstance(replacements, list) or not replacements or len(replacements) > 50:
            return ActionResult(
                False,
                "replacements must be a non-empty list with at most 50 items",
            )

        applied: list[dict[str, Any]] = []
        patched = content
        for index, replacement in enumerate(replacements):
            if not isinstance(replacement, dict):
                return ActionResult(False, f"replacement {index} must be an object")
            old = replacement.get("old")
            new = replacement.get("new")
            if not isinstance(old, str) or not old:
                return ActionResult(False, f"replacement {index}.old must be a non-empty string")
            if not isinstance(new, str):
                return ActionResult(False, f"replacement {index}.new must be a string")
            try:
                expected_count = int(replacement.get("expected_count", 1))
            except (TypeError, ValueError):
                return ActionResult(False, f"replacement {index}.expected_count must be an integer")
            if expected_count < 1 or expected_count > 1000:
                return ActionResult(False, f"replacement {index}.expected_count must be between 1 and 1000")

            actual_count = patched.count(old)
            if actual_count != expected_count:
                return ActionResult(
                    False,
                    f"replacement {index} matched {actual_count} occurrence(s), expected {expected_count}; refusing ambiguous patch",
                    {
                        "path": relative.as_posix(),
                        "replacement_index": index,
                        "actual_count": actual_count,
                        "expected_count": expected_count,
                        "current_sha256": before_sha,
                    },
                )
            patched = patched.replace(old, new)
            applied.append(
                {
                    "index": index,
                    "expected_count": expected_count,
                    "old_length": len(old),
                    "new_length": len(new),
                }
            )

        keep_bom = before_bytes.startswith(b"\xef\xbb\xbf")
        encoded = patched.encode("utf-8-sig" if keep_bom else "utf-8")
        if len(encoded) > _MAX_WRITE_BYTES:
            return ActionResult(
                False,
                f"patched text is too large to write: {len(encoded)} > {_MAX_WRITE_BYTES}",
                {"path": relative.as_posix(), "size_bytes": len(encoded)},
            )

        temp = path.with_name(path.name + f".ordax-{uuid.uuid4().hex}.tmp")
        try:
            temp.write_bytes(encoded)
            if temp.read_bytes() != encoded:
                raise IOError("temporary file verification failed")
            temp.replace(path)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

        after_bytes = path.read_bytes()
        return ActionResult(
            True,
            "project text file patched",
            {
                "project": project.slug,
                "path": relative.as_posix(),
                "before_sha256": before_sha,
                "sha256": _sha256(after_bytes),
                "size_bytes": len(after_bytes),
                "encoding": "utf-8-sig" if keep_bom else "utf-8",
                "replacement_count": len(applied),
                "replacements": applied,
            },
        )

