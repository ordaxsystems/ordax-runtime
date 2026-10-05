from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCOPE = ROOT / "control-plane" / "cloudflare" / "src" / "product_action_scope.ts"
WORKER = ROOT / "control-plane" / "cloudflare" / "src" / "index.ts"
MCP = ROOT / "control-plane" / "cloudflare" / "src" / "mcp_http.ts"


def _quoted_computer_actions(text: str) -> set[str]:
    return set(re.findall(r'"(computer\.[a-z_]+)"', text))


def _mcp_computer_actions(text: str) -> set[str]:
    return set(re.findall(r'action:\s*"(computer\.[a-z_]+)"', text))


class ProductActionScopeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.scope = SCOPE.read_text(encoding="utf-8")
        cls.worker = WORKER.read_text(encoding="utf-8")
        cls.mcp = MCP.read_text(encoding="utf-8")

    def test_every_remote_computer_action_has_explicit_device_scope(self) -> None:
        scoped = _quoted_computer_actions(self.scope)
        published = _mcp_computer_actions(self.mcp)

        self.assertTrue(published, "MCP must publish typed computer actions")
        self.assertEqual(
            scoped,
            published,
            "device-scoped catalog must exactly match the published Computer MCP surface",
        )

    def test_worker_knows_every_device_scoped_computer_action(self) -> None:
        scoped = _quoted_computer_actions(self.scope)
        worker_actions = _quoted_computer_actions(self.worker)
        self.assertTrue(scoped.issubset(worker_actions))

    def test_scope_catalog_does_not_smuggle_shell_or_project_authority(self) -> None:
        scoped = _quoted_computer_actions(self.scope)
        self.assertNotIn("terminal.exec", scoped)
        self.assertNotIn("git.command", scoped)
        self.assertNotIn("process.start", scoped)
        self.assertTrue(all(action.startswith("computer.") for action in scoped))

    def test_binding_contract_forbids_synthetic_project_on_device_actions(self) -> None:
        self.assertIn('return scope === "device" ? project === null : project !== null;', self.scope)
        self.assertIn('if (DEVICE_SCOPED_ACTIONS.has(action)) return "device";', self.scope)

    def test_scope_is_explicit_not_prefix_authority(self) -> None:
        self.assertNotIn('action.startsWith("computer.")', self.scope)
        self.assertIn("new Set<string>([", self.scope)


if __name__ == "__main__":
    unittest.main()
