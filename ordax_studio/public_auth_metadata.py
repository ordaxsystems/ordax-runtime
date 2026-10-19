"""Compatibility import for existing Studio and Electron code.

Public OAuth metadata is owned by ordax_core, not the desktop UI.
"""
from ordax_core.product_auth_metadata import (
    SUPABASE_ORIGIN, SUPABASE_PUBLISHABLE_KEY, PRODUCT_AUTH_ISSUER,
    PRODUCT_AUTH_AUDIENCE, STUDIO_OAUTH_CLIENT_ID,
    STUDIO_OAUTH_REDIRECT_URI, STUDIO_OAUTH_SCOPES,
)
