from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any


_MAX_ENTRY_BYTES = 256 * 1024
_MAX_PENDING_ENTRIES = 2048


class ProductAuditOutbox:
    """Durable local audit journal for Product actions."""

    def __init__(self, state_dir: Path, protocol: str, device_id: str):
        self.root = state_dir / "product-audit-outbox" / protocol
        self.protocol = protocol
        self.device_id = device_id

    @staticmethod
    def _uuid(value: Any, field: str) -> str:
        text = str(value or "")
        try:
            uuid.UUID(text)
        except ValueError as error:
            raise RuntimeError(f"product audit {field} must be a UUID") from error
        return text

    def _key(self, event: dict[str, Any]) -> str:
        request_id = self._uuid(event.get("request_id"), "request_id")
        phase = str(event.get("phase") or "")
        if phase not in {"decision", "result"}:
            raise RuntimeError("product audit phase must be decision or result")
        return f"{request_id}.{phase}.json"

    def persist(self, event: dict[str, Any]) -> Path:
        if not isinstance(event, dict):
            raise RuntimeError("product audit event must be an object")
        self._uuid(event.get("request_id"), "request_id")
        self._uuid(event.get("subject_id"), "subject_id")
        grant_id = event.get("grant_id")
        if grant_id is not None:
            self._uuid(grant_id, "grant_id")
        target = self.root / self._key(event)
        envelope = {
            "schema": 1,
            "protocol": self.protocol,
            "device_id": self.device_id,
            "event": event,
        }
        encoded = json.dumps(
            envelope,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        if len(encoded) > _MAX_ENTRY_BYTES:
            raise RuntimeError("product audit event exceeds local safety limit")

        self.root.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            current = self._read_path(target)
            if current != event:
                raise RuntimeError(f"product audit conflict for {target.name}")
            return target
        existing = [
            item for item in self.root.iterdir()
            if item.is_file() and item.suffix == ".json"
        ]
        if len(existing) >= _MAX_PENDING_ENTRIES:
            raise RuntimeError("product audit backlog safety limit reached")

        pending = target.with_name(f".{target.name}.{os.getpid()}.next")
        try:
            with pending.open("xb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(pending, 0o600)
            except OSError:
                pass
            os.replace(pending, target)
            return target
        finally:
            try:
                pending.unlink()
            except FileNotFoundError:
                pass

    def _read_path(self, path: Path) -> dict[str, Any]:
        size = path.stat().st_size
        if size <= 0 or size > _MAX_ENTRY_BYTES:
            raise RuntimeError(f"product audit entry has invalid size: {path}")
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"product audit entry is invalid: {path}") from error
        if not isinstance(envelope, dict) or envelope.get("schema") != 1:
            raise RuntimeError(f"product audit envelope is invalid: {path}")
        if (
            envelope.get("protocol") != self.protocol
            or envelope.get("device_id") != self.device_id
        ):
            raise RuntimeError(f"product audit envelope scope mismatch: {path}")
        event = envelope.get("event")
        if not isinstance(event, dict):
            raise RuntimeError(f"product audit event is missing: {path}")
        if path.name != self._key(event):
            raise RuntimeError(f"product audit filename mismatch: {path}")
        return event

    def pending(self) -> list[tuple[Path, dict[str, Any]]]:
        if not self.root.exists():
            return []
        if not self.root.is_dir():
            raise RuntimeError("product audit outbox path is not a directory")
        entries: list[tuple[Path, dict[str, Any]]] = []
        for path in sorted(self.root.iterdir()):
            if path.name.startswith(".") and path.name.endswith(".next"):
                continue
            if path.suffix != ".json" or not path.is_file():
                raise RuntimeError(f"unexpected file in product audit outbox: {path}")
            entries.append((path, self._read_path(path)))
        return entries

    @staticmethod
    def acknowledge(path: Path) -> None:
        path.unlink()
