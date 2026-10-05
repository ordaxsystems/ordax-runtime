from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HANDLER = ROOT / "control-plane" / "cloudflare" / "src" / "product_device_grants.ts"
MCP = ROOT / "control-plane" / "cloudflare" / "src" / "mcp_http.ts"


class OwnerDeviceComputerGrantContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.handler = HANDLER.read_text(encoding="utf-8")
        cls.mcp = MCP.read_text(encoding="utf-8")

    def test_owner_identity_and_device_are_server_derived(self) -> None:
        self.assertIn("authenticateProductRequest(request, env)", self.handler)
        self.assertIn("linkedDeviceForOwner(env, identity.subjectId, linkId)", self.handler)
        self.assertIn("l.subject_id = ?2", self.handler)
        self.assertNotIn("body.subject_id", self.handler)
        self.assertNotIn("body.device_id", self.handler)
        self.assertNotIn("body.space_id", self.handler)

    def test_full_computer_control_is_a_fixed_server_mode(self) -> None:
        self.assertIn('FULL_COMPUTER_CONTROL_MODE = "full-computer-control"', self.handler)
        self.assertIn("const actions = stableComputerActions();", self.handler)
        self.assertIn("[...DEVICE_SCOPED_ACTIONS].sort()", self.handler)
        self.assertNotIn("body.actions", self.handler)
        self.assertNotIn("body.projects", self.handler)
        self.assertNotIn("body.project", self.handler)

    def test_device_grants_have_no_synthetic_project_scope(self) -> None:
        self.assertIn('const projectsJson = "[]";', self.handler)
        self.assertIn("projects_json", self.handler)
        self.assertNotIn('project_id', self.handler)

    def test_create_body_is_small_and_allowlisted(self) -> None:
        self.assertIn('new Set(["link_id", "mode", "expires_at"])', self.handler)
        self.assertIn("MAX_BODY_BYTES = 16 * 1024", self.handler)
        self.assertIn("Object.keys(body).every((key) => allowed.has(key))", self.handler)

    def test_revoke_is_subject_scoped_and_only_for_device_computer_grants(self) -> None:
        self.assertIn("g.subject_id = ?2", self.handler)
        self.assertIn("rowIsDeviceComputerGrant(row)", self.handler)
        self.assertIn("DEVICE_SCOPED_ACTIONS.has(action)", self.handler)
        self.assertIn("projects.length === 0", self.handler)

    def test_owner_grant_handler_is_not_an_mcp_tool(self) -> None:
        self.assertNotIn("createOwnerDeviceComputerGrant", self.mcp)
        self.assertNotIn("revokeOwnerDeviceComputerGrant", self.mcp)
        self.assertNotIn("full-computer-control", self.mcp)


if __name__ == "__main__":
    unittest.main()
