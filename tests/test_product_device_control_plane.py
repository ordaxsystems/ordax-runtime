from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
import uuid
from pathlib import Path

import httpx

from ordax_dev_agent import __version__ as DEVICE_AGENT_VERSION
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.models import ActionResult
from ordax_dev_agent.product_device_control_plane import ProductDeviceControlPlane
from ordax_dev_agent.product_remote import PRODUCT_REMOTE_CAPABILITY
from ordax_dev_agent.remote_protocol import DeviceAuthorizationError

DEVICE_ID = "22222222-2222-4222-8222-222222222222"


def config(root: Path) -> AgentConfig:
    return AgentConfig(
        agent_name="test-agent", poll_seconds=1.0,
        state_dir=root / "state", agent_repo_path=root / "agent",
        hordax_path=root / "hordax", bridge_path=root / "bridge",
        control_plane_protocol="cloudflare-v3", device_id=DEVICE_ID,
        product_mcp_url="https://product.example",
    )


def token(root: Path) -> None:
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "device-token.cloudflare-v3.txt").write_text("a" * 64, encoding="utf-8")


class Central:
    def __init__(self): self.ticks = 0
    def tick(self): self.ticks += 1


class ProductDeviceControlPlaneTests(unittest.TestCase):
    def plane(self, root: Path, handler):
        token(root)
        plane = ProductDeviceControlPlane(config(root))
        plane.http.close()
        plane.http = httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://product.example",
            headers={
                "X-Ordax-Device-Id": DEVICE_ID,
                "X-Ordax-Device-Token": "a" * 64,
            },
        )
        plane.product_central_session = Central()
        return plane

    def test_heartbeat_uses_product_transport_only(self):
        seen = []
        def handler(request):
            seen.append((request.url.path, json.loads(request.content)))
            return httpx.Response(200, json={"ok": True, "recorded": True})
        with tempfile.TemporaryDirectory() as raw:
            plane = self.plane(Path(raw), handler)
            plane.heartbeat(["agent.status"], agent_version="1.2.3")
            self.assertEqual(seen[0][0], "/v3/device/transport/heartbeat")
            self.assertEqual(seen[0][1]["capabilities"], ["agent.status"])
            self.assertEqual(plane.product_central_session.ticks, 1)
            self.assertNotIn("legacy-control.invalid", str(seen))
            plane.http.close()

    def test_claim_starts_same_fenced_job_context_with_runtime_provenance(self):
        payload = {"request_id": "r", "action": "agent.status"}
        raw = json.dumps(payload, separators=(",", ":")).encode()
        job = {
            "job_id": "33333333-3333-4333-8333-333333333333",
            "device_id": DEVICE_ID,
            "project_id": None,
            "capability": PRODUCT_REMOTE_CAPABILITY,
            "payload_canonical_b64": base64.b64encode(raw).decode(),
            "payload_sha256": hashlib.sha256(raw).hexdigest(),
            "effect_id": "44444444-4444-4444-8444-444444444444",
            "attempt_id": "55555555-5555-4555-8555-555555555555",
            "lease_id": "66666666-6666-4666-8666-666666666666",
            "execution_epoch": 1,
        }
        seen = []
        def handler(request):
            body = json.loads(request.content)
            seen.append((request.url.path, body))
            if request.url.path.endswith("/claim"):
                job["agent_instance_id"] = body["agent_instance_id"]
                job["boot_id"] = body["boot_id"]
                return httpx.Response(200, json={"ok": True, "job": job})
            return httpx.Response(200, json={"ok": True})
        with tempfile.TemporaryDirectory() as raw_dir:
            plane = self.plane(Path(raw_dir), handler)
            claimed = plane.claim_next_job()
            self.assertIsNotNone(claimed)
            self.assertEqual([item[0] for item in seen], [
                "/v3/device/transport/claim", "/v3/device/transport/start",
            ])
            for _path, body in seen:
                self.assertEqual(body["agent_instance_id"], plane.agent_instance_id)
                self.assertEqual(body["boot_id"], plane.boot_id)
                self.assertEqual(body["runtime_kind"], "desktop")
                self.assertEqual(body["agent_version"], DEVICE_AGENT_VERSION)
            plane.http.close()

    def test_complete_persists_canonical_b64_report_and_runtime_provenance(self):
        seen = []
        def handler(request):
            seen.append((request.url.path, json.loads(request.content)))
            return httpx.Response(200, json={"ok": True})
        with tempfile.TemporaryDirectory() as raw_dir:
            plane = self.plane(Path(raw_dir), handler)
            from ordax_dev_agent.models import AgentJob
            job = AgentJob(
                id="33333333-3333-4333-8333-333333333333",
                action=PRODUCT_REMOTE_CAPABILITY, payload={},
                lease_token="66666666-6666-4666-8666-666666666666",
                effect_id="44444444-4444-4444-8444-444444444444",
                attempt_id="55555555-5555-4555-8555-555555555555",
                execution_epoch=1, agent_instance_id=plane.agent_instance_id,
                boot_id=plane.boot_id,
            )
            plane._jobs[job.id] = job
            plane.complete(job, ActionResult(True, "ok", {"value": 1}))
            self.assertEqual(seen[-1][0], "/v3/device/transport/report")
            report = seen[-1][1]
            decoded = json.loads(base64.b64decode(report["result_canonical_b64"]))
            self.assertEqual(decoded, {"data": {"value": 1}, "ok": True, "summary": "ok"})
            self.assertNotIn("result", report)
            self.assertEqual(report["runtime_kind"], "desktop")
            self.assertEqual(report["agent_version"], DEVICE_AGENT_VERSION)
            plane.http.close()

    def test_recovery_replays_persisted_execution_version_not_current_process_version(self):
        seen = []
        def handler(request):
            seen.append((request.url.path, json.loads(request.content)))
            return httpx.Response(200, json={"ok": True})
        with tempfile.TemporaryDirectory() as raw_dir:
            root = Path(raw_dir)
            plane = self.plane(root, handler)
            report = {
                "job_id": "33333333-3333-4333-8333-333333333333",
                "effect_id": "44444444-4444-4444-8444-444444444444",
                "attempt_id": "55555555-5555-4555-8555-555555555555",
                "lease_id": "66666666-6666-4666-8666-666666666666",
                "execution_epoch": 1,
                "agent_instance_id": "77777777-7777-4777-8777-777777777777",
                "boot_id": "88888888-8888-4888-8888-888888888888",
                "runtime_kind": "desktop",
                "agent_version": "0.3.0-old",
                "report_id": "99999999-9999-4999-8999-999999999999",
                "status": "succeeded",
                "result": {"ok": True, "summary": "done", "data": {}},
                "error_code": None,
            }
            plane._terminal_outbox.persist(report)
            plane.agent_version = "0.4.0-new"
            recovered = plane.recover_pending_reports()
            self.assertEqual(recovered, 1)
            self.assertEqual(seen[-1][0], "/v3/device/transport/recover-report")
            self.assertEqual(seen[-1][1]["agent_version"], "0.3.0-old")
            self.assertEqual(seen[-1][1]["runtime_kind"], "desktop")
            plane.http.close()

    def test_auth_failure_requires_device_recovery(self):
        def handler(_request):
            return httpx.Response(401, json={"ok": False, "error": "invalid_device_token"})
        with tempfile.TemporaryDirectory() as raw_dir:
            plane = self.plane(Path(raw_dir), handler)
            with self.assertRaises(DeviceAuthorizationError):
                plane.heartbeat(["agent.status"], agent_version="1")
            plane.http.close()

    def test_artifacts_never_fall_back_to_legacy_authority(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            plane = self.plane(Path(raw_dir), lambda _: httpx.Response(200, json={"ok": True}))
            with self.assertRaisesRegex(RuntimeError, "refusing legacy D1 artifact fallback"):
                plane.upload_artifact(None)
            plane.http.close()

    def test_runtime_transport_has_no_websocket_or_legacy_operator_dependency(self):
        source = Path(__file__).resolve().parents[1] / "ordax_dev_agent" / "product_device_control_plane.py"
        text = source.read_text(encoding="utf-8")
        self.assertNotIn("from websockets", text)
        self.assertNotIn("websockets.sync", text)
        self.assertNotIn("ORDAX_OPERATOR_TOKEN", text)
        self.assertNotIn("/v3/device/ws", text)


if __name__ == "__main__":
    unittest.main()
