"""Safe Git status/diff/synchronization typed actions."""
from __future__ import annotations

import json
import subprocess
from typing import Any

from .models import ActionResult
from .process_runner import run_command as _run


class GitActions:
    def git_repository_info(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project_path(payload)
        include_status = payload.get("include_status", True)
        if not isinstance(include_status, bool):
            return ActionResult(False, "include_status must be boolean")
        if not (project / ".git").exists():
            return ActionResult(True, "project is not a Git repository", {
                "is_repository": False, "path": str(project),
            })

        branch_result = _run(
            ["git", "-C", str(project), "branch", "--show-current"], timeout=4,
        )
        branch = str(branch_result.data.get("stdout") or "").strip() if branch_result.ok else ""
        remote_result = _run(
            ["git", "-C", str(project), "config", "--get", "remote.origin.url"], timeout=4,
        )
        remote = str(remote_result.data.get("stdout") or "").strip() if remote_result.ok else ""

        dirty: bool | None = None
        changed_entries: int | None = None
        status_available = False
        if include_status:
            status = _run([
                "git", "-C", str(project), "status", "--porcelain=v1",
                "--untracked-files=normal",
            ], timeout=10)
            if status.ok:
                changes = [line for line in str(status.data.get("stdout") or "").splitlines() if line.strip()]
                dirty = bool(changes)
                changed_entries = len(changes)
                status_available = True

        return ActionResult(True, "Git repository identity ready", {
            "is_repository": True,
            "path": str(project),
            "root": str(project.resolve()),
            "branch": branch or "detached",
            "remote": remote or None,
            "has_origin": bool(remote),
            "dirty": dirty,
            "changed_entries": changed_entries,
            "status_available": status_available,
        })

    def git_quick_status(self, payload: dict[str, Any]) -> ActionResult:
        """Bounded tracked-file status for responsive Studio health surfaces."""
        project = self._project_path(payload)
        result = _run([
            "git", "-C", str(project), "status", "--short", "--untracked-files=no",
        ], timeout=3)
        if result.ok:
            result.summary = "quick Git status ready"
        return result

    def git_status(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project_path(payload)
        return _run(["git", "-C", str(project), "status", "--short"], timeout=60)

    def git_diff(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project_path(payload)
        raw_paths = payload.get("paths", [])
        if raw_paths is None:
            raw_paths = []
        if not isinstance(raw_paths, list) or len(raw_paths) > 50:
            return ActionResult(False, "paths must be a list with at most 50 entries")

        paths: list[str] = []
        project_root = project.resolve()
        for raw in raw_paths:
            if not isinstance(raw, str) or not raw.strip():
                return ActionResult(False, "paths must contain only non-empty strings")
            candidate = (project_root / raw.strip()).resolve()
            try:
                candidate.relative_to(project_root)
            except ValueError:
                return ActionResult(False, f"path escapes project root: {raw}")
            paths.append(raw.strip().replace("\\", "/"))

        command = ["git", "-C", str(project), "diff", "--no-ext-diff", "--no-color"]
        if paths:
            command += ["--", *paths]
        result = _run(command, timeout=60)
        if result.ok:
            result.summary = "tracked Git diff inspected"
        return result

    def git_sync(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project_path(payload)
        allowed = self._project(payload).allowed_branches
        branch = payload.get("branch") or (allowed[0] if allowed else None)
        if branch not in allowed:
            return ActionResult(False, f"branch not allowed: {branch}")

        current = _run(
            ["git", "-C", str(project), "rev-parse", "--abbrev-ref", "HEAD"],
            timeout=30,
        )
        if not current.ok:
            return current
        current_branch = current.data["stdout"].strip()

        # Fetch first so we can distinguish a genuinely dirty tree from a local
        # worktree that already contains exactly the content now committed to the
        # authorized remote branch (for example after Unity CLI edits manifest.json).
        fetch = _run(
            ["git", "-C", str(project), "fetch", "--quiet", "origin", branch],
            timeout=180,
        )
        if not fetch.ok:
            return fetch

        status = _run(
            ["git", "-C", str(project), "status", "--porcelain", "--untracked-files=no"],
            timeout=60,
        )
        if not status.ok:
            return status

        tracked_status = status.data.get("stdout", "").strip()
        reconciled_remote_worktree = False
        if tracked_status:
            if current_branch != branch:
                return ActionResult(
                    False,
                    "local tracked changes exist on a different branch; sync refused",
                    {"status": tracked_status, "branch": current_branch},
                )

            changed_paths_result = _run(
                [
                    "git",
                    "-C",
                    str(project),
                    "diff",
                    "--name-only",
                    "HEAD",
                    "--",
                ],
                timeout=60,
            )
            if not changed_paths_result.ok:
                return changed_paths_result
            changed_paths = [
                line.strip()
                for line in changed_paths_result.data.get("stdout", "").splitlines()
                if line.strip()
            ]
            if not changed_paths:
                return ActionResult(
                    False,
                    "tracked status was dirty but no modified tracked paths could be resolved",
                    {"status": tracked_status},
                )

            # Compare only paths modified locally. Remote changes on other paths
            # must not make a safe reconciliation look divergent merely because
            # the local branch is behind the authorized remote branch.
            matches_remote = subprocess.run(
                [
                    "git",
                    "-C",
                    str(project),
                    "diff",
                    "--quiet",
                    f"origin/{branch}",
                    "--",
                    *changed_paths,
                ],
                capture_output=True,
                text=True,
                timeout=60,
                shell=False,
            )
            if matches_remote.returncode not in (0, 1):
                return ActionResult(
                    False,
                    "could not compare local worktree with remote branch",
                    {
                        "returncode": matches_remote.returncode,
                        "stdout": matches_remote.stdout[-4000:],
                        "stderr": matches_remote.stderr[-4000:],
                        "changed_paths": changed_paths,
                    },
                )

            ahead = _run(
                [
                    "git",
                    "-C",
                    str(project),
                    "rev-list",
                    "--left-right",
                    "--count",
                    f"HEAD...origin/{branch}",
                ],
                timeout=30,
            )
            if not ahead.ok:
                return ahead
            try:
                local_ahead, _remote_ahead = [
                    int(value) for value in ahead.data.get("stdout", "").split()
                ]
            except (TypeError, ValueError):
                return ActionResult(
                    False,
                    "could not parse local/remote Git divergence",
                    {"stdout": ahead.data.get("stdout", "")},
                )

            if matches_remote.returncode != 0 or local_ahead != 0:
                return ActionResult(
                    False,
                    "local tracked changes differ from the authorized remote branch; sync refused",
                    {
                        "status": tracked_status,
                        "local_ahead": local_ahead,
                        "matches_remote_target": matches_remote.returncode == 0,
                        "changed_paths": changed_paths,
                    },
                )

            reset = _run(
                ["git", "-C", str(project), "reset", "--hard", f"origin/{branch}"],
                timeout=120,
            )
            if not reset.ok:
                return reset
            reconciled_remote_worktree = True

        if current_branch != branch:
            exists = _run(
                [
                    "git",
                    "-C",
                    str(project),
                    "show-ref",
                    "--verify",
                    "--quiet",
                    f"refs/heads/{branch}",
                ],
                timeout=30,
            )
            switch = (
                ["git", "-C", str(project), "switch", branch]
                if exists.ok
                else [
                    "git",
                    "-C",
                    str(project),
                    "switch",
                    "--track",
                    "-c",
                    branch,
                    f"origin/{branch}",
                ]
            )
            checkout = _run(switch, timeout=120)
            if not checkout.ok:
                return checkout

        merge = _run(
            [
                "git",
                "-C",
                str(project),
                "merge",
                "--ff-only",
                "--quiet",
                f"origin/{branch}",
            ],
            timeout=120,
        )
        if not merge.ok:
            return merge

        head = _run(["git", "-C", str(project), "rev-parse", "HEAD"], timeout=30)
        return ActionResult(
            head.ok,
            "repository synchronized" if head.ok else "sync completed but HEAD lookup failed",
            {
                "branch": branch,
                "head": head.data.get("stdout", "").strip(),
                "reconciled_remote_worktree": reconciled_remote_worktree,
            },
        )

