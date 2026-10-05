from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from mcp_blender_unity.config import find_blender

from .models import ActionResult


EXPECTED_PROTOCOL_VERSION = 9
LEGACY_MAINTENANCE_OPERATIONS = {
    "checkpoint_create",
    "checkpoint_restore",
    "checkpoint_list",
    "save",
    "quit",
}
IDENTITY_RECOVERY_OPERATIONS = {"quit"}


BUNDLE_MANIFEST_NAME = "blender_companion_bundle.json"
BUNDLE_FORMAT_VERSION = 1


def blender_companion_bundle_fingerprint(asset_root: Path) -> str:
    root = asset_root.resolve()
    manifest_path = (root / BUNDLE_MANIFEST_NAME).resolve()
    if not manifest_path.is_relative_to(root):
        raise ValueError("Blender companion bundle manifest escaped asset root")

    data = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("version") != BUNDLE_FORMAT_VERSION:
        raise ValueError(
            f"Blender companion bundle must use version {BUNDLE_FORMAT_VERSION}"
        )

    raw_files = data.get("files")
    if (
        not isinstance(raw_files, list)
        or not raw_files
        or len(raw_files) > 32
        or not all(isinstance(name, str) and name.strip() for name in raw_files)
    ):
        raise ValueError("Blender companion bundle files must be a non-empty bounded list")

    names = [name.strip().replace("\\", "/") for name in raw_files]
    if len(set(names)) != len(names):
        raise ValueError("Blender companion bundle files must be unique")

    digest = hashlib.sha256()
    digest.update(b"ordax-blender-companion-bundle\0")
    digest.update(str(BUNDLE_FORMAT_VERSION).encode("ascii"))
    digest.update(b"\0")

    for name in sorted(names):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"invalid Blender companion bundle path: {name}")
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise FileNotFoundError(path)
        content = path.read_bytes()
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(content).digest())

    return digest.hexdigest()


