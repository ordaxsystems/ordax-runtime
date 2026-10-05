"""Supervised child process for one ORDAX project preview runtime."""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path


class PreviewRuntime:
    def __init__(self, command: list[str], cwd: Path, log_path: Path) -> None:
        self.command = command
        self.cwd = cwd
        self.log_path = log_path
        self.child: subprocess.Popen | None = None

    def stop(self, *_args) -> None:
        child = self.child
        if child is None or child.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(child.pid), "/T", "/F"],
                    capture_output=True, text=True, shell=False, timeout=10,
                )
            else:
                child.terminate()
        except Exception:
            pass

    def run(self) -> int:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        with self.log_path.open("a", encoding="utf-8", errors="replace") as log:
            self.child = subprocess.Popen(
                self.command,
                cwd=str(self.cwd),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                shell=False,
                creationflags=creationflags,
            )
            signal.signal(signal.SIGTERM, self.stop)
            if hasattr(signal, "SIGINT"):
                signal.signal(signal.SIGINT, self.stop)
            return int(self.child.wait())


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="ordax-preview-runtime")
    result.add_argument("--token", required=True)
    result.add_argument("--cwd", required=True)
    result.add_argument("--log", required=True)
    result.add_argument("command", nargs=argparse.REMAINDER)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("preview runtime command is required")
    runtime = PreviewRuntime(
        command=command,
        cwd=Path(args.cwd).resolve(),
        log_path=Path(args.log).resolve(),
    )
    return runtime.run()


if __name__ == "__main__":
    raise SystemExit(main())
