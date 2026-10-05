from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig


class BlenderInstanceReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        project = root / "project"
        project.mkdir()
        self.registry = ActionRegistry(
            AgentConfig(
                agent_name="instance-reconciliation-test",
                poll_seconds=1.0,
                state_dir=root / "state",
                agent_repo_path=root / "managed",
                hordax_path=root / "hordax",
                bridge_path=root / "bridge",
                projects={
                    "demo": {
                        "path": str(project),
                        "apps": ["blender"],
                        "blender": {},
                    }
                },
                default_project="demo",
            )
        )

    def _manager(self, system_pids: list[int]) -> Mock:
        manager = Mock()
        manager.instances.return_value = []
        manager.system_blender_pids.return_value = system_pids
        manager.config_path = Path(self.temp.name) / "state" / "blender-bootstrap.json"
        return manager

    @staticmethod
    def _live(*, pid: int, healthy: bool = True, fresh: bool = True) -> Mock:
        live = Mock()
        live.presence = Path("presence.json")
        live.presence_is_fresh.return_value = fresh
        live.status.return_value = {
            "presence": {
                "pid": pid,
                "file": "C:/workspace/demo.blend",
                "scene": "Demo",
                "is_dirty": False,
            },
            "identity_matches": True,
            "protocol_compatible": True,
            "companion_current": healthy,
            "presence_path": "C:/state/blender-live/demo/presence.json",
        }
        return live

    def test_live_companion_pid_is_not_reported_as_unmanaged(self):
        manager = self._manager([7001, 7002])
        live = self._live(pid=7001)
        with patch.object(self.registry, "_blender_adoption_manager", return_value=manager), patch(
            "ordax_dev_agent.blender_actions.BlenderLiveBridge", return_value=live
        ):
            result = self.registry.execute("blender.instances", {})

        self.assertTrue(result.ok, result.summary)
        self.assertEqual(result.data["live_session_pids"], [7001])
        self.assertEqual(result.data["live_healthy_pids"], [7001])
        self.assertEqual(result.data["live_attention_pids"], [])
        self.assertEqual(result.data["unmanaged_blender_pids"], [7002])

    def test_outdated_live_companion_is_managed_but_requires_attention(self):
        manager = self._manager([7001])
        live = self._live(pid=7001, healthy=False)
        with patch.object(self.registry, "_blender_adoption_manager", return_value=manager), patch(
            "ordax_dev_agent.blender_actions.BlenderLiveBridge", return_value=live
        ):
            result = self.registry.execute("blender.instances", {})

        self.assertTrue(result.ok, result.summary)
        self.assertEqual(result.data["live_session_pids"], [7001])
        self.assertEqual(result.data["live_healthy_pids"], [])
        self.assertEqual(result.data["live_attention_pids"], [7001])
        self.assertEqual(result.data["unmanaged_blender_pids"], [])

    def test_stale_presence_does_not_hide_an_unmanaged_blender_process(self):
        manager = self._manager([7001])
        live = self._live(pid=7001, fresh=False)
        with patch.object(self.registry, "_blender_adoption_manager", return_value=manager), patch(
            "ordax_dev_agent.blender_actions.BlenderLiveBridge", return_value=live
        ):
            result = self.registry.execute("blender.instances", {})

        self.assertTrue(result.ok, result.summary)
        self.assertEqual(result.data["live_session_pids"], [])
        self.assertEqual(result.data["unmanaged_blender_pids"], [7001])


if __name__ == "__main__":
    unittest.main()
