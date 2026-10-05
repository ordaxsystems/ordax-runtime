"""Unity-specific typed actions composed into the central ActionRegistry.

The mixin owns Unity CLI/editor/play/capture behavior but does not register any
remote action by itself. The central ActionRegistry remains the allow-list.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import json
import ntpath
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from mcp_blender_unity.config import find_unity

from .models import ActionResult
from .process_runner import run_command as _run
from .unity_assets import (
    asset_inventory as unity_asset_inventory,
    import_project_asset as unity_import_project_asset,
)
from .unity_cli import (
    cli_status as unity_cli_status,
    install_pipeline as unity_install_pipeline,
    pipeline_catalog as unity_pipeline_catalog,
    pipeline_command as unity_pipeline_command,
)
from .unity_editor_bridge import UnityEditorBridge
from .unity_knowledge import (
    capability_report as unity_capability_report,
    project_profile as unity_project_profile,
    skill_catalog as unity_skill_catalog,
)


def _unity_editor_log_candidates(project: Path) -> list[Path]:
    project_log = project.resolve() / "Logs" / "Editor.log"
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        global_log = base / "Unity" / "Editor" / "Editor.log"
    elif sys.platform == "darwin":
        global_log = Path.home() / "Library" / "Logs" / "Unity" / "Editor.log"
    else:
        global_log = Path.home() / ".config" / "unity3d" / "Editor.log"
    return [project_log, global_log]


def _windows_unity_lock_probe(lock_path: Path) -> dict[str, Any]:
    """Probe UnityLockfile without WMI/CIM or process enumeration.

    Unity keeps Temp/UnityLockfile open while a project is active. On Windows,
    opening the same path with share mode 0 fails with a sharing/lock violation
    while the Editor still owns it. A successful exclusive open means the file
    exists but is no longer actively held and can be treated as stale.
    """
    if not lock_path.exists():
        return {"state": "missing", "path": str(lock_path)}
    if sys.platform != "win32":
        return {"state": "unknown", "path": str(lock_path), "reason": "non-windows"}

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    generic_read = 0x80000000
    open_existing = 3
    file_attribute_normal = 0x80
    invalid_handle_value = ctypes.c_void_p(-1).value

    ctypes.set_last_error(0)
    handle = create_file(
        str(lock_path),
        generic_read,
        0,
        None,
        open_existing,
        file_attribute_normal,
        None,
    )
    handle_value = ctypes.cast(handle, ctypes.c_void_p).value
    if handle_value == invalid_handle_value:
        error = ctypes.get_last_error()
        if error in {32, 33}:
            return {
                "state": "active",
                "path": str(lock_path),
                "winerror": error,
            }
        return {
            "state": "unknown",
            "path": str(lock_path),
            "winerror": error,
        }

    try:
        return {"state": "stale", "path": str(lock_path)}
    finally:
        close_handle(handle)


_UNITY_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+[abfp]\d+$")
_UNITY_CHANGESET_PATTERN = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _normalize_windows_path(value: str | Path) -> str:
    # Do not resolve here: Windows may expose the same executable using an 8.3
    # path through one API and the already-canonical path through another.
    # Normalize separators/case first so identical reported paths compare
    # deterministically without extra filesystem I/O.
    return ntpath.normcase(ntpath.normpath(str(value))).casefold()


def _windows_unity_processes(timeout_seconds: float = 10.0) -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return []
    command = (
        "Get-Process Unity -ErrorAction SilentlyContinue | "
        "Select-Object Id,Path,MainWindowTitle | ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=max(2.0, float(timeout_seconds)),
        shell=False,
    )
    if completed.returncode != 0:
        raise OSError(completed.stderr[-4000:] or "Get-Process Unity failed")
    raw_text = completed.stdout.strip()
    if not raw_text:
        return []
    try:
        raw = json.loads(raw_text)
    except json.JSONDecodeError as error:
        raise OSError(f"Could not parse Unity process list: {error}") from error
    records = raw if isinstance(raw, list) else [raw]
    result: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            pid = int(record.get("Id"))
        except (TypeError, ValueError):
            continue
        result.append(
            {
                "pid": pid,
                "path": str(record.get("Path") or ""),
                "title": str(record.get("MainWindowTitle") or ""),
            }
        )
    return result


def _find_unity_hub() -> Path | None:
    candidates: list[Path] = []
    program_files = os.environ.get("ProgramFiles")
    if program_files:
        candidates.append(Path(program_files) / "Unity Hub" / "Unity Hub.exe")
    candidates.append(Path("C:/Program Files/Unity Hub/Unity Hub.exe"))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.extend(
            [
                Path(local_app_data) / "Programs" / "Unity Hub" / "Unity Hub.exe",
                Path(local_app_data) / "Unity Hub" / "Unity Hub.exe",
            ]
        )
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file():
            return candidate.resolve()
    return None


def _hub_editor_roots() -> list[Path]:
    roots: list[Path] = []
    extra_roots = os.environ.get("UNITY_EDITOR_ROOTS", "")
    for raw in extra_roots.split(";"):
        raw = raw.strip().strip('"')
        if raw:
            roots.append(Path(raw))

    roots.append(Path("C:/Program Files/Unity/Hub/Editor"))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        roots.append(Path(local_app_data) / "Unity" / "Hub" / "Editor")

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = _normalize_windows_path(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def _find_hub_editor_executable(version: str) -> Path | None:
    for root in _hub_editor_roots():
        if not root.is_dir():
            continue
        try:
            entries = sorted(root.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir():
                continue
            name = entry.name.casefold()
            target = version.casefold()
            if name != target and not name.startswith(target + "-"):
                continue
            unity = entry / "Editor" / "Unity.exe"
            if unity.is_file():
                return unity.resolve()
    return None


def _hub_editor_install_exists(version: str) -> bool:
    return _find_hub_editor_executable(version) is not None


def _windows_unity_installation_scan() -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return []

    candidates: dict[str, dict[str, Any]] = {}

    def add_candidate(path: Path, source: str, detail: str | None = None) -> None:
        try:
            if not path.is_file():
                return
        except OSError:
            return
        key = _normalize_windows_path(path)
        item = candidates.setdefault(
            key,
            {
                "editor": str(path),
                "sources": [],
            },
        )
        marker = {"source": source}
        if detail:
            marker["detail"] = detail
        if marker not in item["sources"]:
            item["sources"].append(marker)

    for root in _hub_editor_roots():
        if not root.is_dir():
            continue
        try:
            for version_dir in root.iterdir():
                if version_dir.is_dir():
                    add_candidate(
                        version_dir / "Editor" / "Unity.exe",
                        "hub-root",
                        str(root),
                    )
        except OSError:
            pass

    program_files = Path(os.environ.get("ProgramFiles") or "C:/Program Files")
    extra_patterns = [
        program_files / "Unity" / "Editor" / "Unity.exe",
        program_files / "Unity" / "Hub" / "Editor",
    ]
    add_candidate(extra_patterns[0], "program-files")

    try:
        for item in program_files.glob("Unity*"):
            if not item.is_dir():
                continue
            add_candidate(item / "Editor" / "Unity.exe", "program-files-glob", item.name)
            add_candidate(item / "Unity.exe", "program-files-glob", item.name)
    except OSError:
        pass

    try:
        import winreg
        uninstall_roots = [
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        for hive, key_path in uninstall_roots:
            try:
                with winreg.OpenKey(hive, key_path) as root_key:
                    count, _, _ = winreg.QueryInfoKey(root_key)
                    for index in range(count):
                        try:
                            sub_name = winreg.EnumKey(root_key, index)
                            with winreg.OpenKey(root_key, sub_name) as sub:
                                try:
                                    display_name = str(winreg.QueryValueEx(sub, "DisplayName")[0] or "")
                                except OSError:
                                    continue
                                if "unity" not in display_name.casefold():
                                    continue
                                try:
                                    location = str(winreg.QueryValueEx(sub, "InstallLocation")[0] or "")
                                except OSError:
                                    location = ""
                                if not location:
                                    continue
                                base = Path(location)
                                add_candidate(base / "Unity.exe", "registry", display_name)
                                add_candidate(base / "Editor" / "Unity.exe", "registry", display_name)
                        except OSError:
                            continue
            except OSError:
                continue
    except ImportError:
        pass

    return sorted(candidates.values(), key=lambda item: item["editor"].casefold())


def _unity_release_stream(version: str) -> tuple[int, int] | None:
    match = re.match(r"^(\d+)\.(\d+)\.", version)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _official_windows_unity_installer_url(version: str, changeset: str) -> str:
    if not _UNITY_VERSION_PATTERN.fullmatch(version):
        raise ValueError("invalid Unity Editor version")
    if not _UNITY_CHANGESET_PATTERN.fullmatch(changeset):
        raise ValueError("invalid Unity changeset")
    return (
        "https://download.unity3d.com/download_unity/"
        f"{changeset}/Windows64EditorInstaller/UnitySetup64-{version}.exe"
    )


def _unity_release_installer_metadata(version: str, changeset: str) -> dict[str, str]:
    if not _UNITY_VERSION_PATTERN.fullmatch(version):
        raise ValueError("invalid Unity Editor version")
    if not _UNITY_CHANGESET_PATTERN.fullmatch(changeset):
        raise ValueError("invalid Unity changeset")

    query = urllib.parse.urlencode(
        {
            "version": version,
            "platform": "WINDOWS",
            "architecture": "X86_64",
        }
    )
    api_url = (
        "https://services.api.unity.com/unity/editor/release/v1/releases?"
        + query
    )
    request = urllib.request.Request(
        api_url,
        headers={"Accept": "application/json", "User-Agent": "OrdaX-Dev-Agent"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = json.loads(response.read().decode("utf-8"))

    results = raw.get("results") if isinstance(raw, dict) else None
    if not isinstance(results, list):
        raise ValueError("Unity Releases API returned an invalid results payload")

    release = next(
        (
            item
            for item in results
            if isinstance(item, dict)
            and str(item.get("version") or "") == version
            and str(item.get("shortRevision") or "").casefold()
            == changeset.casefold()
        ),
        None,
    )
    if release is None:
        raise ValueError("Unity Releases API did not return the exact requested version and changeset")

    expected_url = _official_windows_unity_installer_url(version, changeset)
    downloads = release.get("downloads")
    if not isinstance(downloads, list):
        raise ValueError("Unity Releases API release has no downloads list")

    download = next(
        (
            item
            for item in downloads
            if isinstance(item, dict)
            and str(item.get("type") or "").upper() == "EXE"
            and str(item.get("platform") or "").upper() == "WINDOWS"
            and str(item.get("architecture") or "").upper() == "X86_64"
            and str(item.get("url") or "") == expected_url
        ),
        None,
    )
    if download is None:
        raise ValueError("Unity Releases API did not expose the expected Windows x86_64 Editor installer")

    integrity = str(download.get("integrity") or "")
    if "-" not in integrity:
        raise ValueError("Unity Releases API download has no usable integrity value")
    algorithm, encoded = integrity.split("-", 1)
    algorithm = algorithm.casefold()
    if algorithm not in {"md5", "sha1", "sha256", "sha384", "sha512"}:
        raise ValueError(f"unsupported Unity Releases API integrity algorithm: {algorithm}")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise ValueError("Unity Releases API integrity value is not valid base64") from error

    digest_size = hashlib.new(algorithm).digest_size
    if len(decoded) == digest_size:
        expected_hash = decoded.hex()
    else:
        try:
            expected_hash = decoded.decode("ascii", errors="strict").strip().casefold()
        except UnicodeDecodeError as error:
            raise ValueError(
                "Unity Releases API integrity digest is neither raw digest bytes nor ASCII hexadecimal"
            ) from error
        if (
            len(expected_hash) != digest_size * 2
            or not re.fullmatch(r"[0-9a-f]+", expected_hash)
        ):
            raise ValueError(
                "Unity Releases API integrity digest is not a valid hexadecimal digest"
            )

    return {
        "api_url": api_url,
        "url": expected_url,
        "algorithm": algorithm,
        "expected_hash": expected_hash,
        "integrity": integrity,
    }


def _verify_file_integrity(path: Path, algorithm: str, expected_hash: str) -> dict[str, Any]:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual_hash = digest.hexdigest().casefold()
    expected = expected_hash.casefold()
    return {
        "valid": actual_hash == expected,
        "algorithm": algorithm,
        "expected_hash": expected,
        "actual_hash": actual_hash,
    }


def _unity_process_ids_for_project(project: Path) -> list[int]:
    """Return Unity process IDs whose command line references this project."""
    target = str(project.resolve()).replace("\\", "/").lower()
    if sys.platform == "win32":
        def tasklist_unity_pids() -> list[int]:
            try:
                fallback = subprocess.run(
                    [
                        "tasklist",
                        "/FI",
                        "IMAGENAME eq Unity.exe",
                        "/FO",
                        "CSV",
                        "/NH",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    shell=False,
                )
            except subprocess.TimeoutExpired as error:
                raise OSError(
                    "Unity process detection timed out in both CIM and tasklist"
                ) from error

            if fallback.returncode != 0:
                raise OSError(
                    "Unity process detection failed in both CIM and tasklist"
                )

            pids: list[int] = []
            for row in csv.reader(fallback.stdout.splitlines()):
                if len(row) < 2:
                    continue
                image = row[0].strip().lower()
                if image != "unity.exe":
                    continue
                try:
                    pids.append(int(row[1].replace(",", "").strip()))
                except ValueError:
                    continue
            return pids

        try:
            completed = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='Unity.exe'\" | "
                    "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            fallback_pids = tasklist_unity_pids()
            if not fallback_pids:
                return []
            raise OSError(
                "Unity.exe is running, but project command lines could not be "
                "resolved after CIM timeout; refusing unsafe stale-lock cleanup"
            )

        if completed.returncode != 0:
            fallback_pids = tasklist_unity_pids()
            if not fallback_pids:
                return []
            raise OSError(
                "Unity.exe is running, but project command lines could not be "
                "resolved after CIM failure; refusing unsafe stale-lock cleanup"
            )
        if not completed.stdout.strip():
            return []
        try:
            raw = json.loads(completed.stdout)
        except json.JSONDecodeError:
            fallback_pids = tasklist_unity_pids()
            if not fallback_pids:
                return []
            raise OSError(
                "Unity.exe is running, but project command lines could not be "
                "parsed; refusing unsafe stale-lock cleanup"
            )
        records = raw if isinstance(raw, list) else [raw]
        result: list[int] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            command = str(record.get("CommandLine") or "").replace("\\", "/").lower()
            if target and target in command:
                try:
                    result.append(int(record.get("ProcessId")))
                except (TypeError, ValueError):
                    pass
        return result

    completed = subprocess.run(
        ["ps", "-eo", "pid=,args="],
        capture_output=True,
        text=True,
        timeout=20,
        shell=False,
    )
    if completed.returncode != 0:
        return []
    result: list[int] = []
    for line in completed.stdout.splitlines():
        normalized = line.replace("\\", "/").lower()
        if "unity" not in normalized or target not in normalized:
            continue
        head = line.strip().split(None, 1)[0]
        try:
            result.append(int(head))
        except ValueError:
            pass
    return result


class UnityActions:
    def unity_editor_terminate_stuck(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        editor = self._editor(payload)

        if sys.platform != "win32":
            return ActionResult(False, "Unity stuck-editor recovery is currently Windows-only")
        if editor.presence_is_fresh(max_age_seconds=12.0):
            return ActionResult(
                True,
                "Unity companion is already healthy; no process was terminated",
                editor.status(),
            )

        lock_probe = _windows_unity_lock_probe(editor.project_lock_path)
        if lock_probe.get("state") != "active":
            return ActionResult(
                True,
                "Unity project lock is not actively held; no process termination needed",
                {
                    **editor.status(),
                    "project_lock_probe": lock_probe,
                },
            )

        expected = find_unity(project.root)
        if expected is None:
            return ActionResult(
                False,
                "Cannot identify the Unity executable for the locked project",
                {"project_lock_probe": lock_probe},
            )
        expected_norm = _normalize_windows_path(expected)

        processes = _windows_unity_processes(
            timeout_seconds=float(payload.get("process_timeout_seconds", 10))
        )
        candidates = [
            item
            for item in processes
            if _normalize_windows_path(str(item.get("path") or ""))
            == expected_norm
        ]
        if len(candidates) != 1:
            return ActionResult(
                False,
                "Refusing to terminate Unity because the locked project does not map to exactly one matching Editor process",
                {
                    "project_lock_probe": lock_probe,
                    "expected_editor": str(expected),
                    "unity_processes": processes,
                    "matching_processes": candidates,
                },
            )

        pid = int(candidates[0]["pid"])
        stop = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                f"Stop-Process -Id {pid} -Force -ErrorAction Stop",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            shell=False,
        )
        if stop.returncode != 0:
            return ActionResult(
                False,
                f"Failed to terminate stuck Unity Editor PID {pid}",
                {
                    "pid": pid,
                    "stderr": stop.stderr[-4000:],
                    "project_lock_probe": lock_probe,
                },
            )

        deadline = time.monotonic() + max(
            5.0,
            min(float(payload.get("wait_seconds", 25)), 60.0),
        )
        final_probe = lock_probe
        while time.monotonic() < deadline:
            final_probe = _windows_unity_lock_probe(editor.project_lock_path)
            if final_probe.get("state") != "active":
                break
            time.sleep(0.35)

        if final_probe.get("state") == "active":
            return ActionResult(
                False,
                "Unity process terminated but the project lock is still actively held",
                {
                    "pid": pid,
                    "project_lock_probe": final_probe,
                },
            )

        stale_lock_cleared = False
        if final_probe.get("state") == "stale":
            try:
                editor.project_lock_path.unlink(missing_ok=True)
                stale_lock_cleared = True
                final_probe = _windows_unity_lock_probe(editor.project_lock_path)
            except OSError as error:
                return ActionResult(
                    False,
                    f"Stuck Unity process ended but stale lock cleanup failed: {error}",
                    {
                        "pid": pid,
                        "project_lock_probe": final_probe,
                    },
                )

        return ActionResult(
            True,
            "Stuck Unity Editor terminated and project lock released",
            {
                "pid": pid,
                "expected_editor": str(expected),
                "stale_lock_cleared": stale_lock_cleared,
                "project_lock_probe": final_probe,
            },
        )

    def unity_installations(self, payload: dict[str, Any]) -> ActionResult:
        if sys.platform != "win32":
            return ActionResult(False, "Unity installation scan is currently Windows-only")
        installations = _windows_unity_installation_scan()
        requested = str(payload.get("version") or "").strip()
        exact = []
        if requested:
            for item in installations:
                editor = Path(item["editor"])
                parts = [part.casefold() for part in editor.parts]
                target = requested.casefold()
                if target in parts or any(
                    part == target or part.startswith(target + "-")
                    for part in parts
                ):
                    exact.append(item)
        return ActionResult(
            True,
            "Unity installation scan completed",
            {
                "requested_version": requested or None,
                "installations": installations,
                "requested_matches": exact,
                "count": len(installations),
            },
        )

    def unity_hub_install_editor(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        if sys.platform != "win32":
            return ActionResult(False, "Unity Hub Editor installation is currently Windows-only")

        version = str(payload.get("version") or "").strip()
        changeset = str(payload.get("changeset") or "").strip()
        if not _UNITY_VERSION_PATTERN.fullmatch(version):
            return ActionResult(False, "version must be an exact Unity Editor version such as 6000.6.2f1")
        if changeset and not _UNITY_CHANGESET_PATTERN.fullmatch(changeset):
            return ActionResult(False, "changeset must be a hexadecimal Unity changeset")

        if _hub_editor_install_exists(version):
            return ActionResult(
                True,
                "Requested Unity Editor version is already installed",
                {"version": version, "already_installed": True},
            )

        hub = _find_unity_hub()
        if hub is None:
            return ActionResult(False, "Unity Hub executable was not found")

        command = [
            str(hub),
            "--",
            "--headless",
            "install",
            "--version",
            version,
        ]
        if changeset:
            command.extend(["--changeset", changeset])

        timeout = max(
            120,
            min(int(payload.get("timeout_seconds", 2400)), 3600),
        )
        completed = subprocess.run(
            command,
            cwd=str(project.root),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
        installed = _hub_editor_install_exists(version)
        data = {
            "version": version,
            "changeset": changeset or None,
            "hub": str(hub),
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-40000:],
            "stderr": completed.stderr[-20000:],
            "installed": installed,
        }
        if completed.returncode != 0 or not installed:
            return ActionResult(False, "Unity Hub did not complete the requested Editor installation", data)
        return ActionResult(True, "Unity Editor installed through Unity Hub", data)

    def unity_direct_install_editor(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        if sys.platform != "win32":
            return ActionResult(False, "Direct Unity Editor installation is currently Windows-only")

        version = str(payload.get("version") or "").strip()
        changeset = str(payload.get("changeset") or "").strip()
        if not _UNITY_VERSION_PATTERN.fullmatch(version):
            return ActionResult(False, "version must be an exact Unity Editor version such as 6000.6.2f1")
        if not _UNITY_CHANGESET_PATTERN.fullmatch(changeset):
            return ActionResult(False, "changeset is required and must be a hexadecimal Unity changeset")

        if _hub_editor_install_exists(version):
            return ActionResult(
                True,
                "Requested Unity Editor version is already installed",
                {
                    "version": version,
                    "already_installed": True,
                    "editor": str(_find_hub_editor_executable(version)),
                },
            )

        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            return ActionResult(False, "LOCALAPPDATA is unavailable; refusing an ambiguous install location")

        curl = shutil.which("curl.exe") or shutil.which("curl")
        if not curl:
            return ActionResult(False, "curl is unavailable for resumable official Unity installer download")

        installer_dir = self.config.state_dir / "unity-installers"
        installer_dir.mkdir(parents=True, exist_ok=True)
        installer = installer_dir / f"UnitySetup64-{version}.exe"
        partial = installer.with_suffix(installer.suffix + ".part")
        install_dir = Path(local_app_data) / "Unity" / "Hub" / "Editor" / version
        try:
            metadata = _unity_release_installer_metadata(version, changeset)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            return ActionResult(
                False,
                f"Could not verify Unity release metadata: {error}",
            )
        url = metadata["url"]

        integrity: dict[str, Any] | None = None
        if installer.is_file():
            try:
                integrity = _verify_file_integrity(
                    installer,
                    metadata["algorithm"],
                    metadata["expected_hash"],
                )
            except OSError as error:
                return ActionResult(
                    False,
                    f"Cached Unity installer could not be hashed: {error}",
                    {"installer": str(installer), "metadata": metadata},
                )
            if not integrity.get("valid"):
                try:
                    installer.unlink()
                except OSError as error:
                    return ActionResult(
                        False,
                        f"Cached Unity installer failed official integrity verification and could not be removed: {error}",
                        {"installer": str(installer), "integrity": integrity, "metadata": metadata},
                    )

        download_timeout = max(
            120,
            min(int(payload.get("download_timeout_seconds", 900)), 900),
        )
        if not installer.is_file():
            download_command = [
                str(curl),
                "--fail",
                "--location",
                "--retry",
                "3",
                "--retry-delay",
                "2",
            ]
            if partial.is_file() and partial.stat().st_size > 0:
                download_command.extend(["--continue-at", "-"])
            download_command.extend(
                [
                    "--output",
                    str(partial),
                    url,
                ]
            )
            try:
                downloaded = subprocess.run(
                    download_command,
                    cwd=str(project.root),
                    capture_output=True,
                    text=True,
                    timeout=download_timeout,
                    shell=False,
                )
            except subprocess.TimeoutExpired as error:
                return ActionResult(
                    False,
                    "Official Unity installer download timed out",
                    {
                        "url": url,
                        "partial": str(partial),
                        "partial_bytes": partial.stat().st_size if partial.is_file() else 0,
                        "timeout_seconds": error.timeout,
                    },
                )
            if downloaded.returncode != 0 or not partial.is_file():
                return ActionResult(
                    False,
                    "Official Unity installer download failed",
                    {
                        "url": url,
                        "partial": str(partial),
                        "returncode": downloaded.returncode,
                        "stdout": downloaded.stdout[-8000:],
                        "stderr": downloaded.stderr[-8000:],
                    },
                )
            partial.replace(installer)

        try:
            integrity = _verify_file_integrity(
                installer,
                metadata["algorithm"],
                metadata["expected_hash"],
            )
        except OSError as error:
            return ActionResult(
                False,
                f"Official Unity installer could not be hashed: {error}",
                {"url": url, "installer": str(installer), "metadata": metadata},
            )
        if not integrity.get("valid"):
            return ActionResult(
                False,
                "Official Unity installer failed Unity Releases API integrity verification",
                {
                    "url": url,
                    "installer": str(installer),
                    "integrity": integrity,
                    "metadata": metadata,
                },
            )

        install_dir.mkdir(parents=True, exist_ok=True)
        install_timeout = max(
            120,
            min(int(payload.get("install_timeout_seconds", 600)), 900),
        )
        install_command = [
            str(installer),
            "/S",
            f"/D={install_dir}",
        ]
        try:
            installed = subprocess.run(
                install_command,
                cwd=str(project.root),
                capture_output=True,
                text=True,
                timeout=install_timeout,
                shell=False,
            )
        except subprocess.TimeoutExpired as error:
            return ActionResult(
                False,
                "Direct Unity Editor installer timed out",
                {
                    "version": version,
                    "installer": str(installer),
                    "install_dir": str(install_dir),
                    "timeout_seconds": error.timeout,
                },
            )

        unity = install_dir / "Editor" / "Unity.exe"
        ok = installed.returncode == 0 and unity.is_file()
        data = {
            "version": version,
            "changeset": changeset,
            "source": "unity-download-archive",
            "url": url,
            "installer": str(installer),
            "release_metadata": metadata,
            "integrity": integrity,
            "install_dir": str(install_dir),
            "editor": str(unity),
            "returncode": installed.returncode,
            "stdout": installed.stdout[-8000:],
            "stderr": installed.stderr[-8000:],
            "installed": unity.is_file(),
        }
        if not ok:
            return ActionResult(False, "Direct Unity Editor installation did not produce the expected Editor", data)
        return ActionResult(True, "Unity Editor installed from the official signed installer", data)

    def unity_recover_resume(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        version = str(payload.get("version") or "").strip()
        changeset = str(payload.get("changeset") or "").strip()
        if not _UNITY_VERSION_PATTERN.fullmatch(version):
            return ActionResult(False, "version must be an exact Unity Editor version such as 6000.6.2f1")
        if changeset and not _UNITY_CHANGESET_PATTERN.fullmatch(changeset):
            return ActionResult(False, "changeset must be a hexadecimal Unity changeset")

        profile_before = unity_project_profile(project.root)
        current_version = str(profile_before.get("unity_version") or "").strip()
        current_stream = _unity_release_stream(current_version)
        target_stream = _unity_release_stream(version)
        if current_stream is None or target_stream is None:
            return ActionResult(
                False,
                "Could not determine the Unity release stream for safe patch recovery",
                {"current_version": current_version or None, "target_version": version},
            )
        if current_stream != target_stream:
            return ActionResult(
                False,
                "Recovery only permits an Editor patch within the project's current Unity release stream",
                {
                    "current_version": current_version,
                    "target_version": version,
                    "current_stream": ".".join(map(str, current_stream)),
                    "target_stream": ".".join(map(str, target_stream)),
                },
            )

        steps: list[dict[str, Any]] = []

        def run_step(name: str, result: ActionResult) -> ActionResult | None:
            data = dict(result.data or {})
            for key in ("stdout", "stderr", "editor_log_tail"):
                value = data.get(key)
                if isinstance(value, str) and len(value) > 8000:
                    data[key] = value[-8000:]
            steps.append(
                {
                    "step": name,
                    "ok": result.ok,
                    "summary": result.summary,
                    "data": data,
                }
            )
            if result.ok:
                return None
            return ActionResult(
                False,
                f"Unity recovery stopped at {name}: {result.summary}",
                {
                    "project": project.slug,
                    "current_version": current_version,
                    "target_version": version,
                    "failed_step": name,
                    "steps": steps,
                },
            )

        common = dict(payload)
        common["project"] = project.slug

        failure = run_step(
            "terminate_stuck_editor",
            self.unity_editor_terminate_stuck(common),
        )
        if failure:
            return failure

        install_payload = dict(common)
        install_payload["version"] = version
        if changeset:
            install_payload["changeset"] = changeset
        failure = run_step(
            "install_target_editor",
            self.unity_hub_install_editor(install_payload),
        )
        if failure:
            return failure

        failure = run_step(
            "install_companion",
            self.unity_install_companion(common),
        )
        if failure:
            return failure

        start_payload = dict(common)
        start_payload["version"] = version
        start_payload["wait_seconds"] = float(payload.get("editor_wait_seconds", 240))
        start_result = self.unity_editor_start(start_payload)
        failure = run_step("start_target_editor", start_result)
        if failure:
            return failure

        started_presence = start_result.data.get("presence") or {}
        running_version = str(started_presence.get("unityVersion") or "").strip()
        if running_version != version:
            return ActionResult(
                False,
                "Unity companion became ready from a different Editor version than requested",
                {
                    "project": project.slug,
                    "current_version": current_version,
                    "target_version": version,
                    "running_version": running_version or None,
                    "failed_step": "verify_running_editor",
                    "steps": steps,
                },
            )

        profile_after_start = unity_project_profile(project.root)
        project_version_after_start = str(
            profile_after_start.get("unity_version") or ""
        ).strip()

        compile_payload = dict(common)
        compile_payload["timeout_seconds"] = int(payload.get("compile_timeout_seconds", 1800))
        failure = run_step("compile", self.unity_compile(compile_payload))
        if failure:
            return failure

        scene_path = str(payload.get("scene_path") or "").strip()
        if scene_path:
            scene_payload = dict(common)
            scene_payload["scene_path"] = scene_path
            scene_payload["timeout_seconds"] = int(payload.get("scene_timeout_seconds", 120))
            failure = run_step("scene_open", self.unity_scene_open(scene_payload))
            if failure:
                return failure

        failure = run_step("scene_summary", self.unity_scene_summary(common))
        if failure:
            return failure

        play_payload = dict(common)
        play_payload["wait_seconds"] = float(payload.get("play_wait_seconds", 60))
        failure = run_step("play_start", self.unity_play_start(play_payload))
        if failure:
            return failure

        failure = run_step("physics_audit", self.unity_physics_audit(common))
        if failure:
            return failure
        failure = run_step("spatial_audit", self.unity_spatial_audit(common))
        if failure:
            return failure

        capture_payload = dict(common)
        for key in (
            "width",
            "height",
            "warmup_frames",
            "warmup_seconds",
            "time_scale",
            "capture_on_terminal",
            "capture_on_boss",
            "timeout_seconds",
        ):
            if key in payload:
                capture_payload[key] = payload[key]
        capture_payload["timeout_seconds"] = int(payload.get("capture_timeout_seconds", 900))
        capture_result = self.unity_capture(capture_payload)
        failure = run_step("capture", capture_result)
        if failure:
            return failure

        return ActionResult(
            True,
            "Unity recovered on the requested patch and the validation loop completed",
            {
                "project": project.slug,
                "current_version": current_version,
                "target_version": version,
                "changeset": changeset or None,
                "running_version": running_version,
                "project_version_after_start": project_version_after_start or None,
                "steps": steps,
                "artifact": capture_result.data.get("artifact"),
                "snapshot_path": capture_result.data.get("snapshot_path"),
                "play_mode_left_running": True,
            },
        )

    def _editor(self, payload: dict[str, Any]) -> UnityEditorBridge:
        project = self._project(payload)
        source = project.unity.get("companion_source")
        if source:
            source = project.path(source, must_exist=False)
        elif project.unity.get("profile") == "hordax":
            source = project.path(
                "Assets/HORDAX/Editor/OrdaXEditorAgent.cs",
                must_exist=False,
            )
        return UnityEditorBridge(project.root, source)

    def unity_project_profile(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        profile = unity_project_profile(project.root)
        ok = bool(profile.get("unity_version")) and bool(profile.get("has_project_settings"))
        return ActionResult(
            ok,
            "Unity project profile ready" if ok else "Unity project profile is incomplete",
            {
                "project": project.slug,
                **profile,
            },
        )

    def unity_capabilities(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        report = unity_capability_report(project.root)
        profile = report.get("profile") or {}
        supported = bool(profile.get("unity_6_or_newer"))
        return ActionResult(
            supported,
            "Unity 6 capability report ready"
            if supported
            else "Unity project detected, but Unity 6+ is required for the first-party skill baseline",
            {
                "project": project.slug,
                **report,
            },
        )

    def unity_skill_catalog(self, payload: dict[str, Any]) -> ActionResult:
        return ActionResult(
            True,
            "Unity first-party capability catalog ready; no Codex runtime dependency",
            unity_skill_catalog(),
        )


    def unity_cli_status(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        result = unity_cli_status(
            project.root,
            timeout_seconds=float(payload.get("timeout_seconds", 60)),
        )
        result.data.setdefault("project", project.slug)
        return result

    def unity_pipeline_install(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        result = unity_install_pipeline(
            project.root,
            force=bool(payload.get("force", False)),
            timeout_seconds=float(payload.get("timeout_seconds", 240)),
        )
        result.data.setdefault("project", project.slug)
        return result

    def unity_pipeline_catalog(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        result = unity_pipeline_catalog(
            project.root,
            timeout_seconds=float(payload.get("timeout_seconds", 60)),
        )
        result.data.setdefault("project", project.slug)
        return result

    def unity_pipeline_command(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        command_name = str(payload.get("command") or "").strip()
        if not command_name:
            return ActionResult(False, "command is required")

        arguments = payload.get("arguments", [])
        if not isinstance(arguments, list) or len(arguments) > 200:
            return ActionResult(False, "arguments must be a list with at most 200 items")
        if not all(isinstance(item, (str, int, float, bool)) for item in arguments):
            return ActionResult(False, "arguments must contain only scalar values")

        result = unity_pipeline_command(
            project.root,
            command_name,
            [str(item) for item in arguments],
            timeout_seconds=float(payload.get("timeout_seconds", 120)),
        )
        result.data.setdefault("project", project.slug)
        return result

    def unity_asset_inventory(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        raw_terms = payload.get("terms", [])
        if isinstance(raw_terms, str):
            raw_terms = [raw_terms]
        if not isinstance(raw_terms, list) or not all(isinstance(item, str) for item in raw_terms):
            return ActionResult(False, "terms must be a string or list of strings")
        report = unity_asset_inventory(
            project.root,
            terms=raw_terms,
            max_results=int(payload.get("max_results", 500)),
        )
        return ActionResult(
            bool(report.get("exists")),
            "Unity asset inventory ready",
            {"project": project.slug, **report},
        )

    def unity_asset_import(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        source_raw = str(payload.get("source_path") or "").strip()
        destination_raw = str(payload.get("destination_path") or "").strip()
        if not source_raw or not destination_raw:
            return ActionResult(False, "source_path and destination_path are required")

        try:
            source = project.path(source_raw)
            destination = project.path(destination_raw, must_exist=False)
            assets_root = (project.root / "Assets").resolve()
            destination.resolve().relative_to(assets_root)
        except (ValueError, FileNotFoundError) as error:
            return ActionResult(False, str(error))

        try:
            report = unity_import_project_asset(
                project.root,
                source_path=source,
                destination_path=destination,
                overwrite=bool(payload.get("overwrite", False)),
            )
        except (ValueError, FileNotFoundError, FileExistsError, OSError) as error:
            return ActionResult(False, f"{type(error).__name__}: {error}")

        return ActionResult(
            True,
            "Unity project asset imported" if report.get("imported") else "Unity project asset already current",
            {"project": project.slug, **report},
        )

    def _unity_live_inspection(
        self,
        payload: dict[str, Any],
        action: str,
        summary: str,
    ) -> ActionResult:
        editor = self._editor(payload)
        result = self._request_live_unity_editor(
            editor,
            action,
            {},
            timeout_seconds=float(payload.get("timeout_seconds", 60)),
        )
        if result is None:
            return ActionResult(
                False,
                "Unity Editor must be open for live scene inspection",
                editor.status(),
            )
        if result.ok:
            result.summary = summary
        return result

    def unity_scene_open(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        raw = str(payload.get("scene_path") or "").strip()
        if not raw:
            return ActionResult(False, "scene_path is required")

        scene = project.path(raw)
        if scene.suffix.lower() != ".unity":
            return ActionResult(False, "scene_path must point to a .unity scene inside the registered project")

        relative = str(scene.relative_to(project.root)).replace("\\", "/")
        editor = self._editor(payload)
        result = self._request_live_unity_editor(
            editor,
            "scene_open",
            {"scenePath": relative},
            timeout_seconds=float(payload.get("timeout_seconds", 90)),
        )
        if result is None:
            return ActionResult(False, "Unity Editor must be open to open a scene", editor.status())
        if result.ok:
            result.summary = f"Unity scene opened: {relative}"
        return result

    def unity_scene_summary(self, payload: dict[str, Any]) -> ActionResult:
        return self._unity_live_inspection(
            payload,
            "scene_summary",
            "Unity live scene summary ready",
        )

    def unity_physics_audit(self, payload: dict[str, Any]) -> ActionResult:
        return self._unity_live_inspection(
            payload,
            "physics_audit",
            "Unity physics audit passed",
        )

    def unity_spatial_audit(self, payload: dict[str, Any]) -> ActionResult:
        return self._unity_live_inspection(
            payload,
            "spatial_audit",
            "Unity spatial audit completed",
        )

    def unity_benchmark_islands_generate(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        if project.unity.get("profile") != "hordax":
            return ActionResult(False, "Island benchmark generator is currently registered for the HORDAX Unity profile")

        timeout = int(payload.get("timeout_seconds", 1200))
        artifact = (
            project.root
            / "Artifacts"
            / "Unity"
            / "IslandReferenceBenchmark"
            / "island-reference.png"
        )
        report = artifact.with_suffix(".json")
        scene = project.root / "Assets" / "HORDAX" / "Scenes" / "Benchmarks" / "IslandReferenceBenchmark.unity"

        editor = self._editor(payload)
        live = self._request_live_unity_editor(
            editor,
            "benchmark_islands_generate",
            {},
            timeout_seconds=min(timeout, 600),
        )
        if live is not None:
            if not live.ok:
                return live
            if not artifact.is_file() or not report.is_file() or not scene.is_file():
                return ActionResult(
                    False,
                    "Live Unity benchmark completed but expected artifacts are missing",
                    {
                        **live.data,
                        "artifact_exists": artifact.is_file(),
                        "report_exists": report.is_file(),
                        "scene_exists": scene.is_file(),
                    },
                )
            try:
                report_data = json.loads(report.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                report_data = {}
            return ActionResult(
                True,
                "Unity island reference benchmark generated in live Editor",
                {
                    **live.data,
                    "artifact": str(artifact),
                    "snapshot_path": str(report),
                    "scene_path": str(scene),
                    "benchmark": report_data,
                    "transport": "unity-editor-companion",
                },
            )

        script = self._bridge_script("unity-run.ps1")
        execute_method = "HORDAX.EditorTools.IslandReferenceBenchmark.Generate"
        result = _run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-ProjectPath",
                str(project.root),
                "-ExecuteMethod",
                execute_method,
            ],
            timeout=timeout,
        )
        if not result.ok:
            result.summary = "Unity island benchmark generation failed"
            return result

        if not artifact.is_file() or not report.is_file() or not scene.is_file():
            return ActionResult(
                False,
                "Unity benchmark method completed but expected artifacts are missing",
                {
                    **result.data,
                    "artifact_exists": artifact.is_file(),
                    "report_exists": report.is_file(),
                    "scene_exists": scene.is_file(),
                },
            )

        try:
            report_data = json.loads(report.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            report_data = {}

        return ActionResult(
            True,
            "Unity island reference benchmark generated",
            {
                **result.data,
                "artifact": str(artifact),
                "snapshot_path": str(report),
                "scene_path": str(scene),
                "benchmark": report_data,
                "execute_method": execute_method,
                "transport": "unity-batch",
            },
        )

    def _bridge_script(self, name: str) -> Path:
        candidates = [
            self.config.agent_repo_path / "scripts" / "windows" / name,
            self.config.bridge_path / "scripts" / "windows" / name,
        ]
        for script in candidates:
            if script.is_file():
                return script
        raise FileNotFoundError(candidates[0])

    def _request_live_unity_editor(
        self,
        editor: UnityEditorBridge,
        action: str,
        payload: dict[str, Any] | None,
        *,
        timeout_seconds: float,
    ) -> ActionResult | None:
        """Use the already-open Unity Editor safely.

        Returns None only when the project is not open, which allows the caller
        to use the batch-mode fallback. If the project is open, batch mode is
        deliberately never attempted because Unity forbids two Editors on the
        same project.
        """
        if not editor.project_appears_open():
            return None

        deadline = time.monotonic() + max(5.0, timeout_seconds)
        nudged = False

        while time.monotonic() < deadline:
            status = editor.status()
            presence = status.get("presence") or {}
            ready = (
                editor.presence_is_fresh(max_age_seconds=12.0)
                and not bool(presence.get("compiling"))
            )
            if ready:
                remaining = max(5.0, deadline - time.monotonic())
                result = editor.request(
                    action,
                    payload or {},
                    timeout_seconds=remaining,
                )
                if result is not None:
                    return result

            if not nudged:
                editor.nudge_companion(
                    wait_seconds=min(15.0, max(1.0, deadline - time.monotonic()))
                )
                nudged = True

            time.sleep(0.5)

        return ActionResult(
            False,
            "Unity Editor is open but its OrdaX companion did not become ready; "
            "batch fallback was refused to avoid a second Unity instance",
            editor.status(),
        )

    def unity_editor_start(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        editor = self._editor(payload)

        if editor.presence_is_fresh(max_age_seconds=12.0):
            return ActionResult(True, "Unity Editor already open and companion ready", editor.status())

        stale_lock_cleared = False
        if editor.project_appears_open():
            editor.nudge_companion(
                wait_seconds=min(float(payload.get("wait_seconds", 45)), 15.0)
            )
            status = editor.status()
            if status.get("presence_fresh"):
                return ActionResult(
                    True,
                    "Unity Editor project is open and companion ready",
                    status,
                )

            if sys.platform == "win32":
                lock_probe = _windows_unity_lock_probe(editor.project_lock_path)
                status["project_lock_probe"] = lock_probe
                if lock_probe.get("state") == "active":
                    return ActionResult(
                        False,
                        "Unity project lock is actively held, but the OrdaX companion is not ready",
                        status,
                    )
                if lock_probe.get("state") != "stale":
                    return ActionResult(
                        False,
                        "Unity project lock state is uncertain; refusing unsafe lock cleanup or duplicate Editor launch",
                        status,
                    )
            else:
                pids = _unity_process_ids_for_project(project.root)
                status["unity_process_ids"] = pids
                if pids:
                    return ActionResult(
                        False,
                        "Unity process is running for this project but companion is not ready",
                        status,
                    )

            # The lock exists but is not actively held. It survived a crashed
            # Editor and is safe to remove before launching a fresh instance.
            try:
                editor.project_lock_path.unlink(missing_ok=True)
                stale_lock_cleared = True
            except OSError as error:
                return ActionResult(
                    False,
                    f"Unity lock appears stale but could not be cleared: {error}",
                    status,
                )

        requested_version = str(payload.get("version") or "").strip()
        if requested_version:
            if not _UNITY_VERSION_PATTERN.fullmatch(requested_version):
                return ActionResult(False, "version must be an exact Unity Editor version such as 6000.6.2f1")
            unity = _find_hub_editor_executable(requested_version)
            if unity is None:
                return ActionResult(
                    False,
                    "Requested Unity Editor version is not installed",
                    {"requested_version": requested_version},
                )
        else:
            unity = find_unity(project.root)
            if unity is None:
                return ActionResult(False, "Unity executable not found for registered project")

        command = [str(unity), "-projectPath", str(project.root)]
        popen_kwargs = {
            "cwd": str(project.root),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "shell": False,
        }
        if sys.platform == "win32":
            creationflags = 0
            creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
            popen_kwargs["creationflags"] = creationflags
        else:
            popen_kwargs["start_new_session"] = True

        process = subprocess.Popen(command, **popen_kwargs)
        wait_seconds = max(1.0, min(240.0, float(payload.get("wait_seconds", 120))))
        deadline = time.monotonic() + wait_seconds
        status = editor.status()
        while time.monotonic() < deadline:
            status = editor.status()
            if editor.presence_is_fresh(max_age_seconds=12.0):
                return ActionResult(
                    True,
                    "Unity Editor started and OrdaX companion ready",
                    {
                        **status,
                        "launched": True,
                        "pid": process.pid,
                        "command": command,
                        "stale_lock_cleared": stale_lock_cleared,
                        "requested_version": requested_version or None,
                    },
                )
            time.sleep(0.5)

        return ActionResult(
            False,
            "Unity Editor was launched but companion did not become ready in time",
            {
                **status,
                "launched": True,
                "pid": process.pid,
                "command": command,
                "stale_lock_cleared": stale_lock_cleared,
                "requested_version": requested_version or None,
            },
        )

    def unity_editor_status(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project_path(payload)
        editor = self._editor(payload)
        if not editor.presence_is_fresh() and editor.project_appears_open():
            editor.nudge_companion(
                wait_seconds=float(payload.get("wait_seconds", 30)),
            )
        status = editor.status()
        ready = bool(status.get("presence_fresh"))
        return ActionResult(
            ready,
            "Unity Editor companion ready" if ready else "Unity Editor companion not ready",
            status,
        )

    def unity_editor_diagnostics(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        editor = self._editor(payload)

        process_ids: list[int] | None = None
        process_detection_error: str | None = None
        lock_probe: dict[str, Any] | None = None
        if sys.platform == "win32":
            lock_probe = _windows_unity_lock_probe(editor.project_lock_path)
        else:
            try:
                process_ids = _unity_process_ids_for_project(project.root)
            except OSError as error:
                process_detection_error = str(error)

        log_candidates = _unity_editor_log_candidates(project.root)
        log_path = next((path for path in log_candidates if path.is_file()), log_candidates[-1])

        max_lines = max(20, min(int(payload.get("max_lines", 160)), 500))
        max_chars = max(4096, min(int(payload.get("max_chars", 60000)), 200000))
        tail = ""
        log_size = None
        log_mtime = None
        if log_path.is_file():
            try:
                log_size = log_path.stat().st_size
                log_mtime = log_path.stat().st_mtime
                lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                tail = "\n".join(lines[-max_lines:])
                if len(tail) > max_chars:
                    tail = tail[-max_chars:]
            except OSError as error:
                tail = f"<could not read Unity Editor.log: {error}>"

        status = editor.status()
        data = {
            **status,
            "unity_process_ids": process_ids,
            "process_detection_error": process_detection_error,
            "project_lock_probe": lock_probe,
            "editor_log_path": str(log_path),
            "editor_log_candidates": [str(path) for path in log_candidates],
            "editor_log_source": "project" if log_path == log_candidates[0] else "global",
            "editor_log_exists": log_path.is_file(),
            "editor_log_size_bytes": log_size,
            "editor_log_mtime": log_mtime,
            "editor_log_tail": tail,
        }
        return ActionResult(
            True,
            "Unity Editor diagnostics ready",
            data,
        )

    def unity_refresh_editor(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project_path(payload)
        editor = self._editor(payload)

        force = bool(payload.get("force", False))
        wait_seconds = max(5.0, float(payload.get("wait_seconds", 45)))
        if editor.presence_is_fresh() and not force:
            return ActionResult(
                True,
                "Unity Editor companion already ready",
                editor.status(),
            )

        if not editor.project_appears_open():
            return ActionResult(
                False,
                "Unity project does not appear to be open",
                editor.status(),
            )

        # Prefer the typed Editor companion. This does not focus the Unity
        # window, send keyboard shortcuts, or start a second Editor process.
        if not editor.presence_is_fresh(max_age_seconds=12.0):
            editor.nudge_companion(wait_seconds=min(wait_seconds, 15.0))

        if editor.presence_is_fresh(max_age_seconds=12.0):
            try:
                presence_before = editor.presence.stat().st_mtime
            except OSError:
                presence_before = 0.0

            refresh = editor.request(
                "refresh",
                {},
                timeout_seconds=min(max(wait_seconds, 15.0), 120.0),
            )
            if refresh is not None:
                if not refresh.ok:
                    return refresh

                deadline = time.monotonic() + wait_seconds
                status = editor.status()
                while time.monotonic() < deadline:
                    status = editor.status()
                    presence = status.get("presence") or {}
                    try:
                        presence_advanced = (
                            editor.presence.stat().st_mtime > presence_before
                        )
                    except OSError:
                        presence_advanced = False

                    ready = (
                        presence_advanced
                        and editor.presence_is_fresh(max_age_seconds=12.0)
                        and not bool(presence.get("compiling"))
                    )
                    if ready:
                        status["refresh_transport"] = "unity-editor-companion"
                        status["refresh_ack"] = refresh.data
                        return ActionResult(
                            True,
                            "Unity Editor refreshed through typed companion and scripts settled",
                            status,
                        )
                    time.sleep(0.25)

                status["refresh_transport"] = "unity-editor-companion"
                status["refresh_ack"] = refresh.data
                return ActionResult(
                    False,
                    "Unity Editor accepted the typed refresh, but scripts did not settle in time",
                    status,
                )

        # Recovery fallback for projects whose companion has not loaded yet.
        # This path may focus the Editor and send Ctrl+R; it is intentionally
        # not used when the typed companion is reachable.
        script = self._bridge_script("unity-editor-refresh.ps1")
        refresh = _run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-ProjectPath",
                str(project),
            ],
            timeout=min(max(int(wait_seconds), 15), 60),
        )
        if not refresh.ok:
            return refresh

        deadline = time.monotonic() + wait_seconds
        editor.nudge_companion(wait_seconds=min(wait_seconds, 15.0))
        status = editor.status()
        while time.monotonic() < deadline:
            status = editor.status()
            presence = status.get("presence") or {}
            if (
                editor.presence_is_fresh(max_age_seconds=12.0)
                and not bool(presence.get("compiling"))
            ):
                status["refresh_transport"] = "foreground-shortcut-fallback"
                status["refresh_stdout"] = refresh.data.get("stdout", "")
                status["refresh_stderr"] = refresh.data.get("stderr", "")
                return ActionResult(
                    True,
                    "Unity Editor refreshed through recovery fallback and scripts settled",
                    status,
                )
            time.sleep(0.5)

        status["refresh_transport"] = "foreground-shortcut-fallback"
        status["refresh_stdout"] = refresh.data.get("stdout", "")
        status["refresh_stderr"] = refresh.data.get("stderr", "")
        return ActionResult(
            False,
            "Unity Editor refresh fallback was sent, but scripts did not settle in time",
            status,
        )

    def unity_play_start(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project_path(payload)
        editor = self._editor(payload)
        wait_seconds = float(payload.get("wait_seconds", 45))

        status = editor.status()
        presence = status.get("presence") or {}
        if bool(presence.get("playing")):
            return ActionResult(
                True,
                "Unity Editor is already in Play Mode",
                status,
            )

        result = self._request_live_unity_editor(
            editor,
            "play_start",
            {},
            timeout_seconds=min(wait_seconds, 30.0),
        )
        if result is None:
            return ActionResult(
                False,
                "Unity project is not open; background Play Mode requires the open Editor companion",
                editor.status(),
            )
        if not result.ok:
            return result

        deadline = time.monotonic() + max(5.0, wait_seconds)
        while time.monotonic() < deadline:
            current = editor.status()
            current_presence = current.get("presence") or {}
            if (
                editor.presence_is_fresh(max_age_seconds=12.0)
                and bool(current_presence.get("playing"))
            ):
                return ActionResult(
                    True,
                    "Unity Play Mode is running in the background",
                    current,
                )
            time.sleep(0.35)

        return ActionResult(
            False,
            "Play Mode start was requested, but Unity did not enter Play Mode in time",
            editor.status(),
        )

    def unity_play_stop(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project_path(payload)
        editor = self._editor(payload)
        wait_seconds = float(payload.get("wait_seconds", 30))

        status = editor.status()
        presence = status.get("presence") or {}
        if not bool(presence.get("playing")):
            return ActionResult(
                True,
                "Unity Editor is already outside Play Mode",
                status,
            )

        result = self._request_live_unity_editor(
            editor,
            "play_stop",
            {},
            timeout_seconds=min(wait_seconds, 20.0),
        )
        if result is None:
            return ActionResult(
                False,
                "Unity Editor companion is unavailable; focus-stealing fallback is disabled",
                editor.status(),
            )
        if not result.ok:
            return result

        deadline = time.monotonic() + max(5.0, wait_seconds)
        while time.monotonic() < deadline:
            current = editor.status()
            current_presence = current.get("presence") or {}
            if (
                editor.presence_is_fresh(max_age_seconds=12.0)
                and not bool(current_presence.get("playing"))
            ):
                return ActionResult(
                    True,
                    "Unity Play Mode stopped without foreground focus",
                    current,
                )
            time.sleep(0.35)

        return ActionResult(
            False,
            "Play Mode stop was requested, but Unity did not exit Play Mode in time",
            editor.status(),
        )

    def unity_compile(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project_path(payload)
        timeout = int(payload.get("timeout_seconds", 1800))

        editor = self._editor(payload)
        editor_result = self._request_live_unity_editor(
            editor,
            "validate",
            {},
            timeout_seconds=min(timeout, 600),
        )
        if editor_result is not None:
            return editor_result

        script = self._bridge_script("unity-run.ps1")
        return _run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-ProjectPath",
                str(project),
            ],
            timeout=timeout,
        )

    def unity_validate(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project_path(payload)
        timeout = int(payload.get("timeout_seconds", 1800))
        settings = self._project(payload).unity
        method = payload.get("execute_method", settings.get("validate_method"))
        if method and method not in settings.get("allowed_methods", []):
            return ActionResult(False, f"execute method not allowed: {method}")

        editor = self._editor(payload)
        if method and settings.get("profile") != "hordax" and editor.project_appears_open():
            return ActionResult(False, "Custom validation methods require a project-specific live companion; close the Editor to run this allow-listed batch method")
        editor_result = self._request_live_unity_editor(
            editor,
            "validate",
            {},
            timeout_seconds=min(timeout, 600),
        )
        if editor_result is not None:
            return editor_result

        if not method:
            return self.unity_compile(payload)
        script = self._bridge_script("unity-run.ps1")
        return _run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-ProjectPath",
                str(project),
                "-ExecuteMethod",
                method,
            ],
            timeout=timeout,
        )

    def unity_capture(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project_path(payload)
        output = self._capture_output(payload)
        output.parent.mkdir(parents=True, exist_ok=True)

        editor = self._editor(payload)
        editor_result = self._request_live_unity_editor(
            editor,
            "capture",
            {
                "outputPath": str(output),
                "width": int(payload.get("width", 1280)),
                "height": int(payload.get("height", 720)),
                "warmupFrames": int(payload.get("warmup_frames", 120)),
                "warmupSeconds": float(payload.get("warmup_seconds", 0.0)),
                "timeScale": float(payload.get("time_scale", 1.0)),
                "captureOnTerminal": bool(payload.get("capture_on_terminal", True)),
                "captureOnBoss": bool(payload.get("capture_on_boss", False)),
            },
            timeout_seconds=float(payload.get("timeout_seconds", 900)),
        )
        if editor_result is not None:
            if editor_result.ok:
                if not output.is_file() or output.stat().st_size == 0:
                    return ActionResult(False, "Unity reported success without producing a fresh image", editor_result.data)
                editor_result.data["artifact"] = str(output)
                self._record_capture(payload, output)
                snapshot = output.with_suffix(".json")
                if snapshot.is_file():
                    try:
                        editor_result.data["snapshot"] = json.loads(
                            snapshot.read_text(encoding="utf-8-sig")
                        )
                        editor_result.data["snapshot_path"] = str(snapshot)
                    except Exception as error:
                        editor_result.data["snapshot_error"] = str(error)
            return editor_result

        if self._project(payload).unity.get("profile") != "hordax":
            return ActionResult(False, "Open Unity and install its generic companion to capture this project")
        script = self._bridge_script("unity-capture.ps1")
        result = _run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-ProjectPath",
                str(project),
                "-OutputPath",
                str(output),
                "-Width",
                str(int(payload.get("width", 1280))),
                "-Height",
                str(int(payload.get("height", 720))),
                "-WarmupFrames",
                str(int(payload.get("warmup_frames", 120))),
            ],
            timeout=int(payload.get("timeout_seconds", 900)),
        )
        result.data["artifact"] = str(output)
        if result.ok and output.is_file() and output.stat().st_size > 0:
            self._record_capture(payload, output)
        else:
            result.ok = False
        snapshot = output.with_suffix(".json")
        if snapshot.is_file():
            try:
                result.data["snapshot"] = json.loads(snapshot.read_text(encoding="utf-8"))
                result.data["snapshot_path"] = str(snapshot)
            except Exception as error:
                result.data["snapshot_error"] = str(error)
        return result

    def unity_run_method(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project_path(payload)
        method = payload.get("execute_method", "")
        allowed = self._project(payload).unity.get("allowed_methods", [])
        if method not in allowed:
            return ActionResult(False, f"execute method not allowed: {method}")

        script = self._bridge_script("unity-run.ps1")
        return _run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-ProjectPath",
                str(project),
                "-ExecuteMethod",
                method,
            ],
            timeout=int(payload.get("timeout_seconds", 1800)),
        )


