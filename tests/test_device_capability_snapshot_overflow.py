"""The installed Runtime must never break v1 capability discovery on large catalogs."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ordax_dev_agent import capability_snapshot as snapshot


class DeviceCapabilityOverflowTests(unittest.TestCase):
    def test_large_catalog_preserves_computer_actions_without_claiming_ready(self):
        entries = {
            **{
                f"computer.action_{i:02d}": SimpleNamespace(
                    local_action=f"computer.action_{i:02d}", effect="read"
                )
                for i in range(33)
            },
            **{
                f"project.action_{i:02d}": SimpleNamespace(
                    local_action=f"project.action_{i:02d}", effect="write"
                )
                for i in range(57)
            },
        }
        with patch.object(snapshot, "PRODUCT_ACTIONS", entries):
            result = snapshot.device_capability_snapshot(entries)
        self.assertEqual(result["schema"], "ordax.device-agent-capabilities/1")
        self.assertEqual(result["state"], "degraded")
        self.assertEqual(len(result["capabilities"]), 64)
        identifiers = [item["id"] for item in result["capabilities"]]
        self.assertEqual(identifiers, sorted(identifiers))
        self.assertEqual(len([x for x in identifiers if x.startswith("computer.")]), 33)
        self.assertTrue(all(x["modes"] in (["read"], ["write"]) for x in result["capabilities"]))

    def test_exactly_at_limit_is_complete_and_empty_is_degraded(self):
        entries = {
            f"computer.item_{i:02d}": SimpleNamespace(
                local_action=f"computer.item_{i:02d}", effect="read"
            )
            for i in range(64)
        }
        with patch.object(snapshot, "PRODUCT_ACTIONS", entries):
            result = snapshot.device_capability_snapshot(entries)
            empty = snapshot.device_capability_snapshot([])
        self.assertEqual(len(result["capabilities"]), 64)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(empty["state"], "degraded")
        self.assertEqual(empty["capabilities"], [])

    def test_only_locally_available_actions_appear(self):
        entries = {
            "computer.windows": SimpleNamespace(local_action="computer.windows", effect="read"),
            "computer.click": SimpleNamespace(local_action="computer.click", effect="write"),
        }
        with patch.object(snapshot, "PRODUCT_ACTIONS", entries):
            result = snapshot.device_capability_snapshot(["computer.windows", "unknown"])
        self.assertEqual(result["capabilities"], [{"id": "computer.windows", "modes": ["read"]}])
        self.assertEqual(result["state"], "ready")


if __name__ == "__main__":
    unittest.main()
