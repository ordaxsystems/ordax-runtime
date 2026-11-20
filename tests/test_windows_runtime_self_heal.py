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

    def test_native_launcher_bounds_stalled_python_before_agent_watchdog(self) -> None:
        launcher = (ROOT / "packaging/windows/ordax_launcher.c").read_text(encoding="utf-8")
        agent = (ROOT / "ordax_dev_agent/main.py").read_text(encoding="utf-8")
        self.assertIn("ORDAX_BOOTSTRAP_TIMEOUT_MS 600000UL", launcher)
        self.assertIn("Local\\\\OrdaXRuntimeBootstrap_", launcher)
        self.assertIn("SetEnvironmentVariableW(ORDAX_BOOTSTRAP_EVENT_ENV, bootstrap_name)", launcher)
        self.assertIn("SetEnvironmentVariableW(ORDAX_BOOTSTRAP_EVENT_ENV, NULL)", launcher)
        self.assertIn("3, bootstrap_wait, FALSE, ORDAX_BOOTSTRAP_TIMEOUT_MS", launcher)
        child = launcher.split("static DWORD run_python_child(", 1)[1].split("int APIENTRY", 1)[0]
        self.assertIn("CREATE_UNICODE_ENVIRONMENT | CREATE_SUSPENDED", child)
        self.assertLess(child.index("AssignProcessToJobObject"), child.index("ResumeThread"))
        self.assertIn("job = create_kill_job()", launcher)
        self.assertIn("if (result == ERROR_TIMEOUT)", launcher)
        self.assertIn("TerminateJobObject(job, ERROR_TIMEOUT)", launcher)
        self.assertIn("run_python_child(", launcher)
        self.assertIn("def _signal_native_launcher_bootstrap_ready", agent)
        self.assertIn('os.environ.pop("ORDAX_RUNTIME_BOOTSTRAP_EVENT", "")', agent)
        self.assertIn("kernel.SetEvent(handle)", agent)
        self.assertIn('    _startup_log(config, "CONTROL_PLANE_READY")' + chr(10) + '    _signal_native_launcher_bootstrap_ready()', agent)
        local_startup = agent.split('    if not remote_control_configured:\n', 2)[-1]
        self.assertLess(local_startup.index('_signal_native_launcher_bootstrap_ready()'),
                        local_startup.index('while not stop:'))
        self.assertNotIn("ORDAX_RUNTIME_BOOTSTRAP_EVENT", (ROOT / "scripts/windows/ordax-agent-watchdog.ps1").read_text(encoding="utf-8"))

    def test_runtime_start_failure_is_not_silent(self) -> None:
        lifecycle = (ROOT / "native" / "ordax-workbench" / "RuntimeLifecycle.cs").read_text(encoding="utf-8")
        self.assertIn('new(false, "missing"', lifecycle)
        self.assertIn('new(false, "start_failed"', lifecycle)
        self.assertIn('new(false, "exited_early"', lifecycle)


if __name__ == "__main__":
    unittest.main()
