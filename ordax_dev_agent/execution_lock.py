"""OS-released locks shared by cloud agent and local MCP processes."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


class ExecutionLock:
    """Cross-process nonblocking byte lock.

    The default path preserves the historical global execution.lock contract.
    Named resource locks reuse the same primitive under execution-locks/ and
    store only a SHA-256 of the resource key, never a project/path secret.
    """

    def __init__(self, state_dir: Path, resource_key: str | None = None):
        if resource_key is None:
            self.path = state_dir / "execution.lock"
        else:
            if not isinstance(resource_key, str) or not resource_key:
                raise ValueError("resource_key must be a non-empty string")
            digest = hashlib.sha256(resource_key.encode("utf-8")).hexdigest()
            self.path = state_dir / "execution-locks" / f"{digest}.lock"
        self.stream = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            if stream.seek(0, 2) == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            return False
        self.stream = stream
        return True

    def release(self) -> None:
        if self.stream is not None:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            self.stream.close()
            self.stream = None
