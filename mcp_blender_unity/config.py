from __future__ import annotations

import os
import platform
import re
from pathlib import Path


_VERSION_RE = re.compile(r"^m_EditorVersion:\s*(\S+)\s*$", re.MULTILINE)
_API_COMPATIBILITY_RE = re.compile(r"^\s*apiCompatibilityLevel:\s*(\d+)\s*$", re.MULTILINE)


def _unity_editor_roots() -> list[Path]:
    """Return supported Unity Hub editor roots in deterministic order."""
    roots: list[Path] = []

    configured = os.getenv("UNITY_EDITOR_ROOTS", "")
    if configured:
        roots.extend(Path(item).expanduser() for item in configured.split(os.pathsep) if item)

    if platform.system() == "Windows":
        roots.append(Path("C:/Program Files/Unity/Hub/Editor"))
        local_app_data = os.getenv("LOCALAPPDATA")
        user_profile = os.getenv("USERPROFILE")
        if local_app_data:
            roots.append(Path(local_app_data) / "Unity/Hub/Editor")
        if user_profile:
            roots.append(Path(user_profile) / "Unity/Hub/Editor")
    elif platform.system() == "Darwin":
        roots.append(Path("/Applications/Unity/Hub/Editor"))
    else:
        roots.extend(
            [
                Path.home() / "Unity/Hub/Editor",
                Path("/opt/unityhub"),
                Path("/opt/Unity/Hub/Editor"),
            ]
        )

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not str(root):
            continue
        key = str(root).lower()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def _existing(path: str | None) -> Path | None:
    if not path:
        return None
    candidate = Path(path).expanduser()
    return candidate if candidate.is_file() else None


def _newest(candidates: list[Path]) -> Path | None:
    existing = [path for path in candidates if path.is_file()]
    if not existing:
        return None
    return sorted(existing, key=lambda path: str(path).lower(), reverse=True)[0]


def _unity_variant_matches(directory_name: str, required_version: str | None) -> bool:
    """Return whether a Hub directory represents the requested editor version."""
    if not required_version:
        return True
    escaped = re.escape(required_version)
    return re.fullmatch(rf"{escaped}(?:[-_].+)?", directory_name, re.IGNORECASE) is not None


def _unity_install_directory(unity: Path) -> str:
    """Return the Hub editor directory name containing an executable."""
    # Windows/Linux: <version-or-variant>/Editor/Unity[.exe].
    if unity.parent.name.lower() == "editor":
        return unity.parent.parent.name

    # macOS: <version>/Unity.app/Contents/MacOS/Unity.
    for parent in unity.parents:
        if parent.name.lower().endswith(".app"):
            return parent.parent.name
    return unity.parent.name


def _unity_candidate_paths(required_version: str | None) -> list[Path]:
    """Enumerate all matching Hub variants, without selecting one prematurely."""
    candidates: list[Path] = []
    system = platform.system()

    for root in _unity_editor_roots():
        if not root.is_dir():
            continue

        for version_dir in root.iterdir():
            if not version_dir.is_dir() or not _unity_variant_matches(version_dir.name, required_version):
                continue

            if system == "Darwin":
                candidates.append(version_dir / "Unity.app" / "Contents" / "MacOS" / "Unity")
            else:
                executable = "Unity.exe" if system == "Windows" else "Unity"
                candidates.append(version_dir / "Editor" / executable)

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def read_unity_project_version(project: str | Path | None) -> str | None:
    if project is None:
        return None

    root = Path(project).expanduser().resolve()
    version_file = root / "ProjectSettings" / "ProjectVersion.txt"
    if not version_file.is_file():
        return None

    text = version_file.read_text(encoding="utf-8", errors="replace")
    match = _VERSION_RE.search(text)
    return match.group(1) if match else None


def read_unity_api_compatibility_level(project: str | Path | None) -> int | None:
    """Read Unity's serialized API compatibility level from ProjectSettings."""
    if project is None:
        return None

    root = Path(project).expanduser().resolve()
    settings_file = root / "ProjectSettings" / "ProjectSettings.asset"
    if not settings_file.is_file():
        return None

    text = settings_file.read_text(encoding="utf-8", errors="replace")
    match = _API_COMPATIBILITY_RE.search(text)
    return int(match.group(1)) if match else None


def find_blender() -> Path | None:
    explicit = _existing(os.getenv("BLENDER_EXE"))
    if explicit:
        return explicit

    system = platform.system()
    if system == "Windows":
        root = Path("C:/Program Files/Blender Foundation")
        return _newest(list(root.glob("Blender */blender.exe"))) if root.exists() else None

    if system == "Darwin":
        path = Path("/Applications/Blender.app/Contents/MacOS/Blender")
        return path if path.is_file() else None

    for path in (Path("/usr/bin/blender"), Path("/usr/local/bin/blender")):
        if path.is_file():
            return path

    return None


