from __future__ import annotations

import json
import unittest
from urllib.request import urlopen

from ordax_dev_agent.capability_snapshot import (
    DEVICE_AGENT_CAPABILITIES_SCHEMA,
    MAX_DEVICE_CAPABILITIES,
    device_capability_snapshot,
)
from ordax_dev_agent.status_server import start_status_server
from ordax_dev_agent.product_gateway import PRODUCT_ACTIONS


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

    def test_native_transfer_inventory_and_execute_modes_use_real_handlers(self):
        from ordax_dev_agent.capability_snapshot import NATIVE_FILE_TRANSFER_ACTION_IDS
        self.assertEqual(NATIVE_FILE_TRANSFER_ACTION_IDS, frozenset({
            "computer.transfer_begin", "computer.transfer_chunk",
            "computer.transfer_status", "computer.transfer_commit",
            "computer.transfer_abort",
        }))
        snapshot = device_capability_snapshot([
            *NATIVE_FILE_TRANSFER_ACTION_IDS, "computer.transfer_unauthorized",
            "computer.terminal_exec", "process.start",
        ])
        modes = {entry["id"]: entry["modes"] for entry in snapshot["capabilities"]}
        self.assertEqual(modes["computer.transfer_status"], ["read"])
        self.assertEqual(modes["computer.transfer_commit"], ["write"])
        self.assertEqual(modes["computer.transfer_chunk"], ["write"])
        self.assertEqual(modes["computer.terminal_exec"], ["write"])
        self.assertEqual(modes["process.start"], ["write"])
        self.assertNotIn("computer.transfer_unauthorized", modes)

    def test_complete_product_catalog_fits_bounded_local_snapshot(self):
        # In production the runtime publishes only locally registered IDs.
        # This synthetic ceiling check prevents the source catalog from
        # growing beyond the local schema without an explicit contract review.
        local_names = [spec.local_action for spec in PRODUCT_ACTIONS.values()]
        self.assertGreater(len(local_names), 64)
        self.assertLessEqual(len(local_names), MAX_DEVICE_CAPABILITIES)
        self.assertLessEqual(MAX_DEVICE_CAPABILITIES, 128)
        expected = device_capability_snapshot(local_names)
        self.assertEqual(len(expected["capabilities"]), len(PRODUCT_ACTIONS))

        def forbidden_full_status():
            raise AssertionError("capability read must not scan full status")

        server = start_status_server(
            forbidden_full_status, host="127.0.0.1", port=0,
            capabilities_provider=lambda: local_names,
        )
        try:
            with urlopen(
                f"http://127.0.0.1:{server.server_port}/capabilities",
                timeout=3,
            ) as response:
                actual = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 200)
            self.assertEqual(actual, expected)
        finally:
            server.shutdown()
            server.server_close()

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
