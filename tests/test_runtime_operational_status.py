"""Operational status must distinguish Product readiness from liveness."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock
from dataclasses import replace

from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.device_credentials import resolve_pending_token_path, resolve_token_path
from ordax_dev_agent.product_central_session import ProductCentralSessionStatus
from ordax_dev_agent.main import (
    _mark_transport_healthy,
    _mark_transport_delivery_error,
    _mark_identity_loaded,
    _record_product_heartbeat,
    _mark_transport_unavailable,
    _runtime_status_snapshot,
    product_dispatch_readiness,
    _windows_enrollment_restart_ready,
    run,
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

    def test_central_session_diagnostics_does_not_expose_challenge_or_grant(self):
        session = ProductCentralSessionStatus(
            checked=True, confirmed=False,
            challenge_id="33333333-3333-4333-8333-333333333333",
            last_error="central_device_grant_revoked", last_success_at=123.0,
        )
        runtime = self._ready_runtime()
        snapshot = _runtime_status_snapshot(
            runtime, action_registry_ready=True,
            remote_control_configured=True, now=100.0,
            central_session_status=session,
        )
        self.assertTrue(snapshot["product_dispatch_ready"])
        self.assertEqual(snapshot["central_session"], {
            "checked": True,
            "last_confirmation_observed": True,
            "last_error_code": "central_device_grant_revoked",
        })
        self.assertNotIn("challenge_id", snapshot["central_session"])
        self.assertNotIn("last_success_at", snapshot["central_session"])
        self.assertNotIn("central_session", runtime)
        early = _runtime_status_snapshot(
            runtime, action_registry_ready=False,
            remote_control_configured=False, now=100.0,
        )
        self.assertIsNone(early["central_session"])

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

    def test_loaded_identity_is_not_remote_pairing_until_heartbeat(self):
        runtime = {"state": "initializing", "paired": True,
                   "transport_state": "connected", "last_result": None}
        _mark_identity_loaded(runtime)
        self.assertFalse(runtime["paired"])
        self.assertEqual(runtime["state"], "pairing")
        self.assertEqual(runtime["transport_state"], "connecting")
        self.assertNotIn("last_heartbeat_at", runtime)
        self.assertFalse(runtime["last_result"]["ok"])
        unready = product_dispatch_readiness(
            runtime, action_registry_ready=True, remote_control_configured=True, now=100)
        self.assertEqual(unready["product_readiness_code"], "pairing_required")
        _record_product_heartbeat(runtime)
        self.assertTrue(runtime["paired"])
        self.assertEqual(runtime["state"], "ready")
        self.assertEqual(runtime["transport_state"], "connected")
        self.assertTrue(runtime["last_result"]["ok"])
        self.assertIsInstance(runtime["last_heartbeat_at"], float)

    def test_transport_failure_stays_unready_until_confirmed_heartbeat(self):
        runtime = self._ready_runtime()
        runtime["job_phase"] = "idle"
        _mark_transport_delivery_error(runtime, RuntimeError("transport unavailable"))
        self.assertEqual(runtime["state"], "control-plane-error")
        self.assertEqual(runtime["transport_state"], "reconnecting")
        self.assertFalse(runtime["last_result"]["ok"])
        self.assertEqual(runtime["job_phase"], "delivery-error")
        self.assertEqual(
            product_dispatch_readiness(
                runtime, action_registry_ready=True, remote_control_configured=True, now=100.0,
            )["product_readiness_code"],
            "runtime_not_ready",
        )
        _record_product_heartbeat(runtime)
        self.assertEqual(runtime["state"], "ready")
        self.assertEqual(runtime["transport_state"], "connected")
        self.assertEqual(runtime["job_phase"], "idle")
        self.assertTrue(runtime["last_result"]["ok"])
        self.assertIsNotNone(runtime["last_transport_recovered_at"])

    def test_confirmed_heartbeat_cannot_override_denied_or_busy_states(self):
        for state in ("busy", "credential-recovery-required", "local-disabled", "stopping"):
            with self.subTest(state=state):
                runtime = self._ready_runtime(state=state)
                _record_product_heartbeat(runtime)
                self.assertEqual(runtime["state"], state)

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


class WindowsFirstEnrollmentLifecycleTests(unittest.TestCase):
    DEVICE_ID = "33333333-3333-4333-8333-333333333333"

    def test_only_committed_owner_identity_requests_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "owner-device"
            with mock.patch.dict(os.environ, {
                "ORDAX_AGENT_STATE_DIR": str(state),
                "ORDAX_DEVICE_ID": "",
                "ORDAX_PRODUCT_MCP_URL": "https://mcp.ordax.com.br",
            }):
                config = AgentConfig.from_env()
                with mock.patch("ordax_dev_agent.main.sys.platform", "win32"):
                    self.assertFalse(_windows_enrollment_restart_ready(config))
                    settings = state / "agent-settings.json"
                    settings.write_text("{bad-json", encoding="utf-8")
                    self.assertFalse(_windows_enrollment_restart_ready(config))
                    settings.write_text(json.dumps({
                        "device_id": self.DEVICE_ID,
                        "control_plane_protocol": "cloudflare-v3",
                        "product_mcp_url": "https://mcp.ordax.com.br",
                    }), encoding="utf-8")
                    self.assertFalse(_windows_enrollment_restart_ready(config))
                    pending = resolve_pending_token_path(state)
                    pending.write_text("a" * 64, encoding="ascii")
                    self.assertFalse(_windows_enrollment_restart_ready(config))
                    token = resolve_token_path(state)
                    token.write_text("invalid", encoding="ascii")
                    self.assertFalse(_windows_enrollment_restart_ready(config))
                    token.write_text("a" * 64, encoding="ascii")
                    self.assertTrue(_windows_enrollment_restart_ready(config))
                    # A second identity never silently changes a live agent.
                    self.assertFalse(_windows_enrollment_restart_ready(
                        replace(config, device_id=self.DEVICE_ID)
                    ))
                # Non-Windows callers (including Native OS composition) do not
                # restart on this Windows-only first-run file observation.
                self.assertFalse(_windows_enrollment_restart_ready(config))

    def test_other_state_root_cannot_trigger_owner_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "original"
            different = Path(temp) / "other"
            with mock.patch.dict(os.environ, {
                "ORDAX_AGENT_STATE_DIR": str(state), "ORDAX_DEVICE_ID": "",
            }):
                config = AgentConfig.from_env()
                different.mkdir()
                (different / "agent-settings.json").write_text(
                    json.dumps({"device_id": self.DEVICE_ID}), encoding="utf-8")
                resolve_token_path(different).write_text("a" * 64, encoding="ascii")
                with mock.patch.dict(os.environ, {"ORDAX_AGENT_STATE_DIR": str(different)}):
                    with mock.patch("ordax_dev_agent.main.sys.platform", "win32"):
                        self.assertFalse(_windows_enrollment_restart_ready(config))

    @unittest.skipUnless(os.name == "nt", "Windows installed Runtime lifecycle")
    def test_windows_local_ready_exits_to_existing_supervisor_after_enrollment(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "owner-device"
            with mock.patch.dict(os.environ, {
                "ORDAX_AGENT_STATE_DIR": str(state),
                "ORDAX_DEVICE_ID": "",
                "ORDAX_PRODUCT_MCP_URL": "https://mcp.ordax.com.br",
            }):
                config = AgentConfig.from_env()
                (state / "agent-settings.json").write_text(json.dumps({
                    "device_id": self.DEVICE_ID,
                    "control_plane_protocol": "cloudflare-v3",
                    "product_mcp_url": "https://mcp.ordax.com.br",
                }), encoding="utf-8")
                resolve_token_path(state).write_text("a" * 64, encoding="ascii")
                with mock.patch("ordax_dev_agent.main._start_local_watchdog", return_value=None):
                    with redirect_stdout(io.StringIO()):
                        result = run(config, status_port=0)
                self.assertEqual(result, 42)
                # The owner agent exits for the SINGLE existing supervisor;
                # no heartbeat, remote grant, OS Desktop profile or second
                # process is created by this transition.


if __name__ == "__main__":
    unittest.main()
