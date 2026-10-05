from __future__ import annotations

import json
import sys
import urllib.request
from urllib.parse import urlparse


def main() -> int:
    if len(sys.argv) != 4:
        raise SystemExit("usage: verify-product-auth-provider.py <issuer> <audience> <jwks-url>")

    issuer, audience, jwks_url = sys.argv[1:]
    for label, value in (("issuer", issuer), ("jwks", jwks_url)):
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise SystemExit(f"{label} must use HTTPS")
    if not audience.strip():
        raise SystemExit("audience must not be empty")

    request = urllib.request.Request(
        jwks_url,
        headers={
            "Accept": "application/json",
            "User-Agent": "OrdaX-Control-Plane-Deploy",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise SystemExit(f"JWKS endpoint returned HTTP {response.status}")
        data = json.load(response)

    keys = data.get("keys") if isinstance(data, dict) else None
    if not isinstance(keys, list):
        raise SystemExit("JWKS response does not contain a keys array")

    usable = [
        key
        for key in keys
        if isinstance(key, dict)
        and key.get("alg") in {"RS256", "ES256"}
        and isinstance(key.get("kid"), str)
        and key.get("kid")
    ]
    if not usable:
        raise SystemExit(
            "no usable RS256/ES256 signing key; rotate Supabase Auth to an asymmetric key"
        )

    print(f"Product auth JWKS OK: {len(usable)} usable signing key(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
