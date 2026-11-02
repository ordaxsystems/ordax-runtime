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
