"""Windows GUI onboarding must reuse Owner PKCE without silently granting access."""
from __future__ import annotations

import unittest
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

    def test_diagnostics_ready_runtime_never_claims_chatgpt_authorization(self):
        network = {
            "schema": "ordax.product-mcp-https-observation/1",
            "primary": {"transport": "https_responded", "httpStatus": 404},
            "restricted": {"transport": "unavailable", "httpStatus": None},
            "mcpReady": False, "authenticated": False, "grantsAuthorized": False,
        }
        runtime = {
            "state": "ready", "paired": True, "transport_state": "connected",
            "product_dispatch_ready": True, "product_readiness_code": "ready",
            "central_session": {
                "checked": True, "last_confirmation_observed": False,
                "last_error_code": None,
            },
        }
        calls = []
        messages = []
        result = gui.diagnose_with_dialogs(
            show=lambda msg, _flags: messages.append(msg) or 1,
            inspect=lambda: calls.append("HTTPS") or network,
            observe_runtime=lambda: calls.append("LOCAL") or runtime,
        )
        self.assertEqual(result, 0)
        self.assertEqual(calls, ["HTTPS", "LOCAL"])
        self.assertEqual(len(messages), 1)
        self.assertIn("Runtime local: pronto e conectado", messages[0])
        self.assertIn("sem confirmacao observada", messages[0])
        self.assertIn("nao comprova autorizacao desta sessao", messages[0])
        self.assertIn("Servidor principal: HTTPS respondeu (HTTP 404)", messages[0])
        self.assertNotIn("Autorizacao concedida", messages[0])

        old_session = {**runtime, "central_session": {
            "checked": True, "last_confirmation_observed": True,
            "last_error_code": None,
        }}
        history = gui._format_local_runtime_diagnosis(old_session)
        self.assertIn("confirmacao anterior", history)
        self.assertIn("sessao atual precisa ser verificada", history)
        self.assertNotIn("sessao atual ativa", history)

    def test_diagnostics_degraded_runtime_requires_attention_even_when_https_responds(self):
        network = {
            "schema": "ordax.product-mcp-https-observation/1",
            "primary": {"transport": "https_responded", "httpStatus": 404},
            "restricted": {"transport": "unavailable", "httpStatus": None},
            "mcpReady": False, "authenticated": False, "grantsAuthorized": False,
        }
        scenarios = (
            (
                None,
                "Runtime local: sem resposta",
            ),
            (
                {"state": "pairing", "paired": False,
                 "transport_state": "connecting",
                 "product_dispatch_ready": False,
                 "product_readiness_code": "pairing_required",
                 "central_session": None},
                "pareamento com a Platform pendente",
            ),
            (
                {"state": "ready", "paired": True,
                 "transport_state": "reconnecting",
                 "product_dispatch_ready": False,
                 "product_readiness_code": "transport_reconnecting",
                 "central_session": {
                     "checked": True, "last_confirmation_observed": False,
                     "last_error_code": "central_session_challenge_expired",
                 }},
                "pedido de sessao expirado",
            ),
            (
                {"state": "local-disabled", "paired": False,
                 "transport_state": "not-configured",
                 "product_dispatch_ready": False,
                 "product_readiness_code": "local_access_disabled",
                 "central_session": None},
                "acesso local desativado",
            ),
        )
        for runtime, expected in scenarios:
            with self.subTest(expected=expected):
                messages = []
                status = gui.diagnose_with_dialogs(
                    show=lambda msg, _flags: messages.append(msg) or 1,
                    inspect=lambda: network, observe_runtime=lambda: runtime,
                )
                self.assertEqual(status, 1)
                self.assertIn(expected, messages[0])
                self.assertNotIn("foi autorizada", messages[0])

    def test_diagnostics_rejects_forged_local_readiness_and_never_leaks_api_errors(self):
        network = {
            "schema": "ordax.product-mcp-https-observation/1",
            "primary": {"transport": "https_responded", "httpStatus": 404},
            "restricted": {"transport": "unavailable", "httpStatus": None},
            "mcpReady": False, "authenticated": False, "grantsAuthorized": False,
        }
        base = {
            "state": "ready", "paired": True, "transport_state": "connected",
            "product_dispatch_ready": True, "product_readiness_code": "ready",
            "central_session": {
                "checked": True, "last_confirmation_observed": False,
                "last_error_code": None,
            },
        }
        invalid = (
            {**base, "paired": False},
            {**base, "product_dispatch_ready": False},
            {**base, "product_readiness_code": "heartbeat_stale"},
            {**base, "central_session": {
                "checked": True, "last_confirmation_observed": False,
                "last_error_code": "SECRET_TOKEN_MUST_NOT_LEAK",
            }},
            {**base, "central_session": {"last_error_code": None}},
            {**base, "central_session": {
                "checked": "true", "last_confirmation_observed": False,
                "last_error_code": None,
            }},
        )
        for runtime in invalid:
            with self.subTest(runtime=runtime):
                messages = []
                code = gui.diagnose_with_dialogs(
                    show=lambda msg, _flags: messages.append(msg) or 1,
                    inspect=lambda: network, observe_runtime=lambda: runtime,
                )
                self.assertEqual(code, 1)
                self.assertEqual(len(messages), 1)
                self.assertIn("Nao foi possivel", messages[0])
                self.assertNotIn("SECRET_TOKEN_MUST_NOT_LEAK", messages[0])

    def test_local_status_reader_is_single_bounded_loopback_request(self):
        class Response:
            status = 200
            def read(self, max_bytes):
                import json
                self.assert_no_content = max_bytes
                return json.dumps({"runtime": {
                    "state": "ready", "paired": True,
                    "transport_state": "connected",
                    "product_dispatch_ready": True,
                }}).encode()

        response = Response()
        connection = mock.Mock()
        connection.getresponse.return_value = response
        with mock.patch.object(
            gui.http.client, "HTTPConnection", return_value=connection,
        ) as connect:
            record = gui._read_local_runtime()
        self.assertEqual(record["state"], "ready")
        self.assertEqual(response.assert_no_content, 131073)
        connect.assert_called_once_with("127.0.0.1", 8765, timeout=1.5)
        connection.request.assert_called_once_with("GET", "/status")
        connection.close.assert_called_once()

        oversized = mock.Mock()
        oversized.status = 200
        oversized.read.return_value = b"{" + b"x" * 131072
        broken_connection = mock.Mock()
        broken_connection.getresponse.return_value = oversized
        with mock.patch.object(
            gui.http.client, "HTTPConnection",
            return_value=broken_connection,
        ):
            self.assertIsNone(gui._read_local_runtime())
        broken_connection.close.assert_called_once()

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

    def test_all_owner_gui_flows_use_mcp_browser_return_not_terminal(self):
        # The OAuth callback page must tell the user to return to the
        # standalone MCP application, not to an inaccessible CLI window.
        self.assertIs(
            gui.connect_with_dialogs.__kwdefaults__["authorize"],
            gui._browser_authorization,
        )
        self.assertIs(
            gui.select_chatgpt_session_with_dialogs.__kwdefaults__["authorize"],
            gui._browser_authorization,
        )
        self.assertIs(
            gui.retain_full_control_with_dialogs.__kwdefaults__["authorize"],
            gui._browser_authorization,
        )
        with mock.patch.object(
            gui, "browser_authorization_code",
            return_value=("owner-code", "pkce-verifier"),
        ) as authorize:
            self.assertEqual(
                gui._browser_authorization(),
                ("owner-code", "pkce-verifier"),
            )
        authorize.assert_called_once_with(mcp_callback=True)

    def test_one_click_connected_owner_handoff_requires_explicit_yes(self):
        messages = []
        selected = []
        launches = []
        status = gui.activate_with_dialogs(
            show=lambda message, flags: messages.append((message, flags)) or 6,
            observe=lambda: "connected",
            launch=lambda: launches.append("forbidden"),
            select_session=lambda: selected.append("owner-gui") or 0,
        )
        self.assertEqual(status, 0)
        self.assertEqual(selected, ["owner-gui"])
        self.assertEqual(launches, [])
        self.assertEqual(len(messages), 1)
        self.assertIn("NAO significa que o ChatGPT tenha acesso", messages[0][0])
        self.assertEqual(messages[0][1] & gui._MB_YESNO, gui._MB_YESNO)

        selected.clear()
        messages.clear()
        status = gui.activate_with_dialogs(
            show=lambda message, flags: messages.append((message, flags)) or 7,
            observe=lambda: "connected",
            launch=lambda: launches.append("forbidden"),
            select_session=lambda: selected.append("forbidden") or 0,
        )
        self.assertEqual(status, 0)
        self.assertEqual(selected, [])
        self.assertEqual(launches, [])
        self.assertEqual(len(messages), 1)

    def test_one_click_unconfirmed_transport_never_authenticates_owner(self):
        selected = []
        for observed in ("running",):
            self.assertEqual(gui.activate_with_dialogs(
                show=lambda *_args: 6,
                observe=lambda: observed,
                launch=lambda: self.fail("Must not start another agent"),
                select_session=lambda: selected.append("forbidden") or 0,
            ), 0)
        self.assertEqual(selected, [])

    def test_desktop_activation_when_connected_does_not_launch_again(self):
        launches = []
        messages = []
        rc = gui.activate_with_dialogs(
            observe=lambda: "connected", launch=lambda: launches.append(True),
            show=lambda message, _flags: messages.append(message) or 1,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(launches, [])
        self.assertEqual(len(messages), 1)
        self.assertIn("ja esta ativo e conectado", messages[0])
        self.assertNotIn("Registro do dispositivo", messages[0])

    def test_desktop_activation_when_runtime_running_keeps_one_agent(self):
        launches = []
        messages = []
        rc = gui.activate_with_dialogs(
            observe=lambda: "running", launch=lambda: launches.append(True),
            show=lambda message, _flags: messages.append(message) or 1,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(launches, [])
        self.assertIn("ainda nao foi confirmada", messages[0])
        self.assertNotIn("ativo e conectado", messages[0])

    def test_desktop_activation_starts_one_launcher_and_never_forges_ready(self):
        observations = iter(["unavailable", "running"])
        launches = []
        messages = []
        rc = gui.activate_with_dialogs(
            observe=lambda: next(observations),
            launch=lambda: launches.append("canonical-native-launcher"),
            show=lambda message, _flags: messages.append(message) or 1,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(launches, ["canonical-native-launcher"])
        self.assertIn("ainda nao confirmou", messages[0])
        self.assertNotIn("ativo e conectado", messages[0])

    def test_desktop_activation_with_no_status_is_only_a_request(self):
        launches = []
        messages = []
        rc = gui.activate_with_dialogs(
            observe=lambda: "unavailable",
            launch=lambda: launches.append("launcher"),
            show=lambda message, _flags: messages.append(message) or 1,
        )
        self.assertEqual(rc, 0)
        self.assertEqual(launches, ["launcher"])
        self.assertIn("solicitada", messages[0])
        self.assertIn("ainda nao foi confirmada", messages[0])

    def test_desktop_activation_error_cannot_leak_secrets(self):
        messages = []
        rc = gui.activate_with_dialogs(
            observe=lambda: "unavailable",
            launch=lambda: (_ for _ in ()).throw(RuntimeError("SECRET_DEVICE_TOKEN")),
            show=lambda message, _flags: messages.append(message) or 1,
        )
        self.assertEqual(rc, 1)
        self.assertEqual(len(messages), 1)
        self.assertIn("Nao foi possivel", messages[0])
        self.assertNotIn("SECRET_DEVICE_TOKEN", messages[0])

    def test_local_status_requires_true_runtime_readiness(self):
        class FakeReply:
            status = 200

            def __init__(self, runtime):
                self.runtime = runtime

            def read(self, _n):
                import json
                return json.dumps({"runtime": self.runtime}).encode()

        def observe(runtime):
            connection = mock.Mock()
            connection.getresponse.return_value = FakeReply(runtime)
            with mock.patch.object(gui.http.client, "HTTPConnection", return_value=connection) as ctor:
                value = gui._observe_local_runtime()
            ctor.assert_called_once_with("127.0.0.1", 8765, timeout=1.5)
            connection.request.assert_called_once_with("GET", "/status")
            connection.close.assert_called_once()
            return value

        confirmed = {"state": "ready", "paired": True,
                     "transport_state": "connected",
                     "product_dispatch_ready": True}
        self.assertEqual(observe(confirmed), "connected")
        self.assertEqual(observe({**confirmed, "paired": False}), "running")
        self.assertEqual(observe({**confirmed, "product_dispatch_ready": False}), "running")
        self.assertEqual(observe({"state": "pairing"}), "running")
        self.assertEqual(observe({}), "unavailable")

    def test_activation_launcher_uses_only_pinned_install_and_private_host(self):
        import os
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as local_data:
            root = Path(local_data) / "Programs" / "OrdaX MCP"
            private_gui = root / "runtime" / "pythonw.exe"
            launcher = root / "ORDAX Runtime.exe"
            private_gui.parent.mkdir(parents=True)
            private_gui.touch()
            launcher.touch()
            with mock.patch.dict(gui.os.environ, {"LOCALAPPDATA": local_data}):
                with mock.patch.object(gui.sys, "platform", "win32"):
                    with mock.patch.object(gui.sys, "executable", str(private_gui)):
                        with mock.patch.object(gui.subprocess, "Popen") as spawn:
                            gui._launch_canonical_runtime()
                        spawn.assert_called_once()
                        args, options = spawn.call_args
                        self.assertEqual(args, ([str(launcher)],))
                        self.assertEqual(options["cwd"], str(root))
                        self.assertIs(options["shell"], False)
                        self.assertIs(options["close_fds"], True)
                    with mock.patch.object(gui.sys, "executable", str(root / "unauthorized.exe")):
                        with mock.patch.object(gui.subprocess, "Popen") as spawn:
                            with self.assertRaises(RuntimeError):
                                gui._launch_canonical_runtime()
                        spawn.assert_not_called()
                    private_gui.unlink()
                    with mock.patch.object(gui.sys, "executable", str(private_gui)):
                        with mock.patch.object(gui.subprocess, "Popen") as spawn:
                            with self.assertRaises(RuntimeError):
                                gui._launch_canonical_runtime()
                        spawn.assert_not_called()

    def test_activation_cli_is_distinct_from_oauth_and_network_diagnostics(self):
        with mock.patch.object(gui.sys, "platform", "win32"):
            with mock.patch.object(gui, "connect_with_dialogs") as connect:
                with mock.patch.object(gui, "diagnose_with_dialogs") as network:
                    with mock.patch.object(gui, "activate_with_dialogs", return_value=0) as activate:
                        self.assertEqual(gui.main(["--ativar"]), 0)
                    activate.assert_called_once()
                network.assert_not_called()
            connect.assert_not_called()
            with self.assertRaises(SystemExit):
                gui.main(["--ativar", "--verificar-rede"])

    def test_chatgpt_owner_cancel_performs_no_login_or_remote_calls(self):
        actions = []
        self.assertEqual(gui.select_chatgpt_session_with_dialogs(
            show=lambda _message, _flags: 7,
            authorize=lambda: actions.append("oauth"),
            exchange=lambda *_args: actions.append("exchange"),
            client_factory=lambda *_args: actions.append("remote"),
        ), 2)
        self.assertEqual(actions, [])

    def test_chatgpt_owner_selects_exact_existing_ticket_with_separate_attestation(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        device = "33333333-3333-4333-8333-333333333333"
        client = "11111111-1111-4111-8111-111111111111"
        ticket = "22222222-2222-4222-8222-222222222222"
        calls = []
        dialogs = []

        class Remote:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def owner_remote_authority(self, token, *, device_id):
                calls.append(("authority", token, device_id))
                return {"device_id": device_id,
                        "clients": [{"client_id": client}],
                        "grant_groups": []}

            def owner_central_session_targets(self, token, *, client_id):
                calls.append(("targets", token, client_id))
                return {"targets": [{"device_id": device}], "eligible_device_count": 1}

            def owner_central_session_selections(self, token, *, client_id):
                calls.append(("tickets", token, client_id))
                return {"pending_selections": [{"challenge_id": ticket,
                    "created_at": "2026-11-02T10:00:00Z",
                    "expires_at": "2099-01-01T00:00:00Z"}]}

            def owner_select_central_device(self, token, *, client_id, challenge_id, device_id):
                calls.append(("select", token, client_id, challenge_id, device_id))
                return {"ok": True, "status": "pending", "attested": False,
                        "dispatch_authorized": False}

        def dialog(message, flags):
            dialogs.append((message, flags))
            return 6 if len(dialogs) <= 2 else 1

        result = gui.select_chatgpt_session_with_dialogs(
            show=dialog,
            authorize=lambda: ("one-time-code", "verifier"),
            exchange=lambda code, verifier: (
                calls.append(("exchange", code, verifier)) or "owner-token"
            ),
            load_config=lambda: SimpleNamespace(device_id=device,
                                                product_mcp_url="https://mcp.ordax.com.br"),
            client_factory=lambda url: (calls.append(("url", url)) or Remote()),
            clock=lambda: datetime(2026, 11, 2, tzinfo=timezone.utc),
        )
        self.assertEqual(result, 0)
        self.assertEqual(calls[-1], ("select", "owner-token", client, ticket, device))
        self.assertEqual([call[0] for call in calls].count("select"), 1)
        self.assertIn(device, dialogs[1][0])
        self.assertIn(client, dialogs[1][0])
        self.assertIn(ticket, dialogs[1][0])
        self.assertIn("ainda nao esta atestada", dialogs[-1][0])
        self.assertNotIn("owner-token", str(dialogs))
        self.assertNotIn("one-time-code", str(dialogs))

    def test_chatgpt_owner_decline_or_no_tickets_does_not_select(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        device = "33333333-3333-4333-8333-333333333333"
        client = "11111111-1111-4111-8111-111111111111"
        ticket = "22222222-2222-4222-8222-222222222222"
        calls = []

        class Remote:
            def __init__(self, tickets):
                self.tickets = tickets
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                pass
            def owner_remote_authority(self, token, *, device_id):
                return {"device_id": device_id,
                        "clients": [{"client_id": client}], "grant_groups": []}
            def owner_central_session_targets(self, token, *, client_id):
                return {"targets": [{"device_id": device}]}
            def owner_central_session_selections(self, token, *, client_id):
                return {"pending_selections": self.tickets}
            def owner_select_central_device(self, *_args, **_kwargs):
                calls.append("MUST_NOT_SELECT")
                raise AssertionError("Unexpected selection")

        ticket_data = [{"challenge_id": ticket,
                       "created_at": "2026-11-02T10:00:00Z",
                       "expires_at": "2099-01-01T00:00:00Z"}]
        config = lambda: SimpleNamespace(device_id=device,
                                          product_mcp_url="https://mcp.ordax.com.br")
        common = dict(authorize=lambda: ("code", "verifier"),
                      exchange=lambda *_args: "owner-token", load_config=config,
                      clock=lambda: datetime(2026, 11, 2, tzinfo=timezone.utc))
        decisions = iter([6, 7])
        self.assertEqual(gui.select_chatgpt_session_with_dialogs(
            show=lambda *_args: next(decisions),
            client_factory=lambda *_args: Remote(ticket_data), **common,
        ), 2)
        dialogs = []
        self.assertEqual(gui.select_chatgpt_session_with_dialogs(
            show=lambda message, *_args: dialogs.append(message) or 6,
            client_factory=lambda *_args: Remote([]), **common,
        ), 2)
        self.assertIn("Nenhum pedido pendente", dialogs[-1])
        self.assertEqual(calls, [])

    def test_chatgpt_tickets_reject_unrelated_device_and_expired_challenge(self):
        from datetime import datetime, timezone
        device = "33333333-3333-4333-8333-333333333333"
        client = "11111111-1111-4111-8111-111111111111"
        now = datetime(2026, 11, 2, tzinfo=timezone.utc)

        class Remote:
            def owner_central_session_targets(self, *_args, **_kwargs):
                return {"targets": [{"device_id": device}]}
            def owner_central_session_selections(self, *_args, **_kwargs):
                return {"pending_selections": [{"challenge_id": "22222222-2222-4222-8222-222222222222",
                    "created_at": "2026-11-01T00:00:00Z",
                    "expires_at": "2026-11-01T00:01:00Z"}]}
        authority = {"device_id": device, "clients": [{"client_id": client}],
                     "grant_groups": []}
        self.assertEqual(gui._central_selection_tickets(Remote(), "owner-token",
                                                        device, authority, now), [])
        with self.assertRaises(ValueError):
            gui._central_selection_tickets(Remote(), "owner-token", device,
                {**authority, "device_id": "44444444-4444-4444-8444-444444444444"}, now)
        with self.assertRaises(ValueError):
            gui._central_selection_tickets(Remote(), "owner-token", device,
                {**authority, "clients": [{"client_id": "not-uuid"}]}, now)

    def test_owner_dialog_cannot_submit_ticket_expired_during_confirmation(self):
        from datetime import datetime, timedelta, timezone
        from types import SimpleNamespace

        device = "33333333-3333-4333-8333-333333333333"
        client = "11111111-1111-4111-8111-111111111111"
        ticket = "22222222-2222-4222-8222-222222222222"
        start = datetime(2026, 11, 2, 10, tzinfo=timezone.utc)
        timestamps = iter([start, start + timedelta(minutes=3)])
        displayed = []
        selected = []

        class Remote:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def owner_remote_authority(self, _token, *, device_id):
                return {
                    "device_id": device_id,
                    "clients": [{"client_id": client}],
                    "grant_groups": [],
                }

            def owner_central_session_targets(self, _token, *, client_id):
                return {"targets": [{"device_id": device}]}

            def owner_central_session_selections(self, _token, *, client_id):
                return {"pending_selections": [{
                    "challenge_id": ticket,
                    "created_at": start.isoformat(),
                    "expires_at": (start + timedelta(minutes=1)).isoformat(),
                }]}

            def owner_select_central_device(self, *_args, **_kwargs):
                selected.append(True)
                raise AssertionError("Expired challenge must not be submitted")

        replies = iter([6, 6, 1])
        result = gui.select_chatgpt_session_with_dialogs(
            show=lambda msg, _flags: displayed.append(msg) or next(replies),
            authorize=lambda: ("code", "verifier"),
            exchange=lambda *_args: "owner-secret-token",
            load_config=lambda: SimpleNamespace(
                device_id=device, product_mcp_url="https://mcp.ordax.com.br"
            ),
            client_factory=lambda *_args: Remote(),
            clock=lambda: next(timestamps),
        )
        self.assertEqual(result, 2)
        self.assertEqual(selected, [])
        self.assertEqual(len(displayed), 3)
        self.assertIn("expirou antes da confirmacao", displayed[2])
        self.assertNotIn("owner-secret-token", str(displayed))

    def test_chatgpt_owner_gui_routes_only_explicit_selection(self):
        with mock.patch.object(gui.sys, "platform", "win32"):
            with mock.patch.object(gui, "connect_with_dialogs") as connect:
                with mock.patch.object(gui, "activate_with_dialogs") as activate:
                    with mock.patch.object(gui, "select_chatgpt_session_with_dialogs",
                                           return_value=2) as choose:
                        self.assertEqual(gui.main(["--selecionar-chatgpt"]), 2)
                    choose.assert_called_once()
                activate.assert_not_called()
            connect.assert_not_called()
            with self.assertRaises(SystemExit):
                gui.main(["--selecionar-chatgpt", "--ativar"])

    def test_retain_grant_gui_cancel_never_authenticates_or_updates(self):
        called = []
        self.assertEqual(gui.retain_full_control_with_dialogs(
            show=lambda *_: 7,
            authorize=lambda: called.append("oauth"),
            client_factory=lambda *_: called.append("remote"),
        ), 2)
        self.assertEqual(called, [])

    def test_retain_grant_gui_requires_two_explicit_owner_confirmations(self):
        from datetime import datetime, timedelta, timezone
        from types import SimpleNamespace
        device = "33333333-3333-4333-8333-333333333333"
        client = "11111111-1111-4111-8111-111111111111"
        now = datetime(2026, 11, 2, tzinfo=timezone.utc)
        calls = []
        dialogs = []

        class Owner:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def owner_remote_authority(self, _token, *, device_id):
                calls.append(("authority", device_id))
                return {
                    "device_id": device_id, "clients": [{"client_id": client}],
                    "grant_groups": [{
                        "grant_group_id": device, "device_id": device,
                        "client_id": client, "client_consent_active": True,
                        "scope_kind": "device", "space_id": None, "project_id": None,
                        "profile_key": "full-computer-control",
                        "valid_until": (now + timedelta(days=1)).isoformat(),
                    }],
                }

        def display(message, _flags):
            dialogs.append(message)
            return 6 if len(dialogs) <= 2 else 1

        def retention(authority, *, device_id, client_id, token, remote,
                      config, now):
            calls.append(("retain", device_id, client_id, token))
            self.assertEqual(config.device_id, device)
            self.assertIsInstance(authority, dict)
            return {
                "ok": True, "grantMutationPerformed": True,
                "endToEndProven": False, "valid_until": None,
            }

        outcome = gui.retain_full_control_with_dialogs(
            show=display, authorize=lambda: ("code", "verifier"),
            exchange=lambda *_: "owner-token",
            load_config=lambda: SimpleNamespace(
                device_id=device, product_mcp_url="https://mcp.ordax.com.br",
            ),
            client_factory=lambda *_: Owner(),
            clock=lambda: now, retain=retention,
        )
        self.assertEqual(outcome, 0)
        self.assertEqual(calls[-1], ("retain", device, client, "owner-token"))
        self.assertEqual(len(dialogs), 3)
        self.assertIn("AUTORIZAR ACESSO AO COMPUTADOR ATE REVOGAR", dialogs[1])
        self.assertIn(client, dialogs[1])
        self.assertIn("Acesso sem vencimento confirmado", dialogs[2])
        self.assertNotIn("owner-token", str(dialogs))
        self.assertNotIn("code", str(dialogs))

    def test_retain_gui_existing_permanent_grant_requires_no_second_write(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace

        device = "33333333-3333-4333-8333-333333333333"
        client = "11111111-1111-4111-8111-111111111111"
        seen = []

        class Owner:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def owner_remote_authority(self, _token, *, device_id):
                seen.append("authority")
                return {
                    "device_id": device_id,
                    "clients": [{"client_id": client}],
                    "grant_groups": [{
                        "grant_group_id": device,
                        "device_id": device,
                        "client_id": client,
                        "client_consent_active": True,
                        "scope_kind": "device",
                        "space_id": None,
                        "project_id": None,
                        "profile_key": "full-computer-control",
                        "valid_until": None,
                    }],
                }

        messages = []
        result = gui.retain_full_control_with_dialogs(
            show=lambda msg, _flags: messages.append(msg) or 6,
            authorize=lambda: ("code", "verifier"),
            exchange=lambda *_args: "opaque-owner-token",
            load_config=lambda: SimpleNamespace(
                device_id=device, product_mcp_url="https://mcp.ordax.com.br",
            ),
            client_factory=lambda *_args: Owner(),
            clock=lambda: datetime(2026, 11, 2, tzinfo=timezone.utc),
            retain=lambda *_args, **_kwargs: self.fail("Must not rewrite permanent access"),
        )
        self.assertEqual(result, 0)
        self.assertEqual(seen, ["authority"])
        self.assertEqual(len(messages), 2)
        self.assertIn("ja possui acesso completo sem vencimento", messages[1])
        self.assertIn("Nenhuma alteracao foi realizada", messages[1])
        self.assertNotIn("opaque-owner-token", str(messages))

    def test_owner_recovers_expired_full_control_using_existing_discovery_in_gui(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        device = "33333333-3333-4333-8333-333333333333"
        client = "11111111-1111-4111-8111-111111111111"
        now = datetime(2026, 11, 2, tzinfo=timezone.utc)
        calls = []
        dialogs = []

        class Owner:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def owner_remote_authority(self, _token, *, device_id):
                calls.append(("read-authority", device_id))
                return {
                    "device_id": device_id,
                    "clients": [{"client_id": client, "client_name": "ChatGPT OrdaX"}],
                    "grant_groups": [{
                        "grant_group_id": device,
                        "device_id": device,
                        "client_id": client,
                        "client_consent_active": True,
                        "scope_kind": "device",
                        "space_id": None,
                        "project_id": None,
                        "profile_key": "mcp-discovery",
                        "valid_until": None,
                    }],
                }

        def set_grant(authority, *, device_id, client_id, token, remote,
                      config, now, restore_from_discovery=False):
            calls.append(("restore", device_id, client_id, token,
                          restore_from_discovery))
            self.assertTrue(restore_from_discovery)
            self.assertEqual(config.device_id, device)
            return {
                "ok": True, "grantMutationPerformed": True,
                "endToEndProven": False, "valid_until": None,
            }

        def show(message, _flags):
            dialogs.append(message)
            return 6 if len(dialogs) <= 2 else 1

        outcome = gui.retain_full_control_with_dialogs(
            show=show, authorize=lambda: ("code", "verifier"),
            exchange=lambda *_args: "opaque-owner-token",
            load_config=lambda: SimpleNamespace(
                device_id=device, product_mcp_url="https://mcp.ordax.com.br",
            ),
            client_factory=lambda *_args: Owner(),
            clock=lambda: now, retain=set_grant,
        )
        self.assertEqual(outcome, 0)
        self.assertEqual(calls[-1], ("restore", device, client,
                                     "opaque-owner-token", True))
        self.assertIn("ChatGPT OrdaX", dialogs[1])
        self.assertIn("Cliente OAuth: " + client, dialogs[1])
        self.assertIn("expirada ou inexistente", dialogs[1])
        self.assertIn("SEM VENCIMENTO", dialogs[1])
        self.assertIn("Acesso sem vencimento confirmado", dialogs[2])
        self.assertNotIn("opaque-owner-token", str(dialogs))

    def test_recovery_owner_decline_fails_without_grant_mutation(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        device = "33333333-3333-4333-8333-333333333333"
        client = "11111111-1111-4111-8111-111111111111"

        class Owner:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def owner_remote_authority(self, _token, *, device_id):
                return {
                    "device_id": device_id,
                    "clients": [{"client_id": client, "client_name": "ChatGPT"}],
                    "grant_groups": [{
                        "grant_group_id": device, "device_id": device,
                        "client_id": client, "client_consent_active": True,
                        "scope_kind": "device", "space_id": None, "project_id": None,
                        "profile_key": "mcp-discovery", "valid_until": None,
                    }],
                }

        decisions = iter([6, 7])
        outcome = gui.retain_full_control_with_dialogs(
            show=lambda *_args: next(decisions),
            authorize=lambda: ("code", "verifier"),
            exchange=lambda *_args: "opaque-token",
            load_config=lambda: SimpleNamespace(
                device_id=device, product_mcp_url="https://mcp.ordax.com.br",
            ),
            client_factory=lambda *_args: Owner(),
            clock=lambda: datetime(2026, 11, 2, tzinfo=timezone.utc),
            retain=lambda *_args, **_kwargs: self.fail(
                "Refused grant must never be created"
            ),
        )
        self.assertEqual(outcome, 2)

    def test_one_permanent_client_does_not_hide_another_recoverable_client(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        device = "33333333-3333-4333-8333-333333333333"
        permanent_client = "11111111-1111-4111-8111-111111111111"
        expired_client = "22222222-2222-4222-8222-222222222222"
        groups = [{
            "grant_group_id": device, "device_id": device,
            "client_id": permanent_client, "client_consent_active": True,
            "scope_kind": "device", "space_id": None, "project_id": None,
            "profile_key": "full-computer-control", "valid_until": None,
        }, {
            "grant_group_id": expired_client, "device_id": device,
            "client_id": expired_client, "client_consent_active": True,
            "scope_kind": "device", "space_id": None, "project_id": None,
            "profile_key": "mcp-discovery", "valid_until": None,
        }]

        class Owner:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def owner_remote_authority(self, _token, *, device_id):
                return {
                    "device_id": device_id,
                    "clients": [
                        {"client_id": permanent_client, "client_name": "First"},
                        {"client_id": expired_client, "client_name": "Second"},
                    ],
                    "grant_groups": groups,
                }

        calls = []
        dialogs = []
        decisions = iter([6, 6, 1])

        def restore(_authority, *, device_id, client_id, token, remote,
                    config, now, restore_from_discovery=False):
            calls.append((client_id, restore_from_discovery))
            return {
                "ok": True, "grantMutationPerformed": True,
                "endToEndProven": False, "valid_until": None,
            }

        outcome = gui.retain_full_control_with_dialogs(
            show=lambda msg, _flags: dialogs.append(msg) or next(decisions),
            authorize=lambda: ("code", "verifier"),
            exchange=lambda *_args: "owner-token",
            load_config=lambda: SimpleNamespace(
                device_id=device, product_mcp_url="https://mcp.ordax.com.br",
            ),
            client_factory=lambda *_args: Owner(),
            clock=lambda: datetime(2026, 11, 2, tzinfo=timezone.utc),
            retain=restore,
        )
        self.assertEqual(outcome, 0)
        self.assertEqual(calls, [(expired_client, True)])
        self.assertIn("Second", dialogs[1])
        self.assertNotIn("First", dialogs[1])

    def test_retain_grant_gui_owner_decline_never_changes_authority(self):
        from datetime import datetime, timedelta, timezone
        from types import SimpleNamespace
        device = "33333333-3333-4333-8333-333333333333"
        client = "11111111-1111-4111-8111-111111111111"
        now = datetime(2026, 11, 2, tzinfo=timezone.utc)
        class Owner:
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def owner_remote_authority(self, _token, *, device_id):
                return {"device_id": device_id,
                        "clients": [{"client_id": client}],
                        "grant_groups": [{
                            "grant_group_id": device, "device_id": device,
                            "client_id": client, "client_consent_active": True,
                            "scope_kind": "device", "space_id": None, "project_id": None,
                            "profile_key": "full-computer-control",
                            "valid_until": (now + timedelta(days=1)).isoformat(),
                        }]}
        replies = iter([6, 7])
        self.assertEqual(gui.retain_full_control_with_dialogs(
            show=lambda *_: next(replies),
            authorize=lambda: ("code", "verifier"),
            exchange=lambda *_: "token",
            load_config=lambda: SimpleNamespace(
                device_id=device, product_mcp_url="https://mcp.ordax.com.br"),
            client_factory=lambda *_: Owner(),
            clock=lambda: now,
            retain=lambda *_args, **_kwargs: self.fail("Must not retain"),
        ), 2)

    def test_revoke_access_gui_confirms_exact_group_and_canonical_readback(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace

        device = "33333333-3333-4333-8333-333333333333"
        client = "11111111-1111-4111-8111-111111111111"
        group = "22222222-2222-4222-8222-222222222222"
        calls = []
        dialogs = []

        class Owner:
            def __init__(self):
                self.groups = [{
                    "grant_group_id": group,
                    "client_id": client,
                    "device_id": device,
                    "client_consent_active": True,
                    "scope_kind": "device",
                    "profile_key": "full-computer-control",
                    "space_id": None, "project_id": None,
                    "valid_until": None,
                }]

            def __enter__(self): return self
            def __exit__(self, *_args): return False

            def owner_remote_authority(self, token, *, device_id):
                calls.append(("authority", token, device_id))
                return {
                    "device_id": device_id,
                    "clients": [{"client_id": client}],
                    "grant_groups": self.groups,
                }

            def revoke_owner_remote_grant_group(
                self, token, *, client_id, grant_group_id
            ):
                calls.append(("revoke", token, client_id, grant_group_id))
                self.groups = []
                return {
                    "ok": True, "grant_group_id": grant_group_id,
                    "revoked_capabilities": 35,
                }

        def show(message, flags):
            dialogs.append((message, flags))
            return 6 if len(dialogs) <= 2 else 1

        result = gui.revoke_full_control_with_dialogs(
            show=show, authorize=lambda: ("code", "verifier"),
            exchange=lambda *_args: "owner-secret",
            load_config=lambda: SimpleNamespace(
                device_id=device, product_mcp_url="https://mcp.ordax.com.br",
            ),
            client_factory=lambda *_args: Owner(),
            clock=lambda: datetime(2026, 11, 2, tzinfo=timezone.utc),
        )
        self.assertEqual(result, 0)
        self.assertEqual([item[0] for item in calls],
                         ["authority", "revoke", "authority"])
        self.assertEqual(calls[1], ("revoke", "owner-secret", client, group))
        self.assertIn(client, dialogs[1][0])
        self.assertIn(group, dialogs[1][0])
        self.assertIn("REVOGAR CONTROLE DO COMPUTADOR", dialogs[1][0])
        self.assertIn("Revogacao confirmada", dialogs[2][0])
        self.assertNotIn("owner-secret", str(dialogs))

    def test_revoke_access_gui_cancel_prevents_every_remote_mutation(self):
        seen = []
        result = gui.revoke_full_control_with_dialogs(
            show=lambda *_args: 7,
            authorize=lambda: seen.append("oauth"),
            client_factory=lambda *_args: seen.append("remote"),
        )
        self.assertEqual(result, 2)
        self.assertEqual(seen, [])

    def test_revoke_access_gui_owner_decline_never_calls_revoke(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        device = "33333333-3333-4333-8333-333333333333"
        client = "11111111-1111-4111-8111-111111111111"
        group = "22222222-2222-4222-8222-222222222222"

        class Owner:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def owner_remote_authority(self, token, *, device_id):
                return {
                    "device_id": device_id,
                    "clients": [{"client_id": client}],
                    "grant_groups": [{
                        "grant_group_id": group, "client_id": client,
                        "device_id": device,
                        "client_consent_active": True,
                        "scope_kind": "device",
                        "profile_key": "full-computer-control",
                        "space_id": None, "project_id": None,
                        "valid_until": None,
                    }],
                }
            def revoke_owner_remote_grant_group(self, *_args, **_kwargs):
                raise AssertionError("Owner declined revocation")

        decisions = iter([6, 7])
        result = gui.revoke_full_control_with_dialogs(
            show=lambda *_args: next(decisions),
            authorize=lambda: ("code", "verifier"),
            exchange=lambda *_args: "owner-token",
            load_config=lambda: SimpleNamespace(
                device_id=device, product_mcp_url="https://mcp.ordax.com.br",
            ),
            client_factory=lambda *_args: Owner(),
            clock=lambda: datetime(2026, 11, 2, tzinfo=timezone.utc),
        )
        self.assertEqual(result, 2)

    def test_revoke_access_gui_never_claims_success_without_readback(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        device = "33333333-3333-4333-8333-333333333333"
        client = "11111111-1111-4111-8111-111111111111"
        group = "22222222-2222-4222-8222-222222222222"

        class Owner:
            def __init__(self, receipt):
                self.receipt = receipt
                self.revocations = 0
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def owner_remote_authority(self, _token, *, device_id):
                # The original grant is STILL visible after a mock deletion.
                return {
                    "device_id": device_id,
                    "clients": [{"client_id": client}],
                    "grant_groups": [{
                        "grant_group_id": group, "client_id": client,
                        "device_id": device, "client_consent_active": True,
                        "scope_kind": "device", "space_id": None,
                        "project_id": None,
                        "profile_key": "full-computer-control",
                        "valid_until": None,
                    }],
                }
            def revoke_owner_remote_grant_group(self, *_args, **_kwargs):
                self.revocations += 1
                return self.receipt

        for receipt in (
            {"ok": True, "grant_group_id": group,
             "revoked_capabilities": 1},
            {"ok": True, "grant_group_id": group,
             "revoked_capabilities": 0},
            {"ok": True, "grant_group_id": client,
             "revoked_capabilities": 1},
            {"ok": True, "grant_group_id": group,
             "revoked_capabilities": True},
        ):
            remote = Owner(receipt)
            messages = []
            results = iter([6, 6, 1])
            code = gui.revoke_full_control_with_dialogs(
                show=lambda message, flags: messages.append(message) or next(results),
                authorize=lambda: ("code", "verifier"),
                exchange=lambda *_args: "secret-token",
                load_config=lambda: SimpleNamespace(
                    device_id=device, product_mcp_url="https://mcp.ordax.com.br",
                ),
                client_factory=lambda *_args: remote,
                clock=lambda: datetime(2026, 11, 2, tzinfo=timezone.utc),
            )
            self.assertEqual(code, 1)
            self.assertEqual(remote.revocations, 1)
            self.assertNotIn("Revogacao confirmada", messages[-1])
            self.assertNotIn("secret-token", str(messages))

    def test_revocation_concurrent_grant_does_not_claim_control_removed(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        device = "33333333-3333-4333-8333-333333333333"
        client = "11111111-1111-4111-8111-111111111111"
        previous_group = "22222222-2222-4222-8222-222222222222"
        another_group = "44444444-4444-4444-8444-444444444444"

        class Owner:
            def __init__(self):
                self.replaced = False
                self.revoke_calls = 0

            def __enter__(self): return self
            def __exit__(self, *_args): return False

            def owner_remote_authority(self, _token, *, device_id):
                return {
                    "device_id": device_id,
                    "clients": [{"client_id": client}],
                    "grant_groups": [{
                        "grant_group_id": (
                            another_group if self.replaced else previous_group
                        ),
                        "client_id": client, "device_id": device,
                        "client_consent_active": True,
                        "scope_kind": "device", "space_id": None,
                        "project_id": None,
                        "profile_key": "full-computer-control",
                        "valid_until": None,
                    }],
                }

            def revoke_owner_remote_grant_group(
                self, _token, *, client_id, grant_group_id
            ):
                self.revoke_calls += 1
                self.replaced = True
                return {
                    "ok": True, "grant_group_id": grant_group_id,
                    "revoked_capabilities": 20,
                }

        remote = Owner()
        messages = []
        decisions = iter([6, 6, 1])
        result = gui.revoke_full_control_with_dialogs(
            show=lambda msg, _flags: messages.append(msg) or next(decisions),
            authorize=lambda: ("code", "verifier"),
            exchange=lambda *_args: "owner-token",
            load_config=lambda: SimpleNamespace(
                device_id=device, product_mcp_url="https://mcp.ordax.com.br",
            ),
            client_factory=lambda *_args: remote,
            clock=lambda: datetime(2026, 11, 2, tzinfo=timezone.utc),
        )
        self.assertEqual(result, 1)
        self.assertEqual(remote.revoke_calls, 1)
        self.assertNotIn("Revogacao confirmada", messages[-1])
        self.assertNotIn("owner-token", str(messages))

    def test_revoke_access_shortcut_routes_only_explicit_mode(self):
        with mock.patch.object(gui.sys, "platform", "win32"):
            with mock.patch.object(
                gui, "revoke_full_control_with_dialogs", return_value=2,
            ) as revoke:
                with mock.patch.object(gui, "connect_with_dialogs") as connect:
                    self.assertEqual(gui.main(["--revogar-acesso"]), 2)
                    connect.assert_not_called()
                revoke.assert_called_once()
            with self.assertRaises(SystemExit):
                gui.main(["--revogar-acesso", "--manter-acesso"])

    def test_retain_grant_gui_only_routes_explicit_option(self):
        with mock.patch.object(gui.sys, "platform", "win32"):
            with mock.patch.object(gui, "retain_full_control_with_dialogs", return_value=2) as retain:
                with mock.patch.object(gui, "connect_with_dialogs") as connect:
                    self.assertEqual(gui.main(["--manter-acesso"]), 2)
                    connect.assert_not_called()
                retain.assert_called_once()
            with self.assertRaises(SystemExit):
                gui.main(["--manter-acesso", "--selecionar-chatgpt"])

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


if __name__ == "__main__":
    unittest.main()
