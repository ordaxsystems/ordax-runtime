from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(project_path: str, timeout_seconds: int) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    python = repo_root / ".venv" / "Scripts" / "python.exe"
    project = Path(project_path).expanduser().resolve()

    if not python.is_file():
        print("ERROR: .venv Python not found. Run scripts\\windows\\mcp-start.ps1 first.")
        return 2

    if not (project / "Assets").is_dir():
        print(f"ERROR: not a Unity project: {project}")
        return 3

    server = StdioServerParameters(
        command=str(python),
        args=["-m", "mcp_blender_unity.server"],
        cwd=str(repo_root),
        # This is our own trusted local bridge. Unity/UPM needs the normal
        # Windows user environment (USERPROFILE/APPDATA/LOCALAPPDATA/etc.).
        env=dict(os.environ),
    )

    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool(
                "unity_compile_project",
                arguments={
                    "project_path": str(project),
                    "timeout_seconds": timeout_seconds,
                },
            )

            payload = None
            for item in result.content:
                text = getattr(item, "text", None)
                if text:
                    try:
                        payload = json.loads(text)
                    except Exception:
                        print(text)

            if getattr(result, "isError", False):
                print("MCP ERROR: unity_compile_project failed.")
                return 10

            if not isinstance(payload, dict):
                print("ERROR: Unity MCP returned no structured result.")
                return 11

            print(f"Unity MCP compile: {'OK' if payload.get('ok') else 'FAILED'}")
            if payload.get("retried_after_upm_startup_failure"):
                print("UPM recovery: automatic managed-IPC retry was used")
            if payload.get("first_attempt_returncode") is not None:
                print(f"First attempt exit code: {payload['first_attempt_returncode']}")
            if payload.get("returncode") is not None:
                print(f"Exit code: {payload['returncode']}")
            if payload.get("failure_classification"):
                print(f"Failure classification: {payload['failure_classification']}")
            installation = payload.get("installation") or {}
            if installation.get("missing_components"):
                print("Missing Unity installation components:")
                for component in installation["missing_components"]:
                    print(f"  - {component}")
            if payload.get("managed_upm_log_file"):
                print(f"Managed UPM log: {payload['managed_upm_log_file']}")
            if payload.get("stderr"):
                print("Bridge stderr:")
                print(payload["stderr"])
            if payload.get("managed_upm_log") and not payload.get("ok"):
                print("\n--- Managed UPM log tail ---")
                print(payload["managed_upm_log"][-12000:])
            if payload.get("log_file"):
                print(f"Log: {payload['log_file']}")

            patterns = payload.get("detected_error_patterns") or []
            if patterns:
                print("Detected error patterns:")
                for pattern in patterns:
                    print(f"  - {pattern}")

            if not payload.get("ok"):
                log = payload.get("unity_log") or ""
                if log:
                    print("\n--- Unity log tail ---")
                    print(log[-12000:])
                return 20

    print("MCP UNITY COMPILE OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_path")
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    args = parser.parse_args()
    return asyncio.run(run(args.project_path, args.timeout_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
