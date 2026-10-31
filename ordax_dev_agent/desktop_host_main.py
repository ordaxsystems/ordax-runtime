"""OrdaX Desktop-hosted Product Runtime: canonical dispatcher, OS-owned scope.

Only the verified OrdaX Desktop Native host may start this entrypoint. This
module is NOT a Windows computer-control agent or an OS/Linux executable.
The Product Device identity is separately enrolled under Desktop userData.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import replace
from pathlib import Path
from uuid import UUID

from .config import AgentConfig
from .main import run

# Positive, minimal read-only authority during Desktop's first integration.
# Additional OS actions must arrive through verified OS Native adapters;
# do not delegate unscoped Windows filesystem, terminal, or computer control.
DESKTOP_OS_ACTIONS = frozenset({"agent.status"})


def main() -> int:
    if sys.platform != "win32":
        raise RuntimeError("OrdaX Desktop Runtime host requires Windows")
    user_data_text = os.environ.get("ORDAX_DESKTOP_USER_DATA", "")
    state_text = os.environ.get("ORDAX_AGENT_STATE_DIR", "")
    if not user_data_text or not state_text:
        raise RuntimeError("OrdaX Desktop Runtime requires OS-owned userData/state")
    user_data = Path(user_data_text)
    state_dir = Path(state_text)
    if not user_data.is_absolute() or not state_dir.is_absolute():
        raise RuntimeError("OrdaX Desktop Runtime requires absolute profile paths")
    canonical = user_data / "native-state" / "product-runtime"
    if state_dir != canonical:
        raise RuntimeError("OrdaX Desktop Runtime cannot use another device identity")
    for owned in (user_data, user_data / "native-state", canonical):
        if owned.is_symlink() or (hasattr(owned, "is_junction") and owned.is_junction()):
            raise RuntimeError("OrdaX Desktop Runtime rejects linked profile roots")
    config = AgentConfig.from_env()
    settings_path = canonical / "agent-settings.json"
    token_path = canonical / "device-token.cloudflare-v3.txt"
    if (config.state_dir != canonical or not config.device_id
            or not settings_path.is_file() or settings_path.is_symlink()
            or not token_path.is_file() or token_path.is_symlink()):
        raise RuntimeError("OrdaX Desktop Product Device has not been enrolled")
    try:
        if settings_path.stat().st_size > 16384 or token_path.stat().st_size > 65:
            raise ValueError("Oversized private Product identity")
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        device = str(UUID(config.device_id))
        if (settings.get("device_id") != device
                or settings.get("control_plane_protocol") != "cloudflare-v3"
                or settings.get("product_mcp_url") != config.product_mcp_url
                or not re.fullmatch(r"[0-9a-f]{64}", token_path.read_text(encoding="ascii").strip())):
            raise ValueError("Private Product identity mismatch")
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        raise RuntimeError("OrdaX Desktop Product identity is invalid") from None
    if config.adapters or config.projects:
        raise RuntimeError("OrdaX Desktop Runtime cannot load host-side adapters")
    # Never inherit Windows Documents/github or HORDAX workspace defaults;
    # the only valid Product context is the OS-owned userData/Files boundary.
    scoped = replace(config, agent_name="OrdaX Desktop",
                     workspace_root=user_data / "Files",
                     hordax_path=user_data / "Files",
                     bridge_path=user_data / "native-state" / "product-runtime")
    # This is intentionally NOT native_main: its Linux/USB identity and
    # connector policy belong to the OS's native-disk/USB Supervisor.
    return run(scoped, computer_adapter={},
               allowed_action_names=DESKTOP_OS_ACTIONS, status_port=0)


if __name__ == "__main__":
    raise SystemExit(main())
