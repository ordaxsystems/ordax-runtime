from __future__ import annotations

import json

from .cloudflare_control_plane import CloudflareControlPlane
from .config import AgentConfig


def main() -> None:
    control = CloudflareControlPlane(AgentConfig.from_env())
    try:
        pairing = control.create_product_pairing()
    finally:
        control.http.close()

    print(json.dumps({
        "ok": True,
        "pairing": pairing,
        "note": (
            "Pairing grants no actions by itself. "
            "Use the secret once from an authenticated OrdaX Product client."
        ),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
