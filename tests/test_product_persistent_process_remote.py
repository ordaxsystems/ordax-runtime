from __future__ import annotations

import unittest
from pathlib import Path

from ordax_dev_agent.models import ActionResult
from ordax_dev_agent.product_gateway import (
    PRODUCT_ACTIONS,
    _sanitize_product_result,
)
from ordax_dev_agent.product_mcp import PRODUCT_MCP_TOOLS


PROCESS_ACTIONS = {
    "process.status",
    "process.list",
    "process.logs",
    "process.start",
    "process.write_stdin",
    "process.stop",
}


class ProductPersistentProcessRemoteTests(unittest.TestCase):
    def test_gateway_exposes_typed_project_scoped_process_actions(self) -> None:
        self.assertTrue(PROCESS_ACTIONS.issubset(PRODUCT_ACTIONS))
        for name in PROCESS_ACTIONS:
            self.assertTrue(PRODUCT_ACTIONS[name].project_required, name)
    def test_mcp_catalog_maps_all_process_tools_without_generic_executor(self) -> None:
        by_action = {tool.action: tool for tool in PRODUCT_MCP_TOOLS}
        self.assertTrue(PROCESS_ACTIONS.issubset(by_action))
        self.assertEqual(by_action["process.status"].effect, "read")
        self.assertEqual(by_action["process.list"].effect, "read")
        self.assertEqual(by_action["process.logs"].effect, "read")
        self.assertEqual(by_action["process.start"].effect, "execute")
        self.assertEqual(by_action["process.write_stdin"].effect, "execute")
        self.assertEqual(by_action["process.stop"].effect, "write")

    @staticmethod
    def _raw_state() -> dict:
        return {
            "process_id": "11111111-1111-1111-1111-111111111111",
            "project": "demo",
            "state": "running",
            "cwd": ".",
            "manager_pid": 999,
            "child_pid": 1000,
            "running": True,
            "ownership_valid": True,
            "token": "secret-ownership-token",
            "argv": ["python", "server.py"],
            "env": {"SECRET": "value"},
            "log_path": "C:/private/process.log",
        }
    def test_process_state_redacts_runtime_ownership_internals(self) -> None:
        result = _sanitize_product_result(
            "process.status",
            ActionResult(True, "status", self._raw_state()),
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.data["child_pid"], 1000)
        for sensitive in ("token", "argv", "env", "log_path", "manager_pid"):
            self.assertNotIn(sensitive, result.data)

    def test_process_list_and_logs_sanitize_nested_state(self) -> None:
        listed = _sanitize_product_result(
            "process.list",
            ActionResult(True, "list", {"project": "demo", "processes": [self._raw_state()]}),
        )
        self.assertEqual(len(listed.data["processes"]), 1)
        self.assertNotIn("token", listed.data["processes"][0])
        logs = _sanitize_product_result(
            "process.logs",
            ActionResult(True, "logs", {
                "process": self._raw_state(), "tail": "ready\n", "size_bytes": 6, "truncated": False,
            }),
        )
        self.assertEqual(logs.data["tail"], "ready\n")
        self.assertNotIn("log_path", logs.data["process"])
    def test_stdin_result_exposes_only_receipt_metadata(self) -> None:
        result = _sanitize_product_result(
            "process.write_stdin",
            ActionResult(True, "queued", {
                "process_id": "11111111-1111-1111-1111-111111111111",
                "queued_bytes": 4,
                "newline": True,
                "token": "must-not-leak",
            }),
        )
        self.assertEqual(
            set(result.data),
            {"process_id", "queued_bytes", "newline"},
        )


    def test_cloudflare_public_mcp_and_control_plane_expose_same_actions(self) -> None:
        root = Path(__file__).resolve().parents[1]
        mcp = (root / "control-plane" / "cloudflare" / "src" / "mcp_http.ts").read_text(encoding="utf-8")
        worker = (root / "control-plane" / "cloudflare" / "src" / "index.ts").read_text(encoding="utf-8")
        tool_names = {
            "process_status", "process_list", "process_logs",
            "process_start", "process_write_stdin", "process_stop",
        }
        for tool in tool_names:
            self.assertIn(f'name: "{tool}"', mcp, tool)
        for action in PROCESS_ACTIONS:
            self.assertIn(f'"{action}"', worker, action)
        self.assertIn("PROCESS_ARGV", mcp)
        self.assertIn("PROCESS_ENV_OBJECT", mcp)
        self.assertIn("PROCESS_STDIN_TEXT", mcp)


if __name__ == "__main__":
    unittest.main()
