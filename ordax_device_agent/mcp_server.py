"""Compatibility MCP entrypoint for the OrdaX Device Agent.

The current stdio server remains workstation-local. Product remote MCP will be a
separate authenticated gateway over the same typed action/capability model.
"""
from ordax_dev_agent.mcp_server import main, mcp, registry

__all__ = ["main", "mcp", "registry"]


if __name__ == "__main__":
    main()
