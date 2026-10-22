"""Local project allow-list shared by cloud jobs and MCP clients."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .git_readonly import readonly_git_environment


_GITHUB_REPOSITORY_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$"
)


@dataclass(frozen=True)
class Project:
    slug: str
    root: Path
    apps: tuple[str, ...] = ()
    allowed_branches: tuple[str, ...] = ()
    unity: dict[str, Any] = field(default_factory=dict)
    blender: dict[str, Any] = field(default_factory=dict)
    preview: dict[str, Any] = field(default_factory=dict)

    def path(self, value: str, *, must_exist: bool = True) -> Path:
        candidate = Path(value).expanduser()
        resolved = (candidate if candidate.is_absolute() else self.root / candidate).resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise ValueError(f"path is outside registered project: {self.slug}")
        if must_exist and not resolved.exists():
            raise FileNotFoundError(resolved)
        return resolved

    def public(self) -> dict:
        return {"slug": self.slug, "path": str(self.root), "apps": list(self.apps),
                "available": self.root.is_dir(), "allowed_branches": list(self.allowed_branches),
                "unity": self.unity, "blender": self.blender, "preview": self.preview}


def canonical_github_repository_full_name(value: str) -> str | None:
    """Return canonical ``owner/repository`` for supported GitHub remote forms.

    This parser deliberately accepts only github.com remotes. Product project
    authority is GitHub-backed today, so a non-GitHub remote must fail closed
    instead of being guessed from a basename.
    """
    raw = str(value or "").strip()
    if not raw:
        return None

    # Platform Product authority already provides canonical owner/repository.
    # Accept that exact form without relaxing URL/hostname validation.
    if _GITHUB_REPOSITORY_RE.fullmatch(raw):
        return raw.lower()

    candidate: str | None = None
    if raw.startswith("git@github.com:"):
        candidate = raw[len("git@github.com:"):]
    else:
        try:
            parsed = urlparse(raw)
        except ValueError:
            return None
        if (parsed.hostname or "").lower() != "github.com":
            return None
        candidate = parsed.path.lstrip("/")

    candidate = candidate.removesuffix(".git").strip("/")
    if not _GITHUB_REPOSITORY_RE.fullmatch(candidate):
        return None
    owner, repository = candidate.split("/", 1)
    return f"{owner}/{repository}".lower()


def project_repository_full_name(project: Project) -> str | None:
    """Read one registered project's canonical GitHub origin without a shell."""
    if not project.root.is_dir():
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(project.root), "--no-optional-locks",
             "-c", "core.fsmonitor=false", "remote", "get-url", "origin"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
            shell=False,
            env=readonly_git_environment(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return canonical_github_repository_full_name(completed.stdout.strip())


def resolve_project_for_repository(config, repository_full_name: str) -> Project:
    """Bind canonical Product project authority to exactly one local project.

    The caller supplies a repository identity that came from the authenticated
    Platform project resolver. Local slugs are never treated as authority here.
    """
    canonical = canonical_github_repository_full_name(repository_full_name)
    if canonical is None:
        raise RuntimeError("canonical Product project repository is invalid")

    matches = [
        project
        for project in load_projects(config).values()
        if project_repository_full_name(project) == canonical
    ]
    if not matches:
        raise RuntimeError("authorized Product repository is not bound to a registered local project")
    if len(matches) != 1:
        raise RuntimeError("authorized Product repository maps to multiple registered local projects")
    return matches[0]


def load_projects(config) -> dict[str, Project]:
    # Explicit projects replace legacy defaults, including an intentionally empty registry.
    entries = config.projects
    if entries is None:
        entries = {"hordax": {
            "path": str(config.hordax_path), "apps": ["unity", "blender"],
            "allowed_branches": ["dev/unity6-gameplay-pass-1", "upgrade/unity-6000.6.1f1"],
            "unity": {"profile": "hordax", "validate_method": "HORDAX.EditorTools.CiValidation.Run",
                      "allowed_methods": ["HORDAX.EditorTools.CiValidation.Run",
                                          "HORDAX.EditorTools.AutomationCapture.CapturePrototype"]},
        }}
    if not isinstance(entries, dict):
        raise ValueError("projects must be an object keyed by project slug")
    result = {}
    for slug, entry in entries.items():
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", slug):
            raise ValueError(f"invalid project slug: {slug}")
        if not isinstance(entry, dict) or not entry.get("path"):
            raise ValueError(f"project {slug} needs a local path")
        root = Path(entry["path"]).expanduser()
        if not root.is_absolute():
            raise ValueError(f"project {slug} path must be absolute")
        apps = entry.get("apps", [])
        branches = entry.get("allowed_branches", [])
        if not isinstance(apps, list) or not all(isinstance(a, str) for a in apps):
            raise ValueError(f"project {slug} apps must be a list of strings")
        if not isinstance(branches, list) or not all(isinstance(b, str) and not b.startswith('-') for b in branches):
            raise ValueError(f"project {slug} allowed_branches must be a list of branch names")
        for app in ("unity", "blender", "preview"):
            if not isinstance(entry.get(app, {}), dict):
                raise ValueError(f"project {slug} {app} must be an object")
        result[slug] = Project(
            slug, root.resolve(), tuple(apps), tuple(branches),
            entry.get("unity", {}), entry.get("blender", {}), entry.get("preview", {}),
        )
    return result
