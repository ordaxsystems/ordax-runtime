from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.product_remote_client import ProductRemoteError
from ordax_studio import electron_account_session as account
from ordax_studio.product_auth import ProductAccountError, ProductAuthSession


def request(**changes):
    return {
        "schema": "ordax.studio-product-account-session/1",
        "operation": "browser-sign-in",
        **changes,
    }


def runtime_config(
    control_plane: str = "https://control.example.com",
    product_mcp: str = "https://product.example.com",
) -> AgentConfig:
    root = Path("C:/ordax-test")
    return AgentConfig(
        agent_name="test-pc", poll_seconds=1.0, state_dir=root / "state",
        agent_repo_path=root, hordax_path=root / "hordax", bridge_path=root,
        control_plane_url=control_plane, product_mcp_url=product_mcp,
    )


def backend_with(*, subject=None, error=None):
    backend = Mock()
    if error is not None:
        backend.session.side_effect = error
    else:
        backend.session.return_value = subject or {
            "subject_id": "11111111-1111-4111-8111-111111111111"
        }
    backend.__enter__ = Mock(return_value=backend)
    backend.__exit__ = Mock(return_value=None)
    return backend


class CanonicalElectronAccountSessionTests(unittest.TestCase):
    def test_browser_session_uses_product_mcp_but_preserves_operational_control_plane(self):
        backend = backend_with()
        factory = Mock(return_value=backend)
        authenticate = Mock(return_value=ProductAuthSession(
            access_token="private.jwt.signature", email="user@example.com"))
        connect = Mock(return_value={
            "device_id": "22222222-2222-4222-8222-222222222222",
            "enrolled_now": True,
        })
        config = runtime_config(
            control_plane="https://commands.example.com",
            product_mcp="https://product.example.com",
        )
        result = account.authorize_account(
            request(), authenticate=authenticate, remote_factory=factory,
            config_factory=lambda: config, connect_device=connect,
        )
        authenticate.assert_called_once_with()
        factory.assert_called_once_with("https://product.example.com")
        backend.session.assert_called_once_with("private.jwt.signature")
        backend.targets.assert_not_called()
        connect.assert_called_once()
        args, kwargs = connect.call_args
        self.assertEqual("https://commands.example.com", args[0].control_plane_url)
        self.assertEqual("https://product.example.com", args[0].product_mcp_url)
        self.assertEqual(len(args), 1)
        self.assertEqual(kwargs["session"].access_token, "private.jwt.signature")
        self.assertEqual(kwargs["product_mcp_url"], "https://product.example.com")
        self.assertEqual(result["schema"], request()["schema"])
        self.assertIs(result["ok"], True)
        self.assertEqual(result["access_token"], "private.jwt.signature")
        self.assertEqual(result["subject_id"], "11111111-1111-4111-8111-111111111111")
        self.assertEqual(result["device_id"], "22222222-2222-4222-8222-222222222222")
        self.assertIs(result["enrolled_now"], True)
        self.assertNotIn("password", json.dumps(result).lower())

    def test_branded_product_transport_failure_uses_fixed_read_only_transport_fallback(self):
        primary = backend_with(error=ProductRemoteError(
            "product_remote_transport_error", 0, "network unavailable"))
        fallback = backend_with()
        factory = Mock(side_effect=[primary, fallback])
        connect = Mock(return_value={
            "device_id": "22222222-2222-4222-8222-222222222222",
            "enrolled_now": True,
        })
        config = runtime_config(product_mcp="https://mcp.ordax.com.br")
        result = account.authorize_account(
            request(), authenticate=lambda: ProductAuthSession("jwt", None),
            remote_factory=factory, config_factory=lambda: config,
            connect_device=connect,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            [call.args[0] for call in factory.call_args_list],
            [
                "https://mcp.ordax.com.br",
                "https://ordax-mcp.ordaxsystems.workers.dev",
            ],
        )
        args, kwargs = connect.call_args
        self.assertEqual("https://control.example.com", args[0].control_plane_url)
        self.assertEqual(
            "https://ordax-mcp.ordaxsystems.workers.dev",
            args[0].product_mcp_url,
        )
        self.assertEqual(
            "https://ordax-mcp.ordaxsystems.workers.dev",
            kwargs["product_mcp_url"],
        )

    def test_product_auth_failure_never_uses_transport_fallback(self):
        denied = backend_with(error=ProductRemoteError(
            "product_auth_required", 401, "private auth detail"))
        factory = Mock(return_value=denied)
        connect = Mock()
        with self.assertRaises(ProductRemoteError) as raised:
            account.authorize_account(
                request(), authenticate=lambda: ProductAuthSession("jwt", None),
                remote_factory=factory,
                config_factory=lambda: runtime_config(product_mcp="https://mcp.ordax.com.br"),
                connect_device=connect,
            )
        self.assertEqual("product_auth_required", raised.exception.error_code)
        factory.assert_called_once_with("https://mcp.ordax.com.br")
        connect.assert_not_called()

    def test_custom_product_transport_failure_never_jumps_to_ordax_fallback(self):
        unavailable = backend_with(error=ProductRemoteError(
            "product_remote_transport_error", 0, "custom endpoint offline"))
        factory = Mock(return_value=unavailable)
        with self.assertRaises(ProductRemoteError):
            account.authorize_account(
                request(), authenticate=lambda: ProductAuthSession("jwt", None),
                remote_factory=factory,
                config_factory=lambda: runtime_config(product_mcp="https://tenant.example.com"),
                connect_device=Mock(),
            )
        factory.assert_called_once_with("https://tenant.example.com")

    def test_protocol_rejects_credentials_and_extra_fields_before_browser(self):
        authenticate = Mock()
        for bad in (
            None, [], "a", {}, request(schema="wrong"), request(operation="sign-in"),
            dict(request(), extra="value"), dict(request(), email="user@example.com"),
            dict(request(), password="secret"),
        ):
            with self.subTest(bad=str(bad)[:40]), self.assertRaises(ValueError):
                account.authorize_account(bad, authenticate=authenticate)
        authenticate.assert_not_called()

    def test_rejects_fake_auth_and_product_session(self):
        for token in ("", "jwt\r\ninjected", "a" * 16001):
            auth = Mock(return_value=ProductAuthSession(token, "user@example.com"))
            with self.subTest(token=token[:8]), self.assertRaises(ValueError):
                account.authorize_account(request(), authenticate=auth)
        backend = backend_with(subject={"other": "not verified"})
        with self.assertRaisesRegex(ValueError, "invalid_product_subject"):
            account.authorize_account(
                request(), authenticate=lambda: ProductAuthSession("jwt", None),
                remote_factory=lambda _: backend,
                config_factory=lambda: runtime_config(),
            )

    def test_stdin_protocol_redacts_tokens_and_disallows_duplicate_keys(self):
        token = "secret.jwt.value"
        for raw in (
            json.dumps(request()).encode(),
            b'{"schema":"x","schema":"y","operation":"browser-sign-in"}',
            b"x" * 1001,
            b"not-json",
            b"",
        ):
            with self.subTest(raw=raw[:20]):
                output = io.StringIO()
                with patch.object(account, "authorize_account",
                                  side_effect=ProductAccountError("bad", "private details")) as auth:
                    code = account.run(io.BytesIO(raw), output)
                self.assertEqual(code, 1)
                value = json.loads(output.getvalue())
                self.assertIs(value["ok"], False)
                self.assertNotIn(token, output.getvalue())
                self.assertNotIn("private details", output.getvalue())
                if raw == json.dumps(request()).encode():
                    self.assertEqual("bad", value["error"])
                if raw.startswith(b"not-") or len(raw) > 1000 or not raw or b'"schema":"x","schema":"y"' in raw:
                    auth.assert_not_called()
                    self.assertEqual("account_auth_failed", value["error"])

    def test_product_remote_error_exposes_only_bounded_error_code(self):
        output = io.StringIO()
        with patch.object(
            account,
            "authorize_account",
            side_effect=ProductRemoteError(
                "product_token_claims_invalid", 401,
                "private provider detail that must never cross stdio",
                request_id="private-request-id",
            ),
        ):
            code = account.run(io.BytesIO(json.dumps(request()).encode()), output)
        self.assertEqual(1, code)
        value = json.loads(output.getvalue())
        self.assertEqual({
            "schema": request()["schema"],
            "ok": False,
            "error": "product_token_claims_invalid",
        }, value)
        self.assertNotIn("private provider detail", output.getvalue())
        self.assertNotIn("private-request-id", output.getvalue())

    def test_unsafe_product_error_codes_fall_back_without_leaking_details(self):
        failures = (
            ProductAccountError("../../unsafe", "private account detail"),
            ProductAccountError("x" * 129, "private account detail"),
            ProductRemoteError("product-error-with-dash", 401, "private remote detail"),
        )
        for failure in failures:
            with self.subTest(error=type(failure).__name__):
                output = io.StringIO()
                with patch.object(account, "authorize_account", side_effect=failure):
                    code = account.run(io.BytesIO(json.dumps(request()).encode()), output)
                self.assertEqual(1, code)
                value = json.loads(output.getvalue())
                self.assertEqual("account_auth_failed", value["error"])
                self.assertNotIn("private", output.getvalue())

    def test_single_response_exactly_one_line_contains_no_password_field(self):
        out = io.StringIO()
        with patch.object(account, "authorize_account", return_value={
            "schema": "ordax.studio-product-account-session/1", "ok": True,
            "access_token": "jwt", "email": "x@example.com",
            "subject_id": "11111111-1111-4111-8111-111111111111",
            "device_id": "22222222-2222-4222-8222-222222222222",
            "enrolled_now": True,
        }):
            self.assertEqual(account.run(io.BytesIO(json.dumps(request()).encode()), out), 0)
        self.assertEqual(out.getvalue().count("\n"), 1)
        self.assertNotIn("password", out.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
