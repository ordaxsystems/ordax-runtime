"""Canonical MCP entrypoint for ORDAX Studio.\n\nThe Studio is owned by ``ordax-apps`` and hosted by the provider-neutral\nORDAX Runtime. Blender is exposed only as a typed runtime capability.\n"""
from ordax_dev_agent.mcp_server import main, mcp, registry

from .blender_connection import prepare_blender_connection as resolve_blender_connection
from .project_maintenance import execute_project_file_maintenance

__all__ = [
    "main",
    "mcp",
    "registry",
    "project_move",
    "project_delete",
    "repository_info",
    "sync_repository",
    "prepare_blender_connection",
]


def _result(result) -> dict:
    return {"ok": result.ok, "summary": result.summary, "data": result.data}


@mcp.tool()
def project_move(
    project: str | None = None,
    source: str = "",
    destination: str = "",
    expected_sha256: str = "",
) -> dict:
    """Move/rename one approved project text file with an exact SHA-256 precondition."""
    agent = registry()
    selected = agent.select_available_project(project)
    result = execute_project_file_maintenance(
        agent,
        "move",
        {
            "project": selected,
            "source": source,
            "destination": destination,
            "expected_sha256": expected_sha256,
        },
    )
    return _result(result)


@mcp.tool()
def project_delete(
    project: str | None = None,
    path: str = "",
    expected_sha256: str = "",
) -> dict:
    """Delete one approved project text file only when its SHA-256 still matches."""
    agent = registry()
    selected = agent.select_available_project(project)
    result = execute_project_file_maintenance(
        agent,
        "delete",
        {
            "project": selected,
            "path": path,
            "expected_sha256": expected_sha256,
        },
    )
    return _result(result)


@mcp.tool()
def repository_info(project: str | None = None, include_status: bool = True) -> dict:
    """Return Git identity, branch, remote and bounded dirty-state metadata."""
    agent = registry()
    selected = agent.select_available_project(project)
    return _result(
        agent.execute(
            "git.repository_info",
            {"project": selected, "include_status": include_status},
        )
    )


@mcp.tool()
def sync_repository(project: str | None = None, branch: str | None = None) -> dict:
    """Fast-forward one registered repository to an explicitly allowed branch."""
    agent = registry()
    selected = agent.select_available_project(project)
    payload = {"project": selected}
    if branch:
        payload["branch"] = branch
    return _result(agent.execute("git.sync", payload))


@mcp.tool()
def prepare_blender_connection(
    project: str | None = None,
    wait_seconds: float = 4.0,
) -> dict:
    """Inspect/adopt an existing Blender session and return actionable state."""
    agent = registry()
    selected = agent.select_available_project(project)
    return resolve_blender_connection(
        agent,
        selected,
        wait_seconds=max(0.5, min(float(wait_seconds), 10.0)),
    )


if __name__ == "__main__":
    main()
