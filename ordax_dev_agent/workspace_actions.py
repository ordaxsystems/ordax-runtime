"""Safe workspace binding and HORDAX project archiving actions."""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .models import ActionResult
from .projects import Project
from .process_runner import run_command as _run


_SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_FAMILY_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_ALLOWED_APPS = frozenset({"blender", "unity"})
_SKIP_NAMES = frozenset({
    ".git",
    ".vs",
    ".idea",
    ".vscode",
    "__pycache__",
    "Library",
    "Temp",
    "Obj",
    "obj",
    "Build",
    "Builds",
    "Logs",
    "UserSettings",
    "MemoryCaptures",
    "Recordings",
    ".cache",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "target",
    "vendor",
    ".next",
    ".nuxt",
    "site-packages",
})
_SKIP_SUFFIXES = (".blend1", ".blend2", ".blend@", ".tmp", ".temp")


def _load_settings(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("agent settings must be a JSON object")
    return data


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def _copy_ignore(_directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name in _SKIP_NAMES or name.endswith(_SKIP_SUFFIXES):
            ignored.add(name)
    return ignored


def _remove_tree_force(path: Path) -> None:
    last_error: OSError | None = None

    def _onerror(function, target, exc_info) -> None:
        try:
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            function(target)
        except OSError:
            raise exc_info[1]

    for attempt in range(5):
        try:
            shutil.rmtree(path, onerror=_onerror)
            return
        except OSError as error:
            last_error = error
            if attempt >= 4:
                break
            try:
                for item in path.rglob("*"):
                    try:
                        os.chmod(item, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
                    except OSError:
                        pass
                os.chmod(path, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
            except OSError:
                pass
            time.sleep(0.15 * (attempt + 1))

    assert last_error is not None
    raise last_error


class WorkspaceActions:
    def _workspace_root(self) -> Path:
        configured = self.config.workspace_root
        if configured is None:
            return self.config.hordax_path.resolve().parent
        return Path(configured).expanduser().resolve()

    def workspace_repository_catalog(self, payload: dict[str, Any]) -> ActionResult:
        if payload:
            return ActionResult(False, "workspace.repository_catalog does not accept fields")

        active = self._memory_store_instance().active_project()
        active_slug = str(active.get("name") or "") if active else ""
        projects = [project for project in self.projects.values() if project.root.is_dir()]

        def inspect(project) -> dict[str, Any]:
            card = project.public()
            card["preview_mode"] = self._preview_mode(project)
            repository = self.git_repository_info({"project": project.slug, "include_status": False})
            card["repository"] = repository.data if repository.ok else {
                "is_repository": False, "error": repository.summary,
            }
            return card

        workers = max(1, min(8, len(projects)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ordax-repo") as pool:
            cards = list(pool.map(inspect, projects))

        cards.sort(key=lambda item: (item.get("slug") != active_slug, str(item.get("slug") or "").lower()))
        visible: list[dict[str, Any]] = []
        seen: set[str] = set()
        for card in cards:
            repository = card.get("repository") or {}
            if not repository.get("is_repository"):
                continue
            remote = str(repository.get("remote") or "").strip().lower().removesuffix(".git")
            identity = remote or str(repository.get("root") or card.get("path") or "").lower()
            if identity in seen:
                continue
            seen.add(identity)
            visible.append(card)
        return ActionResult(True, "repository catalog ready", {
            "active_project": active_slug or None,
            "projects": visible,
        })

    def workspace_list_projects(self, payload: dict[str, Any]) -> ActionResult:
        allowed = {"query", "max_depth", "max_entries"}
        unsupported = sorted(set(payload) - allowed)
        if unsupported:
            return ActionResult(False, "unsupported field(s): " + ", ".join(unsupported))

        query = str(payload.get("query") or "").strip().lower()
        max_depth = payload.get("max_depth", 3)
        max_entries = payload.get("max_entries", 200)
        if isinstance(max_depth, bool) or not isinstance(max_depth, int) or not (1 <= max_depth <= 5):
            return ActionResult(False, "max_depth must be an integer between 1 and 5")
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) or not (1 <= max_entries <= 500):
            return ActionResult(False, "max_entries must be an integer between 1 and 500")

        root = self._workspace_root()
        if not root.is_dir():
            return ActionResult(False, f"ORDAX workspace not found: {root}")

        entries: list[dict[str, Any]] = []
        queue: list[tuple[Path, int]] = [(root, 0)]
        deadline = time.monotonic() + 8.0
        scanned_directories = 0
        timed_out = False
        while queue and len(entries) < max_entries:
            if time.monotonic() >= deadline:
                timed_out = True
                break
            directory, depth = queue.pop(0)
            scanned_directories += 1
            if depth >= max_depth:
                continue
            try:
                children = sorted(
                    (item for item in directory.iterdir() if item.is_dir()),
                    key=lambda item: item.name.lower(),
                )
            except OSError:
                continue
            for child in children:
                if child.name in _SKIP_NAMES or child.name.startswith("."):
                    continue
                rel = child.relative_to(root).as_posix()
                if query and query not in rel.lower() and query not in child.name.lower():
                    if depth + 1 < max_depth:
                        queue.append((child, depth + 1))
                    continue
                try:
                    blend_count = sum(1 for _ in child.glob("*.blend"))
                except OSError:
                    blend_count = 0
                entries.append(
                    {
                        "relative_path": rel,
                        "name": child.name,
                        "depth": depth + 1,
                        "is_git_repo": (child / ".git").exists(),
                        "blend_files_at_root": blend_count,
                    }
                )
                if len(entries) >= max_entries:
                    break
                if depth + 1 < max_depth:
                    queue.append((child, depth + 1))
            else:
                continue
            break

        return ActionResult(
            True,
            "ORDAX workspace projects discovered",
            {
                "workspace_root": str(root),
                "query": query,
                "max_depth": max_depth,
                "entries": entries,
                "truncated": len(entries) >= max_entries,
                "timed_out": timed_out,
                "scanned_directories": scanned_directories,
            },
        )

    def workspace_project_create(self, payload: dict[str, Any]) -> ActionResult:
        allowed = {
            "slug",
            "name",
            "apps",
            "set_default",
            "git_init",
            "readme",
            "blender_scripts_dir",
        }
        unsupported = sorted(set(payload) - allowed)
        if unsupported:
            return ActionResult(False, "unsupported field(s): " + ", ".join(unsupported))

        slug = str(payload.get("slug") or "").strip().lower()
        if not _SLUG_RE.fullmatch(slug):
            return ActionResult(False, "slug must match [a-z0-9][a-z0-9_-]{0,63}")
        if slug in self.projects:
            return ActionResult(False, f"project already registered: {slug}")

        name = str(payload.get("name") or slug).strip()
        if not name or len(name) > 120 or any(ord(ch) < 32 for ch in name):
            return ActionResult(False, "name must be 1-120 printable characters")

        apps = payload.get("apps", [])
        if (
            not isinstance(apps, list)
            or not all(isinstance(app, str) and app in _ALLOWED_APPS for app in apps)
        ):
            return ActionResult(False, "apps must be a list containing only blender/unity")
        apps = list(dict.fromkeys(apps))

        set_default = payload.get("set_default", True)
        git_init = payload.get("git_init", True)
        readme = payload.get("readme", True)
        for field_name, value in (
            ("set_default", set_default),
            ("git_init", git_init),
            ("readme", readme),
        ):
            if not isinstance(value, bool):
                return ActionResult(False, f"{field_name} must be boolean")

        blender_scripts_dir = payload.get("blender_scripts_dir", "automation/blender")
        if not isinstance(blender_scripts_dir, str) or not blender_scripts_dir.strip():
            return ActionResult(False, "blender_scripts_dir must be a non-empty string")
        scripts_rel = Path(blender_scripts_dir.strip())
        if scripts_rel.is_absolute() or ".." in scripts_rel.parts:
            return ActionResult(False, "blender_scripts_dir must stay inside the project")

        workspace_root = self._workspace_root()
        try:
            workspace_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            return ActionResult(False, f"cannot create ORDAX workspace: {error}")

        target = (workspace_root / slug).resolve()
        try:
            target.relative_to(workspace_root)
        except ValueError:
            return ActionResult(False, "project path escapes the ORDAX workspace")
        if target.exists():
            return ActionResult(False, f"project directory already exists: {slug}")

        settings_path = self.config.state_dir / "agent-settings.json"
        try:
            settings = _load_settings(settings_path)
        except Exception as error:
            return ActionResult(False, f"cannot read agent settings: {error}")
        projects = settings.get("projects")
        if projects is None:
            projects = {}
        if not isinstance(projects, dict):
            return ActionResult(False, "existing projects setting must be an object")
        if slug in projects:
            return ActionResult(False, f"project already exists in settings: {slug}")

        created_files: list[str] = []
        git_initialized = False
        try:
            target.mkdir()
            ordax_dir = target / ".ordax"
            ordax_dir.mkdir()
            metadata = {
                "schema_version": 1,
                "slug": slug,
                "name": name,
                "apps": apps,
            }
            (ordax_dir / "project.json").write_text(
                json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            created_files.append(".ordax/project.json")

            if readme:
                (target / "README.md").write_text(
                    f"# {name}\n\nProjeto gerenciado pelo ORDAX Studio.\n",
                    encoding="utf-8",
                )
                created_files.append("README.md")

            (target / ".gitignore").write_text(
                "\n".join(
                    [
                        ".DS_Store",
                        "Thumbs.db",
                        "__pycache__/",
                        ".pytest_cache/",
                        ".venv/",
                        "node_modules/",
                        "dist/",
                        "build/",
                        "*.blend1",
                        "*.blend2",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            created_files.append(".gitignore")

            blender_config: dict[str, Any] = {}
            if "blender" in apps:
                (target / scripts_rel).mkdir(parents=True, exist_ok=True)
                blender_config["scripts_dir"] = scripts_rel.as_posix()

            if git_init:
                initialized = _run(["git", "init", "-b", "main"], cwd=target, timeout=30)
                if not initialized.ok:
                    initialized = _run(["git", "init"], cwd=target, timeout=30)
                    if initialized.ok:
                        symbolic = _run(
                            ["git", "symbolic-ref", "HEAD", "refs/heads/main"],
                            cwd=target,
                            timeout=30,
                        )
                        if not symbolic.ok:
                            initialized = symbolic
                if not initialized.ok:
                    detail = str(initialized.data.get("stderr") or initialized.summary).strip()
                    raise RuntimeError(f"git init failed: {detail[:1000]}")
                git_initialized = True

            projects[slug] = {
                "path": str(target),
                "apps": apps,
                "allowed_branches": [],
                "blender": blender_config,
                "unity": {},
                "preview": {},
            }
            settings["projects"] = projects
            if set_default:
                settings["default_project"] = slug
            if self.config.workspace_root is not None:
                settings["workspace_root"] = str(workspace_root)
            _atomic_json_write(settings_path, settings)

            self.projects[slug] = Project(
                slug=slug,
                root=target,
                apps=tuple(apps),
                allowed_branches=(),
                unity={},
                blender=blender_config,
                preview={},
            )
        except Exception as error:
            self.projects.pop(slug, None)
            try:
                if target.exists():
                    _remove_tree_force(target)
            except Exception:
                pass
            return ActionResult(False, f"project creation rolled back: {error}")

        memory_active = False
        memory_warning = ""
        try:
            self._memory_store_instance().set_active_project(slug, target)
            memory_active = True
        except Exception as error:
            memory_warning = f"{type(error).__name__}: {error}"

        return ActionResult(
            True,
            (
                "ORDAX project created and registered"
                if memory_active
                else "ORDAX project created; persistent memory activation needs retry"
            ),
            {
                "slug": slug,
                "project_path": str(target),
                "workspace_root": str(workspace_root),
                "apps": apps,
                "set_default": set_default,
                "git_initialized": git_initialized,
                "active_project": slug if memory_active else None,
                "memory_active": memory_active,
                "memory_warning": memory_warning or None,
                "created_files": created_files,
                "restart_required": False,
                "other_processes_must_reload": True,
            },
        )

    def workspace_bind_project(self, payload: dict[str, Any]) -> ActionResult:
        allowed = {
            "slug",
            "relative_path",
            "apps",
            "set_default",
            "blender_scripts_dir",
            "blend_file",
        }
        unsupported = sorted(set(payload) - allowed)
        if unsupported:
            return ActionResult(False, "unsupported field(s): " + ", ".join(unsupported))

        slug = str(payload.get("slug") or "").strip().lower()
        if not _SLUG_RE.fullmatch(slug):
            return ActionResult(False, "slug must match [a-z0-9][a-z0-9_-]{0,63}")

        raw_relative = payload.get("relative_path")
        if not isinstance(raw_relative, str) or not raw_relative.strip():
            return ActionResult(False, "relative_path is required")
        relative = Path(raw_relative.strip())
        if relative.is_absolute() or ".." in relative.parts:
            return ActionResult(False, "relative_path must stay inside the ORDAX workspace")

        workspace_root = self._workspace_root()
        target = (workspace_root / relative).resolve()
        try:
            target.relative_to(workspace_root)
        except ValueError:
            return ActionResult(False, "project path escapes the ORDAX workspace")
        if not target.is_dir():
            return ActionResult(False, f"project directory not found: {target}")

        apps = payload.get("apps", [])
        if (
            not isinstance(apps, list)
            or not all(isinstance(app, str) and app in _ALLOWED_APPS for app in apps)
        ):
            return ActionResult(False, "apps must be a list containing only blender/unity")

        set_default = payload.get("set_default", False)
        if not isinstance(set_default, bool):
            return ActionResult(False, "set_default must be boolean")

        blender_scripts_dir = payload.get("blender_scripts_dir", "automation/blender")
        if not isinstance(blender_scripts_dir, str) or not blender_scripts_dir.strip():
            return ActionResult(False, "blender_scripts_dir must be a non-empty string")
        scripts_rel = Path(blender_scripts_dir.strip())
        if scripts_rel.is_absolute() or ".." in scripts_rel.parts:
            return ActionResult(False, "blender_scripts_dir must stay inside the project")
        if "blender" in apps:
            (target / scripts_rel).mkdir(parents=True, exist_ok=True)

        blend_file = payload.get("blend_file")
        blender_config: dict[str, Any] = {"scripts_dir": scripts_rel.as_posix()}
        if blend_file is not None:
            if not isinstance(blend_file, str) or not blend_file.strip():
                return ActionResult(False, "blend_file must be a non-empty string")
            blend_rel = Path(blend_file.strip())
            if blend_rel.is_absolute() or ".." in blend_rel.parts:
                return ActionResult(False, "blend_file must stay inside the project")
            blend_path = (target / blend_rel).resolve()
            try:
                blend_path.relative_to(target)
            except ValueError:
                return ActionResult(False, "blend_file escapes the project")
            if not blend_path.is_file():
                return ActionResult(False, f"blend_file not found: {blend_path}")
            blender_config["blend_file"] = blend_rel.as_posix()

        settings_path = self.config.state_dir / "agent-settings.json"
        try:
            settings = _load_settings(settings_path)
        except Exception as error:
            return ActionResult(False, f"cannot read agent settings: {error}")

        projects = settings.get("projects")
        if projects is None:
            projects = {}
        if not isinstance(projects, dict):
            return ActionResult(False, "existing projects setting must be an object")

        projects[slug] = {
            "path": str(target),
            "apps": list(dict.fromkeys(apps)),
            "allowed_branches": [],
            "blender": blender_config if "blender" in apps else {},
            "unity": {},
        }
        settings["projects"] = projects
        if set_default:
            settings["default_project"] = slug

        try:
            _atomic_json_write(settings_path, settings)
        except Exception as error:
            return ActionResult(False, f"cannot write agent settings: {error}")

        registered_apps = tuple(dict.fromkeys(apps))
        self.projects[slug] = Project(
            slug=slug,
            root=target,
            apps=registered_apps,
            allowed_branches=(),
            unity={},
            blender=blender_config if "blender" in registered_apps else {},
            preview={},
        )

        memory_active = False
        memory_warning = ""
        try:
            self._memory_store_instance().set_active_project(slug, target)
            memory_active = True
        except Exception as error:
            memory_warning = f"{type(error).__name__}: {error}"

        return ActionResult(
            True,
            (
                "ORDAX workspace project bound and activated"
                if memory_active
                else "ORDAX workspace project bound; persistent memory activation needs retry"
            ),
            {
                "slug": slug,
                "project_path": str(target),
                "workspace_root": str(workspace_root),
                "apps": list(registered_apps),
                "set_default": set_default,
                "active_project": slug if memory_active else None,
                "memory_active": memory_active,
                "memory_warning": memory_warning or None,
                "restart_required": False,
                "other_processes_must_reload": True,
            },
        )

    def project_archive_to_hordax(self, payload: dict[str, Any]) -> ActionResult:
        allowed = {
            "project",
            "family",
            "archive_slug",
            "message",
            "push",
            "include_paths",
            "rebuild_cache",
        }
        unsupported = sorted(set(payload) - allowed)
        if unsupported:
            return ActionResult(False, "unsupported field(s): " + ", ".join(unsupported))

        project = self._project(payload)
        family = str(payload.get("family") or "projects").strip().lower()
        archive_slug = str(payload.get("archive_slug") or project.slug).strip().lower()
        if not _FAMILY_RE.fullmatch(family):
            return ActionResult(False, "family has invalid characters")
        if not _SLUG_RE.fullmatch(archive_slug):
            return ActionResult(False, "archive_slug has invalid characters")

        push = payload.get("push", True)
        if not isinstance(push, bool):
            return ActionResult(False, "push must be boolean")

        rebuild_cache = payload.get("rebuild_cache", False)
        if not isinstance(rebuild_cache, bool):
            return ActionResult(False, "rebuild_cache must be boolean")

        source = project.root.resolve()
        if source == self.config.hordax_path.resolve() or source.is_relative_to(self.config.hordax_path.resolve()):
            return ActionResult(False, "refusing to archive HORDAX-game into itself")

        raw_include_paths = payload.get("include_paths")
        include_paths: list[Path] | None = None
        if raw_include_paths is not None:
            if (
                not isinstance(raw_include_paths, list)
                or not raw_include_paths
                or len(raw_include_paths) > 100
                or not all(isinstance(value, str) and value.strip() for value in raw_include_paths)
            ):
                return ActionResult(
                    False,
                    "include_paths must be a non-empty list of at most 100 relative paths",
                )
            include_paths = []
            seen: set[str] = set()
            for raw in raw_include_paths:
                rel = Path(raw.strip())
                if rel.is_absolute() or ".." in rel.parts:
                    return ActionResult(False, f"include path escapes project: {raw}")
                resolved = (source / rel).resolve()
                try:
                    resolved.relative_to(source)
                except ValueError:
                    return ActionResult(False, f"include path escapes project: {raw}")
                if not resolved.exists():
                    return ActionResult(False, f"include path not found: {raw}")
                key = rel.as_posix()
                if key not in seen:
                    seen.add(key)
                    include_paths.append(rel)

        archive_clone = self.config.state_dir / "project-archive" / "HORDAX-game"
        lfs_skip_smudge = {"GIT_LFS_SKIP_SMUDGE": "1"}

        if rebuild_cache and archive_clone.exists():
            expected_parent = (self.config.state_dir / "project-archive").resolve()
            try:
                archive_clone.resolve().relative_to(expected_parent)
            except ValueError:
                return ActionResult(False, "refusing to rebuild archive cache outside state directory")
            try:
                _remove_tree_force(archive_clone)
            except Exception as error:
                return ActionResult(
                    False,
                    f"cannot rebuild HORDAX archive cache: {type(error).__name__}: {error}",
                )

        origin = _run(
            ["git", "-C", str(self.config.hordax_path), "remote", "get-url", "origin"],
            timeout=30,
        )
        if not origin.ok:
            return origin
        origin_url = origin.data.get("stdout", "").strip()
        if not origin_url:
            return ActionResult(False, "HORDAX-game origin URL is empty")

        if not (archive_clone / ".git").is_dir():
            archive_clone.parent.mkdir(parents=True, exist_ok=True)
            cloned = _run(
                ["git", "clone", "--branch", "main", "--single-branch", origin_url, str(archive_clone)],
                timeout=900,
                env=lfs_skip_smudge,
            )
            if not cloned.ok:
                return cloned
        else:
            dirty = _run(["git", "-C", str(archive_clone), "status", "--porcelain"], timeout=60, env=lfs_skip_smudge)
            if not dirty.ok:
                return dirty
            if dirty.data.get("stdout", "").strip():
                return ActionResult(
                    False,
                    "dedicated HORDAX archive clone is dirty; manual recovery required",
                    {"status": dirty.data.get("stdout", "")[-4000:]},
                )
            fetch = _run(["git", "-C", str(archive_clone), "fetch", "--quiet", "origin", "main"], timeout=300, env=lfs_skip_smudge)
            if not fetch.ok:
                return fetch
            merge = _run(
                ["git", "-C", str(archive_clone), "merge", "--ff-only", "--quiet", "origin/main"],
                timeout=300,
                env=lfs_skip_smudge,
            )
            if not merge.ok:
                return merge

        lfs = _run(["git", "lfs", "version"], timeout=30)
        if not lfs.ok:
            return ActionResult(False, "Git LFS is required for project archiving")

        destination_rel = Path("Projects") / family / archive_slug / "source"
        destination = archive_clone / destination_rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            if include_paths is None:
                if destination.exists():
                    shutil.rmtree(destination)
                shutil.copytree(source, destination, ignore=_copy_ignore)
            else:
                destination.mkdir(parents=True, exist_ok=True)
                for rel in include_paths:
                    src = source / rel
                    dst = destination / rel
                    if dst.exists():
                        if dst.is_dir():
                            shutil.rmtree(dst)
                        else:
                            dst.unlink()
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if src.is_dir():
                        shutil.copytree(src, dst, ignore=_copy_ignore)
                    else:
                        shutil.copy2(src, dst)
        except Exception as error:
            return ActionResult(False, f"project archive copy failed: {type(error).__name__}: {error}")

        add = _run(
            ["git", "-C", str(archive_clone), "add", "--all", "--", destination_rel.as_posix()],
            timeout=600,
        )
        if not add.ok:
            return add

        staged = _run(
            ["git", "-C", str(archive_clone), "diff", "--cached", "--quiet", "--", destination_rel.as_posix()],
            timeout=120,
        )
        if staged.ok:
            return ActionResult(
                True,
                "project archive already current",
                {
                    "project": project.slug,
                    "archive_path": destination_rel.as_posix(),
                    "pushed": False,
                    "changed": False,
                    "include_paths": (
                        [path.as_posix() for path in include_paths]
                        if include_paths is not None
                        else None
                    ),
                },
            )

        message = payload.get("message")
        if not isinstance(message, str) or not message.strip():
            message = f"backup({archive_slug}): update project snapshot"

        commit = _run(
            ["git", "-C", str(archive_clone), "commit", "-m", message.strip(), "--", destination_rel.as_posix()],
            timeout=600,
        )
        if not commit.ok:
            return commit

        head = _run(["git", "-C", str(archive_clone), "rev-parse", "HEAD"], timeout=30)
        if not head.ok:
            return head

        cache_compacted = False
        cache_cleanup_error = None
        if push:
            pushed = _run(["git", "-C", str(archive_clone), "push", "origin", "main"], timeout=1800)
            if not pushed.ok:
                return ActionResult(
                    False,
                    "project archive committed locally but push failed",
                    {
                        "commit": head.data.get("stdout", "").strip(),
                        "archive_path": destination_rel.as_posix(),
                        "push_error": pushed.data,
                    },
                )

            compact = _run(
                [
                    "git",
                    "-C",
                    str(archive_clone),
                    "checkout",
                    "--force",
                    "HEAD",
                    "--",
                    destination_rel.as_posix(),
                ],
                timeout=300,
                env=lfs_skip_smudge,
            )
            if compact.ok:
                pruned = _run(
                    ["git", "-C", str(archive_clone), "lfs", "prune"],
                    timeout=600,
                    env=lfs_skip_smudge,
                )
                cache_compacted = pruned.ok
                if not pruned.ok:
                    cache_cleanup_error = pruned.data
            else:
                cache_cleanup_error = compact.data

        return ActionResult(
            True,
            "project archived to HORDAX-game",
            {
                "project": project.slug,
                "archive_path": destination_rel.as_posix(),
                "commit": head.data.get("stdout", "").strip(),
                "pushed": push,
                "changed": True,
                "archive_cache_rebuilt": rebuild_cache,
                "archive_cache_compacted": cache_compacted,
                "archive_cache_cleanup_error": cache_cleanup_error,
                "include_paths": (
                    [path.as_posix() for path in include_paths]
                    if include_paths is not None
                    else None
                ),
            },
        )
