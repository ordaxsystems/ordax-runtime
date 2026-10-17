"""Real stdio smoke for the existing device-only MCP, without Studio."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]


def tool_payload(result):
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict) and "ok" in structured:
        return structured
    return json.loads(result.content[0].text)


class DeviceMcpEntrypointTests(unittest.TestCase):
    def test_entrypoints_never_import_or_launch_studio(self) -> None:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        scripts = metadata["project"]["scripts"]
        for name in ("ordax-device-mcp", "ordax-connector-mcp"):
            self.assertEqual("ordax_device_agent.mcp_server:main", scripts[name])
        source = (ROOT / "ordax_device_agent" / "mcp_server.py").read_text(encoding="utf-8")
        self.assertNotIn("ordax_studio", source)
        self.assertIn("from ordax_dev_agent.mcp_server import main, mcp, registry", source)


class DeviceMcpFileRoundTripTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_policy_read_patch_write_and_denial_over_stdio(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            state.mkdir()
            allowed = root / "allowed"
            allowed.mkdir()
            target = allowed / "notes.txt"
            outside = root / "blocked.txt"
            outside.write_text("private\n", encoding="utf-8")
            settings_path = state / "agent-settings.json"
            settings = {
                "projects": {},
                "computer_access": {
                    "enabled": True,
                    "full_access": False,
                    "full_filesystem": False,
                    "allowed_roots": [str(allowed)],
                },
            }
            settings_path.write_text(json.dumps(settings), encoding="utf-8")

            server = StdioServerParameters(
                command=sys.executable,
                args=["-m", "ordax_device_agent.mcp_server"],
                env={**os.environ, "ORDAX_AGENT_STATE_DIR": str(state)},
                cwd=str(ROOT),
            )
            async with stdio_client(server) as (read, write):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    self.assertEqual("ordax-runtime", initialized.serverInfo.name)

                    advertised = {tool.name for tool in (await session.list_tools()).tools}
                    required = {
                        "computer_access_status", "computer_directory_list",
                        "computer_text_read", "computer_text_write",
                        "computer_text_patch",
                    }
                    self.assertTrue(required.issubset(advertised))
                    self.assertNotIn("shell_execute", advertised)

                    policy = tool_payload(await session.call_tool("computer_access_status", {}))
                    self.assertTrue(policy["ok"])
                    self.assertEqual([str(allowed)], policy["data"]["allowed_roots"])

                    created = tool_payload(await session.call_tool("computer_text_write", {
                        "path": str(target), "content": "v1\n", "create": True,
                    }))
                    self.assertTrue(created["ok"], created)
                    inventory = tool_payload(await session.call_tool("computer_directory_list", {
                        "path": str(allowed), "max_depth": 1, "max_entries": 5,
                    }))
                    self.assertTrue(inventory["ok"], inventory)
                    self.assertIn("notes.txt", [item["name"] for item in inventory["data"]["entries"]])

                    original = tool_payload(await session.call_tool("computer_text_read", {
                        "path": str(target),
                    }))
                    self.assertTrue(original["ok"], original)
                    self.assertEqual("v1\n", original["data"]["content"])
                    before = original["data"]["sha256"]
                    self.assertEqual(hashlib.sha256(b"v1\n").hexdigest(), before)

                    patched = tool_payload(await session.call_tool("computer_text_patch", {
                        "path": str(target),
                        "expected_sha256": before,
                        "replacements": [{"old": "v1", "new": "v2", "expected_count": 1}],
                    }))
                    self.assertTrue(patched["ok"], patched)
                    self.assertEqual("v2\n", target.read_text(encoding="utf-8"))

                    stale = tool_payload(await session.call_tool("computer_text_write", {
                        "path": str(target), "content": "overwrite\n", "expected_sha256": before,
                    }))
                    self.assertFalse(stale["ok"], stale)
                    self.assertEqual("v2\n", target.read_text(encoding="utf-8"))

                    current = tool_payload(await session.call_tool("computer_text_read", {
                        "path": str(target),
                    }))
                    updated = tool_payload(await session.call_tool("computer_text_write", {
                        "path": str(target), "content": "v3\n",
                        "expected_sha256": current["data"]["sha256"],
                    }))
                    self.assertTrue(updated["ok"], updated)
                    self.assertEqual("v3\n", target.read_text(encoding="utf-8"))

                    denied = tool_payload(await session.call_tool("computer_text_read", {
                        "path": str(outside),
                    }))
                    self.assertFalse(denied["ok"], denied)
                    self.assertEqual("private\n", outside.read_text(encoding="utf-8"))

                    settings["computer_access"]["enabled"] = False
                    settings_path.write_text(json.dumps(settings), encoding="utf-8")
                    disabled = tool_payload(await session.call_tool("computer_text_read", {
                        "path": str(target),
                    }))
                    self.assertFalse(disabled["ok"], disabled)
                    self.assertIn("disabled", disabled["summary"])
                    self.assertEqual("v3\n", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
