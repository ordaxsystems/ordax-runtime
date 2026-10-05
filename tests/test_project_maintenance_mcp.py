from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class ProjectMaintenanceMcpTests(unittest.IsolatedAsyncioTestCase):
    async def test_safe_move_delete_and_git_discovery_over_stdio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            source = project / "src" / "note.txt"
            source.parent.mkdir(parents=True)
            source.write_text("ORDAX safe maintenance\n", encoding="utf-8")
            (root / "agent-settings.json").write_text(
                json.dumps({
                    "default_project": "demo",
                    "projects": {"demo": {"path": str(project), "apps": []}},
                }),
                encoding="utf-8",
            )
            server = StdioServerParameters(
                command=sys.executable,
                args=["-m", "ordax_studio.mcp_server"],
                env={
                    **os.environ,
                    "ORDAX_AGENT_STATE_DIR": directory,
                    "ORDAX_MEMORY_DB": str(root / "memory.db"),
                },
                cwd=str(Path(__file__).resolve().parents[1]),
            )
            async with stdio_client(server) as (read, write):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    self.assertEqual("ordax-runtime", initialized.serverInfo.name)
                    tools = await session.list_tools()
                    names = {tool.name for tool in tools.tools}
                    for name in (
                        "project_move",
                        "project_delete",
                        "repository_info",
                        "sync_repository",
                    ):
                        self.assertIn(name, names)

                    opened = await session.call_tool(
                        "project_read",
                        {"project": "demo", "path": "src/note.txt"},
                    )
                    opened_payload = json.loads(opened.content[0].text)
                    expected = opened_payload["data"]["sha256"]

                    moved = await session.call_tool(
                        "project_move",
                        {
                            "project": "demo",
                            "source": "src/note.txt",
                            "destination": "src/notes/renamed.txt",
                            "expected_sha256": expected,
                        },
                    )
                    moved_payload = json.loads(moved.content[0].text)
                    self.assertTrue(moved_payload["ok"])
                    self.assertFalse(source.exists())
                    destination = project / "src" / "notes" / "renamed.txt"
                    self.assertTrue(destination.is_file())
                    self.assertEqual(expected, moved_payload["data"]["sha256"])

                    stale = await session.call_tool(
                        "project_delete",
                        {
                            "project": "demo",
                            "path": "src/notes/renamed.txt",
                            "expected_sha256": "0" * 64,
                        },
                    )
                    stale_payload = json.loads(stale.content[0].text)
                    self.assertFalse(stale_payload["ok"])
                    self.assertTrue(destination.is_file())

                    current = hashlib.sha256(destination.read_bytes()).hexdigest()
                    deleted = await session.call_tool(
                        "project_delete",
                        {
                            "project": "demo",
                            "path": "src/notes/renamed.txt",
                            "expected_sha256": current,
                        },
                    )
                    deleted_payload = json.loads(deleted.content[0].text)
                    self.assertTrue(deleted_payload["ok"])
                    self.assertFalse(destination.exists())

                    repository = await session.call_tool(
                        "repository_info", {"project": "demo"}
                    )
                    repository_payload = json.loads(repository.content[0].text)
                    self.assertTrue(repository_payload["ok"])
                    self.assertFalse(repository_payload["data"]["is_repository"])

                    sync = await session.call_tool(
                        "sync_repository", {"project": "demo", "branch": "main"}
                    )
                    sync_payload = json.loads(sync.content[0].text)
                    self.assertFalse(sync_payload["ok"])
                    self.assertIn("branch not allowed", sync_payload["summary"])


if __name__ == "__main__":
    unittest.main()