def resolve_unity(
    project: str | Path | None = None,
    required_version: str | None = None,
) -> dict:
    """Resolve Unity once, evaluating every compatible installation first.

    A healthy candidate wins over an unhealthy one. Among candidates with the
    same health, the exact Hub directory name wins, followed by a stable
    case-insensitive directory-name order. UNITY_EXE is an explicit override:
    it is never replaced by an automatic fallback, even when invalid.
    """
    required = required_version or read_unity_project_version(project)
    explicit_raw = os.getenv("UNITY_EXE")

    if explicit_raw:
        explicit = Path(explicit_raw).expanduser()
        diagnostics = unity_installation_diagnostics(explicit, project)
        directory_name = _unity_install_directory(explicit)
        version_compatible = _unity_variant_matches(directory_name, required)
        diagnostics["version_compatible"] = version_compatible

        invalid = not diagnostics["installation_healthy"]
        return {
            "selected_path": str(explicit),
            "source": "explicit",
            "required_version": required,
            "candidates": [
                {
                    "path": str(explicit),
                    "directory_name": directory_name,
                    "healthy": diagnostics["installation_healthy"],
                    "missing_components": diagnostics["missing_components"],
                }
            ],
            "selected_diagnostics": diagnostics,
            "installation_healthy": diagnostics["installation_healthy"],
            "explicit_invalid": invalid,
            "explicit_error": (
                "UNITY_EXE points to an invalid Unity installation: "
                + "; ".join(diagnostics["missing_components"])
                if invalid
                else None
            ),
        }

    records: list[dict] = []
    for candidate in _unity_candidate_paths(required):
        diagnostics = unity_installation_diagnostics(candidate, project)
        records.append(
            {
                "path": str(candidate),
                "directory_name": _unity_install_directory(candidate),
                "healthy": diagnostics["installation_healthy"],
                "missing_components": diagnostics["missing_components"],
                "diagnostics": diagnostics,
            }
        )

    records.sort(
        key=lambda item: (
            0 if item["healthy"] else 1,
            0 if required and item["directory_name"].lower() == required.lower() else 1,
            item["directory_name"].lower(),
            item["path"].lower(),
        )
    )

    selected = records[0] if records else None
    selected_diagnostics = selected["diagnostics"] if selected else unity_installation_diagnostics(None, project)
    return {
        "selected_path": selected["path"] if selected else None,
        "source": "automatic",
        "required_version": required,
        "candidates": [
            {key: value for key, value in record.items() if key != "diagnostics"}
            for record in records
        ],
        "selected_diagnostics": selected_diagnostics,
        "installation_healthy": selected_diagnostics["installation_healthy"],
        "explicit_invalid": False,
        "explicit_error": None,
    }


def find_unity(project: str | Path | None = None) -> Path | None:
    resolved = resolve_unity(project)
    selected = resolved["selected_path"]
    return Path(selected) if selected else None


def unity_installation_diagnostics(
    unity: str | Path | None,
    project: str | Path | None = None,
) -> dict:
    """Describe editor files needed by the selected project's compilation profile."""
    if unity is None:
        return {
            "editor_available": False,
            "editor_path": None,
            "editor_data_path": None,
            "api_compatibility_level": read_unity_api_compatibility_level(project),
            "reference_assemblies_required": None,
            "reference_assemblies_available": None,
            "reference_assemblies_path": None,
            "package_manager_path": None,
            "package_manager_available": None,
            "missing_components": ["Unity.exe"],
            "installation_healthy": False,
        }

    editor = Path(unity).expanduser().resolve()
    editor_dir = editor.parent
    data_dir = editor_dir / "Data"
    api_level = read_unity_api_compatibility_level(project)
    required_version = read_unity_project_version(project)
    installed_version = _unity_install_directory(editor)
    references = data_dir / "UnityReferenceAssemblies" / "unity-4.8-api" / "Facades"
    package_manager = data_dir / "Resources" / "PackageManager" / "Server" / "UnityPackageManager.exe"

    missing: list[str] = []
    if not editor.is_file():
        missing.append(str(editor))
    if not data_dir.is_dir():
        missing.append(str(data_dir))

    version_compatible = _unity_variant_matches(installed_version, required_version)
    if required_version and not version_compatible:
        missing.append(
            f"Unity editor version {installed_version} does not match required {required_version}"
        )

    references_required = api_level == 6 if api_level is not None else None
    references_available = references.is_dir() if references_required is not False else None
    if references_required and not references_available:
        missing.append(str(references))

    package_manager_available = package_manager.is_file()
    if not package_manager_available:
        missing.append(str(package_manager))

    return {
        "editor_available": editor.is_file(),
        "editor_path": str(editor),
        "editor_data_path": str(data_dir),
        "required_unity_version": required_version,
        "installed_unity_version": installed_version,
        "version_compatible": version_compatible,
        "api_compatibility_level": api_level,
        "reference_assemblies_required": references_required,
        "reference_assemblies_path": str(references),
        "reference_assemblies_available": references_available,
        "package_manager_path": str(package_manager),
        "package_manager_available": package_manager_available,
        "missing_components": missing,
        "installation_healthy": not missing,
    }


def default_unity_project() -> Path | None:
    raw = os.getenv("DEFAULT_UNITY_PROJECT")
    if not raw:
        return None

    path = Path(raw).expanduser().resolve()
    return path if path.is_dir() else None
