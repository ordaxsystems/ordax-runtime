from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.models import ActionResult
from ordax_studio.product_web_desktop import StudioProductApi


class OrdaxStudioProductBlenderControlTests(unittest.TestCase):
    def _api(self, directory: str) -> StudioProductApi:
        root = Path(directory)
        project = root / "project"
        project.mkdir()
        (root / "agent-settings.json").write_text(
            json.dumps(
                {
                    "default_project": "demo",
                    "projects": {
                        "demo": {
                            "path": str(project),
                            "apps": ["blender"],
                            "blender": {},
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        env = {
            "ORDAX_AGENT_STATE_DIR": str(root),
            "ORDAX_MEMORY_DB": str(root / "memory.db"),
        }
        with patch.dict(os.environ, env, clear=False):
            return StudioProductApi()

    def test_prepare_exposes_restart_state_for_unmanaged_blender(self):
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(directory)
            with patch.object(api.agent, "execute") as execute:
                execute.side_effect = [
                    ActionResult(False, "not connected", {}),
                    ActionResult(False, "no match", {"no_match": True}),
                    ActionResult(
                        True,
                        "instances",
                        {
                            "instances": [],
                            "ready_pids": [],
                            "restart_required_pids": [],
                            "unmanaged_blender_pids": [7001],
                        },
                    ),
                ]
                result = api.blender_prepare()

        self.assertTrue(result["ok"])
        self.assertEqual("restart_required", result["data"]["state"])
        self.assertTrue(result["data"]["bridge_installable"])
        self.assertTrue(result["data"]["requires_restart"])
        self.assertFalse(result["data"]["can_start"])
        self.assertFalse(result["data"]["can_capture"])

    def test_prepare_adopts_single_clean_blank_window(self):
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(directory)
            with patch.object(api.agent, "execute") as execute:
                execute.side_effect = [
                    ActionResult(False, "not connected", {}),
                    ActionResult(False, "no match", {"no_match": True}),
                    ActionResult(
                        True,
                        "instances",
                        {
                            "instances": [
                                {
                                    "pid": 7123,
                                    "file": "",
                                    "attached_project": "",
                                    "is_dirty": False,
                                }
                            ],
                            "ready_pids": [7123],
                        },
                    ),
                    ActionResult(True, "adopted", {"presence": {"pid": 7123, "file": ""}}),
                ]
                result = api.blender_prepare()

        self.assertEqual("adopted_blank", result["data"]["state"])
        self.assertEqual(7123, result["data"]["pid"])
        self.assertTrue(result["data"]["can_capture"])
        self.assertTrue(execute.call_args_list[-1].args[1]["allow_blank"])

    def test_install_bridge_rechecks_connection_state(self):
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(directory)
            with patch.object(api.agent, "execute") as execute:
                execute.side_effect = [
                    ActionResult(True, "installed", {"installed": ["bridge"]}),
                    ActionResult(False, "not connected", {}),
                    ActionResult(False, "no match", {"no_match": True}),
                    ActionResult(
                        True,
                        "instances",
                        {
                            "instances": [],
                            "ready_pids": [],
                            "restart_required_pids": [],
                            "unmanaged_blender_pids": [7010],
                        },
                    ),
                ]
                result = api.blender_install_bridge()

        self.assertTrue(result["ok"])
        self.assertEqual(["bridge"], result["data"]["installation"]["installed"])
        self.assertEqual("restart_required", result["data"]["connection"]["state"])
        self.assertEqual(
            ["blender.adoption_install", "blender.live_status", "blender.adopt", "blender.instances"],
            [call.args[0] for call in execute.call_args_list],
        )

    def test_explicit_blank_adopt_returns_capture_ready_state(self):
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(directory)
            with patch.object(api.agent, "execute") as execute:
                execute.return_value = ActionResult(
                    True,
                    "adopted",
                    {"presence": {"pid": 8123, "file": ""}},
                )
                result = api.blender_adopt(8123, True)

        self.assertEqual("adopted_blank", result["data"]["state"])
        self.assertEqual(8123, result["data"]["pid"])
        self.assertTrue(result["data"]["can_capture"])
        self.assertTrue(execute.call_args.args[1]["allow_blank"])

    def test_explicit_adopt_rejects_invalid_pid_before_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(directory)
            with patch.object(api.agent, "execute") as execute:
                result = api.blender_adopt(0)

        self.assertFalse(result["ok"])
        execute.assert_not_called()

    def test_start_returns_connected_state_after_live_status(self):
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(directory)
            with patch.object(api.agent, "execute") as execute:
                execute.side_effect = [
                    ActionResult(True, "started", {"pid": 9001}),
                    ActionResult(
                        True,
                        "ready",
                        {"presence": {"pid": 9001, "file": "scene.blend"}},
                    ),
                ]
                result = api.blender_start()

        self.assertEqual("connected", result["data"]["state"])
        self.assertEqual(9001, result["data"]["pid"])
        self.assertTrue(result["data"]["can_capture"])
        self.assertEqual(
            ["blender.live_start", "blender.live_status"],
            [call.args[0] for call in execute.call_args_list],
        )

    def test_prepare_surfaces_fresh_identity_mismatch_without_adoption(self):
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(directory)
            with patch.object(api.agent, "execute") as execute:
                execute.return_value = ActionResult(
                    False,
                    "Visible Blender live session identity does not match this ORDAX project",
                    {
                        "presence_fresh": True,
                        "readiness_state": "identity_mismatch",
                        "project_matches": True,
                        "file_matches_project": False,
                        "presence": {"pid": 9101, "file": "C:/outside.blend"},
                    },
                )
                result = api.blender_prepare()

        self.assertTrue(result["ok"])
        self.assertEqual("identity_mismatch", result["data"]["state"])
        self.assertEqual(9101, result["data"]["pid"])
        self.assertFalse(result["data"]["can_capture"])
        self.assertFalse(result["data"]["can_start"])
        self.assertFalse(result["data"]["requires_restart"])
        execute.assert_called_once_with("blender.live_status", {"project": "demo"})

    def test_prepare_surfaces_dirty_outdated_companion_as_restart_required(self):
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(directory)
            with patch.object(api.agent, "execute") as execute:
                execute.return_value = ActionResult(
                    False,
                    "Visible Blender companion code is outdated",
                    {
                        "presence_fresh": True,
                        "readiness_state": "companion_outdated",
                        "identity_matches": True,
                        "protocol_compatible": True,
                        "companion_current": False,
                        "presence": {
                            "pid": 9102,
                            "file": "C:/project/scene.blend",
                            "is_dirty": True,
                        },
                    },
                )
                result = api.blender_prepare()

        self.assertTrue(result["ok"])
        self.assertEqual("restart_required", result["data"]["state"])
        self.assertEqual([9102], result["data"]["blender_pids"])
        self.assertTrue(result["data"]["requires_restart"])
        self.assertTrue(result["data"]["dirty"])
        self.assertIn("salve o arquivo", result["summary"])
        execute.assert_called_once_with("blender.live_status", {"project": "demo"})

    def test_prepare_surfaces_outdated_protocol_as_restart_required(self):
        with tempfile.TemporaryDirectory() as directory:
            api = self._api(directory)
            with patch.object(api.agent, "execute") as execute:
                execute.return_value = ActionResult(
                    False,
                    "Visible Blender companion protocol is outdated",
                    {
                        "presence_fresh": True,
                        "readiness_state": "protocol_outdated",
                        "identity_matches": True,
                        "protocol_compatible": False,
                        "companion_current": True,
                        "presence": {
                            "pid": 9103,
                            "file": "C:/project/scene.blend",
                            "is_dirty": False,
                        },
                    },
                )
                result = api.blender_prepare()

        self.assertTrue(result["ok"])
        self.assertEqual("restart_required", result["data"]["state"])
        self.assertEqual("protocol_outdated", result["data"]["readiness_state"])
        self.assertEqual([9103], result["data"]["blender_pids"])
        self.assertFalse(result["data"]["can_capture"])
        execute.assert_called_once_with("blender.live_status", {"project": "demo"})


if __name__ == "__main__":
    unittest.main()
