import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.models import ActionResult
from ordax_dev_agent.product_action_scope import DEVICE_BLENDER_ACTIONS


class DeviceBlenderScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.registry = ActionRegistry(AgentConfig(
            agent_name="device-blender",
            poll_seconds=1.0,
            state_dir=root / "state",
            agent_repo_path=root / "managed",
            hordax_path=root / "unused",
            bridge_path=root / "bridge",
            projects={},
        ))

    def test_every_device_operation_reuses_existing_bridge_without_catalog_project(self):
        for action in sorted(DEVICE_BLENDER_ACTIONS):
            operation = action.removeprefix("computer.blender_")
            def invoke(payload):
                scope = self.registry._project(payload)
                self.assertEqual("_device_blender", scope.slug)
                self.assertEqual(self.registry.config.state_dir.resolve() / "blender-workspace", scope.root)
                self.assertEqual({}, self.registry.projects)
                with self.assertRaises(ValueError):
                    scope.path("../outside.blend", must_exist=False)
                return ActionResult(True, "typed operation")
            with patch.object(self.registry, "blender_live_" + operation, side_effect=invoke) as handler:
                result = self.registry._execute_device_blender(operation, {})
                self.assertTrue(result.ok)
                handler.assert_called_once()
            self.assertIsNone(getattr(self.registry._project_execution_local, "project", None))

    def test_scope_injection_and_unknown_operation_denied(self):
        for payload in ({"project": "other"}, {"space_id": "other"}):
            with self.assertRaises(ValueError):
                self.registry._execute_device_blender("status", payload)
        with self.assertRaises(ValueError):
            self.registry._execute_device_blender("run_script", {})

    def test_device_dispatch_requires_no_project_and_no_hidden_attestation(self):
        from ordax_dev_agent.product_device_control_plane import ProductDeviceControlPlane
        control = object.__new__(ProductDeviceControlPlane)
        row = {"capability": "computer.blender_status", "project_id": None, "space_id": None}
        self.assertEqual(control._bind_project_scope(row, {}), ({}, None))
        for invalid in (
            {**row, "project_id": "fake-project"},
            {**row, "space_id": "fake-space"},
        ):
            with self.assertRaisesRegex(RuntimeError, "device Blender cannot carry"):
                control._bind_project_scope(invalid, {})
        with self.assertRaises(RuntimeError):
            control._bind_project_scope(row, {"project": "fake"})
        with self.assertRaises(RuntimeError):
            control._bind_project_scope(row, {"_ordax_project_scope": {"project_id": "fake"}})

    def test_registry_dispatch_uses_computer_access_policy(self):
        from types import SimpleNamespace
        with patch("ordax_dev_agent.actions.load_computer_access_policy",
                   return_value=SimpleNamespace(enabled=False)):
            result = self.registry.execute("computer.blender_status", {})
            self.assertFalse(result.ok)
            self.assertIn("disabled", result.summary)
        with patch("ordax_dev_agent.actions.load_computer_access_policy",
                   return_value=SimpleNamespace(enabled=True)):
            with patch.object(self.registry, "blender_live_status",
                              return_value=ActionResult(True, "status")):
                self.assertTrue(self.registry.execute("computer.blender_status", {}).ok)

    def test_project_controls_remain_registered_project_only(self):
        result = self.registry.execute("blender.live_status", {"project": "_device_blender"})
        self.assertFalse(result.ok)
        self.assertEqual({}, self.registry.projects)


if __name__ == "__main__":
    unittest.main()
