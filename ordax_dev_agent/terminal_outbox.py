from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any


_MAX_OUTBOX_BYTES = 1024 * 1024


class TerminalOutbox:
    """Atomic local persistence for terminal reports awaiting cloud acceptance."""

    def __init__(self, state_dir: Path, protocol: str, device_id: str):
        self.root = state_dir / "terminal-outbox" / protocol
        self.protocol = protocol
        self.device_id = device_id

    @staticmethod
    def _validate_uuid(value: Any, field: str) -> str:
        text = str(value or "")
        try:
            uuid.UUID(text)
        except ValueError as error:
            raise RuntimeError(f"terminal outbox {field} must be a UUID") from error
        return text

    def _path_for(self, report: dict[str, Any]) -> Path:
        job_id = self._validate_uuid(report.get("job_id"), "job_id")
        return self.root / f"{job_id}.json"

    def persist(self, report: dict[str, Any]) -> Path:
        if not isinstance(report, dict):
            raise RuntimeError("terminal outbox report must be an object")
        self._validate_uuid(report.get("report_id"), "report_id")
        target = self._path_for(report)
        envelope = {
            "schema": 1,
            "protocol": self.protocol,
            "device_id": self.device_id,
            "report": report,
        }
        encoded = json.dumps(
            envelope,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        if len(encoded) > _MAX_OUTBOX_BYTES:
            raise RuntimeError("terminal outbox report exceeds local safety limit")

        self.root.mkdir(parents=True, exist_ok=True)
        def confirm_existing() -> Path:
            # The same report is idempotent; a different report for one job
            # is an integrity conflict, including across concurrent processes.
            if self._read_path(target) != report:
                raise RuntimeError(
                    f"terminal outbox conflict for already-persisted job {report.get('job_id')}"
                )
            return target

        if target.is_file():
            return confirm_existing()

        # Unique even for threads/processes sharing a state directory.
        pending = target.with_name(
            f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.next"
        )
        try:
            with pending.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(pending, 0o600)
            except OSError:
                pass
            # os.replace silently overwrites a winner from another writer.
            # Hard-link creation is atomic and never replaces an existing
            # terminal receipt. Both paths share this same directory/volume.
            try:
                os.link(pending, target)
            except FileExistsError:
                return confirm_existing()
            return target
        finally:
            try:
                pending.unlink()
            except FileNotFoundError:
                pass

    def _read_path(self, path: Path) -> dict[str, Any]:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            # A concurrent successful delivery may acknowledge this exact
            # receipt after pending() enumerated it. Preserve that benign race
            # so the scanner can skip it without marking transport unhealthy.
            raise
        except OSError as error:
            raise RuntimeError(f"terminal outbox entry cannot be inspected: {path}") from error
        if size <= 0 or size > _MAX_OUTBOX_BYTES:
            raise RuntimeError(f"terminal outbox entry has invalid size: {path}")
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"terminal outbox entry is invalid: {path}") from error
        if not isinstance(envelope, dict):
            raise RuntimeError(f"terminal outbox envelope must be an object: {path}")
        if envelope.get("schema") != 1:
            raise RuntimeError(f"terminal outbox schema is unsupported: {path}")
        if envelope.get("protocol") != self.protocol:
            raise RuntimeError(f"terminal outbox protocol mismatch: {path}")
        if envelope.get("device_id") != self.device_id:
            raise RuntimeError(f"terminal outbox device mismatch: {path}")
        report = envelope.get("report")
        if not isinstance(report, dict):
            raise RuntimeError(f"terminal outbox report is missing: {path}")
        job_id = self._validate_uuid(report.get("job_id"), "job_id")
        self._validate_uuid(report.get("report_id"), "report_id")
        if path.name != f"{job_id}.json":
            raise RuntimeError(f"terminal outbox filename does not match job id: {path}")
        return report

    def pending(self) -> list[tuple[Path, dict[str, Any]]]:
        if not self.root.exists():
            return []
        if not self.root.is_dir():
            raise RuntimeError("terminal outbox path is not a directory")
        entries: list[tuple[Path, dict[str, Any]]] = []
        for path in sorted(self.root.iterdir()):
            if path.name.startswith(".") and path.name.endswith(".next"):
                continue
            if path.suffix != ".json":
                raise RuntimeError(f"unexpected file in terminal outbox: {path}")
            if not path.is_file():
                # Another delivery may have acknowledged an entry between the
                # directory snapshot and this check.
                if not path.exists():
                    continue
                raise RuntimeError(f"unexpected file in terminal outbox: {path}")
            try:
                report = self._read_path(path)
            except FileNotFoundError:
                continue
            entries.append((path, report))
        return entries

    @staticmethod
    def acknowledge(path: Path) -> None:
        # Cloud acceptance is idempotent. Two delivery paths may both receive
        # the same success receipt; the second local acknowledgement is benign.
        path.unlink(missing_ok=True)
