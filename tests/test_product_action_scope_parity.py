from __future__ import annotations

import re
import unittest
from pathlib import Path

from ordax_dev_agent.product_action_scope import (
    APP_INTELLIGENCE_DEVICE_ACTIONS,
    COMPUTER_DEVICE_ACTIONS,
    DEVICE_SCOPED_ACTIONS,
)
from ordax_dev_agent.product_mcp import PRODUCT_MCP_TOOLS

ROOT = Path(__file__).resolve().parents[1]
PY_SERVER = ROOT / "ordax_dev_agent" / "product_mcp_server.py"


class ProductActionScopeParityTests(unittest.TestCase):
    def test_every_product_mcp_computer_tool_is_device_scoped(self) -> None:
        computer_actions = {
            tool.action for tool in PRODUCT_MCP_TOOLS if tool.action.startswith("computer.")
        }
        self.assertEqual(computer_actions, set(COMPUTER_DEVICE_ACTIONS))

    def test_device_scope_does_not_absorb_other_execution_authority(self) -> None:
        self.assertNotIn("terminal.exec", DEVICE_SCOPED_ACTIONS)
        self.assertNotIn("git.command", DEVICE_SCOPED_ACTIONS)
        self.assertNotIn("process.start", DEVICE_SCOPED_ACTIONS)
        self.assertTrue(all(action.startswith("computer.") for action in COMPUTER_DEVICE_ACTIONS))
        self.assertEqual(
            APP_INTELLIGENCE_DEVICE_ACTIONS,
            frozenset({"intelligence.app_catalog", "intelligence.app_detail"}),
        )
        self.assertEqual(
            DEVICE_SCOPED_ACTIONS,
            frozenset({*COMPUTER_DEVICE_ACTIONS, *APP_INTELLIGENCE_DEVICE_ACTIONS}),
        )

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


# Project Workspace M1 is an extension of Product project-scope isolation.
# Keep the contracts in the already-required Runtime Contracts CI test module.
import dataclasses
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone

from ordax_dev_agent.project_workspace_port import (
    ProjectWorkspaceDenied, ProjectWorkspacePort, VerifiedProjectResource,
    WorkspaceActor,
)


