from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ordax_dev_agent.computer_filesystem_actions import (
    _path_allowed,
    computer_access_management_status,
    load_computer_access_policy,
    update_computer_access_policy,
)
from ordax_dev_agent.config import AgentConfig


class ComputerFullAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.state.mkdir()
        self.allowed = self.root / "allowed"
        self.allowed.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()
        self.app = self.root / "example.exe"
        self.app.write_bytes(b"demo")
        (self.state / "agent-settings.json").write_text(
            json.dumps(
                {
                    "computer_access": {
                        "enabled": True,
                        "full_access": False,
                        "full_filesystem": False,
                        "allowed_roots": [str(self.allowed)],
                        "allowed_applications": [],
                    }
                }
            ),
            encoding="utf-8",
        )
        self.config = AgentConfig(
            agent_name="test",
            poll_seconds=0.25,
            state_dir=self.state,
            agent_repo_path=self.root / "agent",
            hordax_path=self.root / "hordax",
            bridge_path=self.root / "bridge",
            workspace_root=self.allowed,
            projects={},
            default_project="missing",
        )

    def _update(self, **changes):
        before = computer_access_management_status(self.config)
        payload = {
            "enabled": before["enabled"],
            "full_access": before["full_access"],
            "full_filesystem": before["full_filesystem"],
            "allowed_roots": before["allowed_roots"],
            "allowed_applications": before["allowed_applications"],
            "expected_revision": before["revision"],
        }
        payload.update(changes)
        return update_computer_access_policy(self.config, payload)

    def test_full_access_bypasses_ordax_root_and_application_allowlists(self) -> None:
        bounded = load_computer_access_policy(self.config)
        self.assertFalse(_path_allowed(self.outside.resolve(), bounded))
        self.assertFalse(bounded.application_allowed(self.app))

        updated = self._update(
            full_access=True,
            full_filesystem=False,
            allowed_roots=[],
            allowed_applications=[],
        )
        self.assertTrue(updated["full_access"])
        full = load_computer_access_policy(self.config)
        self.assertTrue(_path_allowed(self.outside.resolve(), full))
        self.assertTrue(full.application_allowed(self.app))

    def test_returning_to_bounded_mode_restores_both_allowlists(self) -> None:
        self._update(full_access=True, allowed_roots=[], allowed_applications=[])
        self._update(
            full_access=False,
            full_filesystem=False,
            allowed_roots=[str(self.allowed)],
            allowed_applications=[],
        )
        bounded = load_computer_access_policy(self.config)
        self.assertFalse(_path_allowed(self.outside.resolve(), bounded))
        self.assertFalse(bounded.application_allowed(self.app))

    def test_full_filesystem_alone_does_not_disable_application_allowlist(self) -> None:
        self._update(
            full_access=False,
            full_filesystem=True,
            allowed_roots=[],
            allowed_applications=[],
        )
        policy = load_computer_access_policy(self.config)
        self.assertTrue(_path_allowed(self.outside.resolve(), policy))
        self.assertFalse(policy.application_allowed(self.app))


if __name__ == "__main__":
    unittest.main()
