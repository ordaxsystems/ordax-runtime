"""One external-process environment boundary for all canonical Runtime actions."""
from __future__ import annotations

import os
import sys


def external_process_environment(overrides: dict[str, str] | None = None) -> dict[str, str]:
    # External terminal commands, project browsers and owned child processes
    # are not the OrdaX Native host. Never inherit its per-launch bridge grants,
    # device configuration or PyInstaller boot state. Explicit caller-provided
    # environment remains usable under the existing action/Owner grants.
    environment = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(("ORDAX_", "_PYI_", "PYINSTALLER_"))
    }
    if getattr(sys, "frozen", False) and sys.platform == "linux":
        original = environment.get("LD_LIBRARY_PATH_ORIG")
        if original is None:
            environment.pop("LD_LIBRARY_PATH", None)
        else:
            environment["LD_LIBRARY_PATH"] = original
    if overrides:
        environment.update(overrides)
    return environment


def native_private_worker_command(executable: str, args: list[str]) -> list[str]:
    """Re-enter only the native frozen worker through its OWN verified loader.

    The independent OrdaX Base deliberately does not install the dynamic
    interpreter under /lib. The main agent starts through the package-private
    musl loader, so a detached process manager must use that same loader.
    An absent sidecar retains the normal system-ABI path for other Linux
    candidates; an incomplete sidecar is rejected instead of falling back.
    Windows and non-native workers are completely unchanged.
    """
    from pathlib import Path

    command = [executable, *args]
    if not (
        sys.platform == "linux"
        and getattr(sys, "frozen", False)
        and getattr(sys, "_ordax_native_runtime", False)
    ):
        return command
    executable_path = Path(executable)
    if not executable_path.is_absolute():
        raise RuntimeError("native_worker_executable_not_absolute")
    sidecar = executable_path.parent / "portable-lib"
    if not sidecar.exists() and not sidecar.is_symlink():
        # Previous independently packaged system-ABI Native builds.
        return command
    loader = sidecar / "ld-musl-x86_64.so.1"
    libc = sidecar / "libc.musl-x86_64.so.1"
    zlib = sidecar / "libz.so.1"
    if (sidecar.is_symlink() or not sidecar.is_dir()
            or any(p.is_symlink() or not p.is_file()
                   for p in (loader, libc, zlib))
            or not os.access(loader, os.X_OK)):
        raise RuntimeError("native_private_worker_loader_unavailable")
    return [
        str(loader), "--library-path", str(sidecar), str(executable_path), *args,
    ]