class ProjectWorkspacePortContractTests(unittest.TestCase):
    DEVICE = "device_fixture"
    RESOURCE = "resource_fixture_12345678"
    ACTOR = WorkspaceActor("owner_fixture", "client_fixture", "session_fixture", "project_fixture")

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ordax-workspace-port-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.permitted = True
        self.local_permitted = True
        self.lookups = 0
        self.revoke_at = None
        info = self.root.stat()
        self.grant = VerifiedProjectResource(
            actor_id=self.ACTOR.actor_id, client_id=self.ACTOR.client_id,
            workspace_session_id=self.ACTOR.workspace_session_id,
            project_id=self.ACTOR.project_id, device_id=self.DEVICE,
            resource_id=self.RESOURCE, root=self.root,
            root_device=info.st_dev, root_inode=info.st_ino,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            allowed_actions=frozenset({"read"}),
        )

    def grants(self, actor, device, resource):
        self.lookups += 1
        if (not self.permitted or self.lookups == self.revoke_at
            or actor != self.ACTOR or device != self.DEVICE or resource != self.RESOURCE):
            return None
        return self.grant

    def policy(self, root):
        return self.local_permitted and root == self.root

    def port(self):
        return ProjectWorkspacePort(
            device_id=self.DEVICE, grants=self.grants, local_policy=self.policy
        )

    def snapshot(self):
        return self.port().snapshot(self.ACTOR, self.DEVICE, self.RESOURCE)

    def test_valid_binding_and_read_only_summary(self):
        receipt = self.port().verify_binding(self.ACTOR, self.DEVICE, self.RESOURCE)
        self.assertTrue(receipt["authorized"])
        self.assertEqual(receipt["projectId"], self.ACTOR.project_id)
        (self.root / "index.html").write_text("TOKEN_SHOULD_NOT_LEAK", encoding="utf-8")
        result = self.snapshot()
        self.assertEqual(result["state"], "connected")
        self.assertEqual(result["git"], {"isRepository": False})
        self.assertEqual(result["fileCount"], 1)
        self.assertEqual(result["changedCount"], 0)
        self.assertNotIn("TOKEN_SHOULD_NOT_LEAK", str(result))
        self.assertNotIn(str(self.root), str(result))
        self.assertEqual(self.lookups, 3)
        before = result["revision"]
        (self.root / "index.html").write_text("new value", encoding="utf-8")
        self.assertNotEqual(before, self.snapshot()["revision"])

    def test_grant_revocation_and_device_policy_denial(self):
        self.permitted = False
        with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_grant_missing"):
            self.snapshot()
        self.permitted = True
        self.local_permitted = False
        with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_local_policy_denied"):
            self.port().verify_binding(self.ACTOR, self.DEVICE, self.RESOURCE)

    def test_exact_actor_client_session_project_device_resource(self):
        for field in ("actor_id", "client_id", "workspace_session_id", "project_id"):
            with self.subTest(field=field):
                other = dataclasses.replace(self.ACTOR, **{field: "another_context"})
                with self.assertRaises(ProjectWorkspaceDenied):
                    self.port().verify_binding(other, self.DEVICE, self.RESOURCE)
        for device, resource in (("wrong_device", self.RESOURCE),
                                 (self.DEVICE, "C:\\unsafe\\project"),
                                 (self.DEVICE, "resource_invalid"),
                                 (self.DEVICE, "../escape")):
            with self.subTest(device=device, resource=resource):
                with self.assertRaises(ProjectWorkspaceDenied):
                    self.port().verify_binding(self.ACTOR, device, resource)

    def test_expired_and_overbroad_grants_fail_closed(self):
        self.grant = dataclasses.replace(
            self.grant, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_grant_expired"):
            self.snapshot()
        self.grant = dataclasses.replace(
            self.grant, expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            allowed_actions=frozenset({"read", "execute"}),
        )
        with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_grant_scope_mismatch"):
            self.snapshot()

    def test_missing_grant_authority_has_no_implicit_default(self):
        with self.assertRaisesRegex(ValueError, "workspace_port_authority_required"):
            ProjectWorkspacePort(
                device_id=self.DEVICE, grants=None, local_policy=lambda _: True
            )
        with self.assertRaisesRegex(ValueError, "workspace_port_authority_required"):
            ProjectWorkspacePort(
                device_id=self.DEVICE, grants=lambda *_: None, local_policy=None
            )

    def test_replaced_root_fails_even_with_old_opaque_id(self):
        moved = self.root.parent / (self.root.name + "_moved")
        self.root.rename(moved)
        self.addCleanup(lambda: moved.exists() and moved.rmdir())
        self.root.mkdir()
        with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_root_changed"):
            self.snapshot()

    def test_symlink_root_and_nested_symlink_denied(self):
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "private.txt").write_text("INVISIBLE_VALUE", encoding="utf-8")
        root_alias = self.root.parent / (self.root.name + "_link")
        try:
            root_alias.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation denied by this Windows account")
        self.addCleanup(root_alias.unlink)
        original = self.grant
        self.grant = dataclasses.replace(self.grant, root=root_alias)
        with self.assertRaisesRegex(ProjectWorkspaceDenied, "reparse_point"):
            self.snapshot()
        self.grant = original
        nested = self.root / "outside_link"
        nested.symlink_to(elsewhere, target_is_directory=True)
        result = self.snapshot()
        self.assertNotIn("INVISIBLE_VALUE", str(result))
        self.assertEqual(result["fileCount"], 1)

    def test_git_status_uses_existing_authorized_checkout(self):
        def git(*args):
            subprocess.run(["git", "-C", str(self.root), *args], check=True,
                           capture_output=True, timeout=10)
        git("init", "-q")
        git("config", "user.name", "CI")
        git("config", "user.email", "ci@example.invalid")
        (self.root / "index.html").write_text("initial", encoding="utf-8")
        git("add", "index.html")
        git("commit", "-qm", "initial")
        clean = self.snapshot()
        self.assertTrue(clean["git"]["isRepository"])
        self.assertGreaterEqual(len(clean["git"]["head"]), 40)
        self.assertEqual(clean["changedCount"], 0)
        (self.root / "index.html").write_text("modified", encoding="utf-8")
        (self.root / "untracked.txt").write_text("new", encoding="utf-8")
        dirty = self.snapshot()
        self.assertEqual(dirty["changedCount"], 2)
        self.assertNotEqual(clean["revision"], dirty["revision"])

    def test_revocation_during_snapshot_discards_result(self):
        self.revoke_at = 2
        with self.assertRaisesRegex(ProjectWorkspaceDenied, "workspace_grant_missing"):
            self.snapshot()
        self.assertEqual(self.lookups, 2)


if __name__ == "__main__":
    unittest.main()
