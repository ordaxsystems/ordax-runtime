from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.models import ActionResult
from ordax_dev_agent.product_action_scope import COMPUTER_DEVICE_ACTIONS


class ComputerDispatchPolicyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.registry = ActionRegistry(AgentConfig(
            agent_name="test", poll_seconds=0.25, state_dir=root / "state",
            agent_repo_path=root / "agent", hordax_path=root / "hordax",
            bridge_path=root / "bridge", workspace_root=root,
            projects={}, default_project="missing",
        ))
        self.settings = self.registry.config.state_dir / "agent-settings.json"
        self.settings.parent.mkdir(parents=True, exist_ok=True)

    def policy(self, enabled):
        self.settings.write_text(json.dumps({"computer_access": {
            "enabled": enabled, "full_access": True,
        }}), encoding="utf-8")

    def test_disabled_policy_blocks_every_computer_handler_before_side_effects(self):
        self.policy(False)
        for action in COMPUTER_DEVICE_ACTIONS - {"computer.access_status"}:
            with self.subTest(action=action):
                handler = Mock(return_value=ActionResult(True, "should not run"))
                self.registry._actions[action] = handler
                result = self.registry.execute(action, {})
                self.assertFalse(result.ok)
                self.assertIn("disabled by local ORDAX policy", result.summary)
                handler.assert_not_called()

    def test_disable_and_reenable_take_effect_without_restarting_runtime(self):
        handler = Mock(return_value=ActionResult(True, "observed"))
        self.registry._actions["computer.screen_info"] = handler
        self.policy(True)
        self.assertTrue(self.registry.execute("computer.screen_info", {}).ok)
        self.policy(False)
        self.assertFalse(self.registry.execute("computer.screen_info", {}).ok)
        self.policy(True)
        self.assertTrue(self.registry.execute("computer.screen_info", {}).ok)
        self.assertEqual(handler.call_count, 2)

    def test_environment_disable_overrides_saved_full_access(self):
        self.policy(True)
        with patch.dict("os.environ", {"ORDAX_COMPUTER_ACCESS_ENABLED": "false"}):
            self.assertFalse(self.registry.execute("computer.screen_info", {}).ok)

    def test_invalid_settings_fail_closed_and_disabled_status_stays_readable(self):
        self.settings.write_text("{invalid", encoding="utf-8")
        self.assertFalse(self.registry.execute("computer.screen_info", {}).ok)
        self.policy(False)
        result = self.registry.execute("computer.access_status", {})
        self.assertTrue(result.ok)
        self.assertFalse(result.data["enabled"])


if __name__ == "__main__":
    unittest.main()
