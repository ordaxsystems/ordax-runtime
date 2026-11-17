from __future__ import annotations

import json
import os
import stat
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

    def _sync_directory(self) -> None:
        """Durably publish the hard link before acknowledging a local report.

        Linux Native needs the directory entry flushed, not just the file.
        Windows cannot fsync a directory using this API; retain its existing
        atomic hard-link behavior without introducing a second persistence path.
        """
        if os.name == "nt":
            return
        directory_fd = os.open(
            self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

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
            pending.unlink()
            # fsync(file) alone does not make the newly linked receipt durable
            # across a Native OS power loss. Flush the final directory state.
            self._sync_directory()
            return target
        finally:
            try:
                pending.unlink()
            except FileNotFoundError:
                pass

    def _read_path(self, path: Path) -> dict[str, Any]:
        # Open once: stat(path) followed by read_text(path) could read an
        # unrelated replacement file, including one grown past the 1 MiB
        # safety limit. O_NOFOLLOW blocks symlinked entries on Native Linux.
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            if path.is_symlink():
                raise RuntimeError(f"terminal outbox entry is a symlink: {path}")
            fd = os.open(path, flags)
        except FileNotFoundError:
            # Successful remote delivery may acknowledge a file between
            # enumeration and open. Do not misclassify this benign race.
            raise
        except OSError as error:
            raise RuntimeError(f"terminal outbox entry cannot be opened: {path}") from error
        try:
            with os.fdopen(fd, "rb") as handle:
                metadata = os.fstat(handle.fileno())
                if not stat.S_ISREG(metadata.st_mode):
                    raise RuntimeError(f"terminal outbox entry is not a regular file: {path}")
                if metadata.st_size <= 0 or metadata.st_size > _MAX_OUTBOX_BYTES:
                    raise RuntimeError(f"terminal outbox entry has invalid size: {path}")
                raw = handle.read(_MAX_OUTBOX_BYTES + 1)
                if not raw or len(raw) > _MAX_OUTBOX_BYTES:
                    raise RuntimeError(f"terminal outbox entry has invalid size: {path}")
        except OSError as error:
            raise RuntimeError(f"terminal outbox entry cannot be read: {path}") from error
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
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
            if path.is_symlink():
                raise RuntimeError(f"terminal outbox entry is a symlink: {path}")
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
