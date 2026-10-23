"""Operational status must distinguish Product readiness from liveness."""

import unittest

from ordax_dev_agent.main import (
    _mark_transport_healthy,
    _mark_transport_unavailable,
    _runtime_status_snapshot,
    product_dispatch_readiness,
)


class RuntimeOperationalStatusTests(unittest.TestCase):
    def _ready_runtime(self, *, state="ready", heartbeat_at=100.0):
        return {
            "state": state,
            "paired": True,
            "transport_state": "connected",
            "last_heartbeat_at": heartbeat_at,
        }

    def test_ready_and_busy_runtime_require_recent_product_heartbeat(self):
        for state in ("ready", "busy"):
            with self.subTest(state=state):
                result = product_dispatch_readiness(
                    self._ready_runtime(state=state),
                    action_registry_ready=True,
                    remote_control_configured=True,
                    now=160.0,
                )
                self.assertEqual(
                    {"product_dispatch_ready": True, "product_readiness_code": "ready"},
                    result,
                )

    def test_status_snapshot_exposes_registry_and_product_readiness(self):
        runtime = self._ready_runtime(heartbeat_at=100.0)
        result = _runtime_status_snapshot(
            runtime,
            action_registry_ready=True,
            remote_control_configured=True,
            now=100.0,
        )

        self.assertTrue(result["action_registry_ready"])
        self.assertTrue(result["product_dispatch_ready"])
        self.assertEqual("ready", result["product_readiness_code"])
        self.assertNotIn("product_dispatch_ready", runtime)

    def test_liveness_signals_do_not_substitute_for_product_readiness(self):
        cases = (
            ({}, False, True, "action_registry_unavailable"),
            (self._ready_runtime(), True, False, "remote_control_not_configured"),
            ({"state": "pairing", "paired": False}, True, True, "pairing_required"),
            ({**self._ready_runtime(), "transport_state": "reconnecting"}, True, True, "transport_reconnecting"),
            (self._ready_runtime(heartbeat_at=39.0), True, True, "heartbeat_stale"),
            (self._ready_runtime(heartbeat_at=106.0), True, True, "heartbeat_stale"),
            ({**self._ready_runtime(), "last_heartbeat_at": None}, True, True, "heartbeat_not_observed"),
            ({**self._ready_runtime(), "state": "credential-recovery-required"}, True, True, "credential_recovery_required"),
            ({**self._ready_runtime(), "state": "local-disabled"}, True, True, "local_access_disabled"),
        )
        for runtime, registry_ready, configured, expected_code in cases:
            with self.subTest(code=expected_code):
                result = product_dispatch_readiness(
                    runtime,
                    action_registry_ready=registry_ready,
                    remote_control_configured=configured,
                    now=100.0,
                )
                self.assertFalse(result["product_dispatch_ready"])
                self.assertEqual(expected_code, result["product_readiness_code"])

    def test_busy_heartbeat_failure_marks_transport_without_hiding_active_job(self):
        runtime = {
            "state": "busy",
            "transport_state": "connected",
            "job_phase": "executing",
        }

        _mark_transport_unavailable(runtime, RuntimeError("heartbeat timeout"))

        self.assertEqual("busy", runtime["state"])
        self.assertEqual("executing", runtime["job_phase"])
        self.assertEqual("reconnecting", runtime["transport_state"])
        self.assertEqual("heartbeat timeout", runtime["last_transport_error"]["summary"])

        _mark_transport_healthy(runtime)

        self.assertEqual("busy", runtime["state"])
        self.assertEqual("connected", runtime["transport_state"])
        self.assertIsNotNone(runtime["last_transport_recovered_at"])


if __name__ == "__main__":
    unittest.main()
