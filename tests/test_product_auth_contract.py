from __future__ import annotations

import unittest
from pathlib import Path


class ProductAuthContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]
        self.auth = (
            self.root / "control-plane" / "cloudflare" / "src" / "product_auth.ts"
        ).read_text(encoding="utf-8")
        self.worker = (
            self.root / "control-plane" / "cloudflare" / "src" / "index.ts"
        ).read_text(encoding="utf-8")

    def test_product_auth_is_jwks_signature_based_and_fail_closed(self) -> None:
        self.assertIn('new Set(["RS256", "ES256"])', self.auth)
        self.assertIn("crypto.subtle.verify", self.auth)
        self.assertIn("product_auth_unconfigured", self.auth)
        self.assertIn("product_signature_invalid", self.auth)
        self.assertIn('redirect: "manual"', self.auth)
        self.assertNotIn('redirect: "error"', self.auth)
        self.assertNotIn('"none"', self.auth)
        self.assertNotIn('"HS256"', self.auth)

    def test_product_identity_requires_explicit_issuer_audience_and_https_jwks(self) -> None:
        self.assertIn("PRODUCT_AUTH_ISSUER", self.auth)
        self.assertIn("PRODUCT_AUTH_AUDIENCE", self.auth)
        self.assertIn("PRODUCT_AUTH_JWKS_URL", self.auth)
        self.assertIn('parsed.protocol !== "https:"', self.auth)
        self.assertIn("payload.iss !== issuer", self.auth)
        self.assertIn("audienceMatches(payload.aud, audience)", self.auth)
        self.assertIn("exp <= now - CLOCK_SKEW_SECONDS", self.auth)

    def test_product_session_does_not_reuse_operator_or_device_auth(self) -> None:
        session_start = self.worker.index("async function productSession")
        session_end = self.worker.index("async function createProductAction", session_start)
        session_source = self.worker[session_start:session_end]

        self.assertIn("authenticateProductRequest", session_source)
        self.assertNotIn("operatorAuthorized", session_source)
        self.assertNotIn("authenticateDevice", session_source)
        self.assertIn('"/v3/product/session"', self.worker)

    def test_product_session_is_identity_only_not_execution(self) -> None:
        session_start = self.worker.index("async function productSession")
        session_end = self.worker.index("async function createProductAction", session_start)
        session_source = self.worker[session_start:session_end]

        self.assertNotIn("enqueueJob", session_source)
        self.assertNotIn("resolveProductGrantForContext", session_source)
        self.assertNotIn("wakeDeviceSession", session_source)
        self.assertIn("subject_id", session_source)


if __name__ == "__main__":
    unittest.main()
