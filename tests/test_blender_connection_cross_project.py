from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from ordax_dev_agent.models import ActionResult
from ordax_studio.blender_connection import prepare_blender_connection


class BlenderConnectionCrossProjectTests(unittest.TestCase):
    @staticmethod
    def _agent(instances_data: dict) -> Mock:
        agent = Mock()
        agent.projects = {"demo": SimpleNamespace(apps=["blender"])}
        agent.execute.side_effect = [
            ActionResult(False, "not connected", {}),
            ActionResult(False, "no match", {"no_match": True}),
            ActionResult(True, "instances", instances_data),
        ]
        return agent

    def test_foreign_healthy_live_session_blocks_start(self) -> None:
        agent = self._agent(
            {
                "instances": [],
                "live_sessions": [
                    {
                        "project": "other-project",
                        "pid": 7001,
                        "file": "C:/other/scene.blend",
                        "identity_matches": True,
                        "protocol_compatible": True,
                        "companion_current": True,
                    }
                ],
                "live_attention_pids": [],
                "ready_pids": [],
                "restart_required_pids": [],
                "unmanaged_blender_pids": [],
            }
        )

        result = prepare_blender_connection(agent, "demo")

        self.assertTrue(result["ok"])
        self.assertEqual("occupied", result["data"]["state"])
        self.assertFalse(result["data"]["can_start"])
        self.assertEqual([7001], result["data"]["blender_pids"])
        self.assertEqual("other-project", result["data"]["live_sessions"][0]["project"])
        self.assertEqual(
            ["blender.live_status", "blender.adopt", "blender.instances"],
            [call.args[0] for call in agent.execute.call_args_list],
        )

    def test_foreign_live_attention_stays_explicit_and_not_idle(self) -> None:
        agent = self._agent(
            {
                "instances": [],
                "live_sessions": [
                    {
                        "project": "other-project",
                        "pid": 7002,
                        "file": "C:/other/scene.blend",
                        "identity_matches": True,
                        "protocol_compatible": False,
                        "companion_current": True,
                    }
                ],
                "live_attention_pids": [7002],
                "ready_pids": [],
                "restart_required_pids": [],
                "unmanaged_blender_pids": [],
            }
        )

        result = prepare_blender_connection(agent, "demo")

        self.assertEqual("occupied", result["data"]["state"])
        self.assertEqual([7002], result["data"]["live_attention_pids"])
        self.assertIn("exigem atenção", result["summary"])
        self.assertFalse(result["data"]["can_start"])


if __name__ == "__main__":
    unittest.main()
