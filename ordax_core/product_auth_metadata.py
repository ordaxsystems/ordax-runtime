"""Canonical public Product OAuth metadata. No credentials or user secrets.

Source of truth: ordax-platform/control-plane/cloudflare/wrangler.toml.
Keep parity enforced by the Runtime's public authentication contract tests.
"""
SUPABASE_ORIGIN = "https://jhfphsjptrpmtnzkpwud.supabase.co"
SUPABASE_PUBLISHABLE_KEY = "sb_publishable_JHFaNBnQbkWLvdHI4zZDpQ_3RJWg6Bf"
PRODUCT_AUTH_ISSUER = f"{SUPABASE_ORIGIN}/auth/v1"
PRODUCT_AUTH_AUDIENCE = "authenticated"
STUDIO_OAUTH_CLIENT_ID = "202fd3e1-b058-4531-8750-82c57e4b6ca0"
STUDIO_OAUTH_REDIRECT_URI = "http://127.0.0.1:8766/oauth/callback"
STUDIO_OAUTH_SCOPES = "openid email offline_access"
