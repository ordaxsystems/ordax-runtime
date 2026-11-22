"""Verify a package-private Linux loader closure; never authorize installation.

The OrdaX USB has no distribution-supplied dynamic loader. This check binds
exact bytes needed to bootstrap the existing PyInstaller Runtime to the
candidate. A successful ELF graph is NOT a signed release, full Python
closure, device execution or permission to alter the active USB.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from audit_elf import NativeAbiError, inspect_elf, inspect_embedded_elf

MAX_COMPONENT_BYTES = 32 * 1024 * 1024
LIBRARY_FILES = (
    "ld-musl-x86_64.so.1",
    "libc.musl-x86_64.so.1",
    "libz.so.1",
)


def verified_file(root: Path, filename: str) -> bytes:
    path = root / filename
    if (root.is_symlink() or path.is_symlink() or not path.is_file()
            or not 64 <= path.stat().st_size <= MAX_COMPONENT_BYTES):
        raise NativeAbiError("native_portable_library_missing_or_unsafe:" + filename)
    return path.read_bytes()


def verify(binary_path: Path, lib_root: Path) -> dict:
    if (binary_path.is_symlink() or not binary_path.is_file()
            or binary_path.stat().st_size > 128 * 1024 * 1024):
        raise NativeAbiError("native_portable_binary_invalid")
    if set(p.name for p in lib_root.iterdir()) != set(LIBRARY_FILES):
        raise NativeAbiError("native_portable_library_set_not_exact")

    binary = binary_path.read_bytes()
    head = inspect_elf(binary)
    if head["interpreter"] != "/lib/ld-musl-x86_64.so.1":
        raise NativeAbiError("native_portable_binary_interpreter_changed")
    embedded = inspect_embedded_elf(binary, head["needed_libraries"])
    libraries = {}
    required = set(head["needed_libraries"]) | set(embedded["unprovided_needed_names"])
    for name in LIBRARY_FILES:
        data = verified_file(lib_root, name)
        elf = inspect_elf(data)
        if elf["interpreter"] is not None:
            raise NativeAbiError("native_portable_library_has_external_interpreter:" + name)
        required.update(elf["needed_libraries"])
        libraries[name] = {
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "needed": elf["needed_libraries"],
        }
    if libraries["ld-musl-x86_64.so.1"]["sha256"] != libraries["libc.musl-x86_64.so.1"]["sha256"]:
        raise NativeAbiError("native_portable_libc_not_exact_loader_alias")
    if not {"libc.musl-x86_64.so.1", "libz.so.1"}.issubset(required):
        raise NativeAbiError("native_portable_expected_musl_zlib_missing")
    missing = sorted(required - set(LIBRARY_FILES))
    if missing:
        raise NativeAbiError("native_portable_unprovided_elf_names:" + ",".join(missing))
    return {
        "schema": "ordax.native-portable-bootstrap/1",
        "executable_sha256": hashlib.sha256(binary).hexdigest(),
        "loader": "ld-musl-x86_64.so.1",
        "libraries": libraries,
        "needed_names": sorted(required),
        "bootstrap_elf_closure_verified": True,
        "embedded_native_count": embedded["native_entry_count"],
        # PyInstaller extraction, runtime imports, device identity and native
        # action dispatch still require their own verified target execution.
        "full_runtime_dependency_closure_proven": False,
        "device_execution_proven": False,
        "device_install_authorized": False,
        "stable_activation_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--lib-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = verify(args.binary, args.lib_root)
    except (NativeAbiError, FileNotFoundError, OSError) as exc:
        parser.error("portable bootstrap invalid: " + str(exc))
    args.out.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    print("NATIVE_PRODUCT_PORTABLE_BOOTSTRAP=VERIFIED_NOT_INSTALLABLE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
