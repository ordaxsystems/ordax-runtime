"""Slow Project Catalog I/O must never serialize MCP claim admission."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import threading
import time
import unittest

from ordax_dev_agent.main import _drain_project_binding_reconciliation
from ordax_dev_agent.remote_protocol import DeviceAuthorizationError


class ProjectBindingNonBlockingTests(unittest.TestCase):
    def test_slow_project_lookup_remains_independent_of_claims(self):
        entered = threading.Event()
        release = threading.Event()
        runtime: dict = {"state": "ready"}

        def reconcile() -> int:
            entered.set()
            if not release.wait(3):
                raise AssertionError("test did not release project reconciliation")
            return 2

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future: Future[int] = executor.submit(reconcile)
                self.assertTrue(entered.wait(2))
                start = time.monotonic()
                self.assertIs(_drain_project_binding_reconciliation(future, runtime), future)
                self.assertLess(time.monotonic() - start, 0.2)
                # Independent claims can proceed while project onboarding is
                # blocked on its own network request.
                self.assertEqual(runtime["state"], "ready")
                self.assertNotIn("project_bindings_confirmed", runtime)
                release.set()
                self.assertEqual(future.result(timeout=2), 2)
                self.assertIsNone(_drain_project_binding_reconciliation(future, runtime))
                self.assertEqual(runtime["project_bindings_confirmed"], 2)
                self.assertNotIn("last_project_binding_error", runtime)
        finally:
            release.set()

    def test_transient_project_failure_never_changes_dispatch_health(self):
        future: Future[int] = Future()
        future.set_exception(ConnectionError("sensitive database url"))
        runtime = {"state": "ready"}
        self.assertIsNone(_drain_project_binding_reconciliation(future, runtime))
        self.assertEqual(runtime["state"], "ready")
        self.assertEqual(runtime["last_project_binding_error"], "ConnectionError")
        self.assertNotIn("sensitive", str(runtime))

    def test_native_credential_revocation_still_fails_closed(self):
        future: Future[int] = Future()
        future.set_exception(DeviceAuthorizationError("credential revoked"))
        with self.assertRaises(DeviceAuthorizationError):
            _drain_project_binding_reconciliation(future, {"state": "ready"})

    def test_no_future_and_successful_zero_confirmation(self):
        runtime = {"project_bindings_confirmed": 4, "last_project_binding_error": "OSError"}
        self.assertIsNone(_drain_project_binding_reconciliation(None, runtime))
        future: Future[int] = Future()
        future.set_result(0)
        self.assertIsNone(_drain_project_binding_reconciliation(future, runtime))
        self.assertEqual(runtime["project_bindings_confirmed"], 4)
        self.assertNotIn("last_project_binding_error", runtime)

    def test_main_loop_single_flight_without_new_queue(self):
        source = (Path(__file__).resolve().parents[1] / "ordax_dev_agent" / "main.py"
                  ).read_text(encoding="utf-8")
        self.assertIn("max_workers=1, thread_name_prefix=\"ordax-project-binding\"", source)
        self.assertIn("if heartbeat_healthy and project_binding_future is None:", source)
        self.assertIn("project_binding_executor.submit(", source)
        self.assertIn("project_binding_executor.shutdown(wait=True, cancel_futures=False)", source)
        self.assertIn("job_executor = ThreadPoolExecutor(", source)
        self.assertNotIn("confirmed = int(reconcile_project_bindings())", source)
        self.assertNotIn("queue.Queue(", source)


if __name__ == "__main__":
    unittest.main()
