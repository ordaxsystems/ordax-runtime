from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]


class WindowsRuntimeSelfHealContractTests(unittest.TestCase):
    def test_workbench_bootstraps_runtime_before_ui(self) -> None:
        app = (ROOT / "native" / "ordax-workbench" / "App.xaml.cs").read_text(encoding="utf-8")
        self.assertIn("RuntimeLifecycle.EnsureRunning()", app)
        self.assertIn("ORDAX Runtime indisponível", app)
        self.assertIn("base.OnStartup(e)", app)
        self.assertLess(app.index("RuntimeLifecycle.EnsureRunning()"), app.index("base.OnStartup(e)"))

    def test_runtime_lifecycle_is_idempotent_and_independent(self) -> None:
        lifecycle = (ROOT / "native" / "ordax-workbench" / "RuntimeLifecycle.cs").read_text(encoding="utf-8")
        self.assertIn('"Local\\\\ORDAXRuntime"', lifecycle)
        self.assertIn("Mutex.OpenExisting", lifecycle)
        self.assertIn('Path.Combine(root, "ORDAX Runtime.exe")', lifecycle)
        self.assertIn("Process.Start", lifecycle)
        self.assertNotIn("AssignProcessToJobObject", lifecycle)
        self.assertNotIn("Kill", lifecycle)

    def test_runtime_start_failure_is_not_silent(self) -> None:
        lifecycle = (ROOT / "native" / "ordax-workbench" / "RuntimeLifecycle.cs").read_text(encoding="utf-8")
        self.assertIn('new(false, "missing"', lifecycle)
        self.assertIn('new(false, "start_failed"', lifecycle)
        self.assertIn('new(false, "exited_early"', lifecycle)


if __name__ == "__main__":
    unittest.main()
