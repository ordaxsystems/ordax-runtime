from __future__ import annotations

import unittest
from pathlib import Path


class CloudflareOAuthConsentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parents[1]
        cls.worker = (root / "control-plane" / "cloudflare" / "src" / "index.ts").read_text(encoding="utf-8")
        cls.mcp = (root / "control-plane" / "cloudflare" / "src" / "mcp_http.ts").read_text(encoding="utf-8")
        cls.consent = (root / "control-plane" / "cloudflare" / "src" / "oauth_consent.ts").read_text(encoding="utf-8")

    def test_worker_serves_ordax_consent_and_standard_scope(self) -> None:
        self.assertIn('import { oauthConsentResponse } from "./oauth_consent";', self.worker)
        self.assertIn('url.pathname === "/oauth/consent"', self.worker)
        self.assertIn('scopes_supported: ["openid", "email", "offline_access"]', self.worker)
        self.assertNotIn('scopes_supported: ["authenticated"]', self.worker)

    def test_each_remote_tool_declares_oauth_security_scheme(self) -> None:
        self.assertIn('const OAUTH_SCOPES = ["openid", "email", "offline_access"]', self.mcp)
        self.assertGreaterEqual(self.mcp.count('securitySchemes: [{ type: "oauth2", scopes: OAUTH_SCOPES }]'), 2)
        self.assertIn("_meta: {", self.mcp)
        self.assertIn('"openai/toolInvocation/invoking"', self.mcp)
        self.assertIn('"openai/toolInvocation/invoked"', self.mcp)
        self.assertIn('"openai/profile": true', self.mcp)

    def test_consent_uses_pinned_supabase_sdk_and_publishable_key(self) -> None:
        self.assertIn('@supabase/supabase-js@2.117.2/+esm', self.consent)
        self.assertIn('sb_publishable_', self.consent)
        self.assertNotIn('service_role', self.consent)
        self.assertNotIn('sb_secret_', self.consent)

    def test_consent_never_posts_credentials_to_ordax_worker(self) -> None:
        self.assertIn('client.auth.signInWithPassword', self.consent)
        self.assertIn('client.auth.signInWithOtp', self.consent)
        self.assertNotIn('fetch("/oauth', self.consent)
        self.assertIn('A senha é enviada diretamente ao Supabase Auth', self.consent)

    def test_consent_escapes_script_data_and_sets_strict_headers(self) -> None:
        self.assertIn('jsonForScript', self.consent)
        self.assertIn('\\\\u003c', self.consent)
        self.assertIn('content-security-policy', self.consent)
        self.assertIn("frame-ancestors 'none'", self.consent)
        self.assertIn('cache-control', self.consent)
        self.assertIn('no-store', self.consent)

    def test_consent_uses_supabase_oauth_approval_api(self) -> None:
        self.assertIn('getAuthorizationDetails', self.consent)
        self.assertIn('approveAuthorization', self.consent)
        self.assertIn('denyAuthorization', self.consent)
        self.assertIn('authorization_id', self.consent)
        self.assertIn("data.scope||'openid email offline_access'", self.consent)


if __name__ == "__main__":
    unittest.main()
