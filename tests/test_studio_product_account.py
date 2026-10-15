from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx

from ordax_dev_agent.config import AgentConfig
from ordax_studio.product_auth import (
    ProductAccountError,
    ProductAuthSession,
    connect_existing_device,
    sign_in_with_password,
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
        control_plane_url="https://control.example.test",
    )


class StudioProductAccountTests(unittest.TestCase):
    def test_password_login_goes_directly_to_supabase_and_returns_memory_session(self) -> None:
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "access_token": "header.payload.signature",
            "user": {"email": "user@example.com"},
        }
        client = Mock(spec=httpx.Client)
        client.post.return_value = response
        session = sign_in_with_password(" user@example.com ", "secret-password", http=client)
        self.assertEqual("header.payload.signature", session.access_token)
        self.assertEqual("user@example.com", session.email)
        _, kwargs = client.post.call_args
        self.assertEqual({"grant_type": "password"}, kwargs["params"])
        self.assertEqual({"email": "user@example.com", "password": "secret-password"}, kwargs["json"])
        self.assertTrue(client.post.call_args.args[0].endswith("/auth/v1/token"))

    def test_rejected_login_does_not_surface_provider_body(self) -> None:
        response = Mock()
        response.status_code = 400
        response.json.return_value = {
            "error": "invalid_grant",
            "error_description": "provider internal detail",
        }
        client = Mock(spec=httpx.Client)
        client.post.return_value = response
        with self.assertRaises(ProductAccountError) as raised:
            sign_in_with_password("user@example.com", "wrong", http=client)
        self.assertEqual("product_auth_rejected", raised.exception.code)
        self.assertNotIn("provider internal detail", raised.exception.message)

    def test_existing_product_device_is_resolved_and_owner_revalidated_without_pairing(self) -> None:
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
                patch("ordax_studio.product_auth.sign_in_with_password") as password_login,
            ):
                result = connect_existing_device(config, session=session)
            password_login.assert_not_called()
            enrollment.assert_called_once_with(
                state,
                control_plane_url="https://control.example.test",
                product_access_token="browser.jwt",
            )
            self.assertEqual(DEVICE_ID, validate.call_args.args[0].device_id)
            self.assertIs(validate.call_args.args[1], session)
            self.assertFalse(result["enrolled_now"])
            self.assertEqual(DEVICE_ID, result["device_id"])
            self.assertEqual("owner@example.com", result["email"])
            self.assertNotIn("browser.jwt", repr(result))
            self.assertNotIn("link", result)

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
                control_plane_url="https://control.example.test",
                product_access_token="sensitive-jwt",
            )
            self.assertEqual(DEVICE_ID, validate.call_args.args[0].device_id)
            self.assertTrue(result["enrolled_now"])
            self.assertEqual(DEVICE_ID, result["device_id"])
            self.assertNotIn("sensitive-jwt", repr(result))

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

    def test_product_api_restarts_packaged_runtime_after_first_product_enrollment(self) -> None:
        api = object.__new__(StudioProductApi)
        api.agent = SimpleNamespace(config=SimpleNamespace())
        session = ProductAuthSession(access_token="sensitive-jwt", email="user@example.com")
        with (
            patch("ordax_studio.product_web_desktop.sign_in_with_password", return_value=session),
            patch("ordax_studio.product_web_desktop.connect_existing_device", return_value={
                "email": "user@example.com", "device_id": DEVICE_ID, "enrolled_now": True,
            }) as connect,
            patch(
                "ordax_studio.product_web_desktop._restart_packaged_runtime_after_enrollment",
                return_value=True,
            ) as restart,
        ):
            result = api.connect_product_account("user@example.com", "secret")
        self.assertTrue(result["ok"])
        self.assertTrue(result["data"]["runtime_restarted"])
        self.assertFalse(result["data"]["runtime_restart_required"])
        restart.assert_called_once_with()
        self.assertIs(session, api._product_session)
        self.assertEqual(DEVICE_ID, api._product_device_id)
        self.assertNotIn("sensitive-jwt", repr(result))
        self.assertIs(connect.call_args.kwargs["session"], session)

    def test_product_api_returns_safe_error_shape(self) -> None:
        api = object.__new__(StudioProductApi)
        api.agent = SimpleNamespace(config=SimpleNamespace())
        with (
            patch("ordax_studio.product_web_desktop.sign_in_with_password", return_value=ProductAuthSession(
                access_token="sensitive-jwt", email="user@example.com")),
            patch("ordax_studio.product_web_desktop.connect_existing_device", side_effect=ProductAccountError(
                "device_not_enrolled", "Máquina não registrada")),
        ):
            result = api.connect_product_account("user@example.com", "secret")
        self.assertFalse(result["ok"])
        self.assertEqual("device_not_enrolled", result["code"])
        self.assertEqual("Máquina não registrada", result["summary"])
        self.assertIsNone(getattr(api, "_product_session", None))


if __name__ == "__main__":
    unittest.main()
