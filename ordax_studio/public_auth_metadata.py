"""Public Product authentication metadata snapshot of the Platform owner.

Authority: ordaxsystems/ordax-platform/control-plane/cloudflare/wrangler.toml
This is public metadata, not a credential. Windows CI verifies exact parity.
"""
from __future__ import annotations

SUPABASE_ORIGIN = "https://jhfphsjptrpmtnzkpwud.supabase.co"
SUPABASE_PUBLISHABLE_KEY = "sb_publishable_JHFaNBnQbkWLvdHI4zZDpQ_3RJWg6Bf"
PRODUCT_AUTH_ISSUER = f"{SUPABASE_ORIGIN}/auth/v1"
PRODUCT_AUTH_AUDIENCE = "authenticated"

# First-party desktop OAuth client. This is a public identifier, never a secret.
STUDIO_OAUTH_CLIENT_ID = "202fd3e1-b058-4531-8750-82c57e4b6ca0"
STUDIO_OAUTH_REDIRECT_URI = "http://127.0.0.1:8766/oauth/callback"
STUDIO_OAUTH_SCOPES = "openid email offline_access"
