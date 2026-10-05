"""Typed integration with Unity's official CLI and Pipeline package.

This module never invokes a shell. It only calls the official `unity` binary
with structured argv, keeps the registered project path explicit, and returns
machine-readable JSON envelopes when available.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .models import ActionResult


_COMMAND_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")
_BLOCKED_ARGUMENTS = {
    "--project-path",
    "--runtime",
    "--runtime-path",
    "--format",
    "--json",
}


def find_unity_cli() -> Path | None:
    override = os.environ.get("ORDAX_UNITY_CLI")
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return path.resolve()

    resolved = shutil.which("unity")
    if resolved:
        return Path(resolved).resolve()
    return None


def _parse_json(stdout: str) -> Any:
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Defensive fallback for older builds that may print a short banner
        # before a JSON envelope. Prefer the last decodable JSON line.
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def run_unity_cli(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout_seconds: float = 120.0,
) -> ActionResult:
    executable = find_unity_cli()
    if executable is None:
        return ActionResult(
            False,
            "Unity CLI executable not found",
            {
                "available": False,
                "hint": "Install the official Unity CLI or set ORDAX_UNITY_CLI",
            },
        )

    command = [str(executable), *args]
    env = os.environ.copy()
    # Avoid a background update check blocking first command execution on
    # workstations where the CLI's network/update helper is unhealthy.
    env.setdefault("UNITY_NO_UPDATE_CHECK", "1")
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=max(1.0, float(timeout_seconds)),
        shell=False,
        env=env,
    )
    parsed = _parse_json(completed.stdout)
    success = completed.returncode == 0
    if isinstance(parsed, dict) and "success" in parsed:
        success = success and bool(parsed.get("success"))

    data = {
        "available": True,
        "executable": str(executable),
        "command": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-40000:],
        "stderr": completed.stderr[-20000:],
        "json": parsed,
    }
    return ActionResult(
        success,
        "Unity CLI command completed" if success else "Unity CLI command failed",
        data,
    )


def cli_status(project_root: Path, *, timeout_seconds: float = 60.0) -> ActionResult:
    executable = find_unity_cli()
    if executable is None:
        return ActionResult(
            True,
            "Unity CLI is not installed or not on PATH",
            {"available": False},
        )

    version = run_unity_cli(
        ["--version"],
        cwd=project_root,
        timeout_seconds=timeout_seconds,
    )
    status = run_unity_cli(
        [
            "status",
            "--project-path",
            str(project_root),
            "--format",
            "json",
            "--non-interactive",
        ],
        cwd=project_root,
        timeout_seconds=timeout_seconds,
    )
    pipeline = run_unity_cli(
        ["pipeline", "list", "--format", "json", "--non-interactive"],
        cwd=project_root,
        timeout_seconds=timeout_seconds,
    )

    return ActionResult(
        True,
        "Unity CLI status inspected",
        {
            "available": True,
            "executable": str(executable),
            "version": version.data,
            "editor_status": status.data,
            "pipeline": pipeline.data,
        },
    )


def install_pipeline(
    project_root: Path,
    *,
    force: bool = False,
    timeout_seconds: float = 180.0,
) -> ActionResult:
    args = [
        "pipeline",
        "install",
        "--project-path",
        str(project_root),
        "--format",
        "json",
        "--non-interactive",
    ]
    if force:
        args.append("--force")
    return run_unity_cli(
        args,
        cwd=project_root,
        timeout_seconds=timeout_seconds,
    )


def pipeline_catalog(
    project_root: Path,
    *,
    timeout_seconds: float = 60.0,
) -> ActionResult:
    return run_unity_cli(
        [
            "list",
            "--project-path",
            str(project_root),
            "--format",
            "json",
            "--non-interactive",
        ],
        cwd=project_root,
        timeout_seconds=timeout_seconds,
    )


def pipeline_command(
    project_root: Path,
    command_name: str,
    arguments: list[str] | None = None,
    *,
    timeout_seconds: float = 120.0,
) -> ActionResult:
    name = command_name.strip()
    if not _COMMAND_NAME.fullmatch(name):
        return ActionResult(False, "Invalid Unity Pipeline command name")

    argv = []
    for raw in arguments or []:
        value = str(raw)
        if "\x00" in value or "\n" in value or "\r" in value:
            return ActionResult(False, "Unity Pipeline arguments may not contain control characters")
        option_name = value.split("=", 1)[0] if value.startswith("--") else value
        if option_name in _BLOCKED_ARGUMENTS:
            return ActionResult(
                False,
                f"Unity Pipeline argument is managed by OrdaX and cannot be overridden: {option_name}",
            )
        argv.append(value)

    return run_unity_cli(
        [
            "command",
            name,
            "--project-path",
            str(project_root),
            "--format",
            "json",
            "--non-interactive",
            *argv,
        ],
        cwd=project_root,
        timeout_seconds=timeout_seconds,
    )
