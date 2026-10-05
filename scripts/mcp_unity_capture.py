from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(
    project_path: str,
    output_path: str | None,
    width: int,
    height: int,
    warmup_frames: int,
    timeout_seconds: int,
) -> int:
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
        env=dict(os.environ),
    )

    arguments = {
        "project_path": str(project),
        "width": width,
        "height": height,
        "warmup_frames": warmup_frames,
        "timeout_seconds": timeout_seconds,
    }
    if output_path:
        arguments["output_path"] = str(Path(output_path).expanduser().resolve())

    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            result = await session.call_tool("unity_capture_project", arguments=arguments)

            payload = None
            for item in result.content:
                text = getattr(item, "text", None)
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except Exception:
                    print(text)

            if getattr(result, "isError", False):
                print("MCP ERROR: unity_capture_project failed.")
                return 10

            if not isinstance(payload, dict):
                print("ERROR: Unity MCP returned no structured result.")
                return 11

            print(f"Unity MCP capture: {'OK' if payload.get('ok') else 'FAILED'}")
            if payload.get("returncode") is not None:
                print(f"Exit code: {payload['returncode']}")
            if payload.get("failure_classification"):
                print(f"Failure classification: {payload['failure_classification']}")
            if payload.get("capture_file"):
                print(f"Capture: {payload['capture_file']}")
            if payload.get("capture_size_bytes") is not None:
                print(f"Capture size: {payload['capture_size_bytes']} bytes")
            if payload.get("snapshot_file"):
                print(f"Snapshot: {payload['snapshot_file']}")
            snapshot = payload.get("snapshot")
            if isinstance(snapshot, dict):
                print(
                    "State: "
                    f"{snapshot.get('gameState', 'unknown')} | "
                    f"enemies={snapshot.get('activeEnemies', '?')} | "
                    f"elite={snapshot.get('activeElites', '?')} | "
                    f"boss={snapshot.get('activeBosses', '?')}"
                )
            if payload.get("log_file"):
                print(f"Log: {payload['log_file']}")

            if not payload.get("ok"):
                if payload.get("stderr"):
                    print(payload["stderr"])
                log = payload.get("unity_log") or ""
                if log:
                    print("\n--- Unity log tail ---")
                    print(log[-12000:])
                return 20

    print("MCP UNITY CAPTURE OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_path")
    parser.add_argument("--output-path")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--warmup-frames", type=int, default=120)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()
    return asyncio.run(
        run(
            args.project_path,
            args.output_path,
            args.width,
            args.height,
            args.warmup_frames,
            args.timeout_seconds,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
