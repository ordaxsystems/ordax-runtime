from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig


class BlenderLiveStatusReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        project = root / "project"
        project.mkdir()
        self.registry = ActionRegistry(
            AgentConfig(
                agent_name="live-readiness-test",
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

    def _run(self, **status):
        live = Mock()
        live.status.return_value = status
        with patch.object(self.registry, "_blender_live", return_value=live):
            return self.registry.execute("blender.live_status", {"project": "demo"})

    def test_ready_requires_fresh_identity_protocol_and_current_companion(self):
        result = self._run(
            presence_fresh=True,
            identity_matches=True,
            protocol_compatible=True,
            companion_current=True,
            presence={"pid": 4001},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("ready", result.data["readiness_state"])

    def test_fresh_identity_mismatch_is_not_ready(self):
        result = self._run(
            presence_fresh=True,
            identity_matches=False,
            protocol_compatible=True,
            companion_current=True,
            presence={"pid": 4001},
        )
        self.assertFalse(result.ok)
        self.assertEqual("identity_mismatch", result.data["readiness_state"])

    def test_fresh_outdated_protocol_is_not_ready(self):
        result = self._run(
            presence_fresh=True,
            identity_matches=True,
            protocol_compatible=False,
            companion_current=True,
            presence={"pid": 4001},
        )
        self.assertFalse(result.ok)
        self.assertEqual("protocol_outdated", result.data["readiness_state"])

    def test_fresh_outdated_companion_is_not_ready(self):
        result = self._run(
            presence_fresh=True,
            identity_matches=True,
            protocol_compatible=True,
            companion_current=False,
            presence={"pid": 4001},
        )
        self.assertFalse(result.ok)
        self.assertEqual("companion_outdated", result.data["readiness_state"])

    def test_stale_presence_is_not_running(self):
        result = self._run(presence_fresh=False)
        self.assertFalse(result.ok)
        self.assertEqual("not_running", result.data["readiness_state"])


if __name__ == "__main__":
    unittest.main()
