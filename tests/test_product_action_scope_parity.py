from __future__ import annotations

import re
import unittest
from pathlib import Path

from ordax_dev_agent.product_action_scope import DEVICE_SCOPED_ACTIONS
from ordax_dev_agent.product_mcp import PRODUCT_MCP_TOOLS


ROOT = Path(__file__).resolve().parents[1]
TS_SCOPE = ROOT / "control-plane" / "cloudflare" / "src" / "product_action_scope.ts"
PY_SERVER = ROOT / "ordax_dev_agent" / "product_mcp_server.py"


def _ts_device_actions() -> set[str]:
    text = TS_SCOPE.read_text(encoding="utf-8")
    return set(re.findall(r'"(computer\.[a-z_]+)"', text))


class ProductActionScopeParityTests(unittest.TestCase):
    def test_python_and_cloudflare_device_scope_catalogs_are_identical(self) -> None:
        self.assertEqual(set(DEVICE_SCOPED_ACTIONS), _ts_device_actions())

    def test_every_product_mcp_computer_tool_is_device_scoped(self) -> None:
        computer_actions = {
            tool.action for tool in PRODUCT_MCP_TOOLS if tool.action.startswith("computer.")
        }
        self.assertEqual(computer_actions, set(DEVICE_SCOPED_ACTIONS))

    def test_device_scope_does_not_absorb_other_execution_authority(self) -> None:
        self.assertNotIn("terminal.exec", DEVICE_SCOPED_ACTIONS)
        self.assertNotIn("git.command", DEVICE_SCOPED_ACTIONS)
        self.assertNotIn("process.start", DEVICE_SCOPED_ACTIONS)
        self.assertTrue(all(action.startswith("computer.") for action in DEVICE_SCOPED_ACTIONS))

    def test_python_product_mcp_server_must_not_keep_project_bound_computer_tools(self) -> None:
        server = PY_SERVER.read_text(encoding="utf-8")
        definitions = re.findall(
            r"def (computer_[a-z_]+)\((.*?)\) -> dict\[str, Any\]:\n(.*?)(?=\n\n@mcp\.tool\(\)|\n\ndef main\(|\Z)",
            server,
            flags=re.S,
        )
        self.assertTrue(definitions, "expected executable computer_* MCP wrappers")
        for name, signature, body in definitions:
            self.assertNotRegex(signature, r"\bproject\s*:", f"{name} still requires project")
            self.assertNotIn("project=project", body, f"{name} still injects project into _invoke")
            self.assertNotIn('"project": project', body, f"{name} still injects project payload")


if __name__ == "__main__":
    unittest.main()
