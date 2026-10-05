from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse


def _https_url(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def validate_metadata(metadata: Any, expected_issuer: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(metadata, dict):
        return ["OAuth discovery response must be a JSON object"]

    issuer = metadata.get("issuer")
    if issuer != expected_issuer:
        errors.append("issuer does not match the ORDAX Product Auth issuer")

    for field in ("authorization_endpoint", "token_endpoint", "registration_endpoint"):
        if not _https_url(metadata.get(field)):
            errors.append(f"{field} must be an HTTPS URL")

    methods = metadata.get("code_challenge_methods_supported")
    if not isinstance(methods, list) or "S256" not in methods:
        errors.append("code_challenge_methods_supported must include S256")

    grants = metadata.get("grant_types_supported")
    if isinstance(grants, list) and "authorization_code" not in grants:
        errors.append("grant_types_supported must include authorization_code")

    responses = metadata.get("response_types_supported")
    if isinstance(responses, list) and "code" not in responses:
        errors.append("response_types_supported must include code")

    auth_methods = metadata.get("token_endpoint_auth_methods_supported")
    if not isinstance(auth_methods, list) or not auth_methods:
        errors.append("token_endpoint_auth_methods_supported must be advertised")

    return errors


def main() -> int:
    if len(sys.argv) not in {2, 3}:
        raise SystemExit(
            "usage: verify-product-oauth-server.py <issuer> [discovery-url]"
        )

    issuer = sys.argv[1].rstrip("/")
    if not _https_url(issuer):
        raise SystemExit("issuer must use HTTPS")

    discovery_url = (
        sys.argv[2]
        if len(sys.argv) == 3
        else issuer.split("/auth/v1", 1)[0]
        + "/.well-known/oauth-authorization-server/auth/v1"
    )
    if not _https_url(discovery_url):
        raise SystemExit("discovery URL must use HTTPS")

    request = urllib.request.Request(
        discovery_url,
        headers={
            "Accept": "application/json",
            "User-Agent": "OrdaX-OAuth-Readiness",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if response.status != 200:
                raise SystemExit(
                    f"OAuth discovery endpoint returned HTTP {response.status}"
                )
            metadata = json.load(response)
    except urllib.error.HTTPError as exc:
        raise SystemExit(
            f"OAuth discovery endpoint returned HTTP {exc.code}; "
            "enable Authentication > OAuth Server in Supabase"
        ) from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"OAuth discovery request failed: {exc.reason}") from exc

    errors = validate_metadata(metadata, issuer)
    if errors:
        raise SystemExit("OAuth server is not MCP-ready: " + "; ".join(errors))

    print("Product OAuth server OK: discovery, DCR and PKCE S256 are available")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
