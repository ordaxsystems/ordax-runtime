"""Public Product authentication metadata snapshot of the Platform owner.

Authority: ordaxsystems/ordax-platform/control-plane/cloudflare/wrangler.toml
This is public metadata, not a credential. Windows CI verifies exact parity.
"""
from __future__ import annotations

SUPABASE_ORIGIN = "https://jhfphsjptrpmtnzkpwud.supabase.co"
SUPABASE_PUBLISHABLE_KEY = "sb_publishable_JHFaNBnQbkWLvdHI4zZDpQ_3RJWg6Bf"
PRODUCT_AUTH_ISSUER = f"{SUPABASE_ORIGIN}/auth/v1"
PRODUCT_AUTH_AUDIENCE = "authenticated"
