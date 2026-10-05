from __future__ import annotations

import json
import unittest
from urllib.request import urlopen

from ordax_dev_agent.capability_snapshot import (
    DEVICE_AGENT_CAPABILITIES_SCHEMA,
    device_capability_snapshot,
)
from ordax_dev_agent.status_server import start_status_server


class DeviceCapabilitySnapshotTests(unittest.TestCase):
    def test_snapshot_exposes_only_product_approved_actions(self):
        snapshot = device_capability_snapshot(
            [
                "projects.list",
                "project.text_write",
                "blender.live_status",
                "shell.generic",
                "agent.self_test",
            ]
        )

        self.assertEqual(DEVICE_AGENT_CAPABILITIES_SCHEMA, snapshot["schema"])
        self.assertEqual("ready", snapshot["state"])
        by_id = {item["id"]: item for item in snapshot["capabilities"]}
        self.assertEqual(["read"], by_id["projects.list"]["modes"])
        self.assertEqual(["write"], by_id["project.text_write"]["modes"])
        self.assertEqual(["read"], by_id["blender.live_status"]["modes"])
        self.assertNotIn("shell.generic", by_id)
        self.assertNotIn("agent.self_test", by_id)

    def test_snapshot_is_degraded_without_product_capabilities(self):
        snapshot = device_capability_snapshot(["agent.self_test"])
        self.assertEqual("degraded", snapshot["state"])
        self.assertEqual([], snapshot["capabilities"])

    def test_loopback_status_server_publishes_compatible_snapshot(self):
        server = start_status_server(
            lambda: {
                "actions": ["projects.list", "project.text_write"],
                "runtime": {"state": "local-ready"},
                "agent_version": "test",
            },
            host="127.0.0.1",
            port=0,
        )
        try:
            with urlopen(
                f"http://127.0.0.1:{server.server_port}/capabilities",
                timeout=2,
            ) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual(200, response.status)
            self.assertEqual(DEVICE_AGENT_CAPABILITIES_SCHEMA, payload["schema"])
            self.assertEqual("ready", payload["state"])
            self.assertEqual(
                ["project.text_write", "projects.list"],
                [item["id"] for item in payload["capabilities"]],
            )
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
