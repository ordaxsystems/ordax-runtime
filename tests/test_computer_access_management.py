from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.computer_filesystem_actions import (
    computer_access_management_status,
    update_computer_access_policy,
)
from ordax_dev_agent.config import AgentConfig


class ComputerAccessManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.allowed = self.root / "allowed"
        self.allowed.mkdir()
        self.app = self.root / "tool.exe"
        self.app.write_bytes(b"demo")
        self.settings = self.state / "agent-settings.json"
        self.settings.write_text(json.dumps({
            "control_plane_protocol": "cloudflare-v3",
            "computer_access": {
                "enabled": True,
                "full_access": False,
                "full_filesystem": False,
                "allowed_roots": [str(self.allowed)],
                "allowed_applications": [],
            },
        }), encoding="utf-8")
        self.config = AgentConfig(
            agent_name="test", poll_seconds=0.25, state_dir=self.state,
            agent_repo_path=self.root / "agent", hordax_path=self.root / "hordax",
            bridge_path=self.root / "bridge", workspace_root=self.allowed,
            projects={}, default_project="missing",
        )

    def test_status_exposes_revision_without_secret_material(self) -> None:
        status = computer_access_management_status(self.config)
        self.assertTrue(status["enabled"])
        self.assertFalse(status["full_access"])
        self.assertFalse(status["full_filesystem"])
        self.assertEqual([str(self.allowed.resolve())], status["allowed_roots"])
        self.assertEqual(str(self.settings), status["settings_path"])
        self.assertEqual(64, len(status["revision"]))
        self.assertEqual({
            "enabled": False, "full_access": False, "full_filesystem": False,
            "allowed_roots": False, "allowed_applications": False,
        }, status["managed_by_environment"])

    def test_update_is_atomic_preserves_unrelated_settings_and_rejects_stale_write(self) -> None:
        before = computer_access_management_status(self.config)
        second = self.root / "second"
        second.mkdir()
        updated = update_computer_access_policy(self.config, {
            "enabled": True,
            "full_access": False,
            "full_filesystem": False,
            "allowed_roots": [str(second)],
            "allowed_applications": [str(self.app)],
            "expected_revision": before["revision"],
        })
        self.assertNotEqual(before["revision"], updated["revision"])
        self.assertEqual([str(second.resolve())], updated["allowed_roots"])
        self.assertEqual([os.path.normcase(str(self.app.resolve()))], updated["allowed_applications"])
        raw = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual("cloudflare-v3", raw["control_plane_protocol"])
        self.assertFalse(any(self.state.glob(".agent-settings.json.*.tmp")))
        with self.assertRaisesRegex(ValueError, "reload before saving"):
            update_computer_access_policy(self.config, {
                "enabled": False,
                "expected_revision": before["revision"],
            })

    def test_environment_managed_field_is_effective_and_not_overwritten(self) -> None:
        before_raw = json.loads(self.settings.read_text(encoding="utf-8"))
        before = computer_access_management_status(self.config)
        with patch.dict(os.environ, {"ORDAX_COMPUTER_FULL_FILESYSTEM": "1"}, clear=False):
            updated = update_computer_access_policy(self.config, {
                "enabled": True,
                "full_access": False,
                "full_filesystem": False,
                "allowed_roots": [str(self.allowed)],
                "allowed_applications": [],
                "expected_revision": before["revision"],
            })
            self.assertTrue(updated["full_filesystem"])
            self.assertTrue(updated["managed_by_environment"]["full_filesystem"])
            self.assertIn("full_filesystem", updated["ignored_environment_managed_fields"])
        after_raw = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(
            before_raw["computer_access"]["full_filesystem"],
            after_raw["computer_access"]["full_filesystem"],
        )

    def test_full_access_can_be_managed_by_environment_without_persisting_override(self) -> None:
        before_raw = json.loads(self.settings.read_text(encoding="utf-8"))
        before = computer_access_management_status(self.config)
        with patch.dict(os.environ, {"ORDAX_COMPUTER_FULL_ACCESS": "1"}, clear=False):
            updated = update_computer_access_policy(self.config, {
                "enabled": True,
                "full_access": False,
                "full_filesystem": False,
                "allowed_roots": [],
                "allowed_applications": [],
                "expected_revision": before["revision"],
            })
            self.assertTrue(updated["full_access"])
            self.assertTrue(updated["managed_by_environment"]["full_access"])
            self.assertIn("full_access", updated["ignored_environment_managed_fields"])
        after_raw = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(
            before_raw["computer_access"]["full_access"],
            after_raw["computer_access"]["full_access"],
        )

    def test_enabled_policy_requires_a_root_without_full_access_or_full_filesystem(self) -> None:
        before = computer_access_management_status(self.config)
        with self.assertRaisesRegex(ValueError, "at least one allowed root"):
            update_computer_access_policy(self.config, {
                "enabled": True,
                "full_access": False,
                "full_filesystem": False,
                "allowed_roots": [],
                "allowed_applications": [],
                "expected_revision": before["revision"],
            })


if __name__ == "__main__":
    unittest.main()
