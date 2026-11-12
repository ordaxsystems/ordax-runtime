"""Resumable, bounded, verified file transfers for device-scoped Computer Control.

The local state directory is the only staging authority. No caller-provided temp
path, shell execution, bypass of computer-access roots, or partial target writes.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .models import ActionResult

MAX_TRANSFER_BYTES = 32 * 1024 * 1024
MAX_CHUNK_BYTES = 48 * 1024
TRANSFER_TTL_SECONDS = 24 * 3600
MAX_ACTIVE_TRANSFERS = 8
_SHA = re.compile(r"^[a-f0-9]{64}$")
_ID = re.compile(r"^[a-f0-9]{32}$")


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(256 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA.fullmatch(value))


class ComputerTransferActions:
    def _transfer_root(self) -> Path:
        return self.config.state_dir / "computer-file-transfers"

    def _transfer_paths(self, transfer_id: Any) -> tuple[Path, Path]:
        if not isinstance(transfer_id, str) or not _ID.fullmatch(transfer_id):
            raise ValueError("transfer_id must be a canonical lowercase hex token")
        root = self._transfer_root()
        return root / (transfer_id + ".json"), root / (transfer_id + ".part")

    def _transfer_load(self, transfer_id: Any) -> tuple[dict[str, Any], Path, Path]:
        meta_path, part_path = self._transfer_paths(transfer_id)
        if not meta_path.is_file():
            raise ValueError("transfer not found or expired")
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict) or metadata.get("id") != transfer_id:
            raise ValueError("transfer metadata invalid")
        if time.time() - metadata.get("created_at", 0) > TRANSFER_TTL_SECONDS:
            raise ValueError("transfer expired")
        # Permission can be revoked between chunks; never trust the begin check.
        self._computer_path(metadata["path"], must_exist=False, mutating=True)
        return metadata, meta_path, part_path

    def _transfer_cleanup(self, root: Path) -> int:
        active = 0
        for meta in root.glob("*.json"):
            try:
                entry = json.loads(meta.read_text(encoding="utf-8"))
                age = time.time() - entry["created_at"]
                if 0 <= age <= TRANSFER_TTL_SECONDS:
                    if not entry.get("committed"):
                        active += 1
                    continue
            except (OSError, ValueError, KeyError, TypeError):
                pass
            meta.unlink(missing_ok=True)
            meta.with_suffix(".part").unlink(missing_ok=True)
        return active

    def computer_transfer_begin(self, payload: dict[str, Any]) -> ActionResult:
        if set(payload) - {"path", "size_bytes", "sha256", "expected_sha256", "create"}:
            return ActionResult(False, "unrecognized transfer parameters")
        size = payload.get("size_bytes")
        sha = payload.get("sha256")
        expected = payload.get("expected_sha256")
        create = payload.get("create", False)
        if type(size) is not int or not 0 <= size <= MAX_TRANSFER_BYTES:
            return ActionResult(False, "size_bytes outside the bounded transfer range")
        if not _valid_hash(sha) or type(create) is not bool:
            return ActionResult(False, "invalid transfer digest or create flag")
        if expected is not None and not _valid_hash(expected):
            return ActionResult(False, "expected_sha256 must be a lowercase SHA-256")
        path, _ = self._computer_path(payload.get("path"), must_exist=False, mutating=True)
        if path.exists() and not path.is_file():
            return ActionResult(False, "destination is not a regular file")
        initial = _digest(path) if path.is_file() else None
        if initial is not None and (expected is None or expected != initial):
            return ActionResult(False, "existing file requires its current SHA-256")
        if initial is None and not create:
            return ActionResult(False, "new destination requires create=true")
        root = self._transfer_root()
        root.mkdir(parents=True, exist_ok=True)
        if self._transfer_cleanup(root) >= MAX_ACTIVE_TRANSFERS:
            return ActionResult(False, "too many active transfers")
        transfer_id = uuid.uuid4().hex
        meta_path, part_path = self._transfer_paths(transfer_id)
        data = {
            "id": transfer_id, "path": str(path), "size_bytes": size,
            "sha256": sha, "expected_sha256": initial,
            "created_at": time.time(), "committed": False,
        }
        with part_path.open("xb"):
            pass
        try:
            with meta_path.open("x", encoding="utf-8") as handle:
                json.dump(data, handle)
        except BaseException:
            part_path.unlink(missing_ok=True)
            raise
        return ActionResult(True, "transfer initialized", {
            "transfer_id": transfer_id, "offset": 0,
            "max_chunk_bytes": MAX_CHUNK_BYTES, "size_bytes": size,
        })

    def computer_transfer_status(self, payload: dict[str, Any]) -> ActionResult:
        if set(payload) != {"transfer_id"}:
            return ActionResult(False, "transfer_id required")
        meta, _, part = self._transfer_load(payload["transfer_id"])
        offset = meta["size_bytes"] if meta["committed"] else (part.stat().st_size if part.is_file() else 0)
        return ActionResult(True, "transfer status ready", {
            "transfer_id": meta["id"], "offset": offset,
            "size_bytes": meta["size_bytes"], "sha256": meta["sha256"],
            "committed": bool(meta["committed"]), "max_chunk_bytes": MAX_CHUNK_BYTES,
        })

    def computer_transfer_chunk(self, payload: dict[str, Any]) -> ActionResult:
        keys = set(payload)
        if keys not in (
            {"transfer_id", "offset", "data_base64"},
            {"transfer_id", "offset", "text_utf8"},
        ):
            return ActionResult(False, "provide exactly one chunk encoding")
        offset = payload["offset"]
        if type(offset) is not int or offset < 0:
            return ActionResult(False, "offset must be a nonnegative integer")
        if "text_utf8" in payload:
            raw = payload["text_utf8"]
            if not isinstance(raw, str) or not 0 < len(raw) <= 32768:
                return ActionResult(False, "UTF-8 chunk exceeds its character bound")
            try:
                chunk = raw.encode("utf-8")
            except UnicodeEncodeError:
                return ActionResult(False, "invalid UTF-8 chunk")
        else:
            raw = payload["data_base64"]
            if not isinstance(raw, str) or not 0 < len(raw) <= 65536:
                return ActionResult(False, "chunk encoding exceeds its bound")
            try:
                chunk = base64.b64decode(raw, validate=True)
            except (ValueError, binascii.Error):
                return ActionResult(False, "invalid base64 chunk")
        if not 0 < len(chunk) <= MAX_CHUNK_BYTES:
            return ActionResult(False, "decoded chunk exceeds its bound")
        meta, _, part = self._transfer_load(payload["transfer_id"])
        if meta["committed"] or not part.is_file():
            return ActionResult(False, "transfer is already committed")
        size = part.stat().st_size
        if offset + len(chunk) > meta["size_bytes"]:
            return ActionResult(False, "chunk exceeds declared transfer size")
        if offset < size and offset + len(chunk) <= size:
            with part.open("rb") as handle:
                handle.seek(offset)
                identical = handle.read(len(chunk)) == chunk
            if identical:
                return ActionResult(True, "chunk already received", {
                    "transfer_id": meta["id"], "offset": size, "replayed": True,
                })
        if offset != size:
            return ActionResult(False, "chunk offset conflict", {
                "expected_offset": size,
            })
        with part.open("ab") as handle:
            handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        return ActionResult(True, "chunk received", {
            "transfer_id": meta["id"], "offset": size + len(chunk),
            "replayed": False,
        })

    def computer_transfer_commit(self, payload: dict[str, Any]) -> ActionResult:
        if set(payload) != {"transfer_id"}:
            return ActionResult(False, "transfer_id required")
        meta, meta_path, part = self._transfer_load(payload["transfer_id"])
        path, _ = self._computer_path(meta["path"], must_exist=False, mutating=True)
        if meta["committed"]:
            if path.is_file() and _digest(path) == meta["sha256"]:
                return ActionResult(True, "transfer already committed", {
                    "sha256": meta["sha256"], "size_bytes": meta["size_bytes"],
                    "replayed": True,
                })
            return ActionResult(False, "committed destination has changed")
        # Commit may have succeeded before the receipt was persisted; replay
        # is safe if the destination already contains the exact target bytes.
        if not part.is_file():
            return ActionResult(False, "transfer stage unavailable")
        if part.stat().st_size != meta["size_bytes"]:
            return ActionResult(False, "transfer incomplete")
        if _digest(part) != meta["sha256"]:
            return ActionResult(False, "transfer SHA-256 mismatch")
        # This check is repeated immediately before replacement to prevent stale writes.
        original = _digest(path) if path.is_file() else None
        if original != meta["expected_sha256"]:
            # A crash after the atomic replace but before recording the receipt
            # must never reapply the operation or reject a correct final file.
            if original == meta["sha256"]:
                meta["committed"] = True
                meta_path.write_text(json.dumps(meta), encoding="utf-8")
                part.unlink(missing_ok=True)
                return ActionResult(True, "transfer already committed", {
                    "path": str(path), "sha256": meta["sha256"],
                    "size_bytes": meta["size_bytes"], "replayed": True,
                })
            return ActionResult(False, "destination changed since transfer began")
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name("." + path.name + ".ordax-" + uuid.uuid4().hex + ".tmp")
        try:
            with part.open("rb") as source, temp.open("xb") as target:
                shutil.copyfileobj(source, target, 256 * 1024)
                target.flush()
                os.fsync(target.fileno())
            if _digest(temp) != meta["sha256"]:
                return ActionResult(False, "temporary copy SHA-256 mismatch")
            path, _ = self._computer_path(meta["path"], must_exist=False, mutating=True)
            if (_digest(path) if path.is_file() else None) != original:
                return ActionResult(False, "destination changed during commit")
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
        meta["committed"] = True
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
        part.unlink(missing_ok=True)
        return ActionResult(True, "file committed atomically", {
            "path": str(path), "sha256": meta["sha256"],
            "size_bytes": meta["size_bytes"], "previous_sha256": original,
            "replayed": False,
        })

    def computer_transfer_abort(self, payload: dict[str, Any]) -> ActionResult:
        if set(payload) != {"transfer_id"}:
            return ActionResult(False, "transfer_id required")
        meta, meta_path, part = self._transfer_load(payload["transfer_id"])
        if meta["committed"]:
            return ActionResult(False, "committed transfer cannot be aborted")
        meta_path.unlink(missing_ok=True)
        part.unlink(missing_ok=True)
        return ActionResult(True, "transfer aborted; original file untouched")
