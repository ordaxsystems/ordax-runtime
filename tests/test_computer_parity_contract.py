from __future__ import annotations

import unittest
from pathlib import Path

from ordax_dev_agent.product_gateway import PRODUCT_ACTIONS
from ordax_dev_agent.product_mcp import PRODUCT_MCP_TOOLS


ROOT = Path(__file__).parents[1]


class ComputerParityContractTests(unittest.TestCase):
    ACTIONS = {
        "computer.screen_info": "computer_screen_info",
        "computer.mouse_move": "computer_mouse_move",
        "computer.drag": "computer_drag",
        "computer.clipboard_read": "computer_clipboard_read",
        "computer.clipboard_write": "computer_clipboard_write",
        "computer.launch_app": "computer_launch_app",
    }

    def test_gateway_and_product_mcp_expose_every_parity_action(self):
        gateway = set(PRODUCT_ACTIONS)
        tools = {tool.action: tool.name for tool in PRODUCT_MCP_TOOLS}
        for action, tool_name in self.ACTIONS.items():
            with self.subTest(action=action):
                self.assertIn(action, gateway)
                self.assertEqual(tool_name, tools.get(action))

    def test_product_mcp_server_has_typed_functions(self):
        source = (ROOT / "ordax_dev_agent" / "product_mcp_server.py").read_text(encoding="utf-8")
        for tool_name in self.ACTIONS.values():
            with self.subTest(tool=tool_name):
                self.assertIn(f"def {tool_name}(", source)

    def test_cloudflare_worker_advertises_and_authorizes_actions(self):
        mcp = (ROOT / "control-plane" / "cloudflare" / "src" / "mcp_http.ts").read_text(encoding="utf-8")
        worker = (ROOT / "control-plane" / "cloudflare" / "src" / "index.ts").read_text(encoding="utf-8")
        for action, tool_name in self.ACTIONS.items():
            with self.subTest(action=action):
                self.assertIn(f'name: "{tool_name}"', mcp)
                self.assertIn(f'action: "{action}"', mcp)
                self.assertIn(f'"{action}"', worker)

    def test_effect_classification_is_explicit(self):
        mcp = (ROOT / "control-plane" / "cloudflare" / "src" / "mcp_http.ts").read_text(encoding="utf-8")
        self.assertIn('"computer_screen_info"', mcp)
        self.assertIn('"computer_clipboard_read"', mcp)
        self.assertIn('"computer_drag"', mcp)
        self.assertIn('"computer_mouse_move"', mcp)
        self.assertIn('"computer_clipboard_write"', mcp)
        self.assertIn('"computer_launch_app"', mcp)

    def test_device_scoped_computer_runtime_does_not_require_project(self):
        for relative in (
            "ordax_dev_agent/computer_control_actions.py",
            "ordax_dev_agent/computer_parity_actions.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            with self.subTest(source=relative):
                self.assertNotIn("self._project(payload)", source)

    def test_computer_screenshot_uses_device_artifact_namespace(self):
        source = (
            ROOT / "ordax_dev_agent" / "computer_control_actions.py"
        ).read_text(encoding="utf-8")
        self.assertIn('self.config.state_dir / "artifacts" / "computer"', source)
        self.assertIn('"scope": "device"', source)
        self.assertNotIn('self._computer_artifact_path(project)', source)


if __name__ == "__main__":
    unittest.main()
