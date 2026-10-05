"""OS-released lock shared by cloud agent and local MCP processes."""
from __future__ import annotations

import os
from pathlib import Path


class ExecutionLock:
    def __init__(self, state_dir: Path):
        self.path = state_dir / "execution.lock"
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
