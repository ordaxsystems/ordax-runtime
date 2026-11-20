"""A slow durable receipt transport must not stop independent MCP claims."""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import threading
import time
import unittest

from ordax_dev_agent.main import _schedule_terminal_report_recovery
from ordax_dev_agent.remote_protocol import DeviceAuthorizationError


class TerminalReceiptNonBlockingTests(unittest.TestCase):
    def test_slow_report_confirmation_never_blocks_claim_loop_or_starts_second(self):
        entered = threading.Event()
        released = threading.Event()
        attempts = [0]
        runtime = {"state": "busy", "active_job_count": 1}
        def recover() -> int:
            attempts[0] += 1
            entered.set()
            if not released.wait(3):
                raise AssertionError("report recovery not released")
            return 1

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                future, due = _schedule_terminal_report_recovery(
                    pool, None, recover, runtime, now=100.0, next_due=0.0,
                )
                self.assertIsNotNone(future)
                self.assertTrue(entered.wait(2))
                start = time.monotonic()
                same, same_due = _schedule_terminal_report_recovery(
                    pool, future, recover, runtime, now=101.0, next_due=due,
                )
                self.assertLess(time.monotonic() - start, 0.2)
                self.assertIs(same, future)
                self.assertEqual(same_due, due)
                self.assertEqual(attempts[0], 1, "no backlog of duplicate recovery jobs")
                self.assertEqual(runtime["state"], "busy", "MCP command lane stays independent")
                released.set()
                self.assertTrue(future.result(timeout=2))
                next_future, deadline = _schedule_terminal_report_recovery(
                    pool, future, recover, runtime, now=104.0, next_due=due,
                )
                self.assertIsNone(next_future)
                self.assertEqual(deadline, 105.0)
                self.assertEqual(runtime["terminal_reports_recovered"], 1)
                self.assertEqual(attempts[0], 1)
                self.assertIsNone(_schedule_terminal_report_recovery(
                    pool, None, recover, runtime, now=104.5, next_due=deadline,
                )[0])
                new_future, _ = _schedule_terminal_report_recovery(
                    pool, None, recover, runtime, now=105.0, next_due=deadline,
                )
                self.assertIsNotNone(new_future)
                self.assertTrue(new_future.result(timeout=2))
                self.assertEqual(attempts[0], 2)
        finally:
            released.set()

    def test_transient_error_retries_after_five_seconds_without_hiding_status(self):
        runtime = {"state": "ready"}
        attempts = [0]
        def failure() -> int:
            attempts[0] += 1
            raise ConnectionError("private credential data")
        with ThreadPoolExecutor(max_workers=2) as executor:
            future, _ = _schedule_terminal_report_recovery(
                executor, None, failure, runtime, now=30.0, next_due=0.0,
            )
            self.assertFalse(future.result(timeout=2))
            new, due = _schedule_terminal_report_recovery(
                executor, future, failure, runtime, now=32.0, next_due=0.0,
            )
            self.assertIsNone(new)
            self.assertEqual(due, 37.0)
            self.assertEqual(attempts[0], 1)
            self.assertEqual(runtime["state"], "ready")
            self.assertEqual(runtime["last_terminal_recovery_error"]["code"], "ConnectionError")
            self.assertNotIn("private credential", str(runtime))

    def test_revoked_native_credential_is_terminal_not_silently_retried(self):
        runtime = {"state": "ready"}
        def reject() -> int:
            raise DeviceAuthorizationError("native credential revoked")
        with ThreadPoolExecutor(max_workers=2) as executor:
            future, due = _schedule_terminal_report_recovery(
                executor, None, reject, runtime, now=10.0, next_due=0.0,
            )
            with self.assertRaises(DeviceAuthorizationError):
                future.result(timeout=2)
            with self.assertRaises(DeviceAuthorizationError):
                _schedule_terminal_report_recovery(
                    executor, future, reject, runtime, now=11.0, next_due=due,
                )

    def test_no_recovery_configured_means_no_maintenance_work(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            future, due = _schedule_terminal_report_recovery(
                executor, None, None, {"state": "ready"},
                now=100.0, next_due=0.0,
            )
            self.assertIsNone(future)
            self.assertEqual(due, 0.0)

    def test_same_local_control_maintenance_pool_no_new_remote_queue(self):
        source = (Path(__file__).resolve().parents[1] / "ordax_dev_agent" / "main.py"
                  ).read_text(encoding="utf-8")
        self.assertIn("control_maintenance_executor = ThreadPoolExecutor(", source)
        self.assertIn('max_workers=2, thread_name_prefix="ordax-control-maintenance"', source)
        self.assertIn("terminal_recovery_future, next_terminal_recovery = (", source)
        self.assertIn("_schedule_terminal_report_recovery(", source)
        self.assertIn("control_maintenance_executor.shutdown(wait=True, cancel_futures=False)", source)
        self.assertNotIn("project_binding_executor = ThreadPoolExecutor(", source)
        self.assertNotIn("recovery_healthy = _recover_terminal_reports_tick(", source)
        self.assertNotIn("queue.Queue(", source)


if __name__ == "__main__":
    unittest.main()
