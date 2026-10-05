"""Compatibility entrypoint for the OrdaX Device Agent.

The packaged ORDAX Runtime executes this module with ``python -m``. Keep the
historical ``main`` symbol as the exact canonical agent function while performing
packaged-only identity recovery immediately before module execution.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ordax_dev_agent.device_identity_recovery import recover_existing_device_identity
from ordax_dev_agent.main import main

__all__ = ["main"]


def _recover_packaged_identity() -> None:
    try:
        result = recover_existing_device_identity()
    except Exception as error:
        result = {
            "ok": False,
            "state": f"recovery-error:{type(error).__name__}",
            "changed": False,
        }

    try:
        local_app_data = Path(
            os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
        )
        state = Path(
            os.environ.get(
                "ORDAX_AGENT_STATE_DIR",
                local_app_data / "OrdaX" / "DevAgent",
            )
        )
        state.mkdir(parents=True, exist_ok=True)
        safe = {
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "ok": bool(result.get("ok")),
            "state": str(result.get("state") or "unknown"),
            "changed": bool(result.get("changed")),
            "device_id": result.get("device_id"),
        }
        (state / "packaged-runtime-recovery.json").write_text(
            json.dumps(safe, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


if __name__ == "__main__":
    _recover_packaged_identity()
    raise SystemExit(main())
