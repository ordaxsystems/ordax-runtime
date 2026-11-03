"""Windows GUI onboarding must reuse Owner PKCE without silently granting access."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from ordax_core.owner_oauth import OwnerOAuthError
from ordax_dev_agent.device_setup import SetupError
from ordax_dev_agent import owner_device_connect_gui as gui


class OneClickOwnerConnectTests(unittest.TestCase):
    def setUp(self):
        self.messages = []

    def dialog(self, message, flags):
        self.messages.append((message, flags))
        return 6 if len(self.messages) == 1 else 1

    def test_cancel_cannot_start_oauth_or_device_enrollment(self):
        calls = []
        result = gui.connect_with_dialogs(
            show=lambda _message, _flags: 7,
            enroll=lambda **_k: calls.append("enroll"),
            authorize=lambda: calls.append("oauth"),
        )
        self.assertEqual(result, 2)
        self.assertEqual(calls, [])

    def test_approved_click_reuses_existing_enrollment_not_grant(self):
        calls = []
        def enrolled(*, auth_code):
            calls.append(auth_code())
            return {
                "ok": True, "device_id": "11111111-1111-4111-8111-111111111111",
                "runtime_activated": False, "grants_issued": False,
                "end_to_end_proven": False,
            }
        code = gui.connect_with_dialogs(
            show=self.dialog, enroll=enrolled,
            authorize=lambda: ("oauth-code", "pkce-verifier"),
        )
        self.assertEqual(code, 0)
        self.assertEqual(calls, [("oauth-code", "pkce-verifier")])
        self.assertEqual(len(self.messages), 2)
        self.assertIn("Permissoes", self.messages[-1][0])
        self.assertNotIn("oauth-code", str(self.messages))

    def test_gui_browser_callback_names_correct_product(self):
        with mock.patch.object(gui, "browser_authorization_code",
                               return_value=("code", "verifier")) as browser:
            self.assertEqual(gui._browser_authorization(), ("code", "verifier"))
        browser.assert_called_once_with(mcp_callback=True)

    def test_denial_is_visible_and_does_not_display_secrets(self):
        for failure in (
            OwnerOAuthError("owner_oauth_rejected"),
            SetupError("SETUP_ALREADY_RUNNING"),
            RuntimeError("SECRET_ACCOUNT_TOKEN_SHOULD_NOT_LEAK"),
        ):
            with self.subTest(failure=type(failure).__name__):
                self.messages = []
                result = gui.connect_with_dialogs(
                    show=self.dialog,
                    enroll=lambda **_k: (_ for _ in ()).throw(failure),
                    authorize=lambda: self.fail("Authorization already denied"),
                )
                self.assertEqual(result, 1)
                self.assertEqual(len(self.messages), 2)
                self.assertNotIn("SECRET_ACCOUNT_TOKEN_SHOULD_NOT_LEAK",
                                 str(self.messages))

    def test_diagnostics_are_explicit_and_do_not_open_oauth(self):
        observed = []
        def probe():
            observed.append("probe")
            return {
                "schema": "ordax.product-mcp-https-observation/1",
                "primary": {"transport": "unavailable", "httpStatus": None},
                "restricted": {"transport": "https_responded", "httpStatus": 404},
                "mcpReady": False, "authenticated": False,
                "grantsAuthorized": False,
            }
        prompts = []
        status = gui.diagnose_with_dialogs(
            show=lambda message, flags: prompts.append((message, flags)) or 1,
            inspect=probe,
        )
        self.assertEqual(status, 1)
        self.assertEqual(observed, ["probe"])
        self.assertEqual(len(prompts), 1)
        self.assertIn("Servidor principal: sem resposta HTTPS", prompts[0][0])
        self.assertIn("HTTPS respondeu (HTTP 404)", prompts[0][0])
        self.assertIn("nao comprova", prompts[0][0])
        self.assertNotIn("Registro do dispositivo concluido", prompts[0][0])

    def test_diagnostics_rejects_forged_readiness_and_error_leaks(self):
        invalid = [
            {"schema": "ordax.product-mcp-https-observation/1",
             "primary": {"transport": "https_responded", "httpStatus": 200},
             "restricted": {"transport": "unavailable", "httpStatus": None},
             "mcpReady": True, "authenticated": False, "grantsAuthorized": False},
            {"schema": "ordax.product-mcp-https-observation/1",
             "primary": {"transport": "https_responded", "httpStatus": "200"},
             "restricted": {"transport": "unavailable", "httpStatus": None},
             "mcpReady": False, "authenticated": False, "grantsAuthorized": False},
        ]
        for payload in invalid:
            messages = []
            result = gui.diagnose_with_dialogs(
                show=lambda msg, _flags: messages.append(msg) or 1,
                inspect=lambda: payload,
            )
            self.assertEqual(result, 1)
            self.assertEqual(len(messages), 1)
            self.assertIn("Nao foi possivel", messages[0])
            self.assertNotIn("grants", messages[0])
        messages = []
        result = gui.diagnose_with_dialogs(
            show=lambda msg, _flags: messages.append(msg) or 1,
            inspect=lambda: (_ for _ in ()).throw(
                RuntimeError("SECRET_TOKEN_MUST_NOT_LEAK")),
        )
        self.assertEqual(result, 1)
        self.assertNotIn("SECRET_TOKEN_MUST_NOT_LEAK", messages[0])

    def test_local_runtime_health_probe_is_fixed_bounded_and_unauthenticated(self):
        response = mock.Mock(status=200)
        response.getheader.return_value = "application/json; charset=utf-8"
        response.read.return_value = b'{"ok":true,"state":"busy","version":"1.31.0"}'
        connection = mock.Mock()
        connection.getresponse.return_value = response
        with mock.patch.object(gui.http.client, "HTTPConnection", return_value=connection) as factory:
            observed = gui.inspect_local_runtime_status()
        self.assertEqual(observed, {
            "responded": True, "healthy": True, "state": "busy", "version": "1.31.0"
        })
        factory.assert_called_once_with("127.0.0.1", 8765, timeout=1.5)
        connection.request.assert_called_once_with(
            "GET", "/health", headers={"Accept": "application/json"}
        )
        response.read.assert_called_once_with(4097)
        connection.close.assert_called_once()

    def test_local_runtime_diagnostic_fails_closed_on_invalid_response_or_error(self):
        cases = [
            (200, "application/json", b'{"ok":true,"state":"busy","version":"SECRET"}'),
            (200, "application/json", b'{"ok":"true","state":"busy","version":"1.31.0"}'),
            (200, "application/json", b'{"ok":true,"state":"busy","version":"1.31.0"}' + b' ' * 4100),
            (302, "text/html", b'{"ok":true,"state":"busy","version":"1.31.0"}'),
        ]
        for status, content_type, payload in cases:
            with self.subTest(status=status, size=len(payload)):
                response = mock.Mock(status=status)
                response.getheader.return_value = content_type
                response.read.return_value = payload
                connection = mock.Mock()
                connection.getresponse.return_value = response
                with mock.patch.object(gui.http.client, "HTTPConnection", return_value=connection):
                    result = gui.inspect_local_runtime_status()
                self.assertEqual(result, {
                    "responded": False, "healthy": None, "state": None, "version": None
                })
                connection.close.assert_called_once()
        connection = mock.Mock()
        connection.request.side_effect = OSError("SECRET_TOKEN_MUST_NOT_LEAK")
        with mock.patch.object(gui.http.client, "HTTPConnection", return_value=connection):
            self.assertIs(gui.inspect_local_runtime_status()["responded"], False)
        connection.close.assert_called_once()

    def test_diagnostic_reports_local_health_without_claiming_remote_authority(self):
        network = {
            "schema": "ordax.product-mcp-https-observation/1",
            "primary": {"transport": "https_responded", "httpStatus": 405},
            "restricted": {"transport": "unavailable", "httpStatus": None},
            "mcpReady": False, "authenticated": False, "grantsAuthorized": False,
        }
        messages = []
        for inspect_local, expected in [
            (lambda: {"responded": True, "healthy": True, "state": "busy", "version": "1.31.0"},
             "v1.31.0; busy"),
            (lambda: (_ for _ in ()).throw(RuntimeError("SECRET_TOKEN")),
             "Estado local: nao foi possivel validar"),
        ]:
            messages.clear()
            result = gui.diagnose_with_dialogs(
                show=lambda message, _flags: messages.append(message) or 1,
                inspect=lambda: network, inspect_local=inspect_local,
            )
            self.assertEqual(result, 0)
            self.assertEqual(len(messages), 1)
            self.assertIn(expected, messages[0])
            self.assertIn("nao comprova cadastro", messages[0])
            self.assertNotIn("SECRET_TOKEN", messages[0])
            self.assertNotIn("MCP autorizado", messages[0])

    def test_diagnostic_cli_routes_separately_from_owner_enrollment(self):
        with mock.patch.object(gui.sys, "platform", "win32"):
            with mock.patch.object(gui, "connect_with_dialogs") as connect:
                with mock.patch.object(gui, "diagnose_with_dialogs",
                                       return_value=0) as diagnose:
                    self.assertEqual(gui.main(["--verificar-rede"]), 0)
                diagnose.assert_called_once()
                connect.assert_not_called()
            with mock.patch.object(gui, "connect_with_dialogs",
                                   return_value=2) as connect:
                self.assertEqual(gui.main([]), 2)
                connect.assert_called_once()

    def test_untrusted_grant_claim_does_not_get_success_message(self):
        self.assertEqual(gui.connect_with_dialogs(
            show=self.dialog,
            enroll=lambda **_k: {"ok": True, "grants_issued": True,
                                  "runtime_activated": False,
                                  "end_to_end_proven": False},
            authorize=lambda: ("code", "verifier"),
        ), 1)
        self.assertNotIn("Registro do dispositivo concluido",
                         self.messages[-1][0])



DEVICE = "14cef8f4-3c06-4b1b-9e3a-42c74ef7dec5"
OTHER_DEVICE = "51ad4baf-7805-499e-9c48-b89f52af1953"
CLIENT = "3529fbb0-c72a-4f79-bf4c-4f1243939a78"
OTHER_CLIENT = "95d41a8f-0fb8-4c4b-9757-83ba6345062d"
CHALLENGE = "51758e78-f162-41f1-a3ca-509f7eefe11e"
OTHER_CHALLENGE = "93a85c06-e71a-4808-9e5f-0cd325b21705"


class FakeOwnerSelectionRemote:
    def __init__(self):
        self.clients = [
            {"client_id": CLIENT, "client_name": "ChatGPT"},
        ]
        self.eligible_device = DEVICE
        self.pending = {CLIENT: [CHALLENGE]}
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def owner_remote_authority(self, token, *, device_id):
        self.calls.append(("authority", device_id))
        return {"device_id": device_id, "clients": self.clients, "grant_groups": []}

    def owner_central_session_targets(self, token, *, client_id):
        self.calls.append(("targets", client_id))
        return {"targets": [{"device_id": self.eligible_device}]}

    def owner_select_central_device(self, token, *, client_id, challenge_id, device_id):
        self.calls.append(("selected", client_id, challenge_id, device_id))
        return {"ok": True, "status": "pending", "challenge_id": challenge_id,
                "device_id": device_id, "attested": False, "dispatch_authorized": False}

    def owner_central_session_selections(self, token, *, client_id):
        self.calls.append(("selections", client_id))
        tickets = self.pending.get(client_id, [])
        return {"pending_selections": [
            {"challenge_id": x,
             "created_at": datetime.now(timezone.utc).isoformat(),
             "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=4)).isoformat()}
            for x in tickets
        ]}


class OwnerSessionSelectionGuiTests(unittest.TestCase):
    def setUp(self):
        self.remote = FakeOwnerSelectionRemote()
        self.messages = []
        self.operations = []
        self.oauth_calls = []
        self.config = SimpleNamespace(device_id=DEVICE, product_mcp_url="https://mcp.example.test")

    def show(self, text, _flags):
        self.messages.append(text)
        return 6

    def authorize(self):
        self.oauth_calls.append("owner-pkce")
        return "authorization-code", "verifier"

    def select(self, args, token, remote, *, config):
        self.operations.append((args.operation, args.device, args.client, args.challenge,
                                args.confirm, token, config))
        return {"ok": True, "status": "pending", "attested": False,
                "dispatch_authorized": False, "challenge_id": args.challenge,
                "device_id": args.device}

    def run_gui(self, **overrides):
        options = {
            "show": self.show,
            "authorize": self.authorize,
            "exchange": lambda code, verifier: "ephemeral-owner-token",
            "config_provider": lambda: self.config,
            "remote_factory": lambda url: self.remote,
            "select": self.select,
        }
        options.update(overrides)
        return gui.select_session_with_dialogs(**options)

    def test_explicit_confirm_uses_exact_client_device_ticket_and_does_not_grant(self):
        self.assertEqual(self.run_gui(), 0)
        self.assertEqual(self.oauth_calls, ["owner-pkce"])
        self.assertEqual(len(self.operations), 1)
        action, device, client, challenge, confirmed, token, config = self.operations[0]
        self.assertEqual((action, device, client, challenge, confirmed),
                         ("central-select", DEVICE, CLIENT, CHALLENGE, True))
        self.assertIs(config, self.config)
        self.assertEqual(token, "ephemeral-owner-token")
        self.assertIn(CHALLENGE, self.messages[1])
        self.assertIn(CLIENT, self.messages[1])
        self.assertIn(DEVICE, self.messages[1])
        self.assertIn("aguarda a confirmacao do Runtime", self.messages[-1])
        self.assertNotIn("ephemeral-owner-token", str(self.messages))
        self.assertFalse(any(x[0] == "grant" for x in self.remote.calls))

    def test_real_canonical_owner_cli_execution_is_used_for_selected_ticket(self):
        self.assertEqual(self.run_gui(select=gui.owner_cli_perform), 0)
        self.assertIn(("selected", CLIENT, CHALLENGE, DEVICE), self.remote.calls)
        self.assertEqual(len([c for c in self.remote.calls if c[0] == "selected"]), 1)
        self.assertEqual(len([c for c in self.remote.calls if c[0] == "authority"]), 2)
        self.assertNotIn("ephemeral-owner-token", str(self.messages))

    def test_standalone_windows_gui_refuses_ordax_desktop_host_identity(self):
        with mock.patch.dict(gui.os.environ, {"ORDAX_RUNTIME_HOST_PROFILE": "ordax-desktop"}):
            self.assertEqual(self.run_gui(), 1)
        self.assertEqual(self.oauth_calls, [])
        self.assertEqual(self.remote.calls, [])
        self.assertEqual(self.operations, [])
        self.assertNotIn("Escolha registrada", str(self.messages))

    def test_ticket_expired_before_display_is_ignored(self):
        future = datetime.now(timezone.utc) + timedelta(days=1)
        result = gui.list_local_owner_session_candidates(
            self.remote, "opaque-owner-token", self.config, now=future,
        )
        self.assertEqual(result, [])
        self.assertFalse(any(x[0] == "selected" for x in self.remote.calls))

    def test_initial_rejection_never_starts_oauth_or_remote(self):
        self.assertEqual(self.run_gui(show=lambda message, flags: 7), 2)
        self.assertEqual(self.oauth_calls, [])
        self.assertEqual(self.remote.calls, [])
        self.assertEqual(self.operations, [])

    def test_each_pending_ticket_requires_its_own_confirm(self):
        self.remote.clients.append({"client_id": OTHER_CLIENT, "client_name": "ChatGPT"})
        self.remote.pending[OTHER_CLIENT] = [OTHER_CHALLENGE]
        decisions = iter([6, 7, 6, 1])  # intro yes, first no, second yes, final dialog
        self.assertEqual(self.run_gui(show=lambda text, flags: (
            self.messages.append(text), next(decisions)
        )[-1]), 0)
        self.assertEqual(len(self.operations), 1)
        self.assertEqual(self.operations[0][2:4], (OTHER_CLIENT, OTHER_CHALLENGE))
        self.assertIn(CHALLENGE, self.messages[1])
        self.assertIn(OTHER_CHALLENGE, self.messages[2])

    def test_device_not_eligible_or_ticket_expired_never_posts(self):
        self.remote.eligible_device = OTHER_DEVICE
        self.assertEqual(self.run_gui(), 2)
        self.assertEqual(self.operations, [])
        self.remote.eligible_device = DEVICE
        self.remote.pending = {CLIENT: []}
        self.assertEqual(self.run_gui(), 2)
        self.assertEqual(self.operations, [])

    def test_idle_client_skips_secondary_target_query(self):
        self.remote.pending = {CLIENT: []}
        self.assertEqual(self.run_gui(), 2)
        self.assertFalse(any(call[0] == "targets" for call in self.remote.calls))
        self.assertEqual(len([call for call in self.remote.calls
                              if call[0] == "selections"]), 1)
        self.assertEqual(self.operations, [])

    def test_ambiguous_queue_aborts_before_any_confirmation(self):
        self.remote.pending = {CLIENT: [CHALLENGE] * 9}
        self.assertEqual(self.run_gui(), 1)
        self.assertEqual(self.operations, [])
        self.assertEqual(len(self.messages), 2)  # no ticket picker for unbounded queue

    def test_failed_or_ambiguous_post_does_not_retry_or_claim_success(self):
        def bad_selection(*args, **kwargs):
            self.operations.append(("attempt",))
            raise RuntimeError("SECRET_SHOULD_NOT_LEAK")
        self.assertEqual(self.run_gui(select=bad_selection), 1)
        self.assertEqual(len(self.operations), 1)
        self.assertNotIn("SECRET_SHOULD_NOT_LEAK", str(self.messages))
        self.assertNotIn("Escolha registrada", str(self.messages))

    def test_invalid_result_cannot_be_interpreted_as_device_attestation(self):
        def false_attestation(args, token, remote, *, config):
            self.operations.append(("attempt",))
            return {"status": "pending", "attested": True,
                    "dispatch_authorized": True, "challenge_id": args.challenge,
                    "device_id": args.device}
        self.assertEqual(self.run_gui(select=false_attestation), 1)
        self.assertEqual(len(self.operations), 1)
        self.assertNotIn("Escolha registrada", str(self.messages))

    def test_main_routes_owner_ui_without_launching_enrollment(self):
        with mock.patch.object(gui.sys, "platform", "win32"):
            with mock.patch.object(gui, "select_session_with_dialogs", return_value=2) as select:
                with mock.patch.object(gui, "connect_with_dialogs") as connect:
                    self.assertEqual(gui.main(["--selecionar-sessao"]), 2)
                    select.assert_called_once()
                    connect.assert_not_called()
            with self.assertRaises(SystemExit):
                gui.main(["--selecionar-sessao", "--verificar-rede"])


if __name__ == "__main__":
    unittest.main()
