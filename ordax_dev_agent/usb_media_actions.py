"""Native MCP USB media observation backed by the OS-owned host adapter.

The host, not a remote model payload, selects the canonical ordax-os checkout.
No Creator executable, shell, arbitrary command, writer or GPT task is needed.
These actions do not update or repair physical USB media.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from .models import ActionResult

USB_SCHEMA = "ordax.owner-dev-usb-media-inventory/1"
_OS_SCRIPT = Path("creator/tools/owner_dev_usb_inventory.py")
_DEPENDENCY = Path("creator/tools/owner_dev_qemu_partition_readonly.py")
_ORIGINS = frozenset({
    "https://github.com/ordaxsystems/ordax-os.git",
    "https://github.com/ordaxsystems/ordax-os",
    "git@github.com:ordaxsystems/ordax-os.git",
})
_SERIAL = re.compile(r"[A-Za-z0-9_-]{4,64}\Z")


class USBMediaActions:
    def _verified_os_usb_command(self) -> Path:
        """Trust a local host-only OS root, never a caller-supplied filepath."""
        root = getattr(self.config, "os_source_root", None)
        if os.name != "nt":
            raise ValueError("USB media host adapter is available only on Windows")
        if root is None:
            # Find the exact canonical OS checkout in the locally configured
            # workspace, never from a remote MCP argument. Multiple matches
            # are ambiguous and require an explicit host-only configuration.
            workspace = getattr(self.config, "workspace_root", None)
            if workspace is None:
                raise ValueError("canonical OrdaX OS host adapter is not configured")
            workspace = Path(workspace)
            candidates = [workspace / "ordax-os"]
            worktrees = workspace / "_worktrees"
            if worktrees.is_dir() and not worktrees.is_symlink():
                children = sorted(worktrees.iterdir())[:64]
                candidates.extend(child for child in children if child.is_dir())
            found = []
            for candidate in candidates:
                if (not candidate.is_symlink() and
                        (candidate / _OS_SCRIPT).is_file()):
                    found.append(candidate)
            if len(found) != 1:
                raise ValueError("canonical OS host checkout missing or ambiguous")
            root = found[0]
        root = Path(root)
        if (root.is_symlink() or getattr(root, "is_junction", lambda: False)()
                or not root.is_dir()):
            raise ValueError("OrdaX OS host source is not a real directory")
        try:
            origin = subprocess.run(
                ["git", "-C", str(root), "remote", "get-url", "origin"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=15, check=False,
            )
            branch = subprocess.run(
                ["git", "-C", str(root), "branch", "--show-current"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=15, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("OrdaX OS Git identity is unavailable") from exc
        if (origin.returncode or branch.returncode
                or origin.stdout.decode("utf-8", "replace").strip() not in _ORIGINS
                or branch.stdout.strip() != b"main"):
            raise ValueError("USB host adapter must use ordax-os/main")
        for rel in (_OS_SCRIPT, _DEPENDENCY):
            path = root / rel
            if (path.is_symlink() or getattr(path, "is_junction", lambda: False)()
                    or not path.is_file()
                    or path.resolve().parent != (root / rel.parent).resolve()):
                raise ValueError("OS USB adapter path is linked or unavailable")
        # Refuse local edits, including untracked substitution of the executable.
        try:
            tracked = subprocess.run(
                ["git", "-C", str(root), "ls-files", "--error-unmatch",
                 "--", _OS_SCRIPT.as_posix(), _DEPENDENCY.as_posix()],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=15, check=False,
            )
            dirty = subprocess.run(
                ["git", "-C", str(root), "status", "--porcelain=v1",
                 "--untracked-files=all", "--",
                 _OS_SCRIPT.as_posix(), _DEPENDENCY.as_posix()],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=15, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("OS USB adapter Git integrity unavailable") from exc
        if tracked.returncode or dirty.returncode or dirty.stdout.strip():
            raise ValueError("OS USB adapter source differs from the canonical checkout")
        return root / _OS_SCRIPT

    def _usb_os_read(self, arguments: list[str]) -> ActionResult:
        try:
            script = self._verified_os_usb_command()
            response = subprocess.run(
                [sys.executable, "-B", str(script), *arguments],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=70, check=False,
            )
            if response.returncode or len(response.stdout) > 64 * 1024:
                return ActionResult(False, "canonical USB host read-only inspection failed",
                                    {"physical_write": False, "state": "unavailable"})
            data = json.loads(response.stdout.decode("utf-8"))
            if (not isinstance(data, dict) or data.get("$schema") != USB_SCHEMA
                    or data.get("physical_write") is not False):
                return ActionResult(False, "OS USB host inspection returned invalid receipt",
                                    {"physical_write": False, "state": "invalid"})
            return ActionResult(True, "OrdaX USB media inspected without Creator GUI", data)
        except (ValueError, OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
            return ActionResult(False, str(exc), {"physical_write": False,
                                                "state": "unavailable"})

    def usb_list(self, payload: dict[str, Any]) -> ActionResult:
        if payload:
            return ActionResult(False, "usb.list does not accept target overrides")
        receipt = self._usb_os_read(["--list"])
        if receipt.ok:
            devices = receipt.data.get("devices")
            if (not isinstance(devices, list)
                    or receipt.data.get("device_count") != len(devices)
                    or any(type(entry) is not dict
                           or entry.get("physical_write") is not False
                           or entry.get("profile") != "owner-development"
                           for entry in devices)):
                return ActionResult(False, "OS USB inventory result is inconsistent")
        return receipt

    def usb_inspect(self, payload: dict[str, Any]) -> ActionResult:
        if set(payload) != {"disk_number", "serial"}:
            return ActionResult(False, "usb.inspect requires exact disk_number and serial")
        number, serial = payload["disk_number"], payload["serial"]
        if type(number) is not int or not 0 <= number <= 999:
            return ActionResult(False, "USB physical disk number is invalid")
        if type(serial) is not str or _SERIAL.fullmatch(serial) is None:
            return ActionResult(False, "USB serial is invalid")
        receipt = self._usb_os_read(["--inspect", "--disk-number", str(number),
                                     "--serial", serial])
        if receipt.ok and (
                receipt.data.get("disk_number") != number
                or receipt.data.get("usb_serial", "").upper() != serial.upper()
                or receipt.data.get("ext_filesystem_superblock_verified") is not True
                or receipt.data.get("physical_write_authorized") is not False):
            return ActionResult(False, "OS USB inspection target identity differs")
        return receipt
