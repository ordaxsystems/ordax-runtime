from __future__ import annotations

import io
import json
import unittest
from unittest.mock import Mock, patch

from ordax_studio import electron_account_session as account
from ordax_studio.product_auth import ProductAccountError, ProductAuthSession


def request(**changes):
    return {"schema": "ordax.studio-product-account-session/1", "operation": "sign-in",
            "email": "user@example.com", "password": "private-password", **changes}


class CanonicalElectronAccountSessionTests(unittest.TestCase):
    def test_uses_existing_auth_owner_and_checks_product_subject(self):
        backend = Mock()
        backend.session.return_value = {"subject_id": "user-123"}
        backend.__enter__ = Mock(return_value=backend)
        backend.__exit__ = Mock(return_value=None)
        factory = Mock(return_value=backend)
        authenticate = Mock(return_value=ProductAuthSession(
            access_token="private.jwt.signature", email="user@example.com"))
        result = account.authorize_account(request(), authenticate=authenticate,
                                           remote_factory=factory,
                                           control_plane="https://control.example.com")
        authenticate.assert_called_once_with("user@example.com", "private-password")
        factory.assert_called_once_with("https://control.example.com")
        backend.session.assert_called_once_with("private.jwt.signature")
        self.assertEqual(result["schema"], request()["schema"])
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["access_token"], "private.jwt.signature")
        self.assertNotIn("password", result)

    def test_rejects_noncanonical_shape_before_request_or_login(self):
        authenticate = Mock()
        for bad in (
            None, [], "a", {}, request(schema="wrong"),
            request(operation="execute"), dict(request(), extra="value"),
            request(email=["user@example.com"]), request(password=None),
            request(email="x\r\nHeader: injected"), request(password="\x00"),
            request(password="x" * 2049), request(password=""),
        ):
            with self.subTest(bad=str(bad)[:30]), self.assertRaises(ValueError):
                account.authorize_account(bad, authenticate=authenticate)
        authenticate.assert_not_called()

    def test_rejects_fake_auth_and_product_session(self):
        for token in ("", "jwt\r\ninjected", "a" * 16001):
            auth = Mock(return_value=ProductAuthSession(token, "user@example.com"))
            with self.subTest(token=token[:8]), self.assertRaises(ValueError):
                account.authorize_account(request(), authenticate=auth)
        backend = Mock()
        backend.session.return_value = {"other": "not verified"}
        backend.__enter__ = Mock(return_value=backend)
        backend.__exit__ = Mock(return_value=None)
        with self.assertRaisesRegex(ValueError, "invalid_product_subject"):
            account.authorize_account(
                request(), authenticate=lambda *_: ProductAuthSession("jwt", None),
                remote_factory=lambda _: backend,
            )

    def test_stdin_protocol_redacts_secrets_and_disallows_duplicate_keys(self):
        secret = "private-password"
        token = "secret.jwt.value"
        for raw in (
            json.dumps(request()).encode(),
            b'{"schema":"x","schema":"y","operation":"sign-in","email":"x","password":"private-password"}',
            b"x" * 3001,
            b"not-json",
            b"",
        ):
            with self.subTest(raw=raw[:15]):
                output = io.StringIO()
                with patch.object(account, "authorize_account",
                                  side_effect=ProductAccountError("bad", "private details")) as auth:
                    code = account.run(io.BytesIO(raw), output)
                self.assertEqual(code, 1)
                self.assertTrue(json.loads(output.getvalue())["ok"] is False)
                self.assertNotIn(secret, output.getvalue())
                self.assertNotIn(token, output.getvalue())
                self.assertNotIn("private details", output.getvalue())
                if raw.startswith(b"not-") or len(raw) > 3000 or not raw or b'"schema":"x","schema":"y"' in raw:
                    auth.assert_not_called()

    def test_single_response_exactly_one_line_never_logs_password(self):
        out = io.StringIO()
        with patch.object(account, "authorize_account", return_value={
            "schema": "ordax.studio-product-account-session/1", "ok": True,
            "access_token": "jwt", "email": "x@example.com",
        }):
            self.assertEqual(account.run(io.BytesIO(json.dumps(request()).encode()), out), 0)
        self.assertEqual(out.getvalue().count("\n"), 1)
        self.assertNotIn("private-password", out.getvalue())


if __name__ == "__main__":
    unittest.main()
