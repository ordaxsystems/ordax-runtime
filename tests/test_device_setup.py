import hashlib
import inspect
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import httpx

from ordax_dev_agent.device_setup import SetupError, configure, private_directory, setup_lock


class SetupTests(unittest.TestCase):
    def test_windows_private_acl_uses_dotnet_not_security_cmdlets(self):
        source = inspect.getsource(private_directory)
        self.assertNotIn("Get-Acl", source)
        self.assertNotIn("Set-Acl", source)
        self.assertIn("DirectoryInfo", source)
        self.assertIn("GetAccessControl", source)
        self.assertIn("SetAccessControl", source)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "state"
        self.home = Path(self.temp.name) / "home"
        self.state.mkdir()
        (self.home / "Documents/github/cerco-no-interior-mvp").mkdir(parents=True)
        self.device = str(uuid.uuid4())
        self.binding = "a" * 64
        self.credential_hash = None
        self.enrollments = 0
        self.calls = []
        self.offline = False
        self.lost_ack = False
        self.mismatch = False
        self.acl = patch(
            "ordax_dev_agent.device_setup.private_directory",
            side_effect=lambda p: p.mkdir(exist_ok=True),
        )
        self.acl.start()
        self.addCleanup(self.acl.stop)
        self.client = httpx.Client(transport=httpx.MockTransport(self.server))
        self.addCleanup(self.client.close)

    def server(self, req):
        if self.offline:
            raise httpx.ConnectError("offline")
        body = json.loads(req.content)
        self.calls.append(body)
        self.last_request_path = req.url.path
        if body["operation"] == "enroll":
            self.assertEqual("Bearer ordax-product-session", req.headers["Authorization"])
            self.assertNotIn("token", body)
            self.assertEqual("desktop", body["device_kind"])
            self.assertEqual("development", body["channel"])
            self.credential_hash = body["token_sha256"]
            self.enrollments += 1
            if self.lost_ack:
                self.lost_ack = False
                raise httpx.ReadTimeout("ack lost after commit")
        elif self.mismatch:
            return httpx.Response(403, json={"ok": False})
        elif (
            hashlib.sha256(
                req.headers["X-Ordax-Device-Token"].encode()
            ).hexdigest()
            != self.credential_hash
        ):
            return httpx.Response(401, json={"ok": False})

        return httpx.Response(
            200,
            json={
                "ok": True,
                "protocol": "cloudflare-v3",
                "device_id": self.device,
            },
        )

    def run_setup(self, **kwargs):
        kwargs.setdefault("control_plane_url", "https://control.example")
        kwargs.setdefault("product_access_token", "ordax-product-session")
        return configure(
            self.state,
            client=self.client,
            binding=self.binding,
            home=self.home,
            **kwargs,
        )

    def test_new_machine_and_ten_idempotent_runs(self):
        self.run_setup()
        token_path = self.state / "device-token.cloudflare-v3.txt"
        token = token_path.read_text()
        for _ in range(10):
            self.assertEqual(self.device, self.run_setup()["device_id"])
        self.assertEqual(1, self.enrollments)
        self.assertEqual(token, token_path.read_text())
        self.assertNotIn("ordax-product-session", (self.state / "agent-settings.json").read_text())
        settings = json.loads((self.state / "agent-settings.json").read_text())
        self.assertEqual("cloudflare-v3", settings["control_plane_protocol"])
        self.assertEqual("https://control.example", settings["control_plane_url"])
        self.assertEqual(self.device, settings["device_id"])
        self.assertIn("cerco-no-interior-mvp", settings["projects"])
        self.assertEqual("/v3/device/setup", self.last_request_path)

    def test_explicit_device_kind_and_channel_reach_enrollment(self):
        original_server = self.server

        def custom_server(req):
            body = json.loads(req.content)
            if body["operation"] == "enroll":
                self.assertEqual("laptop", body["device_kind"])
                self.assertEqual("stable", body["channel"])
                self.credential_hash = body["token_sha256"]
                self.enrollments += 1
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "protocol": "cloudflare-v3",
                        "device_id": self.device,
                    },
                )
            return original_server(req)

        custom_client = httpx.Client(transport=httpx.MockTransport(custom_server))
        self.addCleanup(custom_client.close)
        result = configure(
            self.state,
            client=custom_client,
            binding=self.binding,
            home=self.home,
            control_plane_url="https://control.example",
            product_access_token="ordax-product-session",
            device_kind="laptop",
            channel="stable",
        )
        self.assertEqual(self.device, result["device_id"])

    def test_invalid_enrollment_metadata_fails_before_network(self):
        with self.assertRaisesRegex(SetupError, "DEVICE_KIND_INVALID"):
            self.run_setup(device_kind="toaster")
        with self.assertRaisesRegex(SetupError, "PRODUCT_CHANNEL_INVALID"):
            self.run_setup(channel="nightly")
        self.assertEqual([], self.calls)

    def test_new_machine_requires_explicit_ordax_account_session(self):
        with self.assertRaisesRegex(SetupError, "PRODUCT_ACCOUNT_LOGIN_REQUIRED"):
            self.run_setup(product_access_token=None)
        self.assertFalse((self.state / "device-token.cloudflare-v3.txt").exists())
        self.assertFalse((self.state / "device-token.cloudflare-v3.txt.pending-setup").exists())

    def test_lost_response_reuses_committed_pending_token(self):
        self.lost_ack = True
        with self.assertRaises(SetupError):
            self.run_setup()
        pending = (
            self.state / "device-token.cloudflare-v3.txt.pending-setup"
        ).read_text()
        self.run_setup()
        self.assertEqual(
            pending,
            (self.state / "device-token.cloudflare-v3.txt").read_text(),
        )
        self.assertEqual(1, self.enrollments)

    def test_malformed_pending_token_is_quarantined_and_replaced_during_authenticated_enrollment(self):
        pending = self.state / "device-token.cloudflare-v3.txt.pending-setup"
        pending.write_text("legacy-interrupted-value", encoding="utf-8")

        result = self.run_setup()

        self.assertEqual(self.device, result["device_id"])
        self.assertEqual(1, self.enrollments)
        active = self.state / "device-token.cloudflare-v3.txt"
        self.assertTrue(active.is_file())
        self.assertEqual(64, len(active.read_text()))
        quarantined = list(self.state.glob("device-token.cloudflare-v3.txt.pending-setup.invalid-*"))
        self.assertEqual(1, len(quarantined))
        self.assertEqual("legacy-interrupted-value", quarantined[0].read_text())
        self.assertFalse(pending.exists())

    def test_malformed_pending_token_is_not_rotated_without_product_session(self):
        pending = self.state / "device-token.cloudflare-v3.txt.pending-setup"
        pending.write_text("legacy-interrupted-value", encoding="utf-8")

        with self.assertRaisesRegex(SetupError, "PRODUCT_ACCOUNT_LOGIN_REQUIRED"):
            self.run_setup(product_access_token=None)

        self.assertEqual("legacy-interrupted-value", pending.read_text())
        self.assertEqual([], list(self.state.glob("*.invalid-*")))
        self.assertEqual([], self.calls)

    def test_revoked_token_reenrolls_and_preserves_device(self):
        self.run_setup()
        token_path = self.state / "device-token.cloudflare-v3.txt"
        old = token_path.read_text()
        self.credential_hash = None
        self.assertEqual(self.device, self.run_setup()["device_id"])
        self.assertNotEqual(old, token_path.read_text())

    def test_missing_token_recovers_same_machine(self):
        self.run_setup()
        (self.state / "device-token.cloudflare-v3.txt").unlink()
        self.assertEqual(self.device, self.run_setup()["device_id"])
        self.assertEqual(2, self.enrollments)

    def test_offline_never_rotates_or_changes_settings(self):
        self.run_setup()
        before = {p.name: p.read_bytes() for p in self.state.iterdir()}
        self.offline = True
        with self.assertRaisesRegex(SetupError, "UNAVAILABLE"):
            self.run_setup()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.state.iterdir()})
        self.assertEqual(1, self.enrollments)

    def test_other_machine_token_is_refused(self):
        self.run_setup()
        self.mismatch = True
        with self.assertRaisesRegex(SetupError, "MACHINE_BINDING_MISMATCH"):
            self.run_setup()
        self.assertEqual(1, self.enrollments)

    def test_existing_project_customization_is_preserved(self):
        custom = {"path": "D:/custom", "apps": ["blender"]}
        (self.state / "agent-settings.json").write_text(
            json.dumps(
                {
                    "custom": 123,
                    "projects": {"cerco-no-interior-mvp": custom},
                }
            )
        )

        self.run_setup()
        settings = json.loads((self.state / "agent-settings.json").read_text())
        self.assertEqual(custom, settings["projects"]["cerco-no-interior-mvp"])
        self.assertEqual(123, settings["custom"])
        self.assertEqual("cloudflare-v3", settings["control_plane_protocol"])
        self.assertEqual(self.device, settings["device_id"])
        self.assertTrue((self.state / "device-token.cloudflare-v3.txt").is_file())

    def test_requires_https_outside_loopback(self):
        with self.assertRaisesRegex(SetupError, "CONTROL_PLANE_URL_INVALID"):
            self.run_setup(control_plane_url="http://control.example")
        self.assertEqual([], self.calls)

    def test_allows_loopback_http_for_local_verification(self):
        result = self.run_setup(control_plane_url="http://127.0.0.1:8787")
        self.assertEqual("cloudflare-v3", result["protocol"])

    def test_corrupt_settings_are_not_overwritten(self):
        (self.state / "agent-settings.json").write_text("{broken")
        with self.assertRaisesRegex(SetupError, "SETTINGS_INVALID_PRESERVED"):
            self.run_setup()
        self.assertEqual([], self.calls)

    def test_failed_acl_prevents_network_and_secret_creation(self):
        with patch(
            "ordax_dev_agent.device_setup.private_directory",
            side_effect=SetupError("PRIVATE_ACL_FAILED"),
        ):
            with self.assertRaisesRegex(SetupError, "ACL"):
                self.run_setup()
        self.assertEqual([], self.calls)
        self.assertEqual([], list(self.state.iterdir()))

    def test_concurrent_supervisor_and_setup_cannot_rotate_twice(self):
        with setup_lock(self.state):
            with self.assertRaisesRegex(SetupError, "SETUP_ALREADY_RUNNING"):
                self.run_setup()
        self.assertEqual([], self.calls)
        self.run_setup()
        self.assertEqual(1, self.enrollments)


if __name__ == "__main__":
    unittest.main()