def _process_is_running(pid: int) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            process_query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                process_query_limited_information, False, pid
            )
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except (AttributeError, OSError):
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class BlenderLiveBridge:
    """File-protocol bridge to one visible Blender session per registered project."""

    def __init__(self, config, project):
        self.config = config
        self.project = project
        self.root = (config.state_dir / "blender-live" / project.slug).resolve()
        self.inbox = self.root / "inbox"
        self.responses = self.root / "responses"
        self.results = self.root / "results"
        self.inflight = self.root / "inflight"
        self.presence = self.root / "presence.json"
        self.trajectory_path = self.root / "trajectory.jsonl"
        self.artifacts_root = (config.state_dir / "artifacts" / project.slug).resolve()
        self.scripts_root = project.path(
            project.blender.get("scripts_dir", "automation/blender"),
            must_exist=False,
        )
        self.assets_root = (Path(__file__).resolve().parent / "assets").resolve()
        self.companion = self.assets_root / "blender_live_companion.py"

    def _companion_fingerprint(self) -> str | None:
        try:
            return blender_companion_bundle_fingerprint(self.assets_root)
        except (OSError, ValueError, json.JSONDecodeError):
            return None

    def _ensure_dirs(self) -> None:
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.responses.mkdir(parents=True, exist_ok=True)
        self.results.mkdir(parents=True, exist_ok=True)
        self.inflight.mkdir(parents=True, exist_ok=True)
        self.artifacts_root.mkdir(parents=True, exist_ok=True)

    def presence_is_fresh(self, max_age_seconds: float = 5.0) -> bool:
        try:
            age = time.time() - self.presence.stat().st_mtime
            # Filesystem timestamps can be fractionally ahead of time.time() on
            # Windows/virtualized runners. Accept only a small bounded future
            # skew; genuinely stale or implausibly future presence stays invalid.
            if age < -2.0 or age > max_age_seconds:
                return False
            presence = self._read_presence()
            pid = presence.get("pid")
            if pid is None:
                return True
            numeric_pid = int(pid)
            return _process_is_running(numeric_pid)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False

    def _read_json_object_retry(
        self,
        path: Path,
        *,
        label: str,
        attempts: int = 6,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                raw = path.read_text(encoding="utf-8-sig")
                loaded = json.loads(raw)
                if not isinstance(loaded, dict):
                    raise ValueError(f"{label} must contain a JSON object")
                return loaded
            except (OSError, json.JSONDecodeError, ValueError) as error:
                last_error = error
                if attempt >= attempts - 1:
                    break
                time.sleep(0.02 * (attempt + 1))
        assert last_error is not None
        raise last_error

    def _read_presence(self) -> dict[str, Any]:
        return self._read_json_object_retry(self.presence, label="presence.json")

    def _presence_identity(self, presence: dict[str, Any]) -> dict[str, Any]:
        reported_project = str(presence.get("project") or "").strip()
        project_matches = reported_project == self.project.slug
        raw_file = str(presence.get("file") or "").strip()
        file_matches = True
        resolved_file = ""
        if raw_file:
            try:
                candidate = Path(raw_file).resolve()
                resolved_file = str(candidate)
                file_matches = candidate.is_relative_to(self.project.root.resolve())
            except (OSError, RuntimeError, ValueError):
                file_matches = False
        return {
            "identity_matches": bool(project_matches and file_matches),
            "presence_project": reported_project,
            "project_matches": project_matches,
            "presence_file": raw_file,
            "resolved_presence_file": resolved_file,
            "file_matches_project": file_matches,
            "expected_project": self.project.slug,
            "expected_project_root": str(self.project.root.resolve()),
        }

    def status(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "project": self.project.slug,
            "presence_fresh": self.presence_is_fresh(),
            "presence_path": str(self.presence),
            "control_root": str(self.root),
            "results_root": str(self.results),
            "inflight_root": str(self.inflight),
            "inflight_commands": sorted(p.stem for p in self.inflight.glob("*.json")),
            "scripts_root": str(self.scripts_root),
            "expected_protocol_version": EXPECTED_PROTOCOL_VERSION,
            "expected_companion_fingerprint": self._companion_fingerprint(),
        }
        if self.presence.is_file():
            try:
                presence = self._read_presence()
                data["presence"] = presence
                data.update(self._presence_identity(presence))
                protocol = presence.get("protocol_version")
                data["protocol_version"] = protocol
                data["protocol_compatible"] = protocol == EXPECTED_PROTOCOL_VERSION
                loaded_fingerprint = presence.get("companion_fingerprint")
                expected_fingerprint = data.get("expected_companion_fingerprint")
                data["companion_fingerprint"] = loaded_fingerprint
                data["companion_current"] = bool(
                    loaded_fingerprint
                    and expected_fingerprint
                    and loaded_fingerprint == expected_fingerprint
                )
                capabilities = presence.get("capabilities")
                if isinstance(capabilities, list):
                    data["capabilities"] = capabilities
            except Exception as error:
                data["presence_error"] = str(error)
                data["protocol_compatible"] = False
                data["identity_matches"] = False
        return data

    def start(
        self,
        *,
        blend_file: str | None = None,
        wait_seconds: float = 30.0,
    ) -> ActionResult:
        if self.presence_is_fresh():
            status = self.status()
            if status.get("identity_matches") is False:
                return ActionResult(
                    False,
                    "Visible Blender session identity no longer matches this ORDAX project",
                    {"identity_mismatch": True, **status},
                )
            if status.get("protocol_compatible") and status.get("companion_current"):
                return ActionResult(
                    True,
                    "Visible Blender live session already running",
                    status,
                )

            presence = status.get("presence") or {}
            if bool(presence.get("is_dirty")):
                return ActionResult(
                    False,
                    "Visible Blender companion is outdated and the current file has unsaved changes; save before restarting the companion",
                    status,
                )

            self.request("quit", timeout_seconds=15.0)
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline and self.presence_is_fresh():
                time.sleep(0.05)

        blender = find_blender()
        if blender is None:
            return ActionResult(False, "Blender executable not found")
        if not self.companion.is_file():
            return ActionResult(False, f"Blender live companion missing: {self.companion}")
        expected_fingerprint = self._companion_fingerprint()
        if not expected_fingerprint:
            return ActionResult(
                False,
                "Blender live companion bundle is missing or invalid",
                {
                    "companion": str(self.companion),
                    "bundle_manifest": str(self.assets_root / BUNDLE_MANIFEST_NAME),
                },
            )

        self._ensure_dirs()
        try:
            self.presence.unlink(missing_ok=True)
        except OSError:
            pass
        for folder in (self.inbox, self.inflight):
            for stale in folder.glob("*.json"):
                try:
                    stale.unlink()
                except OSError:
                    pass

        command = [str(blender), "--factory-startup", "--disable-autoexec"]

        if blend_file:
            blend = self.project.path(blend_file)
            if blend.suffix.lower() != ".blend":
                return ActionResult(False, "blend_file must be a .blend file")
            command.append(str(blend))

        command.extend(
            [
                "--python",
                str(self.companion),
                "--",
                "--ordax-control-root",
                str(self.root),
                "--ordax-project-root",
                str(self.project.root.resolve()),
                "--ordax-scripts-root",
                str(self.scripts_root.resolve()),
                "--ordax-artifacts-root",
                str(self.artifacts_root),
                "--ordax-project-slug",
                self.project.slug,
            ]
        )

        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        startup_log = self.root / "blender-startup.log"
        startup_log.parent.mkdir(parents=True, exist_ok=True)
        with startup_log.open("a", encoding="utf-8", errors="replace") as log_handle:
            log_handle.write(f"\n--- OrdaX Blender start {time.time():.3f} ---\n")
            log_handle.flush()
            process = subprocess.Popen(
                command,
                cwd=str(self.project.root),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                shell=False,
                creationflags=creationflags,
            )

        deadline = time.monotonic() + max(3.0, wait_seconds)
        while time.monotonic() < deadline:
            if self.presence_is_fresh():
                data = self.status()
                if (
                    data.get("identity_matches")
                    and data.get("protocol_compatible")
                    and data.get("companion_current")
                ):
                    data["pid"] = process.pid
                    data["log_file"] = str(startup_log)
                    data["transport"] = "blender-visible-companion"
                    return ActionResult(True, "Visible Blender live session started", data)
                if data.get("identity_matches") is False:
                    return ActionResult(
                        False,
                        "Blender companion started with a mismatched project identity",
                        {"identity_mismatch": True, **data},
                    )
            if process.poll() is not None:
                return ActionResult(
                    False,
                    f"Blender exited before live companion became ready: {process.returncode}",
                    {
                        "pid": process.pid,
                        "returncode": process.returncode,
                        "log_file": str(startup_log),
                    },
                )
            time.sleep(0.10)

        return ActionResult(
            False,
            "Blender opened but live companion did not become ready in time",
            {"pid": process.pid, "log_file": str(startup_log), **self.status()},
        )

    def request(
        self,
        operation: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout_seconds: float = 120.0,
    ) -> ActionResult:
        if not self.presence_is_fresh():
            return ActionResult(False, "Visible Blender live session is not running", self.status())

        status = self.status()
        if operation not in IDENTITY_RECOVERY_OPERATIONS and status.get("identity_matches") is False:
            return ActionResult(
                False,
                "Visible Blender file no longer belongs to this ORDAX project; operation blocked",
                {"identity_mismatch": True, "blocked_operation": operation, **status},
            )
        maintenance = operation in LEGACY_MAINTENANCE_OPERATIONS
        if not maintenance and status.get("protocol_compatible") is False:
            return ActionResult(
                False,
                "Visible Blender companion protocol is outdated; restart the live session before using this operation",
                status,
            )
        if not maintenance and status.get("companion_current") is False:
            return ActionResult(
                False,
                "Visible Blender companion code is outdated; restart the live session before using this operation",
                status,
            )
        capabilities = status.get("capabilities")
        if isinstance(capabilities, list) and operation not in capabilities:
            return ActionResult(
                False,
                f"Visible Blender companion does not advertise operation: {operation}",
                status,
            )

        self._ensure_dirs()
        command_id = uuid.uuid4().hex
        command_path = self.inbox / f"{command_id}.json"
        temp_path = self.inbox / f"{command_id}.tmp"
        response_path = self.responses / f"{command_id}.json"
        inflight_path = self.inflight / f"{command_id}.json"

        body = {"id": command_id, "operation": operation, **(payload or {})}
        temp_path.write_text(json.dumps(body), encoding="utf-8")
        temp_path.replace(command_path)

        deadline = time.monotonic() + max(1.0, timeout_seconds)
        while time.monotonic() < deadline:
            if response_path.is_file():
                try:
                    response = self._read_json_object_retry(
                        response_path,
                        label="Blender live response",
                    )
                except (OSError, json.JSONDecodeError, ValueError):
                    time.sleep(0.05)
                    continue
                try:
                    response_path.unlink()
                except OSError:
                    pass

                ok = bool(response.get("ok"))
                return ActionResult(
                    ok,
                    str(response.get("summary") or "Blender live response"),
                    {"transport": "blender-visible-companion", **response},
                )

            if not inflight_path.is_file() and not self.presence_is_fresh(max_age_seconds=12.0):
                return ActionResult(
                    False,
                    "Blender live session stopped responding",
                    {
                        "command_id": command_id,
                        "result_query": "blender.live_result",
                        "inflight": False,
                        **self.status(),
                    },
                )
            time.sleep(0.05)

        inflight = inflight_path.is_file()
        if not inflight:
            try:
                command_path.unlink()
            except OSError:
                pass

        return ActionResult(
            False,
            f"Blender live operation timed out: {operation}",
            {
                "command_id": command_id,
                "result_query": "blender.live_result",
                "retry_without_querying_result": False,
                "inflight": inflight,
                **self.status(),
            },
        )

    def trajectory(self, *, limit: int = 50) -> ActionResult:
        limit = max(1, min(int(limit), 500))
        if not self.trajectory_path.is_file():
            return ActionResult(
                True,
                "Blender live trajectory is empty",
                {
                    "project": self.project.slug,
                    "trajectory_path": str(self.trajectory_path),
                    "events": [],
                },
            )

        try:
            lines = self.trajectory_path.read_text(
                encoding="utf-8-sig",
                errors="replace",
            ).splitlines()
        except OSError as error:
            return ActionResult(
                False,
                f"Blender live trajectory could not be read: {error}",
            )

        events = []
        for line in lines[-limit:]:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)

        return ActionResult(
            True,
            "Blender live trajectory ready",
            {
                "project": self.project.slug,
                "trajectory_path": str(self.trajectory_path),
                "returned_events": len(events),
                "events": events,
            },
        )

    def result(self, command_id: str) -> ActionResult:
        """Read a durable result without requiring the Blender session to still be alive."""
        try:
            parsed = uuid.UUID(str(command_id))
        except (ValueError, AttributeError, TypeError):
            return ActionResult(False, "command_id must be a UUID hex string")
        normalized = parsed.hex
        if str(command_id).lower() != normalized:
            return ActionResult(False, "command_id must be the canonical UUID hex string")

        self._ensure_dirs()
        path = (self.results / f"{normalized}.json").resolve()
        if not path.is_relative_to(self.results.resolve()):
            return ActionResult(False, "result path escaped managed directory")
        if not path.is_file():
            inflight = (self.inflight / f"{normalized}.json").is_file()
            return ActionResult(
                False,
                "Blender live result is not available",
                {
                    "command_id": normalized,
                    "retryable": True,
                    "in_progress": inflight,
                    "presence_fresh": self.presence_is_fresh(),
                },
            )

        try:
            response = self._read_json_object_retry(path, label="Blender live result")
        except (OSError, json.JSONDecodeError, ValueError) as error:
            return ActionResult(
                False,
                f"Blender live result could not be read: {error}",
                {"command_id": normalized},
            )

        if str(response.get("id") or "") != normalized:
            return ActionResult(
                False,
                "Blender live result ID mismatch",
                {"command_id": normalized},
            )
        return ActionResult(
            bool(response.get("ok")),
            str(response.get("summary") or "Blender live result"),
            {
                "transport": "blender-visible-companion",
                "durable_result": True,
                **response,
            },
        )
