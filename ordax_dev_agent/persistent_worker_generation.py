"""Content-addressed private worker payload derived from the verified MCP build.

This is not an agent, installer, identity or process registry. The existing
supervisor owns process receipts. A generation is published once, atomically,
and never modified in place while a worker may be executing from it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from contextlib import contextmanager

SCHEMA = "ordax.persistent-worker-generation/1"
MODULE_DIR = Path("Lib/site-packages/ordax_dev_agent")
MODULES = ("__init__.py", "persistent_process_runtime.py", "process_environment.py")
_HEX = re.compile(r"[0-9a-f]{64}")
_ROOT_FILE = re.compile(r"[A-Za-z0-9_.-]+\.(?:exe|dll|pyd|zip|_pth|txt|cat)", re.I)


def _ordinary(path: Path, *, directory: bool = False) -> None:
    info = path.lstat()
    if (path.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))):
        raise ValueError("persistent_worker_generation_unsafe_path")


def _digest(path: Path) -> str:
    _ordinary(path)
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _generation(files: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def inventory(runtime: Path) -> dict:
    """Build-time manifest from the existing staged private interpreter."""
    _ordinary(runtime, directory=True)
    files = {}
    for path in sorted(runtime.iterdir()):
        if _ROOT_FILE.fullmatch(path.name):
            files[path.name] = _digest(path)
    for name in MODULES:
        path = MODULE_DIR / name
        files[path.as_posix()] = _digest(runtime / path)
    if not {"python.exe", "python3.dll"}.issubset(files) or not any(
        re.fullmatch(r"python\d+\._pth", name) for name in files
    ) or not any(re.fullmatch(r"python\d+\.zip", name) for name in files):
        raise ValueError("persistent_worker_generation_incomplete")
    return {"schema": SCHEMA, "generation": _generation(files), "files": files}


def _validate_descriptor(descriptor: dict) -> dict[str, str]:
    if not isinstance(descriptor, dict) or descriptor.get("schema") != SCHEMA:
        raise ValueError("persistent_worker_generation_manifest_missing")
    files = descriptor.get("files")
    allowed_modules = {(MODULE_DIR / name).as_posix() for name in MODULES}
    if not isinstance(files, dict) or not allowed_modules.issubset(files) or not {"python.exe", "python3.dll"}.issubset(files):
        raise ValueError("persistent_worker_generation_incomplete")
    for name, digest in files.items():
        if not isinstance(name, str) or not (_ROOT_FILE.fullmatch(name) or name in allowed_modules):
            raise ValueError("persistent_worker_generation_invalid_path")
        if not isinstance(digest, str) or not _HEX.fullmatch(digest):
            raise ValueError("persistent_worker_generation_invalid_digest")
    if len({name.casefold() for name in files}) != len(files) or descriptor.get("generation") != _generation(files):
        raise ValueError("persistent_worker_generation_invalid_identity")
    return files


def _verify(root: Path, files: dict[str, str]) -> None:
    _ordinary(root, directory=True)
    expected_dirs = {parent.as_posix() for name in files for parent in Path(name).parents if parent != Path(".")}
    found = set()
    for path in root.rglob("*"):
        name = path.relative_to(root).as_posix()
        if name in expected_dirs:
            _ordinary(path, directory=True)
            continue
        if name not in files or _digest(path) != files[name]:
            raise ValueError("persistent_worker_generation_integrity_mismatch")
        found.add(name)
    if found != set(files):
        raise ValueError("persistent_worker_generation_incomplete")


def prepare_generation(runtime: Path, root: Path, descriptor: dict) -> Path:
    """Publish one verified derived generation; concurrent starts reuse it."""
    files = _validate_descriptor(descriptor)
    root.mkdir(parents=True, exist_ok=True)
    _ordinary(root, directory=True)
    target = root / descriptor["generation"]
    if target.exists() or target.is_symlink():
        _verify(target, files)
        return target
    staging = Path(tempfile.mkdtemp(prefix=".prepare-", dir=root))
    try:
        for name, digest in files.items():
            source = runtime / name
            if _digest(source) != digest:
                raise ValueError("persistent_worker_generation_source_mismatch")
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        _verify(staging, files)
        try:
            staging.rename(target)
        except OSError:
            # Another start may have published the same content concurrently.
            # A damaged preexisting directory is never replaced or executed.
            if not target.exists():
                raise
        _verify(target, files)
        return target
    finally:
        if staging.exists():
            shutil.rmtree(staging)


class WorkerLaunchGuardError(RuntimeError):
    """A launch cannot safely cross the canonical installation switch."""


def _standalone_runtime_root() -> Path | None:
    """Identify the same installed standalone host, not source/native hosts."""
    if sys.platform != "win32" or getattr(sys, "frozen", False):
        return None
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    base = Path(local)
    install = base / "Programs" / "OrdaX MCP"
    runtime = install / "runtime"
    if Path(sys.executable).resolve() != (runtime / "python.exe").resolve():
        return None
    return runtime


@contextmanager
def shared_worker_launch_guard(path: Path):
    """Shared Win32 file handle; quiesce uses exclusive FileShare.None.

    Each start can proceed independently. The non-inheritable handle is held
    through the existing receipt/handshake, never through the child's lifetime.
    No lease, timestamp or second process registry is authoritative.
    """
    import ctypes
    from ctypes import wintypes

    _ordinary(path.parent, directory=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # READ|WRITE access, share READ|WRITE, OPEN_ALWAYS, OPEN_REPARSE_POINT.
    # Refuse deletion/rename of the fence while any participant owns a handle.
    handle = kernel.CreateFileW(str(path), 0xC0000000, 3, None, 4, 0x00200080, None)
    if handle == ctypes.c_void_p(-1).value or not handle:
        error = ctypes.get_last_error()
        category = ("persistent_worker_launch_upgrade_in_progress"
                    if error in (32, 33) else "persistent_worker_launch_fence_unavailable")
        raise WorkerLaunchGuardError(f"{category}: win32={error}")
    try:
        _ordinary(path)
        yield
    finally:
        kernel.CloseHandle(handle)


def assert_upgrade_not_applying(updates: Path) -> None:
    """Reuse the canonical lifecycle journal across switch and rollback.

    The updater writes applying BEFORE its exclusive quiesce scan. Starts
    already holding shared handles must finish the handshake before that
    scan; later starts cannot cross activation or candidate health checks.
    No second transaction state or persistent lease.
    """
    if not updates.exists() and not updates.is_symlink():
        return
    try:
        _ordinary(updates, directory=True)
        for transaction in updates.iterdir():
            if not transaction.is_dir() and not transaction.is_symlink():
                continue
            _ordinary(transaction, directory=True)
            journal = transaction / "journal.json"
            if not journal.exists() and not journal.is_symlink():
                continue
            _ordinary(journal)
            record = json.loads(journal.read_text(encoding="utf-8-sig"))
            if not isinstance(record, dict) or record.get("schema") not in {
                "ordax.mcp-upgrade/1", "ordax.mcp-dev-upgrade/2",
                "ordax.mcp-dev-upgrade/3", "ordax.mcp-dev-upgrade/4",
            } or not isinstance(record.get("phase"), str):
                raise ValueError("unrecognized upgrade journal")
            if record["phase"] == "applying":
                raise WorkerLaunchGuardError("persistent_worker_launch_upgrade_in_progress")
    except (OSError, ValueError) as error:
        raise WorkerLaunchGuardError("persistent_worker_launch_upgrade_journal_unverified") from error


@contextmanager
def installed_worker_launch_guard():
    runtime = _standalone_runtime_root()
    if runtime is None:
        yield
        return
    private = Path(os.environ["LOCALAPPDATA"]) / "OrdaX"
    try:
        private.mkdir(exist_ok=True)
        _ordinary(private, directory=True)
    except (OSError, ValueError) as error:
        raise WorkerLaunchGuardError("persistent_worker_launch_fence_unavailable") from error
    with shared_worker_launch_guard(private / "worker-launch.lock"):
        assert_upgrade_not_applying(private / "updates" / "mcp")
        yield


def installed_worker() -> tuple[Path, Path, str] | None:
    """Standalone Windows MCP only; other product hosts keep their own ABI."""
    runtime = _standalone_runtime_root()
    if runtime is None:
        return None
    base = Path(os.environ["LOCALAPPDATA"])
    install = runtime.parent
    for path in (install, runtime):
        _ordinary(path, directory=True)
    manifest_path = install / "product-manifest.json"
    _ordinary(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("product") != "OrdaX MCP" or manifest.get("schema") != "ordax.mcp-windows-candidate/1":
        raise ValueError("persistent_worker_generation_product_mismatch")
    private = base / "OrdaX"
    private.mkdir(exist_ok=True)
    _ordinary(private, directory=True)
    descriptor = manifest.get("persistent_worker")
    target = prepare_generation(runtime, private / "worker-generations", descriptor)
    return target / "python.exe", target / MODULE_DIR / "persistent_process_runtime.py", target.name


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(inventory(args.inventory), separators=(",", ":")))


if __name__ == "__main__":
    main()
