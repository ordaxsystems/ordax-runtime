"""Local project allow-list shared by cloud jobs and MCP clients."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
