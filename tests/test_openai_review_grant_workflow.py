from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


class OpenAiReviewGrantWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.workflow = (
            root / ".github" / "workflows" / "openai-review-grant.yml"
        ).read_text(encoding="utf-8")
        self.plugin = json.loads(
            (root / "plugins" / "ordax-chatgpt" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        self.mcp_http = (
            root / "control-plane" / "cloudflare" / "src" / "mcp_http.ts"
        ).read_text(encoding="utf-8")

    def _workflow_actions(self, variable: str) -> set[str]:
        block = self.workflow.split(f"{variable} = [", 1)[1].split("]", 1)[0]
        return set(re.findall(r'"([a-z][a-z0-9_.-]+)"', block))

    def test_review_grants_split_device_and_project_authority(self) -> None:
        self.assertIn(
            "ORDAX_OPERATOR_TOKEN: ${{ secrets.ORDAX_OPERATOR_TOKEN }}",
            self.workflow,
        )
        self.assertIn("/v3/product-grants/from-link", self.workflow)
        self.assertIn("timedelta(days=30)", self.workflow)
        self.assertIn('create_grant(device_actions, [], "device")', self.workflow)
        self.assertIn(
            'project_actions,\n              ["ordax-review-demo"],\n              "project",',
            self.workflow,
        )

        device_actions = self._workflow_actions("device_actions")
        project_actions = self._workflow_actions("project_actions")
        self.assertEqual(
            device_actions,
            {
                "computer.access_status",
                "computer.active_window",
                "computer.click",
                "computer.focus_window",
                "computer.launch_app",
                "computer.mouse_move",
                "computer.processes",
                "computer.screen_info",
                "computer.screenshot",
                "computer.scroll",
                "computer.type",
                "computer.windows",
            },
        )
        self.assertEqual(
            project_actions,
            {
                "git.status",
                "project.search_text",
                "project.text_read",
                "project.text_write",
                "projects.list",
                "workspace.repository_catalog",
            },
        )
        self.assertFalse(device_actions & project_actions)
        self.assertNotIn("projects.list", device_actions)
        self.assertNotIn("workspace.repository_catalog", device_actions)
        self.assertTrue(
            {"projects.list", "workspace.repository_catalog"} <= project_actions
        )

        forbidden = {
            "computer.hotkey",
            "computer.terminate_process",
            "git.command",
            "process.start",
            "process.stop",
            "terminal.exec",
        }
        self.assertFalse(forbidden & (device_actions | project_actions))

    def test_review_api_client_has_explicit_product_identity(self) -> None:
        self.assertIn(
            '"User-Agent": "ORDAX-OpenAI-Review-Provisioner/1.0 '
            '(+https://github.com/washingtonmsdj/mcp-blender)"',
            self.workflow,
        )
        self.assertNotIn('"User-Agent": "Mozilla/', self.workflow)

    def test_operator_secret_is_not_a_dispatch_input(self) -> None:
        dispatch = self.workflow.split("permissions:", 1)[0]
        self.assertIn("link_id:", dispatch)
        self.assertNotIn("operator_token", dispatch.lower())
        self.assertIn("environment: cloudflare-v3", self.workflow)
        self.assertIn("set -euo pipefail", self.workflow)

    def test_positive_review_tools_are_covered_by_correct_scope(self) -> None:
        device_actions = self._workflow_actions("device_actions")
        project_actions = self._workflow_actions("project_actions")
        granted_actions = device_actions | project_actions

        tool_actions: dict[str, str | None] = {}
        for line in self.mcp_http.splitlines():
            name_match = re.search(r'\{ name: "([^"]+)"', line)
            if not name_match:
                continue
            action_match = re.search(r'action: "([^"]+)"', line)
            tool_actions[name_match.group(1)] = (
                action_match.group(1) if action_match else None
            )

        positive_cases = self.plugin["extensions"]["com.openai"]["review"][
            "test_cases"
        ]["positive"]
        for case in positive_cases:
            triggered = [
                item.strip()
                for item in str(case.get("tools_triggered") or "").split(",")
                if item.strip()
            ]
            self.assertTrue(triggered, case.get("description"))
            for tool in triggered:
                self.assertIn(tool, tool_actions, f"review tool missing from MCP: {tool}")
                action = tool_actions[tool]
                if action is None:
                    continue
                self.assertIn(
                    action,
                    granted_actions,
                    f"review tool {tool} requires ungranted action {action}",
                )
                if action.startswith("computer."):
                    self.assertIn(action, device_actions)
                    self.assertNotIn(action, project_actions)
                elif (
                    action.startswith("project.")
                    or action in {
                        "git.status",
                        "projects.list",
                        "workspace.repository_catalog",
                    }
                ):
                    self.assertIn(action, project_actions)
                    self.assertNotIn(action, device_actions)


if __name__ == "__main__":
    unittest.main()
