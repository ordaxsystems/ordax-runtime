from __future__ import annotations

import unittest
from pathlib import Path, PureWindowsPath

from ordax_dev_agent.device_setup import migrate_legacy_bridge_path


class LegacyBridgePathMigrationTests(unittest.TestCase):
    def test_exact_legacy_bridge_is_migrated_to_managed_runtime(self):
        home = PureWindowsPath(r"C:\Users\TONECOS")
        state = PureWindowsPath(r"C:\Users\TONECOS\AppData\Local\OrdaX\DevAgent")
        settings = {"bridge_path": r"C:\Users\TONECOS\Documents\github\mcp-blender"}

        changed = migrate_legacy_bridge_path(settings, state=state, home=home)

        self.assertTrue(changed)
        self.assertEqual(
            settings["bridge_path"],
            str(state / "src"),
        )

    def test_custom_bridge_path_is_preserved(self):
        home = Path("/home/user")
        state = Path("/home/user/.local/share/ordax")
        settings = {"bridge_path": "/work/custom-bridge"}

        changed = migrate_legacy_bridge_path(settings, state=state, home=home)

        self.assertFalse(changed)
        self.assertEqual(settings["bridge_path"], "/work/custom-bridge")

    def test_missing_bridge_path_is_untouched(self):
        settings = {}

        changed = migrate_legacy_bridge_path(
            settings,
            state=Path("/state"),
            home=Path("/home/user"),
        )

        self.assertFalse(changed)
        self.assertNotIn("bridge_path", settings)


if __name__ == "__main__":
    unittest.main()
