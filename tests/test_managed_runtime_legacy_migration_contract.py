from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "scripts" / "windows" / "ordax-device-agent-setup.ps1"


class ManagedRuntimeLegacyMigrationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SETUP.read_text(encoding="utf-8-sig")

    def test_only_known_legacy_repository_is_migrated(self):
        self.assertIn("https://github.com/washingtonmsdj/mcp-blender.git", self.source)
        self.assertIn("https://github.com/ordaxsystems/ordax-runtime.git", self.source)
        self.assertIn("https://github.com/washingtonmsdj/ordax-runtime.git", self.source)
        self.assertIn("MANAGED_REMOTE_MISMATCH", self.source)
        self.assertIn("$isLegacyRemote", self.source)

    def test_cutover_is_staged_and_has_rollback(self):
        for marker in [
            "src.next-",
            "src.legacy-",
            "LEGACY_MIGRATION_CLONE_FAILED",
            "LEGACY_MIGRATION_SWAP_FAILED",
            "$migrationActivated = $true",
            "Move-Item -LiteralPath $migrationBackup -Destination $repo",
        ]:
            self.assertIn(marker, self.source)

    def test_agent_busy_and_dirty_guards_precede_cutover(self):
        dirty = self.source.index("MANAGED_CHECKOUT_DIRTY_PRESERVED")
        busy = self.source.index("AGENT_BUSY_RETRY_SETUP")
        swap = self.source.index("$migrationActivated = $true")
        self.assertLess(dirty, swap)
        self.assertLess(busy, swap)

    def test_health_success_removes_backup_only_after_ready_predicate(self):
        ready_predicate = self.source.index("$health.control_plane_protocol -eq 'cloudflare-v3'")
        remove_backup = self.source.index("Remove-Item -LiteralPath $migrationBackup")
        self.assertLess(ready_predicate, remove_backup)


if __name__ == "__main__":
    unittest.main()
