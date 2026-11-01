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
