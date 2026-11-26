"""Fail-closed contract for protected Windows services recycling legacy manager PIDs."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/windows/ordax-upgrade-quiesce.ps1"


class RecycledLegacyManagerContract(unittest.TestCase):
    def test_only_legacy_non_python_name_can_use_unreadable_path_fallback(self):
        s = SOURCE.read_text(encoding="utf-8-sig")
        start = s.index("function Assert-NoActiveSupervisedManagers")
        end = s.index("# The canonical generation launch path", start)
        f = s[start:end]
        self.assertIn("if (!$record.worker_generation -and", f)
        self.assertIn("$actualName -notin @('python', 'pythonw')", f)
        self.assertIn("Assert-NoLiveOrphanSupervisedChild -Record $record", f)
        self.assertLess(f.index("$actualName -notin @('python', 'pythonw')"),
                        f.index("Assert-NoLiveOrphanSupervisedChild -Record $record",
                                f.index("$actualName -notin @('python', 'pythonw')")))
        self.assertIn("throw 'ORDAX_UPGRADE_SUPERVISOR_MANAGER_IDENTITY_UNVERIFIED'", f)
        self.assertIn("Test-VerifiedDetachedWorker -Record $record", f)

    def test_preflight_is_read_only_with_preserved_workers(self):
        s = SOURCE.read_text(encoding="utf-8-sig")
        self.assertIn("if ($AssessWorkerContinuity -and !$PreflightOnly)", s)
        self.assertIn("ORDAX_UPGRADE_SUPERVISOR_PREFLIGHT_CLEAR", s)


if __name__ == "__main__":
    unittest.main()
