"""Owner USB executable composition; Product identity remains owned by the OS."""
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import time
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
        protected_roots=(state,),
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
        # Validate the frozen entrypoint's canonical action composition, not
        # just the few commands exercised by this offline executable probe.
        # PyInstaller follows those imports without collecting every unrelated
        # submodule by name. A missing Owner capability must fail the build.
        required = {
            "computer.terminal_exec", "computer.process_start",
            "computer.process_status", "computer.process_stop",
            "computer.text_write", "computer.text_read",
            "workspace.repository_catalog", "git.command",
            "orchestrator.status", "intelligence.app_catalog",
            "blender.inspect",
        }
        missing = required.difference(registry.names)
        if missing:
            raise RuntimeError("native_package_actions_missing:" + ",".join(sorted(missing)))
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
            "argv": ["/bin/sh", "-c",
                     "printf native-process-probe; IFS= read -r line; printf ':%s' \"$line\"; sleep 30"],
            "cwd": str(root), "wait_seconds": 1,
        })
        process_id = started.data.get("process_id")
        try:
            if not started.ok or not process_id:
                raise RuntimeError("native_package_process_start_probe_failed")
            # The frozen supervisor must prove *all* published process I/O
            # operations, not just its ability to start and stop a child.
            deadline = time.monotonic() + 6
            while True:
                status = registry.execute("computer.process_status", {"process_id": process_id})
                if status.ok and status.data.get("ownership_valid") and status.data.get("running"):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("native_package_process_ownership_probe_failed")
                time.sleep(0.05)
            listed = registry.execute("computer.process_list", {})
            if not listed.ok or not any(
                    item.get("process_id") == process_id and item.get("ownership_valid")
                    for item in listed.data.get("processes", [])):
                raise RuntimeError("native_package_process_list_probe_failed")
            queued = registry.execute("computer.process_write_stdin", {
                "process_id": process_id, "text": "native-stdin-probe", "newline": True,
            })
            if not queued.ok or queued.data.get("queued_bytes") != len("native-stdin-probe"):
                raise RuntimeError("native_package_process_stdin_probe_failed")
            deadline = time.monotonic() + 6
            while True:
                logs = registry.execute("computer.process_logs", {"process_id": process_id})
                if logs.ok and "native-process-probe:native-stdin-probe" in logs.data.get("tail", ""):
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError("native_package_process_logs_probe_failed")
                time.sleep(0.05)
        finally:
            if process_id:
                stopped = registry.execute("computer.process_stop", {"process_id": process_id})
                if not stopped.ok:
                    raise RuntimeError("native_package_process_stop_probe_failed")
        return {"ok": True, "profile": "owner-development-usb", "network_used": False,
                "device_credential_used": False, "graphical_actions": [],
                "registered_action_count": len(registry.names),
                "supervised_process_actions": ["start", "status", "list", "write_stdin", "logs", "stop"],
                "probes": ["filesystem", "terminal", "supervised-process"]}


def acquire_native_product_instance_lock(
    runtime_state: Path, *, timeout_seconds: float = 6.0,
) -> int:
    """Acquire the same owner-development lock used by OS discovery reports.

    ProductDevicePresenceClient briefly locks this existing Runtime instance
    file while a device-authenticated online/offline report is in flight.
    Retry that bounded contention, but never run beside another Runtime.
    No separate lock, owner registry, bus, probe or new credential is created.
    """
    if (not isinstance(runtime_state, Path) or not runtime_state.is_absolute()
            or not 0 <= timeout_seconds <= 10):
        raise ValueError("invalid native Runtime singleton lock configuration")
    directory_fd = None
    descriptor = None
    try:
        directory_fd = os.open(
            runtime_state,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        directory = os.fstat(directory_fd)
        if (not stat.S_ISDIR(directory.st_mode)
                or directory.st_uid != os.geteuid()
                or stat.S_IMODE(directory.st_mode) != 0o700):
            raise RuntimeError("native_product_runtime_state_unsafe")
        descriptor = os.open(
            "instance.lock",
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600, dir_fd=directory_fd,
        )
        owner = os.fstat(descriptor)
        if (not stat.S_ISREG(owner.st_mode)
                or owner.st_uid != os.geteuid()
                or owner.st_nlink != 1
                or stat.S_IMODE(owner.st_mode) != 0o600):
            raise RuntimeError("native_product_runtime_instance_lock_unsafe")
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                owned = descriptor
                descriptor = None
                return owned
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("native_product_runtime_instance_busy") from None
                time.sleep(min(0.2, max(0, deadline - time.monotonic())))
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory_fd is not None:
            os.close(directory_fd)


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
    # The OS lifecycle owner must still verify and install the signed
    # executable. The same cross-process singleton lock is shared with
    # discovery so one in-flight presence report can complete before Runtime
    # takes over, without permitting two simultaneous agents.
    lock = acquire_native_product_instance_lock(config.state_dir)
    try:
        from .main import run
        return run(config, device_credential_provider=store.read_enrolled_credential,
                   computer_adapter={}, status_port=args.status_port)
    finally:
        os.close(lock)


if __name__ == "__main__":
    raise SystemExit(main())
