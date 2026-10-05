from __future__ import annotations

import base64
import hashlib
import json
import unittest
from pathlib import Path

from ordax_dev_agent.action_contracts import (
    DEVICE_ACTION_PREFIXES,
    dispatch_device_capability,
    is_device_owned_capability,
)
from ordax_dev_agent.models import ActionResult
from ordax_dev_agent.remote_protocol import (
    canonical_result,
    decode_job_payload,
    dispatch_job,
)


class ProviderNeutralRemoteProtocolTests(unittest.TestCase):
    def test_cloudflare_transport_does_not_import_development_v2(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source = (root / "ordax_dev_agent" / "cloudflare_control_plane.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("development_control_plane", source)
        self.assertNotIn("DevelopmentControlPlane", source)
        self.assertIn("from .remote_protocol import", source)

    def test_digest_dispatch_and_result_are_provider_neutral(self) -> None:
        payload = {"project": "scene", "limit": 12}
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        row = {
            "capability": "blender.live_inspect",
            "payload_canonical_b64": base64.b64encode(raw).decode("ascii"),
            "payload_sha256": hashlib.sha256(raw).hexdigest(),
        }

        decoded = decode_job_payload(row)
        action, action_payload, project = dispatch_job(row, decoded)
        body, digest = canonical_result(
            ActionResult(True, "ok", {"transport": "provider-neutral"})
        )

        self.assertEqual(decoded, payload)
        self.assertEqual(action, "blender.live_inspect")
        self.assertEqual(action_payload, payload)
        self.assertEqual(project, "scene")
        self.assertEqual(body["data"]["transport"], "provider-neutral")
        self.assertEqual(len(digest), 64)

    def test_digest_mismatch_fails_before_dispatch(self) -> None:
        raw = b"{}"
        row = {
            "capability": "agent.status",
            "payload_canonical_b64": base64.b64encode(raw).decode("ascii"),
            "payload_sha256": "0" * 64,
        }
        with self.assertRaisesRegex(RuntimeError, "digest mismatch"):
            decode_job_payload(row)

    def test_device_action_contract_keeps_existing_owned_prefixes(self) -> None:
        self.assertIn("blender.", DEVICE_ACTION_PREFIXES)
        self.assertIn("unity.", DEVICE_ACTION_PREFIXES)
        self.assertIn("git.", DEVICE_ACTION_PREFIXES)
        self.assertIn("workspace.", DEVICE_ACTION_PREFIXES)
        self.assertIn("terminal.", DEVICE_ACTION_PREFIXES)
        self.assertIn("process.", DEVICE_ACTION_PREFIXES)
        self.assertIn("browser.", DEVICE_ACTION_PREFIXES)
        self.assertIn("computer.", DEVICE_ACTION_PREFIXES)
        self.assertTrue(is_device_owned_capability("artifact.preview"))
        self.assertTrue(is_device_owned_capability("workspace.bind_project"))
        self.assertTrue(is_device_owned_capability("terminal.exec"))
        self.assertTrue(is_device_owned_capability("browser.screenshot"))
        self.assertTrue(is_device_owned_capability("computer.processes"))
        self.assertFalse(is_device_owned_capability("shell.exec"))

    def test_contract_dispatches_direct_capability_without_rewriting_payload(self) -> None:
        payload = {"project": "scene", "frames": 2}
        dispatched = dispatch_device_capability("observation.capture", payload)

        self.assertEqual(dispatched.action, "observation.capture")
        self.assertIs(dispatched.payload, payload)
        self.assertEqual(dispatched.project, "scene")

    def test_adapter_invoke_preserves_existing_blender_scope(self) -> None:
        dispatched = dispatch_device_capability(
            "ordax.dev.adapter.invoke",
            {
                "adapter": "blender",
                "action": "blender.live_inspect",
                "payload": {"limit": 3},
                "project": "scene",
            },
        )

        self.assertEqual(dispatched.action, "blender.live_inspect")
        self.assertEqual(dispatched.payload, {"limit": 3})
        self.assertEqual(dispatched.project, "scene")

    def test_adapter_invoke_does_not_expand_permissions(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "not an allowed capability"):
            dispatch_device_capability(
                "ordax.dev.adapter.invoke",
                {
                    "adapter": "unity",
                    "action": "unity.scene_summary",
                    "payload": {},
                    "project": "scene",
                },
            )

        with self.assertRaisesRegex(RuntimeError, "not owned by Device Agent"):
            dispatch_device_capability("shell.exec", {"command": "whoami"})


if __name__ == "__main__":
    unittest.main()
