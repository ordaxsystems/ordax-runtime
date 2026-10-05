from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .models import ActionResult


class UnityEditorBridge:
    def __init__(self, project_path: Path, companion_source: Path | None = None):
        self.project = project_path.resolve()
        self._companion_source = companion_source
        self.root = self.project / "Library" / "OrdaXAgent"
        self.inbox = self.root / "inbox"
        self.responses = self.root / "responses"
        self.presence = self.root / "editor-presence.json"

    @property
    def project_lock_path(self) -> Path:
        return self.project / "Temp" / "UnityLockfile"

    @property
    def companion_source_path(self) -> Path:
        if self._companion_source:
            return self._companion_source
        return self.project / "Assets/OrdaX/Editor/OrdaXGenericAgent.cs"

    def project_appears_open(self) -> bool:
        return self.project_lock_path.exists()

    def presence_is_fresh(self, max_age_seconds: float = 4.0) -> bool:
        try:
            age = time.time() - self.presence.stat().st_mtime
            return 0 <= age <= max_age_seconds
        except OSError:
            return False

    def nudge_companion(self, wait_seconds: float = 30.0) -> bool:
        """Ask Unity's file watcher to import the companion without launching a second Editor."""
        if self.presence_is_fresh():
            return True
        if not self.project_appears_open():
            return False
        if not self.companion_source_path.is_file():
            return False

        try:
            self.companion_source_path.touch(exist_ok=True)
        except OSError:
            return False

        deadline = time.monotonic() + max(1.0, wait_seconds)
        while time.monotonic() < deadline:
            if self.presence_is_fresh(max_age_seconds=8.0):
                return True
            time.sleep(0.5)
        return False

    def status(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "project_appears_open": self.project_appears_open(),
            "presence_fresh": self.presence_is_fresh(),
            "presence_path": str(self.presence),
            "companion_source": str(self.companion_source_path),
            "presence_age_seconds": max(0.0, time.time() - self.presence.stat().st_mtime) if self.presence.is_file() else None,
        }
        if self.presence.is_file():
            try:
                data["presence"] = json.loads(
                    self.presence.read_text(encoding="utf-8-sig")
                )
            except Exception as error:
                data["presence_error"] = str(error)
        return data

    def request(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout_seconds: float = 120.0,
    ) -> ActionResult | None:
        if not self.presence_is_fresh():
            return None

        self.inbox.mkdir(parents=True, exist_ok=True)
        self.responses.mkdir(parents=True, exist_ok=True)

        command_id = uuid.uuid4().hex
        response_path = self.responses / f"{command_id}.json"
        command_path = self.inbox / f"{command_id}.json"
        temp_path = self.inbox / f"{command_id}.tmp"

        body = {
            **(payload or {}),
            "id": command_id,
            "action": action,
        }
        temp_path.write_text(json.dumps(body), encoding="utf-8")
        temp_path.replace(command_path)

        deadline = time.monotonic() + max(1.0, timeout_seconds)
        while time.monotonic() < deadline:
            if response_path.is_file():
                try:
                    response = json.loads(response_path.read_text(encoding="utf-8-sig"))
                finally:
                    try:
                        response_path.unlink()
                    except OSError:
                        pass

                ok = bool(response.get("ok"))
                return ActionResult(
                    ok,
                    str(response.get("summary") or "Unity Editor bridge response"),
                    {
                        "transport": "unity-editor-companion",
                        **response,
                    },
                )

            # Long Unity Editor commands can block the main thread and therefore
            # pause heartbeat writes while still progressing normally. Keep polling
            # for the authoritative response for a bounded grace period instead of
            # treating a short stale presence as immediate failure.
            stale_grace = min(max(30.0, timeout_seconds), 300.0)
            if (
                not self.presence_is_fresh(max_age_seconds=stale_grace)
                and not self.project_appears_open()
            ):
                break
            time.sleep(0.2)

        try:
            command_path.unlink()
        except OSError:
            pass

        return ActionResult(False, "Unity response timed out; command outcome is unknown; inspect before retrying", {
            "command_id": command_id, "outcome_unknown": True,
            "transport": "unity-editor-companion",
        })
