"""Frozen Desktop component adapter; dispatch remains owned by ordax_dev_agent."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch

from ordax_dev_agent.desktop_host_main import DESKTOP_OS_ACTIONS, main as desktop_main


def self_test() -> dict:
    """Offline package probe with disposable state and no enrolled identity."""
    from ordax_dev_agent.actions import ActionRegistry
    from ordax_dev_agent.config import AgentConfig
    from ordax_dev_agent.product_device_control_plane import ProductDeviceControlPlane
    from ordax_dev_agent.product_central_session import ProductCentralSessionClient
    from ordax_dev_agent.versioning import runtime_package_version

    with tempfile.TemporaryDirectory(prefix="ordax-desktop-probe-") as temporary:
        root = Path(temporary)
        state = root / "native-state" / "product-runtime"
        state.mkdir(parents=True)
        with (patch.dict(os.environ, {}, clear=True),
              patch.object(socket.socket, "connect", side_effect=RuntimeError("offline probe")),
              patch.object(socket.socket, "connect_ex", side_effect=RuntimeError("offline probe"))):
            config = AgentConfig(agent_name="OrdaX Desktop", poll_seconds=60,
                                 state_dir=state, agent_repo_path=root,
                                 hordax_path=root / "Files", bridge_path=state,
                                 workspace_root=root / "Files", projects={})
            registry = ActionRegistry(config, computer_adapter={},
                                      allowed_action_names=DESKTOP_OS_ACTIONS)
            if registry.names != ["agent.status", "computer.access_status"]:
                raise RuntimeError("Desktop capability boundary changed")
            result = registry.execute("agent.status", {})
            if not result.ok or result.data["actions"] != ["agent.status", "computer.access_status"]:
                raise RuntimeError("Packaged canonical agent.status failed")
            policy = registry.execute("computer.access_status", {})
            if not policy.ok or policy.data.get("full_access") is True:
                raise RuntimeError("Packaged read-only Desktop policy status failed")
            if registry.execute("computer.access_status", {"enabled": True}).ok:
                raise RuntimeError("Desktop policy status accepted mutation fields")
            for action in ("terminal.exec", "computer.click", "workspace.text_read",
                           "workspace.text_write", "process.start", "git.command"):
                if registry.execute(action, {}).ok:
                    raise RuntimeError("Host action escaped Desktop scope")
            if not runtime_package_version():
                raise RuntimeError("Runtime distribution metadata missing")
            if any(state.rglob("*token*")):
                raise RuntimeError("Offline probe unexpectedly created credentials")
            if not ProductDeviceControlPlane or not ProductCentralSessionClient:
                raise RuntimeError("Canonical Product transport missing")
    return {"schema": "ordax.desktop-runtime-offline-proof/1", "ok": True,
            "profile": "ordax-desktop", "actions": ["agent.status", "computer.access_status"],
            "network_used": False, "device_credential_used": False,
            "remote_action_proven": False, "runtime_version": runtime_package_version()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OrdaX Desktop Product Runtime component")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        raise RuntimeError("Desktop component requires Windows")
    if args.self_test:
        print(json.dumps(self_test(), sort_keys=True))
        return 0
    if os.environ.get("ORDAX_RUNTIME_HOST_PROFILE") != "ordax-desktop":
        raise RuntimeError("Desktop component requires the Supervisor host profile")
    return desktop_main()


if __name__ == "__main__":
    raise SystemExit(main())
