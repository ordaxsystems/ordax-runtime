"""Public issuer/key parity with the canonical Platform owner (checked out in CI)."""
from __future__ import annotations

import tomllib
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from ordax_studio import public_auth_metadata
from ordax_studio.product_auth import _auth_config


class PublicProductAuthOwnerParityTests(TestCase):
    def test_owner_public_auth_metadata_matches_runtime_default_without_overrides(self):
        root = Path(__file__).resolve().parents[1]
        source = root / ".ordax" / "platform" / "control-plane" / "cloudflare" / "wrangler.toml"
        self.assertTrue(source.is_file(), "CI must check out canonical ordax-platform owner")
        with source.open("rb") as stream:
            owner = tomllib.load(stream)
        vars_ = owner["vars"]
        origin = public_auth_metadata.SUPABASE_ORIGIN
        issuer = public_auth_metadata.PRODUCT_AUTH_ISSUER
        key = public_auth_metadata.SUPABASE_PUBLISHABLE_KEY

        self.assertEqual(issuer, vars_["PRODUCT_AUTH_ISSUER"])
        self.assertEqual(public_auth_metadata.PRODUCT_AUTH_AUDIENCE,
                         vars_["PRODUCT_AUTH_AUDIENCE"])
        self.assertEqual(key, vars_["SUPABASE_PUBLISHABLE_KEY"])
        self.assertEqual(vars_["PRODUCT_AUTH_JWKS_URL"],
                         issuer + "/.well-known/jwks.json")
        self.assertEqual(origin + "/auth/v1", issuer)
        self.assertTrue(key.startswith("sb_publishable_"))
        with patch.dict("os.environ", {
            "ORDAX_PRODUCT_AUTH_URL": "",
            "ORDAX_PRODUCT_AUTH_PUBLISHABLE_KEY": "",
        }):
            configured_origin, configured_key = _auth_config()
        self.assertEqual((configured_origin, configured_key), (origin, key))

    def test_runtime_cannot_silently_retain_the_prior_wrong_issuer(self):
        self.assertNotIn("eobcxuyvhkvdmkbaihwh",
                         public_auth_metadata.SUPABASE_ORIGIN)
        self.assertNotEqual(public_auth_metadata.SUPABASE_PUBLISHABLE_KEY,
                            "sb_publishable_GQUBlAVTzgNtscw9iE5vLQ_GGtdmsL5")
