from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.device_setup import SetupError
from ordax_studio.product_auth import (
    ProductAccountError,
    ProductAuthSession,
    connect_existing_device,
)
from ordax_studio.product_web_desktop import StudioProductApi


DEVICE_ID = "22222222-2222-4222-8222-222222222222"
LEGACY_ID = "11111111-1111-4111-8111-111111111111"


def config_for(state: Path, *, device_id: str | None) -> AgentConfig:
    return AgentConfig(
        agent_name="test",
        poll_seconds=1.0,
        state_dir=state,
        agent_repo_path=state.parent / "agent",
        hordax_path=state.parent / "hordax",
        bridge_path=state.parent / "bridge",
        projects={},
        device_id=device_id,
        product_mcp_url="https://product.example.test",
    )


class StudioProductAccountTests(unittest.TestCase):
    def test_existing_product_device_uses_canonical_product_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw) / "state"
            state.mkdir()
            config = config_for(state, device_id=DEVICE_ID)
            session = ProductAuthSession("browser.jwt", "owner@example.com")
            with (
                patch("ordax_studio.product_auth.configure_device", return_value={
                    "ok": True, "device_id": DEVICE_ID, "protocol": "cloudflare-v3",
                }) as enrollment,
                patch("ordax_studio.product_auth._revalidate_existing_device", return_value=DEVICE_ID) as validate,
            ):
                result = connect_existing_device(config, session=session)
            enrollment.assert_called_once_with(
                state,
                product_mcp_url="https://product.example.test",
                product_access_token="browser.jwt",
            )
            validated = validate.call_args.args[0]
            self.assertEqual("https://product.example.test", validated.product_mcp_url)
            self.assertIs(validate.call_args.args[1], session)
            self.assertEqual(
                "https://product.example.test",
                validate.call_args.kwargs["product_mcp_url"],
            )
            self.assertFalse(result["enrolled_now"])
            self.assertEqual(DEVICE_ID, result["device_id"])
            self.assertEqual("owner@example.com", result["email"])
            self.assertNotIn("browser.jwt", repr(result))
            self.assertNotIn("link", result)

    def test_explicit_product_endpoint_override_is_single_selected_origin(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw) / "state"
            state.mkdir()
            config = config_for(state, device_id=None)
            session = ProductAuthSession("jwt", None)
            with (
                patch("ordax_studio.product_auth.configure_device", return_value={
                    "ok": True, "device_id": DEVICE_ID, "protocol": "cloudflare-v3",
                }) as enrollment,
                patch("ordax_studio.product_auth._revalidate_existing_device", return_value=DEVICE_ID) as validate,
            ):
                connect_existing_device(
                    config,
                    session=session,
                    product_mcp_url="https://fallback.product.test",
                )
            enrollment.assert_called_once_with(
                state,
                product_mcp_url="https://fallback.product.test",
                product_access_token="jwt",
            )
            self.assertEqual(
                "https://fallback.product.test",
                validate.call_args.args[0].product_mcp_url,
            )

    def test_legacy_local_identity_can_migrate_to_product_identity(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw) / "state"
            state.mkdir()
            config = config_for(state, device_id=LEGACY_ID)
            session = ProductAuthSession("browser.jwt", "owner@example.com")
            with (
                patch("ordax_studio.product_auth.configure_device", return_value={
                    "ok": True, "device_id": DEVICE_ID, "protocol": "cloudflare-v3",
                }),
                patch("ordax_studio.product_auth._revalidate_existing_device", return_value=DEVICE_ID),
            ):
                result = connect_existing_device(config, session=session)
            self.assertTrue(result["enrolled_now"])
            self.assertEqual(DEVICE_ID, result["device_id"])

    def test_new_device_enrolls_with_product_session_and_is_revalidated(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw) / "state"
            state.mkdir()
            config = config_for(state, device_id=None)
            session = ProductAuthSession("sensitive-jwt", "user@example.com")
            with (
                patch("ordax_studio.product_auth.configure_device", return_value={
                    "ok": True, "device_id": DEVICE_ID, "protocol": "cloudflare-v3",
                }) as enrollment,
                patch("ordax_studio.product_auth._revalidate_existing_device", return_value=DEVICE_ID) as validate,
            ):
                result = connect_existing_device(config, session=session)
            enrollment.assert_called_once_with(
                state,
                product_mcp_url="https://product.example.test",
                product_access_token="sensitive-jwt",
            )
            self.assertEqual(DEVICE_ID, validate.call_args.args[0].device_id)
            self.assertTrue(result["enrolled_now"])
            self.assertEqual(DEVICE_ID, result["device_id"])
            self.assertNotIn("sensitive-jwt", repr(result))

    def test_specific_setup_failure_is_preserved_for_studio_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw) / "state"
            state.mkdir()
            config = config_for(state, device_id=None)
            with (
                patch(
                    "ordax_studio.product_auth.configure_device",
                    side_effect=SetupError("SETTINGS_INVALID_PRESERVED"),
                ),
                self.assertRaises(ProductAccountError) as raised,
            ):
                connect_existing_device(
                    config,
                    session=ProductAuthSession("jwt", "owner@example.com"),
                )
            self.assertEqual("SETTINGS_INVALID_PRESERVED", raised.exception.code)
            self.assertIn("configuração local", raised.exception.message)
            self.assertNotIn("jwt", repr(raised.exception))

    def test_owner_revalidation_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw) / "state"
            state.mkdir()
            config = config_for(state, device_id=DEVICE_ID)
            with (
                patch("ordax_studio.product_auth.configure_device", return_value={
                    "ok": True, "device_id": DEVICE_ID, "protocol": "cloudflare-v3",
                }),
                patch("ordax_studio.product_auth._revalidate_existing_device", return_value=LEGACY_ID),
                self.assertRaises(ProductAccountError) as raised,
            ):
                connect_existing_device(config, session=ProductAuthSession("jwt", "owner@example.com"))
            self.assertEqual("device_identity_changed", raised.exception.code)

    def test_product_api_accepts_no_credentials(self) -> None:
        signature = inspect.signature(StudioProductApi.connect_product_account)
        self.assertEqual(["self"], list(signature.parameters))

    def test_product_api_uses_browser_oauth_and_restarts_after_first_enrollment(self) -> None:
        api = object.__new__(StudioProductApi)
        api.agent = SimpleNamespace(config=SimpleNamespace())
        session = ProductAuthSession(access_token="sensitive-jwt", email="user@example.com")
        with (
            patch("ordax_studio.product_web_desktop.browser_oauth_session", return_value=session) as oauth,
            patch("ordax_studio.product_web_desktop.connect_existing_device", return_value={
                "email": "user@example.com", "device_id": DEVICE_ID, "enrolled_now": True,
            }) as connect,
            patch(
                "ordax_studio.product_web_desktop._restart_packaged_runtime_after_enrollment",
                return_value=True,
            ) as restart,
        ):
            result = api.connect_product_account()
        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["runtime_restarted"])
        self.assertFalse(result["data"]["runtime_restart_required"])
        oauth.assert_called_once_with()
        restart.assert_called_once_with()
        self.assertIs(session, api._product_session)
        self.assertEqual(DEVICE_ID, api._product_device_id)
        self.assertNotIn("sensitive-jwt", repr(result))
        self.assertIs(connect.call_args.kwargs["session"], session)
        self.assertEqual((api.agent.config,), connect.call_args.args)

    def test_product_api_returns_safe_error_shape(self) -> None:
        api = object.__new__(StudioProductApi)
        api.agent = SimpleNamespace(config=SimpleNamespace())
        with (
            patch(
                "ordax_studio.product_web_desktop.browser_oauth_session",
                return_value=ProductAuthSession(access_token="sensitive-jwt", email="user@example.com"),
            ),
            patch("ordax_studio.product_web_desktop.connect_existing_device", side_effect=ProductAccountError(
                "device_not_enrolled", "Máquina não registrada")),
        ):
            result = api.connect_product_account()
        self.assertFalse(result["ok"])
        self.assertEqual("device_not_enrolled", result["code"])
        self.assertEqual("Máquina não registrada", result["summary"])
        self.assertIsNone(getattr(api, "_product_session", None))


if __name__ == "__main__":
    unittest.main()
