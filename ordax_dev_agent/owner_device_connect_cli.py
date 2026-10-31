"""Explicit Owner OAuth enrollment of this Windows Runtime, without Studio UI.

The browser OAuth flow, canonical device setup and local credential policy are
reused. This command never receives raw tokens as CLI flags or persists OAuth.
This is NOT activation of the OS-owned Runtime supervisor or a remote grant.
"""
from __future__ import annotations

import argparse
import json
import sys
from uuid import UUID
from collections.abc import Callable
from typing import Any

from ordax_core.owner_oauth import (
    OwnerOAuthError, browser_authorization_code, exchange_owner_code,
)
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.device_setup import SetupError, configure as configure_device


def enroll_owner_device(
    *,
    config_provider: Callable[[], AgentConfig] = AgentConfig.from_env,
    auth_code: Callable[[], tuple[str, str]] = browser_authorization_code,
    exchange: Callable[[str, str], str] = exchange_owner_code,
    configure: Callable[..., dict[str, Any]] = configure_device,
) -> dict[str, Any]:
    """Onboard only this locally bound device through the registered Owner PKCE.

    No token is accepted from argv, returned, printed or cached in the caller.
    The canonical device_setup authority owns private device state.
    """
    config = config_provider()
    if not config.product_mcp_url:
        raise SetupError("PRODUCT_MCP_URL_INVALID")
    code, verifier = auth_code()
    access_token = exchange(code, verifier)
    result = configure(
        config.state_dir,
        product_mcp_url=config.product_mcp_url,
        product_access_token=access_token,
        device_kind="desktop",
        channel="development",
    )
    if (result.get("ok") is not True
        or not isinstance(result.get("device_id"), str)
        or result.get("protocol") != "cloudflare-v3"):
        raise SetupError("DEVICE_ENROLLMENT_RESULT_INVALID")
    try:
        device = UUID(result["device_id"])
        if str(device) != result["device_id"] or device.version not in {1, 4, 5}:
            raise ValueError("Invalid device identifier")
    except ValueError:
        raise SetupError("DEVICE_ENROLLMENT_RESULT_INVALID") from None
    return {
        "ok": True,
        "device_id": result["device_id"],
        "channel": "development",
        "runtime_activated": False,
        "grants_issued": False,
        "end_to_end_proven": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ordax-owner-device-connect",
        description="Enroll this OrdaX Runtime device via explicit Owner OAuth PKCE",
    )
    parser.add_argument("--connect", action="store_true", required=True)
    args = parser.parse_args(argv)
    if not args.connect:
        return 2
    try:
        result = enroll_owner_device()
        print(json.dumps(result, sort_keys=True))
        return 0
    except OwnerOAuthError as error:
        print("OWNER_OAUTH_" + error.code, file=sys.stderr)
    except SetupError as error:
        print("DEVICE_SETUP_" + str(error), file=sys.stderr)
    except (OSError, ValueError, TypeError):
        print("DEVICE_SETUP_FAILED_CLOSED", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
