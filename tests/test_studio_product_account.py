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

        session = sign_in_with_password(
            " user@example.com ",
            "secret-password",
            http=client,
        )

        self.assertEqual("header.payload.signature", session.access_token)
        self.assertEqual("user@example.com", session.email)
        _, kwargs = client.post.call_args
        self.assertEqual({"grant_type": "password"}, kwargs["params"])
        self.assertEqual(
            {"email": "user@example.com", "password": "secret-password"},
            kwargs["json"],
        )
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

    def test_connect_existing_device_uses_device_pairing_and_never_returns_jwt(self) -> None:
        config = SimpleNamespace(
            control_plane_url="https://control.example.test",
            device_id="22222222-2222-4222-8222-222222222222",
        )
        control = Mock()
        control.http = Mock()
        control.create_product_pairing.return_value = {
            "pairing_id": "33333333-3333-4333-8333-333333333333",
            "pairing_secret": "a" * 64,
            "expires_at": "2026-09-30T20:00:00Z",
        }
        remote = Mock()
        remote.__enter__ = Mock(return_value=remote)
        remote.__exit__ = Mock(return_value=None)
        remote.claim_device_pairing.return_value = {
            "link_id": "44444444-4444-4444-8444-444444444444",
            "device_id": config.device_id,
            "device_name": "TONECOS",
            "space_id": None,
        }

        with (
            patch(
                "ordax_studio.product_auth.sign_in_with_password",
                return_value=ProductAuthSession(
                    access_token="sensitive-jwt",
                    email="user@example.com",
                ),
            ),
            patch(
                "ordax_studio.product_auth.CloudflareControlPlane",
                return_value=control,
            ),
            patch(
                "ordax_studio.product_auth.ProductRemoteClient",
                return_value=remote,
            ),
        ):
            result = connect_existing_device(config, "user@example.com", "secret")

        control.create_product_pairing.assert_called_once_with()
        control.http.close.assert_called_once_with()
        remote.claim_device_pairing.assert_called_once_with(
            "sensitive-jwt",
            pairing_id="33333333-3333-4333-8333-333333333333",
            pairing_secret="a" * 64,
        )
        self.assertEqual(config.device_id, result["link"]["device_id"])
        self.assertEqual("user@example.com", result["email"])
        self.assertNotIn("access_token", result)
        self.assertNotIn("sensitive-jwt", repr(result))

    def test_connect_new_device_enrolls_with_product_session_before_pairing(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw) / "state"
            state.mkdir()
            config = AgentConfig(
                agent_name="test",
                poll_seconds=1.0,
                state_dir=state,
                agent_repo_path=Path(raw) / "agent",
                hordax_path=Path(raw) / "hordax",
                bridge_path=Path(raw) / "bridge",
                projects={},
                device_id=None,
                control_plane_url="https://control.example.test",
            )
            control = Mock()
            control.http = Mock()
            control.create_product_pairing.return_value = {
                "pairing_id": "33333333-3333-4333-8333-333333333333",
                "pairing_secret": "a" * 64,
                "expires_at": "2026-09-30T20:00:00Z",
            }
            remote = Mock()
            remote.__enter__ = Mock(return_value=remote)
            remote.__exit__ = Mock(return_value=None)
            remote.claim_device_pairing.return_value = {
                "link_id": "44444444-4444-4444-8444-444444444444",
                "device_id": "22222222-2222-4222-8222-222222222222",
                "device_name": "REVIEW-PC",
                "space_id": None,
            }

            with (
                patch(
                    "ordax_studio.product_auth.sign_in_with_password",
                    return_value=ProductAuthSession(
                        access_token="sensitive-jwt",
                        email="user@example.com",
                    ),
                ),
                patch(
                    "ordax_studio.product_auth.configure_device",
                    return_value={
                        "ok": True,
                        "device_id": "22222222-2222-4222-8222-222222222222",
                        "protocol": "cloudflare-v3",
                    },
                ) as enrollment,
                patch(
                    "ordax_studio.product_auth.CloudflareControlPlane",
                    return_value=control,
                ),
                patch(
                    "ordax_studio.product_auth.ProductRemoteClient",
                    return_value=remote,
                ),
            ):
                result = connect_existing_device(config, "user@example.com", "secret")

            enrollment.assert_called_once_with(
                state,
                control_plane_url="https://control.example.test",
                product_access_token="sensitive-jwt",
            )
            self.assertTrue(result["enrolled_now"])
            self.assertEqual(
                "22222222-2222-4222-8222-222222222222",
                result["device_id"],
            )
            self.assertNotIn("sensitive-jwt", repr(result))

    def test_product_api_restarts_packaged_runtime_after_first_enrollment(self) -> None:
        api = object.__new__(StudioProductApi)
        api.agent = SimpleNamespace(config=SimpleNamespace())
        session = ProductAuthSession(access_token="sensitive-jwt", email="user@example.com")
        with (
            patch(
                "ordax_studio.product_web_desktop.sign_in_with_password",
                return_value=session,
            ),
            patch(
                "ordax_studio.product_web_desktop.connect_existing_device",
                return_value={
                    "email": "user@example.com",
                    "link": {"device_id": "dev-1"},
                    "device_id": "dev-1",
                    "enrolled_now": True,
                },
            ) as connect,
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
        self.assertEqual("dev-1", api._product_device_id)
        self.assertNotIn("sensitive-jwt", repr(result))
        self.assertIs(connect.call_args.kwargs["session"], session)
    def test_product_api_returns_safe_error_shape(self) -> None:
        api = object.__new__(StudioProductApi)
        api.agent = SimpleNamespace(config=SimpleNamespace())
        with (
            patch(
                "ordax_studio.product_web_desktop.sign_in_with_password",
                return_value=ProductAuthSession(access_token="sensitive-jwt", email="user@example.com"),
            ),
            patch(
                "ordax_studio.product_web_desktop.connect_existing_device",
                side_effect=ProductAccountError("device_not_enrolled", "M?quina n?o registrada"),
            ),
        ):
            result = api.connect_product_account("user@example.com", "secret")
        self.assertFalse(result["ok"])
        self.assertEqual("device_not_enrolled", result["code"])
        self.assertEqual("M?quina n?o registrada", result["summary"])
        self.assertIsNone(getattr(api, "_product_session", None))
    def test_remote_grant_requires_in_memory_product_session(self) -> None:
        api = object.__new__(StudioProductApi)
        api.agent = SimpleNamespace(config=SimpleNamespace(
            control_plane_url="https://control.example.test",
            device_id="dev-1",
        ))
        api._product_session = None
        api._product_device_id = "dev-1"
        result = api.remote_computer_grants()
        self.assertFalse(result["ok"])
        self.assertEqual("product_auth_session_required", result["code"])

    def test_owner_can_authorize_interactive_profile_for_current_device_link(self) -> None:
        api = object.__new__(StudioProductApi)
        api.agent = SimpleNamespace(config=SimpleNamespace(
            control_plane_url="https://control.example.test",
            device_id="dev-1",
        ))
        api._product_session = ProductAuthSession(access_token="sensitive-jwt", email="user@example.com")
        api._product_device_id = "dev-1"
        remote = Mock()
        remote.__enter__ = Mock(return_value=remote)
        remote.__exit__ = Mock(return_value=None)
        remote.device_links.return_value = [
            {"link_id": "link-1", "device_id": "dev-1"},
            {"link_id": "foreign", "device_id": "dev-2"},
        ]
        remote.create_device_computer_grant.return_value = {
            "mode": "interactive-computer-control",
            "replayed": False,
            "grant": {"id": "grant-1", "device_id": "dev-1"},
        }
        with patch("ordax_studio.product_web_desktop.ProductRemoteClient", return_value=remote):
            result = api.authorize_remote_computer_grant("interactive-computer-control", expires_days=30)
        self.assertTrue(result["ok"])
        args, kwargs = remote.create_device_computer_grant.call_args
        self.assertEqual("sensitive-jwt", args[0])
        self.assertEqual("link-1", kwargs["link_id"])
        self.assertEqual("interactive-computer-control", kwargs["mode"])
        self.assertNotIn("sensitive-jwt", repr(result))

    def test_owner_can_authorize_app_intelligence_read_for_current_device_link(self) -> None:
        api = object.__new__(StudioProductApi)
        api.agent = SimpleNamespace(config=SimpleNamespace(
            control_plane_url="https://control.example.test",
            device_id="dev-1",
        ))
        api._product_session = ProductAuthSession(
            access_token="sensitive-jwt",
            email="user@example.com",
        )
        api._product_device_id = "dev-1"
        remote = Mock()
        remote.__enter__ = Mock(return_value=remote)
        remote.__exit__ = Mock(return_value=None)
        remote.device_links.return_value = [
            {"link_id": "link-1", "device_id": "dev-1"},
            {"link_id": "foreign", "device_id": "dev-2"},
        ]
        remote.create_device_intelligence_grant.return_value = {
            "mode": "app-intelligence-read",
            "replayed": False,
            "grant": {"id": "grant-intelligence-1", "device_id": "dev-1"},
        }
        with patch("ordax_studio.product_web_desktop.ProductRemoteClient", return_value=remote):
            result = api.authorize_remote_app_intelligence_grant(expires_days=30)
        self.assertTrue(result["ok"])
        args, kwargs = remote.create_device_intelligence_grant.call_args
        self.assertEqual("sensitive-jwt", args[0])
        self.assertEqual("link-1", kwargs["link_id"])
        self.assertEqual("app-intelligence-read", kwargs["mode"])
        self.assertNotIn("sensitive-jwt", repr(result))

    def test_app_intelligence_grant_listing_filters_current_device(self) -> None:
        api = object.__new__(StudioProductApi)
        api.agent = SimpleNamespace(config=SimpleNamespace(
            control_plane_url="https://control.example.test",
            device_id="dev-1",
        ))
        api._product_session = ProductAuthSession(
            access_token="sensitive-jwt",
            email="user@example.com",
        )
        api._product_device_id = "dev-1"
        remote = Mock()
        remote.__enter__ = Mock(return_value=remote)
        remote.__exit__ = Mock(return_value=None)
        remote.device_links.return_value = [
            {"link_id": "link-1", "device_id": "dev-1"},
            {"link_id": "link-2", "device_id": "dev-2"},
        ]
        remote.device_intelligence_grants.return_value = [
            {"id": "grant-intelligence-1", "device_id": "dev-1"},
            {"id": "grant-intelligence-2", "device_id": "dev-2"},
        ]
        with patch("ordax_studio.product_web_desktop.ProductRemoteClient", return_value=remote):
            result = api.remote_app_intelligence_grants()
        self.assertTrue(result["ok"])
        self.assertEqual(["link-1"], [item["link_id"] for item in result["data"]["links"]])
        self.assertEqual(
            ["grant-intelligence-1"],
            [item["id"] for item in result["data"]["grants"]],
        )
        self.assertEqual(["app-intelligence-read"], result["data"]["available_modes"])
        self.assertNotIn("sensitive-jwt", repr(result))

    def test_owner_full_profile_requires_local_full_access(self) -> None:
        api = object.__new__(StudioProductApi)
        api.agent = SimpleNamespace(config=SimpleNamespace(
            control_plane_url="https://control.example.test",
            device_id="dev-1",
        ))
        api._product_session = ProductAuthSession(access_token="sensitive-jwt", email="user@example.com")
        api._product_device_id = "dev-1"
        with patch(
            "ordax_studio.product_web_desktop.computer_access_management_status",
            return_value={"enabled": True, "full_access": False},
        ):
            result = api.authorize_remote_computer_grant("full-computer-control")
        self.assertFalse(result["ok"])
        self.assertEqual("local_full_access_required", result["code"])

    def test_owner_can_authorize_full_profile_after_local_full_access(self) -> None:
        api = object.__new__(StudioProductApi)
        api.agent = SimpleNamespace(config=SimpleNamespace(
            control_plane_url="https://control.example.test",
            device_id="dev-1",
        ))
        api._product_session = ProductAuthSession(access_token="sensitive-jwt", email="user@example.com")
        api._product_device_id = "dev-1"
        remote = Mock()
        remote.__enter__ = Mock(return_value=remote)
        remote.__exit__ = Mock(return_value=None)
        remote.device_links.return_value = [{"link_id": "link-1", "device_id": "dev-1"}]
        remote.create_device_computer_grant.return_value = {
            "mode": "full-computer-control",
            "replayed": False,
            "grant": {"id": "grant-full", "device_id": "dev-1"},
        }
        with (
            patch(
                "ordax_studio.product_web_desktop.computer_access_management_status",
                return_value={"enabled": True, "full_access": True},
            ),
            patch("ordax_studio.product_web_desktop.ProductRemoteClient", return_value=remote),
        ):
            result = api.authorize_remote_computer_grant("full-computer-control", expires_days=30)
        self.assertTrue(result["ok"])
        args, kwargs = remote.create_device_computer_grant.call_args
        self.assertEqual("sensitive-jwt", args[0])
        self.assertEqual("link-1", kwargs["link_id"])
        self.assertEqual("full-computer-control", kwargs["mode"])

    def test_owner_ui_host_refuses_non_computer_or_unknown_profile(self) -> None:
        api = object.__new__(StudioProductApi)
        api.agent = SimpleNamespace(config=SimpleNamespace(
            control_plane_url="https://control.example.test",
            device_id="dev-1",
        ))
        api._product_session = ProductAuthSession(access_token="sensitive-jwt", email="user@example.com")
        api._product_device_id = "dev-1"
        for mode in ("terminal.exec", "unknown"):
            result = api.authorize_remote_computer_grant(mode)
            self.assertFalse(result["ok"], mode)
            self.assertEqual("owner_device_grant_mode_not_allowed", result["code"])

    def test_owner_grant_listing_filters_current_device(self) -> None:
        api = object.__new__(StudioProductApi)
        api.agent = SimpleNamespace(config=SimpleNamespace(
            control_plane_url="https://control.example.test",
            device_id="dev-1",
        ))
        api._product_session = ProductAuthSession(access_token="sensitive-jwt", email="user@example.com")
        api._product_device_id = "dev-1"
        remote = Mock()
        remote.__enter__ = Mock(return_value=remote)
        remote.__exit__ = Mock(return_value=None)
        remote.device_links.return_value = [
            {"link_id": "link-1", "device_id": "dev-1"},
            {"link_id": "link-2", "device_id": "dev-2"},
        ]
        remote.device_computer_grants.return_value = [
            {"id": "grant-1", "device_id": "dev-1"},
            {"id": "grant-2", "device_id": "dev-2"},
        ]
        with patch("ordax_studio.product_web_desktop.ProductRemoteClient", return_value=remote):
            result = api.remote_computer_grants()
        self.assertTrue(result["ok"])
        self.assertEqual(["link-1"], [item["link_id"] for item in result["data"]["links"]])
        self.assertEqual(["grant-1"], [item["id"] for item in result["data"]["grants"]])
        self.assertNotIn("sensitive-jwt", repr(result))

    def test_product_identity_is_separate_from_github_provider(self) -> None:
        root = Path(__file__).resolve().parents[1]
        auth_source = (root / "ordax_studio" / "product_auth.py").read_text(encoding="utf-8")
        ui_source = (root / "ordax_studio" / "assets" / "product_account.js").read_text(encoding="utf-8")
        contract = (root / "docs" / "ORDAX_STUDIO_WINDOWS_PRODUCT.md").read_text(encoding="utf-8")
        self.assertIn("conta ORDAX", ui_source)
        self.assertIn("GitHub", ui_source)
        self.assertIn("provedor de projetos", ui_source)
        self.assertIn("Conta ORDAX", contract)
        self.assertIn("GitHub", contract)
        self.assertIn("plugin GitHub", contract)
        self.assertNotIn("github.com/login/oauth", auth_source.lower())
        self.assertNotIn("api.github.com/user", auth_source.lower())
    def test_product_surface_source_never_persists_password_or_access_token(self) -> None:
        root = Path(__file__).resolve().parents[1]
        auth_source = (root / "ordax_studio" / "product_auth.py").read_text(encoding="utf-8")
        ui_source = (root / "ordax_studio" / "assets" / "product_account.js").read_text(encoding="utf-8")
        self.assertNotIn("write_text", auth_source)
        self.assertNotIn("open(", auth_source)
        self.assertNotIn("localStorage", ui_source)
        self.assertNotIn("sessionStorage", ui_source)
        self.assertIn("password.value=''", ui_source)


if __name__ == "__main__":
    unittest.main()
