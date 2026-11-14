from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

from ordax_dev_agent.main import (
    _begin_job_lease,
    _complete_job_after_stopping_keepalive,
    _renew_active_job_tick,
    _heartbeat_active_job_tick,
    _end_job_lease,
    _mark_transport_delivery_error,
    _mark_transport_healthy,
    _recover_terminal_reports_tick,
    _record_job_lease_error,
    _record_job_lease_renewal,
    _transport_retry_delay,
)
from ordax_dev_agent.remote_protocol import TransientDeliveryError


class TransportHealthRecoveryTests(unittest.TestCase):
    def test_bounded_retry_progression_limits_backend_and_log_pressure(self) -> None:
        delays = [_transport_retry_delay(attempt) for attempt in range(1, 11)]
        self.assertEqual([5.0, 10.0, 20.0, 30.0], delays[:4])
        self.assertTrue(all(delay == 30.0 for delay in delays[4:]))
        self.assertEqual(5.0, _transport_retry_delay(1))  # first poll after recovery
        with self.assertRaises(ValueError):
            _transport_retry_delay(0)

    def test_terminal_report_recovery_failure_never_becomes_global_claim_backoff(self) -> None:
        runtime = {
            "state": "busy",
            "transport_state": "connected",
            "job_phase": "concurrent",
            "terminal_reports_recovered": 2,
        }
        with patch("ordax_dev_agent.main.time.time", return_value=123.0):
            healthy = _recover_terminal_reports_tick(
                lambda: (_ for _ in ()).throw(RuntimeError("private backend detail")),
                runtime,
            )
        self.assertFalse(healthy)
        self.assertEqual("connected", runtime["transport_state"])
        self.assertEqual("busy", runtime["state"])
        self.assertEqual("concurrent", runtime["job_phase"])
        self.assertEqual(2, runtime["terminal_reports_recovered"])
        self.assertEqual(
            {"code": "RuntimeError", "at": 123.0},
            runtime["last_terminal_recovery_error"],
        )
        self.assertNotIn("private backend detail", str(runtime["last_terminal_recovery_error"]))

        recovered = _recover_terminal_reports_tick(lambda: 1, runtime)
        self.assertTrue(recovered)
        self.assertEqual(3, runtime["terminal_reports_recovered"])
        self.assertNotIn("last_terminal_recovery_error", runtime)

    def test_terminal_report_recovery_keeps_device_authorization_fail_closed(self) -> None:
        from ordax_dev_agent.remote_protocol import DeviceAuthorizationError

        with self.assertRaises(DeviceAuthorizationError):
            _recover_terminal_reports_tick(
                lambda: (_ for _ in ()).throw(
                    DeviceAuthorizationError("invalid_device_token")
                ),
                {},
            )

    def test_successful_heartbeat_clears_active_delivery_error_but_preserves_history(self) -> None:
        runtime = {
            "state": "ready",
            "job_phase": "idle",
            "last_result": None,
            "transport_state": "connected",
            "last_transport_error": None,
            "last_transport_recovered_at": None,
        }
        with patch("ordax_dev_agent.main.time.time", side_effect=[100.0, 125.0]):
            _mark_transport_delivery_error(runtime, RuntimeError("socket closed"))
            self.assertEqual("reconnecting", runtime["transport_state"])
            self.assertEqual("delivery-error", runtime["job_phase"])
            runtime["state"] = "ready"
            _mark_transport_healthy(runtime)

        self.assertEqual("connected", runtime["transport_state"])
        self.assertEqual("idle", runtime["job_phase"])
        self.assertEqual(125.0, runtime["last_transport_recovered_at"])
        self.assertEqual("socket closed", runtime["last_transport_error"]["summary"])
        self.assertTrue(runtime["last_result"]["ok"])

    def test_healthy_heartbeat_does_not_rewrite_normal_job_phase(self) -> None:
        runtime = {
            "transport_state": "connected",
            "job_phase": "completed",
            "last_result": {"ok": True, "summary": "job complete"},
        }
        _mark_transport_healthy(runtime)
        self.assertEqual("completed", runtime["job_phase"])
        self.assertEqual("job complete", runtime["last_result"]["summary"])

    def test_lease_renewal_reports_status_separately_from_transport(self) -> None:
        runtime = {"transport_state": "connected", "job_phase": "executing"}
        with patch("ordax_dev_agent.main.time.time", return_value=100.0):
            _begin_job_lease(runtime, "safe-request-id")
            _record_job_lease_renewal(runtime, {"lease_expires_at": "2026-10-20T16:30:00Z"})
        self.assertEqual("renewed", runtime["job_lease"]["state"])
        self.assertEqual(100.0, runtime["job_lease"]["last_renewed_at"])
        self.assertEqual(1, runtime["job_lease"]["renewal_attempts"])
        self.assertEqual("connected", runtime["transport_state"])
        self.assertNotIn("lease_id", runtime["job_lease"])

    def test_transient_lease_failure_is_not_erased_by_heartbeat(self) -> None:
        runtime = {"transport_state": "connected", "job_phase": "executing"}
        _begin_job_lease(runtime, "safe-request-id")
        _record_job_lease_error(runtime, TransientDeliveryError("database url and token secret"))
        self.assertEqual("unverified", runtime["job_lease"]["state"])
        self.assertEqual("lease_transport_unavailable", runtime["job_lease"]["last_error_code"])
        self.assertNotIn("secret", str(runtime["job_lease"]))
        _mark_transport_healthy(runtime)
        self.assertEqual("connected", runtime["transport_state"])
        self.assertEqual("unverified", runtime["job_lease"]["state"])
        self.assertEqual("executing", runtime["job_phase"])
        _record_job_lease_renewal(runtime, {"lease_expires_at": "2026-10-20T16:30:00Z"})
        self.assertEqual("renewed", runtime["job_lease"]["state"])
        self.assertIsNone(runtime["job_lease"]["last_error_code"])

    def test_fencing_loss_is_sticky_until_an_authoritative_terminal_report(self) -> None:
        runtime = {"transport_state": "connected", "job_phase": "executing"}
        _begin_job_lease(runtime, "safe-request-id")
        _record_job_lease_error(runtime, RuntimeError("canonical Product transport rejected request: lease_not_active"))
        self.assertEqual("fencing_lost", runtime["job_lease"]["state"])
        self.assertEqual("lease_not_active", runtime["job_lease"]["last_error_code"])
        _record_job_lease_renewal(runtime, {"lease_expires_at": "2026-10-20T16:30:00Z"})
        _record_job_lease_renewal(runtime, {})
        self.assertEqual("fencing_lost", runtime["job_lease"]["state"])
        _end_job_lease(runtime, report_committed=False)
        self.assertEqual("fencing_lost", runtime["job_lease"]["state"])
        _end_job_lease(runtime, report_committed=True)
        self.assertEqual("reported", runtime["job_lease"]["state"])
        self.assertIsNone(runtime["job_lease"]["lease_expires_at"])
        _record_job_lease_error(runtime, RuntimeError("late lease_not_active"))
        _record_job_lease_renewal(runtime, {"lease_expires_at": "2026-10-20T17:30:00Z"})
        self.assertEqual("reported", runtime["job_lease"]["state"])

    def test_invalid_lease_reply_never_claims_a_verified_renewal(self) -> None:
        runtime = {}
        _begin_job_lease(runtime, "safe-request-id")
        _record_job_lease_renewal(runtime, {"ok": True, "lease_expires_at": None})
        self.assertEqual("unverified", runtime["job_lease"]["state"])
        self.assertEqual("lease_response_invalid", runtime["job_lease"]["last_error_code"])
        _record_job_lease_renewal(runtime, {"lease_expires_at": "bogus"})
        self.assertEqual("unverified", runtime["job_lease"]["state"])
        _record_job_lease_renewal(runtime, {"lease_expires_at": "2026-10-20T17:30:00"})
        self.assertEqual("unverified", runtime["job_lease"]["state"])

    def test_slow_lease_renewal_never_starves_busy_presence(self) -> None:
        stop_event = threading.Event()
        lease_lock, presence_lock = threading.Lock(), threading.Lock()
        renewal_started, release_renewal = threading.Event(), threading.Event()
        heartbeat_seen = threading.Event()
        operations: list[str] = []
        runtime = {"state": "busy", "transport_state": "connected"}
        _begin_job_lease(runtime, "safe-request-id")

        class Control:
            def renew(self, _job: object) -> dict:
                operations.append("renew_started")
                renewal_started.set()
                if not release_renewal.wait(2):
                    raise AssertionError("test did not release the renewal")
                operations.append("renew_returned")
                return {"lease_expires_at": "2026-10-24T12:00:00Z"}

            def heartbeat(self, *_args: object, **_kwargs: object) -> None:
                operations.append("busy_heartbeat")
                heartbeat_seen.set()

            def complete(self, *_args: object) -> None:
                operations.append("complete")

        control = Control()
        lease_thread = threading.Thread(
            target=_renew_active_job_tick,
            args=(control, object(), stop_event, lease_lock, runtime),
        )
        lease_thread.start()
        self.assertTrue(renewal_started.wait(1))
        presence_thread = threading.Thread(
            target=_heartbeat_active_job_tick,
            args=(control, stop_event, presence_lock, runtime, [], "test"),
        )
        presence_thread.start()
        self.assertTrue(heartbeat_seen.wait(1), "heartbeat blocked behind slow renewal")
        self.assertFalse(release_renewal.is_set())

        reporter = threading.Thread(
            target=_complete_job_after_stopping_keepalive,
            args=(control, object(), object(), stop_event, lease_lock, presence_lock),
        )
        reporter.start()
        self.assertTrue(stop_event.wait(1))
        release_renewal.set()
        for thread in (lease_thread, presence_thread, reporter):
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(
            ["renew_started", "busy_heartbeat", "renew_returned", "complete"],
            operations,
        )
        self.assertTrue(runtime["paired"])
        self.assertEqual("renewed", runtime["job_lease"]["state"])
        # A tick awakened after the terminal report must not publish stale busy.
        _heartbeat_active_job_tick(
            control, stop_event, presence_lock, runtime, [], "test",
        )
        self.assertEqual("complete", operations[-1])

    def test_terminal_report_waits_for_in_flight_presence_and_blocks_late_busy(self) -> None:
        stop_event = threading.Event()
        lease_lock, presence_lock = threading.Lock(), threading.Lock()
        heartbeat_started, release_heartbeat = threading.Event(), threading.Event()
        operations: list[str] = []
        runtime = {"state": "busy", "transport_state": "connected"}

        class Control:
            def heartbeat(self, *_args: object, **_kwargs: object) -> None:
                operations.append("busy_started")
                heartbeat_started.set()
                if not release_heartbeat.wait(2):
                    raise AssertionError("test did not release the heartbeat")
                operations.append("busy_returned")

            def complete(self, *_args: object) -> None:
                operations.append("complete")

        control = Control()
        presence_thread = threading.Thread(
            target=_heartbeat_active_job_tick,
            args=(control, stop_event, presence_lock, runtime, [], "test"),
        )
        presence_thread.start()
        self.assertTrue(heartbeat_started.wait(1))
        reporter = threading.Thread(
            target=_complete_job_after_stopping_keepalive,
            args=(control, object(), object(), stop_event, lease_lock, presence_lock),
        )
        reporter.start()
        self.assertTrue(stop_event.wait(1))
        self.assertNotIn("complete", operations)
        release_heartbeat.set()
        for thread in (presence_thread, reporter):
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(["busy_started", "busy_returned", "complete"], operations)

    def test_non_transient_state_is_not_reported_as_recovery(self) -> None:
        runtime = {
            "transport_state": "credential-error",
            "job_phase": "delivery-error",
            "last_result": {"ok": False, "summary": "credential rejected"},
        }
        _mark_transport_healthy(runtime)
        self.assertEqual("connected", runtime["transport_state"])
        self.assertEqual("delivery-error", runtime["job_phase"])
        self.assertEqual("credential rejected", runtime["last_result"]["summary"])
        self.assertNotIn("last_transport_recovered_at", runtime)


if __name__ == "__main__":
    unittest.main()
