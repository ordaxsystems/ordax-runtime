from __future__ import annotations

import http.client
import threading
import urllib.parse
import unittest
from unittest.mock import Mock

import httpx

from ordax_studio import electron_account_session as account
from ordax_studio.product_auth import ProductAccountError, ProductAuthSession, exchange_authorization_code
from ordax_studio.public_auth_metadata import (
    STUDIO_OAUTH_CLIENT_ID,
    STUDIO_OAUTH_REDIRECT_URI,
    STUDIO_OAUTH_SCOPES,
)


class StudioBrowserOAuthTests(unittest.TestCase):
    def test_public_client_metadata_is_fixed_and_loopback_only(self):
        self.assertEqual(STUDIO_OAUTH_CLIENT_ID, "202fd3e1-b058-4531-8750-82c57e4b6ca0")
        self.assertEqual(STUDIO_OAUTH_REDIRECT_URI, "http://127.0.0.1:8766/oauth/callback")
        self.assertEqual(STUDIO_OAUTH_SCOPES, "openid email offline_access")

    def test_browser_flow_uses_s256_state_and_exact_loopback_callback(self):
        observed = {}

        def open_browser(url, **kwargs):
            parsed = urllib.parse.urlparse(url)
            query = urllib.parse.parse_qs(parsed.query)
            observed.update({"url": url, "query": query, "kwargs": kwargs})
            self.assertEqual(query["response_type"], ["code"])
            self.assertEqual(query["client_id"], [STUDIO_OAUTH_CLIENT_ID])
            self.assertEqual(query["redirect_uri"], [STUDIO_OAUTH_REDIRECT_URI])
            self.assertEqual(query["code_challenge_method"], ["S256"])
            self.assertEqual(query["scope"], [STUDIO_OAUTH_SCOPES])
            self.assertEqual(len(query["code_challenge"][0]), 43)
            self.assertGreaterEqual(len(query["state"][0]), 32)

            def callback():
                path = "/oauth/callback?" + urllib.parse.urlencode({
                    "code": "browser-code",
                    "state": query["state"][0],
                })
                connection = http.client.HTTPConnection("127.0.0.1", 8766, timeout=5)
                connection.request("GET", path, headers={"Host": "127.0.0.1:8766"})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                observed["callback_body"] = response.read().decode("utf-8")
                connection.close()
            threading.Thread(target=callback, daemon=True).start()
            return True

        exchange = Mock(return_value=ProductAuthSession("access.jwt", "owner@example.com"))
        session = account.browser_oauth_session(open_browser=open_browser, exchange=exchange, timeout_seconds=5)
        self.assertEqual(session.email, "owner@example.com")
        self.assertEqual(exchange.call_count, 1)
        code, verifier = exchange.call_args.args
        self.assertEqual(code, "browser-code")
        self.assertGreaterEqual(len(verifier), 43)
        self.assertLessEqual(len(verifier), 128)
        self.assertNotIn("password", observed["url"].lower())
        self.assertEqual(observed["kwargs"], {"new": 1, "autoraise": True})
        self.assertIn("Autorização recebida", observed["callback_body"])
        self.assertNotIn("Conexão concluída", observed["callback_body"])
        self.assertIn("concluir a validação deste computador", observed["callback_body"])

    def test_state_mismatch_fails_closed_without_exchanging_code(self):
        def open_browser(_url, **_kwargs):
            def callback():
                try:
                    connection = http.client.HTTPConnection("127.0.0.1", 8766, timeout=5)
                    connection.request("GET", "/oauth/callback?code=forged&state=wrong-state", headers={"Host": "127.0.0.1:8766"})
                    response = connection.getresponse()
                    response.read()
                    connection.close()
                except Exception:
                    pass
            threading.Thread(target=callback, daemon=True).start()
            return True

        exchange = Mock()
        with self.assertRaisesRegex(ProductAccountError, "cancelada|confirmada"):
            account.browser_oauth_session(open_browser=open_browser, exchange=exchange, timeout_seconds=5)
        exchange.assert_not_called()

    def test_token_exchange_has_pkce_and_no_client_secret(self):
        requests = []

        def handler(request: httpx.Request):
            requests.append(request)
            if request.url.path.endswith("/oauth/token"):
                form = urllib.parse.parse_qs(request.content.decode())
                self.assertEqual(form["grant_type"], ["authorization_code"])
                self.assertEqual(form["client_id"], [STUDIO_OAUTH_CLIENT_ID])
                self.assertEqual(form["redirect_uri"], [STUDIO_OAUTH_REDIRECT_URI])
                self.assertEqual(form["code"], ["auth-code"])
                self.assertEqual(form["code_verifier"], ["v" * 64])
                self.assertNotIn("client_secret", form)
                return httpx.Response(200, json={"access_token": "oauth.jwt", "token_type": "bearer"})
            if request.url.path.endswith("/oauth/userinfo"):
                self.assertEqual(request.headers["authorization"], "Bearer oauth.jwt")
                return httpx.Response(200, json={"email": "owner@example.com"})
            return httpx.Response(404)

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            session = exchange_authorization_code("auth-code", "v" * 64, http=client)
        self.assertEqual(session, ProductAuthSession("oauth.jwt", "owner@example.com"))
        self.assertEqual(len(requests), 2)

    def test_browser_open_failure_and_short_pkce_fail_closed(self):
        with self.assertRaisesRegex(ProductAccountError, "navegador"):
            account.browser_oauth_session(open_browser=lambda *_a, **_kw: False, timeout_seconds=1)
        with self.assertRaisesRegex(ProductAccountError, "PKCE"):
            exchange_authorization_code("code", "short")


if __name__ == "__main__":
    unittest.main()
