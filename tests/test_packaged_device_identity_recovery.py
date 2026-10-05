from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path

from ordax_dev_agent.device_credentials import resolve_token_path
from ordax_dev_agent.device_identity_recovery import recover_existing_device_identity


class _Response:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class _Client:
    def __init__(self, response: _Response):
        self.response = response
        self.calls = []

    def post(self, endpoint, *, json, headers):
        self.calls.append((endpoint, json, headers))
        return self.response


class PackagedDeviceIdentityRecoveryTests(unittest.TestCase):
    def test_existing_token_repairs_missing_device_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            token = resolve_token_path(state)
            token.write_text("a" * 64, encoding="utf-8")
            device_id = str(uuid.uuid4())
            client = _Client(
                _Response(
                    200,
                    {
                        "ok": True,
                        "device_id": device_id,
                        "protocol": "cloudflare-v3",
                    },
                )
            )

            result = recover_existing_device_identity(
                state,
                client=client,
                binding="machine-binding",
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["state"], "recovered")
            self.assertTrue(result["changed"])
            settings = json.loads((state / "agent-settings.json").read_text())
            self.assertEqual(settings["device_id"], device_id)
            self.assertEqual(settings["control_plane_protocol"], "cloudflare-v3")
            self.assertEqual(len(client.calls), 1)
            _, body, headers = client.calls[0]
            self.assertEqual(body["operation"], "identify")
            self.assertEqual(body["machine_binding_sha256"], "machine-binding")
            self.assertEqual(headers["X-Ordax-Device-Token"], "a" * 64)

    def test_configured_identity_does_not_touch_network(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            device_id = str(uuid.uuid4())
            (state / "agent-settings.json").write_text(
                json.dumps(
                    {
                        "device_id": device_id,
                        "control_plane_protocol": "cloudflare-v3",
                    }
                ),
                encoding="utf-8",
            )
            client = _Client(_Response(500, {}))

            result = recover_existing_device_identity(state, client=client)

            self.assertTrue(result["ok"])
            self.assertEqual(result["state"], "already-configured")
            self.assertFalse(result["changed"])
            self.assertEqual(client.calls, [])

    def test_missing_credential_never_attempts_enrollment(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            client = _Client(
                _Response(
                    200,
                    {
                        "ok": True,
                        "device_id": str(uuid.uuid4()),
                        "protocol": "cloudflare-v3",
                    },
                )
            )

            result = recover_existing_device_identity(state, client=client)

            self.assertFalse(result["ok"])
            self.assertEqual(result["state"], "credential-missing")
            self.assertFalse(result["changed"])
            self.assertEqual(client.calls, [])
            self.assertFalse((state / "agent-settings.json").exists())

    def test_machine_binding_mismatch_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            resolve_token_path(state).write_text("b" * 64, encoding="utf-8")
            client = _Client(_Response(403, {"ok": False}))

            result = recover_existing_device_identity(
                state,
                client=client,
                binding="wrong-machine",
            )

            self.assertFalse(result["ok"])
            self.assertEqual(result["state"], "machine-binding-mismatch")
            self.assertFalse((state / "agent-settings.json").exists())


if __name__ == "__main__":
    unittest.main()
