from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ordax_dev_agent.blender_live_bridge import BlenderLiveBridge
from ordax_dev_agent.projects import Project


class BlenderLiveIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project_root = self.root / "project"
        self.project_root.mkdir()
        (self.project_root / "automation" / "blender").mkdir(parents=True)
        self.scene = self.project_root / "scene.blend"
        self.scene.write_bytes(b"BLENDER")
        self.outside = self.root / "outside.blend"
        self.outside.write_bytes(b"BLENDER")
        self.project = Project(
            "demo",
            self.project_root,
            ("blender",),
            blender={"scripts_dir": "automation/blender"},
        )
        self.bridge = BlenderLiveBridge(
            SimpleNamespace(state_dir=self.root / "state"),
            self.project,
        )

    def tearDown(self):
        self.temp.cleanup()

    def _presence(self, *, project: str = "demo", file: Path | None = None) -> None:
        self.bridge.presence.parent.mkdir(parents=True, exist_ok=True)
        self.bridge.presence.write_text(
            json.dumps({
                "pid": 4321,
                "project": project,
                "file": str(file or self.scene),
                "protocol_version": 9,
                "companion_fingerprint": "fp",
                "capabilities": ["inspect", "quit"],
            }),
            encoding="utf-8",
        )

    def _status(self):
        with patch(
            "ordax_dev_agent.blender_live_bridge._process_is_running",
            return_value=True,
        ), patch.object(self.bridge, "_companion_fingerprint", return_value="fp"):
            return self.bridge.status()

    def test_status_accepts_matching_project_and_file(self):
        self._presence()
        status = self._status()
        self.assertTrue(status["identity_matches"])
        self.assertTrue(status["project_matches"])
        self.assertTrue(status["file_matches_project"])

    def test_status_rejects_cross_project_presence(self):
        self._presence(project="other")
        status = self._status()
        self.assertFalse(status["identity_matches"])
        self.assertFalse(status["project_matches"])
        self.assertTrue(status["file_matches_project"])

    def test_status_rejects_file_outside_project_root(self):
        self._presence(file=self.outside)
        status = self._status()
        self.assertFalse(status["identity_matches"])
        self.assertTrue(status["project_matches"])
        self.assertFalse(status["file_matches_project"])

    def test_request_blocks_before_writing_command_on_identity_mismatch(self):
        self._presence(file=self.outside)
        with patch(
            "ordax_dev_agent.blender_live_bridge._process_is_running",
            return_value=True,
        ), patch.object(self.bridge, "_companion_fingerprint", return_value="fp"):
            result = self.bridge.request("inspect", timeout_seconds=0.1)
        self.assertFalse(result.ok)
        self.assertTrue(result.data["identity_mismatch"])
        self.assertEqual(result.data["blocked_operation"], "inspect")
        self.assertFalse(self.bridge.inbox.exists())

    def test_start_refuses_fresh_mismatched_session_instead_of_spawning(self):
        self._presence(project="other")
        with patch.object(
            self.bridge,
            "presence_is_fresh",
            return_value=True,
        ), patch.object(self.bridge, "_companion_fingerprint", return_value="fp"), patch(
            "ordax_dev_agent.blender_live_bridge.subprocess.Popen"
        ) as popen:
            result = self.bridge.start(wait_seconds=0.1)
        self.assertFalse(result.ok)
        self.assertTrue(
            result.data.get("identity_mismatch"),
            (result.summary, result.data),
        )
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
