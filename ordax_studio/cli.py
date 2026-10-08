from __future__ import annotations

import argparse
import json
from typing import Any

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig


def registry() -> ActionRegistry:
    return ActionRegistry(AgentConfig.from_env())


def select_project(agent: ActionRegistry, requested: str | None = None) -> str:
    return agent.select_available_project(requested)

def emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def status(agent: ActionRegistry) -> dict[str, Any]:
    store = agent._memory_store_instance()
    return {
        "product": "ORDAX Studio",
        "role": "persistent-agentic-development-runtime",
        "default_project": agent.config.default_project,
        "active_project": store.active_project(),
        "projects": [project.public() for project in agent.projects.values()],
        "memory": store.status(),
        "action_groups": sorted({name.split(".", 1)[0] for name in agent.names}),
        "action_count": len(agent.names),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ordax-studio", description="ORDAX Studio local persistent runtime")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="show projects, memory and capability summary")
    resume = sub.add_parser("resume", help="resume a persistent project session")
    resume.add_argument("project", nargs="?")
    context = sub.add_parser("context", help="show persistent context for a project")
    context.add_argument("project", nargs="?")

    checkpoint = sub.add_parser("checkpoint", help="save a resumable project checkpoint")
    checkpoint.add_argument("project")
    checkpoint.add_argument("summary")
    finish = sub.add_parser("finish", help="finish a persistent session")
    finish.add_argument("session_id", type=int)
    sub.add_parser("mcp", help="start the local ORDAX project MCP server")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "mcp":
        from ordax_dev_agent.mcp_server import main as mcp_main
        mcp_main()
        return 0
    agent = registry()
    if args.command == "status":
        emit(status(agent))
        return 0
    if args.command == "finish":
        result = agent.execute("session.finish", {"session_id": args.session_id})
    else:
        project = select_project(agent, getattr(args, "project", None))
        action = {"resume": "session.resume", "context": "memory.context", "checkpoint": "memory.checkpoint"}[args.command]
        payload: dict[str, Any] = {"project": project}
        if args.command == "checkpoint":
            payload["summary"] = args.summary
        result = agent.execute(action, payload)

    emit({"ok": result.ok, "summary": result.summary, "data": result.data})
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
