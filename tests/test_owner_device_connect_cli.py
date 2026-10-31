from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from ordax_core.owner_oauth import OwnerOAuthError
from ordax_dev_agent.device_setup import SetupError
from ordax_dev_agent import owner_device_connect_cli as connect


class OwnerDeviceConnectCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name) / "state"
        self.config = SimpleNamespace(
            state_dir=self.state, product_mcp_url="https://mcp.ordax.com.br"
        )
        self.calls = []

    def provider(self):
        return self.config

    def auth(self):
        self.calls.append("oauth")
        return "opaque-code", "opaque-verifier"

    def exchange(self, code, verifier):
        self.assertEqual((code, verifier), ("opaque-code", "opaque-verifier"))
        self.calls.append("exchange")
        return "TOP_SECRET_OWNER_ACCESS_TOKEN"

    def configure(self, state, **kwargs):
        self.assertEqual(state, self.state)
        self.assertEqual(kwargs, {
            "product_mcp_url": "https://mcp.ordax.com.br",
            "product_access_token": "TOP_SECRET_OWNER_ACCESS_TOKEN",
            "device_kind": "desktop",
            "channel": "development",
        })
        self.calls.append("configure")
        return {
            "ok": True,
            "device_id": "11111111-1111-4111-8111-111111111111",
            "protocol": "cloudflare-v3",
        }

    def test_one_explicit_owner_login_then_canonical_device_enrollment(self):
        result = connect.enroll_owner_device(
            config_provider=self.provider, auth_code=self.auth,
            exchange=self.exchange, configure=self.configure,
        )
        self.assertEqual(self.calls, ["oauth", "exchange", "configure"])
        self.assertEqual(result["device_id"], "11111111-1111-4111-8111-111111111111")
        self.assertTrue(result["ok"])
        self.assertFalse(result["runtime_activated"])
        self.assertFalse(result["grants_issued"])
        self.assertFalse(result["end_to_end_proven"])
        self.assertNotIn("TOKEN", json.dumps(result))
        self.assertFalse(self.state.exists())

    def test_owner_rejection_cannot_call_enrollment(self):
        def reject():
            raise OwnerOAuthError("owner_oauth_rejected")
        with self.assertRaises(OwnerOAuthError):
            connect.enroll_owner_device(
                config_provider=self.provider, auth_code=reject,
                exchange=self.exchange, configure=self.configure,
            )
        self.assertEqual(self.calls, [])
        self.assertFalse(self.state.exists())

    def test_missing_endpoint_denied_before_opening_browser(self):
        self.config.product_mcp_url = None
        with self.assertRaises(SetupError):
            connect.enroll_owner_device(
                config_provider=self.provider, auth_code=self.auth,
                exchange=self.exchange, configure=self.configure,
            )
        self.assertEqual(self.calls, [])

    def test_untrusted_response_never_reported_as_pairing(self):
        with self.assertRaises(SetupError):
            connect.enroll_owner_device(
                config_provider=self.provider, auth_code=self.auth,
                exchange=self.exchange,
                configure=lambda *a, **k: {
                    "ok": True, "device_id": "", "protocol": "cloudflare-v3"
                },
            )

    def test_main_requires_explicit_connect_switch(self):
        out = io.StringIO()
        with contextlib.redirect_stderr(out), self.assertRaises(SystemExit) as err:
            connect.main([])
        self.assertEqual(err.exception.code, 2)
        self.assertIn("--connect", out.getvalue())

    def test_main_outputs_only_device_id_and_non_authoritative_state(self):
        out = io.StringIO()
        with mock.patch.object(connect, "enroll_owner_device", return_value={
            "ok": True, "device_id": "11111111-1111-4111-8111-111111111111",
            "channel": "development", "runtime_activated": False,
            "grants_issued": False, "end_to_end_proven": False,
        }), contextlib.redirect_stdout(out):
            self.assertEqual(connect.main(["--connect"]), 0)
        self.assertEqual(json.loads(out.getvalue())["grants_issued"], False)
        self.assertNotIn("TOKEN", out.getvalue())

    def test_main_denied_owner_oauth_no_secret_leak(self):
        out = io.StringIO()
        with mock.patch.object(connect, "enroll_owner_device",
                               side_effect=OwnerOAuthError("owner_oauth_rejected")), \
             contextlib.redirect_stderr(out):
            self.assertEqual(connect.main(["--connect"]), 1)
        self.assertIn("owner_oauth_rejected", out.getvalue())
        self.assertNotIn("TOKEN", out.getvalue())

    def test_main_setup_denial_no_secret_leak(self):
        out = io.StringIO()
        with mock.patch.object(connect, "enroll_owner_device",
                               side_effect=SetupError("USER_AUTHORIZATION_REQUIRED")), \
             contextlib.redirect_stderr(out):
            self.assertEqual(connect.main(["--connect"]), 1)
        self.assertIn("USER_AUTHORIZATION_REQUIRED", out.getvalue())
        self.assertNotIn("TOKEN", out.getvalue())


if __name__ == "__main__":
    unittest.main()
