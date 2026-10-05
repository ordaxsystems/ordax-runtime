from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main(project: str | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    python = repo_root / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        print("ERROR: .venv Python not found. Run scripts\\windows\\mcp-start.ps1 first.")
        return 2

    server = StdioServerParameters(
        command=str(python),
        args=["-m", "ordax_studio.mcp_server"],
        cwd=str(repo_root),
        env=dict(os.environ),
    )

    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialized = await session.initialize()
            if initialized.serverInfo.name != "ordax-runtime":
                print(f"ERROR: unexpected MCP identity: {initialized.serverInfo.name}")
                return 3

            tools = await session.list_tools()
            tool_names = {tool.name for tool in tools.tools}
            required = {
                "studio_status", "repository_catalog", "agent_capabilities",
                "project_inventory", "project_read", "project_write", "project_patch",
                "project_move", "project_delete", "repository_info", "sync_repository",
                "project_preview_status", "git_status", "git_diff",
                "install_blender_adoption", "blender_instances", "adopt_blender",
            }
            missing = sorted(required - tool_names)
            if missing:
                print("ERROR: missing ORDAX Studio MCP tools:", ", ".join(missing))
                return 4

            print("ORDAX STUDIO MCP CONNECTED")
            print("Tools:", ", ".join(sorted(tool_names)))
            catalog = await session.call_tool("repository_catalog", {})
            if catalog.isError:
                print("ERROR: repository_catalog failed")
                return 5

            payload = json.loads(catalog.content[0].text)
            if payload.get("ok") is not True:
                print("ERROR: repository_catalog returned a non-success payload")
                print(json.dumps(payload, indent=2, ensure_ascii=False))
                return 5
            print(json.dumps(payload, indent=2, ensure_ascii=False))

            if project:
                status = await session.call_tool("studio_status", {"project": project})
                if status.isError:
                    print(f"ERROR: studio_status failed for project {project}")
                    return 6
                print(status.content[0].text)

    print("ORDAX STUDIO MCP SMOKE TEST OK")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("project", nargs="?", help="optional registered ORDAX project slug")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.project)))
