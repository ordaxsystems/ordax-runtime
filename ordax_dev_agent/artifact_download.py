"""Receiver for artifact.read_chunk; transport and credentials stay with the caller."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Callable

from .execution_lock import ExecutionLock


def download_artifact(
    invoke: Callable[[str, dict], dict],
    *,
    selector: dict,
    destination: str | Path,
    expected_sha256: str | None = None,
    max_size_bytes: int = 1024 * 1024 * 1024,
    progress: Callable[[dict], None] | None = None,
) -> dict:
    """Invoke must wait for a job and return its {ok, summary, data} result.

    Failed transfers retain a checked checkpoint. Call again with identical
    arguments to resume. Existing destinations are never replaced.
    """
    if set(selector) - {"project", "project_artifact_path", "artifact_name"}:
        raise ValueError("Unsupported artifact selector fields")
    if not isinstance(selector.get("project"), str) or not selector["project"]:
        raise ValueError("A project is required")
    if sum(bool(selector.get(key)) for key in ("project_artifact_path", "artifact_name")) != 1:
        raise ValueError("Choose exactly one artifact selector")
    if any(not isinstance(value, str) for value in selector.values()):
        raise ValueError("Artifact selectors must be strings")
    if type(max_size_bytes) is not int or max_size_bytes < 0:
        raise ValueError("max_size_bytes must be a nonnegative integer")
    if expected_sha256 is not None:
        expected_sha256 = expected_sha256.lower()
        if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
            raise ValueError("Invalid expected SHA256")
    target = Path(destination).expanduser().absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    state_dir = target.with_name(target.name + ".ordax-transfer")
    lock = ExecutionLock(state_dir)
    if not lock.acquire():
        raise RuntimeError("Another download owns this destination")
    try:
        if target.exists() or target.is_symlink():
            raise FileExistsError(target)
        partial = state_dir / "partial"
        checkpoint = state_dir / "checkpoint.json"
        identity = {"selector": dict(selector), "expected_sha256": expected_sha256}
        state = {**identity, "offset": 0, "prefix_sha256": hashlib.sha256(b"").hexdigest()}
        if checkpoint.exists():
            if checkpoint.stat().st_size > 16384:
                raise ValueError("Invalid download checkpoint")
            state = json.loads(checkpoint.read_text(encoding="utf-8"))
            if any(state.get(key) != value for key, value in identity.items()):
                raise ValueError("Checkpoint belongs to a different download")
        offset = state.get("offset")
        if type(offset) is not int or not 0 <= offset <= max_size_bytes:
            raise ValueError("Invalid checkpoint offset")
        if not partial.exists() and offset:
            raise ValueError("Partial file is missing")
        digest = hashlib.sha256()
        with partial.open("r+b" if partial.exists() else "w+b") as stream:
            remaining = offset
            while remaining:
                block = stream.read(min(remaining, 1024 * 1024))
                if not block:
                    raise ValueError("Partial file is shorter than its checkpoint")
                digest.update(block)
                remaining -= len(block)
            if digest.hexdigest() != state.get("prefix_sha256"):
                raise ValueError("Partial file checksum does not match checkpoint")
            # Discard an uncommitted tail left by a crash before checkpoint rename.
            stream.truncate(offset)
            while True:
                payload = {**selector, "offset": offset, "max_bytes": 32768}
                if state.get("source_version"):
                    payload["source_version"] = state["source_version"]
                response = invoke("artifact.read_chunk", payload)
                if not isinstance(response, dict) or response.get("ok") is not True:
                    raise RuntimeError("Artifact job failed; partial download retained")
                data = response.get("data")
                if not isinstance(data, dict):
                    raise ValueError("Missing artifact chunk data")
                size = data.get("source_size_bytes")
                version = data.get("source_version")
                if type(size) is not int or not offset <= size <= max_size_bytes:
                    raise ValueError("Invalid or excessive artifact size")
                if not isinstance(version, str) or len(version) != 64:
                    raise ValueError("Invalid source version")
                if "source_version" in state and (version != state["source_version"] or size != state["source_size_bytes"]):
                    raise ValueError("Artifact changed; restart with a new destination")
                encoded = data.get("base64")
                if not isinstance(encoded, str) or len(encoded) > 43692:
                    raise ValueError("Oversized or invalid encoded chunk")
                chunk = base64.b64decode(encoded, validate=True)
                next_offset = offset + len(chunk)
                if (len(chunk) > 32768 or data.get("offset") != offset
                        or data.get("size_bytes") != len(chunk) or data.get("next_offset") != next_offset
                        or next_offset > size or data.get("eof") is not (next_offset == size)
                        or (not chunk and next_offset != size)
                        or hashlib.sha256(chunk).hexdigest() != data.get("chunk_sha256")):
                    raise ValueError("Artifact chunk integrity check failed")
                stream.seek(offset)
                stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
                digest.update(chunk)
                state.update(offset=next_offset, source_version=version, source_size_bytes=size,
                             prefix_sha256=digest.hexdigest())
                pending = checkpoint.with_suffix(".next")
                with pending.open("w", encoding="utf-8") as handle:
                    json.dump(state, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(pending, checkpoint)
                offset = next_offset
                if progress:
                    progress({"received_bytes": offset, "total_bytes": size})
                if data["eof"]:
                    break
        checksum = digest.hexdigest()
        if expected_sha256 is not None and checksum != expected_sha256:
            raise ValueError("Full artifact SHA256 mismatch; destination not published")
        # Same-filesystem hard link publishes without replacing an existing target.
        os.link(partial, target)
        partial.unlink()
        checkpoint.unlink()
        return {"path": str(target), "size_bytes": offset, "sha256": checksum,
                "source_version": state["source_version"]}
    finally:
        lock.release()
