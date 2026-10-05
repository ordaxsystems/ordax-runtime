from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "cloudflare" / "verify_product_oauth_server.py"
SPEC = importlib.util.spec_from_file_location("verify_product_oauth_server", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class ProductOAuthServerReadinessTests(unittest.TestCase):
    def test_accepts_mcp_ready_dcr_metadata(self) -> None:
        issuer = "https://example.supabase.co/auth/v1"
        metadata = {
            "issuer": issuer,
            "authorization_endpoint": issuer + "/oauth/authorize",
            "token_endpoint": issuer + "/oauth/token",
            "registration_endpoint": issuer + "/oauth/clients/register",
            "code_challenge_methods_supported": ["S256"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "response_types_supported": ["code"],
            "token_endpoint_auth_methods_supported": ["none"],
        }
        self.assertEqual(module.validate_metadata(metadata, issuer), [])

    def test_requires_registration_endpoint_for_dcr(self) -> None:
        issuer = "https://example.supabase.co/auth/v1"
        metadata = {
            "issuer": issuer,
            "authorization_endpoint": issuer + "/oauth/authorize",
            "token_endpoint": issuer + "/oauth/token",
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        }
        self.assertIn(
            "registration_endpoint must be an HTTPS URL",
            module.validate_metadata(metadata, issuer),
        )

    def test_requires_pkce_s256(self) -> None:
        issuer = "https://example.supabase.co/auth/v1"
        metadata = {
            "issuer": issuer,
            "authorization_endpoint": issuer + "/oauth/authorize",
            "token_endpoint": issuer + "/oauth/token",
            "registration_endpoint": issuer + "/oauth/clients/register",
            "code_challenge_methods_supported": ["plain"],
            "token_endpoint_auth_methods_supported": ["none"],
        }
        self.assertIn(
            "code_challenge_methods_supported must include S256",
            module.validate_metadata(metadata, issuer),
        )

    def test_requires_exact_issuer(self) -> None:
        metadata = {
            "issuer": "https://wrong.example/auth/v1",
            "authorization_endpoint": "https://example.test/oauth/authorize",
            "token_endpoint": "https://example.test/oauth/token",
            "registration_endpoint": "https://example.test/oauth/register",
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        }
        self.assertIn(
            "issuer does not match the ORDAX Product Auth issuer",
            module.validate_metadata(
                metadata,
                "https://example.supabase.co/auth/v1",
            ),
        )


if __name__ == "__main__":
    unittest.main()
