"""Safe project-scoped file maintenance with optimistic concurrency guards."""
from __future__ import annotations

from typing import Any

from .models import ActionResult
from .project_text_actions import (
    _MAX_READ_BYTES,
    _WRITABLE_SUFFIXES,
    _relative_project_path,
    _sha256,
)


def _verify_current_sha(path, expected: str) -> tuple[bool, str]:
    """Recheck a file immediately before mutation to narrow the stale-write window."""
    current = _sha256(path.read_bytes())
    return current == expected, current


class ProjectFileActions:
    def project_text_move(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        allowed = {"project", "source", "destination", "expected_sha256"}
        unsupported = sorted(set(payload) - allowed)
        if unsupported:
            return ActionResult(False, "unsupported field(s): " + ", ".join(unsupported))

        source_raw = str(payload.get("source") or "")
        destination_raw = str(payload.get("destination") or "")
        source, source_relative = _relative_project_path(
            project, source_raw, must_exist=True
        )
        destination, destination_relative = _relative_project_path(
            project, destination_raw, must_exist=False
        )

        if not source.is_file():
            return ActionResult(False, f"source is not a file: {source_relative.as_posix()}")
        if source.suffix.lower() not in _WRITABLE_SUFFIXES:
            return ActionResult(
                False,
                f"source extension is not maintainable: {source.suffix or '<none>'}",
            )
        if destination.suffix.lower() not in _WRITABLE_SUFFIXES:
            return ActionResult(
                False,
                f"destination extension is not maintainable: {destination.suffix or '<none>'}",
            )
        if source == destination:
            return ActionResult(False, "source and destination must be different")
        if destination.exists():
            return ActionResult(
                False,
                "destination already exists; implicit overwrite is not allowed",
                {"destination": destination_relative.as_posix()},
            )

        size = source.stat().st_size
        if size > _MAX_READ_BYTES:
            return ActionResult(
                False,
                f"text file is too large to move through generic maintenance: {size} > {_MAX_READ_BYTES}",
                {"source": source_relative.as_posix(), "size_bytes": size},
            )
        before = source.read_bytes()
        current_sha = _sha256(before)
        expected = str(payload.get("expected_sha256") or "").strip().lower()
        if not expected:
            return ActionResult(
                False,
                "expected_sha256 is required when moving an existing file",
                {"source": source_relative.as_posix(), "current_sha256": current_sha},
            )
        if expected != current_sha:
            return ActionResult(
                False,
                "source file changed since it was read; refusing stale move",
                {
                    "source": source_relative.as_posix(),
                    "expected_sha256": expected,
                    "current_sha256": current_sha,
                },
            )

        destination.parent.mkdir(parents=True, exist_ok=True)
        still_current, final_sha = _verify_current_sha(source, expected)
        if not still_current:
            return ActionResult(
                False,
                "source file changed immediately before move; refusing mutation",
                {
                    "source": source_relative.as_posix(),
                    "expected_sha256": expected,
                    "current_sha256": final_sha,
                },
            )
        if destination.exists():
            return ActionResult(
                False,
                "destination appeared before move; refusing implicit overwrite",
                {"destination": destination_relative.as_posix()},
            )

        source.replace(destination)
        if source.exists() or not destination.is_file():
            return ActionResult(False, "file move verification failed")
        after = destination.read_bytes()
        after_sha = _sha256(after)
        if after_sha != expected:
            return ActionResult(
                False,
                "file move changed content unexpectedly",
                {"before_sha256": expected, "after_sha256": after_sha},
            )

        return ActionResult(
            True,
            "project text file moved",
            {
                "project": project.slug,
                "source": source_relative.as_posix(),
                "destination": destination_relative.as_posix(),
                "sha256": after_sha,
                "size_bytes": len(after),
            },
        )

    def project_text_delete(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        allowed = {"project", "path", "expected_sha256"}
        unsupported = sorted(set(payload) - allowed)
        if unsupported:
            return ActionResult(False, "unsupported field(s): " + ", ".join(unsupported))

        raw = str(payload.get("path") or "")
        path, relative = _relative_project_path(project, raw, must_exist=True)
        if not path.is_file():
            return ActionResult(False, f"path is not a file: {relative.as_posix()}")
        if path.suffix.lower() not in _WRITABLE_SUFFIXES:
            return ActionResult(
                False,
                f"text file extension is not maintainable: {path.suffix or '<none>'}",
            )

        size = path.stat().st_size
        if size > _MAX_READ_BYTES:
            return ActionResult(
                False,
                f"text file is too large to delete through generic maintenance: {size} > {_MAX_READ_BYTES}",
                {"path": relative.as_posix(), "size_bytes": size},
            )
        before = path.read_bytes()
        current_sha = _sha256(before)
        expected = str(payload.get("expected_sha256") or "").strip().lower()
        if not expected:
            return ActionResult(
                False,
                "expected_sha256 is required when deleting an existing file",
                {"path": relative.as_posix(), "current_sha256": current_sha},
            )
        if expected != current_sha:
            return ActionResult(
                False,
                "text file changed since it was read; refusing stale delete",
                {
                    "path": relative.as_posix(),
                    "expected_sha256": expected,
                    "current_sha256": current_sha,
                },
            )

        still_current, final_sha = _verify_current_sha(path, expected)
        if not still_current:
            return ActionResult(
                False,
                "text file changed immediately before delete; refusing mutation",
                {
                    "path": relative.as_posix(),
                    "expected_sha256": expected,
                    "current_sha256": final_sha,
                },
            )

        path.unlink()
        if path.exists():
            return ActionResult(False, "file delete verification failed")
        return ActionResult(
            True,
            "project text file deleted",
            {
                "project": project.slug,
                "path": relative.as_posix(),
                "deleted_sha256": expected,
                "size_bytes": len(before),
            },
        )
