"""Owner USB executable composition; Product identity remains owned by the OS."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

from .config import AgentConfig, DEFAULT_PRODUCT_MCP_URL


def native_config(state: Path, workspace: Path, device_id: str) -> AgentConfig:
    return AgentConfig(
        agent_name="OrdaX OS", poll_seconds=12,
        state_dir=state / "product-runtime",
        agent_repo_path=Path(sys.executable).resolve().parent,
        hordax_path=workspace / ".unused-project",
        bridge_path=workspace / ".unused-bridge",
        projects={}, default_project="", workspace_root=workspace,
        device_id=device_id, product_mcp_url=DEFAULT_PRODUCT_MCP_URL,
    )


def identity_store(system_root: Path, state: Path):
    # This file arrives in the OS's verified main/release. The executable does
    # not embed another credential parser, enroll a device or copy its token.
    source = system_root / "surface/runtime/product_device_identity.py"
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode) or source.is_symlink() or info.st_mode & 0o022:
        raise ValueError("OS identity owner source is unsafe")
    spec = importlib.util.spec_from_file_location("ordax_native_product_identity", source)
    if spec is None or spec.loader is None:
        raise ValueError("OS identity owner source is unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.ProductDeviceIdentityStore(str(state))


def self_test() -> dict:
    """Exercise packaged dispatch without a device, network or persistent state."""
    from .actions import ActionRegistry, COMPUTER_GRAPHICAL_ACTIONS
    with tempfile.TemporaryDirectory(prefix="ordax-native-probe-") as temporary:
        root = Path(temporary)
        state = root / "private"
        state.mkdir()
        config = native_config(state, root, "22222222-2222-4222-8222-222222222222")
        config.state_dir.mkdir()
        (config.state_dir / "agent-settings.json").write_text(json.dumps({
            "computer_access": {"enabled": True, "full_access": True, "allowed_roots": [str(root)]},
        }))
        registry = ActionRegistry(config, computer_adapter={})
        target = root / "probe.txt"
        written = registry.execute("computer.text_write", {
            "path": str(target), "content": "native-package-probe\n", "create": True,
        })
        read = registry.execute("computer.text_read", {"path": str(target)})
        if not written.ok or not read.ok or target.read_text() != "native-package-probe\n":
            raise RuntimeError("native_package_filesystem_probe_failed")
        if COMPUTER_GRAPHICAL_ACTIONS.intersection(registry.names):
            raise RuntimeError("native_package_win32_fallback_detected")
        terminal = registry.execute("computer.terminal_exec", {
            "argv": ["/bin/sh", "-c", "printf native-terminal-probe"], "cwd": str(root),
        })
        if not terminal.ok or terminal.data.get("stdout") != "native-terminal-probe":
            raise RuntimeError("native_package_terminal_probe_failed")
        started = registry.execute("computer.process_start", {
            "argv": ["/bin/sh", "-c", "printf native-process-probe; sleep 30"],
            "cwd": str(root), "wait_seconds": 1,
        })
        process_id = started.data.get("process_id")
        try:
            if not started.ok or not process_id:
                raise RuntimeError("native_package_process_start_probe_failed")
            status = registry.execute("computer.process_status", {"process_id": process_id})
            if not status.ok or not status.data.get("ownership_valid"):
                raise RuntimeError("native_package_process_ownership_probe_failed")
        finally:
            if process_id:
                stopped = registry.execute("computer.process_stop", {"process_id": process_id})
                if not stopped.ok:
                    raise RuntimeError("native_package_process_stop_probe_failed")
        return {"ok": True, "profile": "owner-development-usb", "network_used": False,
                "device_credential_used": False, "graphical_actions": [],
                "probes": ["filesystem", "terminal", "supervised-process"]}


def main(argv: list[str] | None = None) -> int:
    sys._ordax_native_runtime = True
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--persistent-process-worker"]:
        from .persistent_process_runtime import main as worker
        return worker(argv[1:])
    parser = argparse.ArgumentParser(description="OrdaX OS Product Runtime (Owner USB)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--distribution-profile", choices=["owner-development"])
    parser.add_argument("--product-mode", choices=["usb"])
    parser.add_argument("--system-root", type=Path, default=Path("/workspace/ordax/system"))
    parser.add_argument("--state-root", type=Path, default=Path("/state/ordax/native-state"))
    parser.add_argument("--workspace", type=Path, default=Path("/workspace/ordax"))
    parser.add_argument("--status-port", type=int, default=8766)
    parser.add_argument("--full-access", action="store_true", help="explicit local Owner DEV policy; remote grants still required")
    args = parser.parse_args(argv)
    if args.self_test:
        print(json.dumps(self_test(), sort_keys=True))
        return 0
    if args.distribution_profile != "owner-development" or args.product_mode != "usb":
        parser.error("explicit Owner/Development USB identity is required")
    if sys.platform != "linux":
        parser.error("native executable requires the OrdaX kernel ABI")
    if not all(path.is_absolute() for path in (args.system_root, args.state_root, args.workspace)):
        parser.error("host paths must be absolute")
    if not 1 <= args.status_port <= 65535:
        parser.error("status port must be between 1 and 65535")
    if args.full_access:
        os.environ.update({"ORDAX_COMPUTER_ACCESS_ENABLED": "1", "ORDAX_COMPUTER_FULL_ACCESS": "1",
                           "ORDAX_COMPUTER_FULL_FILESYSTEM": "1"})
    store = identity_store(args.system_root, args.state_root)
    credential = store.read_enrolled_credential()
    if credential is None or not store.development_visibility_enabled():
        raise RuntimeError("native_product_identity_or_visibility_unavailable")
    config = native_config(args.state_root, args.workspace, credential["device_id"])
    config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    # The OS lifecycle owner holds this lock before launching. This second
    # process-wide guard also rejects accidental direct duplicate launches.
    import fcntl
    lock = os.open(config.state_dir / "instance.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        from .main import run
        return run(config, device_credential_provider=store.read_enrolled_credential,
                   computer_adapter={}, status_port=args.status_port)
    finally:
        os.close(lock)


if __name__ == "__main__":
    raise SystemExit(main())
