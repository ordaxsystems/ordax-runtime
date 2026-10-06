from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "scripts" / "windows" / "ordax-device-agent-setup.ps1"


class ManagedRuntimePrecompileContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SETUP.read_text(encoding="utf-8-sig")

    def test_setup_precompiles_runtime_before_starting_task(self):
        compile_pos = self.source.index("& $python -m compileall -q @compileTargets")
        start_pos = self.source.index("Start-ScheduledTask -TaskName $taskName")
        self.assertLess(compile_pos, start_pos)

    def test_precompile_covers_runtime_packages_and_fails_closed(self):
        for target in (
            "ordax_dev_agent",
            "ordax_device_agent",
            "mcp_blender_unity",
        ):
            self.assertIn(target, self.source)
        self.assertIn("RUNTIME_PRECOMPILE_FAILED_RETRY_SETUP", self.source)

    def test_precompile_runs_after_checkout_update(self):
        merge_pos = self.source.index("& git -C $repo merge --ff-only origin/main")
        compile_pos = self.source.index("& $python -m compileall -q @compileTargets")
        self.assertLess(merge_pos, compile_pos)


if __name__ == "__main__":
    unittest.main()
