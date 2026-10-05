from __future__ import annotations

import unittest
from unittest.mock import patch

from ordax_dev_agent.main import _mark_transport_delivery_error, _mark_transport_healthy


class TransportHealthRecoveryTests(unittest.TestCase):
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
