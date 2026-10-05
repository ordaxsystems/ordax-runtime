from __future__ import annotations

import json
import os
import sys

from ordax_dev_agent.product_remote_client import ProductRemoteClient, ProductRemoteError


def main() -> int:
    base_url = os.environ.get(
        "ORDAX_PRODUCT_CONTROL_PLANE_URL",
        "https://ordax-control-plane-v3.ordax-ac1ca1b50d09.workers.dev",
    ).strip()
    token = os.environ.get("ORDAX_PRODUCT_ACCESS_TOKEN", "").strip()

    if not token:
        print(json.dumps({
            "ok": False,
            "error": "ORDAX_PRODUCT_ACCESS_TOKEN is required",
        }))
        return 2

    try:
        with ProductRemoteClient(base_url) as client:
            session = client.session(token)
            targets = client.targets(token)
    except (ProductRemoteError, ValueError) as error:
        print(json.dumps({
            "ok": False,
            "error": str(error),
        }))
        return 1

    print(json.dumps({
        "ok": True,
        "session": session,
        "target_count": len(targets),
        "targets": targets,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
