"""Regression guard: watchdog recovery must not wait indefinitely on Task Scheduler."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WATCHDOG = ROOT / "scripts" / "windows" / "ordax-agent-watchdog.ps1"


class WindowsMcpWatchdogRecoveryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.code = WATCHDOG.read_text(encoding="utf-8")

    def test_task_scheduler_commands_are_bounded(self) -> None:
        code = self.code
        self.assertIn("function Invoke-BoundedScheduledTask", code)
        self.assertIn("[ValidateSet('State', 'Start', 'Stop')]", code)
        self.assertIn("WaitForExit(8000)", code)
        self.assertIn("$child.Kill(); [void]$child.WaitForExit(1000)", code)
        self.assertIn("TASK_TIMEOUT operation=$Operation name=$Name", code)
        self.assertIn("Only the two existing OrdaX-owned task names are accepted", code)
        self.assertIn("Name -cnotmatch '^[A-Za-z0-9][A-Za-z0-9 ._-]{0,127}

    def test_upgrade_and_recovery_reuse_bounded_calls(self) -> None:
        code = self.code
        self.assertIn("Invoke-BoundedScheduledTask -Operation State -Name 'OrdaX MCP Verified Upgrade'", code)
        self.assertIn("Invoke-BoundedScheduledTask -Operation State -Name $RestartTaskName", code)
        self.assertIn("Invoke-BoundedScheduledTask -Operation Start -Name $RestartTaskName", code)
        self.assertIn("Invoke-BoundedScheduledTask -Operation Stop -Name $RestartTaskName", code)
        # Direct unbounded operations outside the bounded child are prohibited.
        self.assertNotIn("Get-ScheduledTask -TaskName $RestartTaskName", code)
        self.assertNotIn("Start-ScheduledTask -TaskName $RestartTaskName", code)
        self.assertNotIn("Stop-ScheduledTask -TaskName $RestartTaskName", code)


if __name__ == "__main__":
    unittest.main()
", code)

    def test_upgrade_and_recovery_reuse_bounded_calls(self) -> None:
        code = self.code
        self.assertIn("Invoke-BoundedScheduledTask -Operation State -Name 'OrdaX MCP Verified Upgrade'", code)
        self.assertIn("Invoke-BoundedScheduledTask -Operation State -Name $RestartTaskName", code)
        self.assertIn("Invoke-BoundedScheduledTask -Operation Start -Name $RestartTaskName", code)
        self.assertIn("Invoke-BoundedScheduledTask -Operation Stop -Name $RestartTaskName", code)
        # Direct unbounded operations outside the bounded child are prohibited.
        self.assertNotIn("Get-ScheduledTask -TaskName $RestartTaskName", code)
        self.assertNotIn("Start-ScheduledTask -TaskName $RestartTaskName", code)
        self.assertNotIn("Stop-ScheduledTask -TaskName $RestartTaskName", code)


if __name__ == "__main__":
    unittest.main()
