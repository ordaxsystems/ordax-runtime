"""Audit native ELF requirements against OrdaX Independent Base (kernel-only).

No dependency on ldd, readelf, Alpine, a target rootfs or executable probing.
This tool reads exact ELF headers and the canonical build recipe; it never
executes the binary or treats a PyInstaller archive as static Linux proof.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import struct

PT_LOAD = 1
PT_DYNAMIC = 2
PT_INTERP = 3
DT_NULL = 0
DT_NEEDED = 1
DT_STRTAB = 5
DT_STRSZ = 10
MAX_BYTES = 128 * 1024 * 1024


class NativeAbiError(ValueError):
    pass


def _slice(binary: bytes, offset: int, size: int) -> bytes:
    if offset < 0 or size < 0 or offset + size > len(binary):
        raise NativeAbiError("native_elf_range_invalid")
    return binary[offset:offset + size]


def inspect_elf(binary: bytes, recipe: str = "") -> dict:
    """Return explicit release blockers for an x86-64 independent static base."""
    if len(binary) < 64 or len(binary) > MAX_BYTES:
        raise NativeAbiError("native_elf_size_invalid")
    if binary[:6] != b"\x7fELF\x02\x01" or binary[6] != 1:
        raise NativeAbiError("native_elf_format_unsupported")
    # Little-endian ELF64 only, independent of the host architecture.
    _, elf_type, machine, _, _, ph_offset, _, _, _, ph_entry_size, ph_count, _, _, _ = (
        struct.unpack_from("<16sHHIQQQIHHHHHH", binary, 0)
    )
    if machine != 62 or elf_type not in (2, 3):
        raise NativeAbiError("native_elf_architecture_invalid")
    if ph_entry_size != 56 or not 1 <= ph_count <= 256:
        raise NativeAbiError("native_elf_program_headers_invalid")
    if ph_offset + ph_count * ph_entry_size > len(binary):
        raise NativeAbiError("native_elf_program_headers_truncated")

    headers = []
    for index in range(ph_count):
        ptype, _, offset, vaddr, _, filesz, memsz, _ = struct.unpack_from(
            "<IIQQQQQQ", binary, ph_offset + index * ph_entry_size
        )
        _slice(binary, offset, filesz)
        if memsz < filesz:
            raise NativeAbiError("native_elf_segment_invalid")
        headers.append((ptype, offset, vaddr, filesz))

    interp = [row for row in headers if row[0] == PT_INTERP]
    if len(interp) > 1:
        raise NativeAbiError("native_elf_duplicate_interpreter")
    interpreter = None
    if interp:
        _, offset, _, size = interp[0]
        raw = _slice(binary, offset, size)
        if not raw.endswith(b"\0") or b"\0" in raw[:-1]:
            raise NativeAbiError("native_elf_interpreter_invalid")
        try:
            interpreter = raw[:-1].decode("ascii")
        except UnicodeDecodeError as exc:
            raise NativeAbiError("native_elf_interpreter_invalid") from exc

    dynamic = [row for row in headers if row[0] == PT_DYNAMIC]
    if len(dynamic) > 1:
        raise NativeAbiError("native_elf_duplicate_dynamic_table")
    needed = []
    if dynamic:
        _, dyn_offset, _, dyn_size = dynamic[0]
        if dyn_size % 16 or dyn_size == 0:
            raise NativeAbiError("native_elf_dynamic_table_invalid")
        tags = []
        for offset in range(dyn_offset, dyn_offset + dyn_size, 16):
            tag, value = struct.unpack_from("<qQ", binary, offset)
            if tag == DT_NULL:
                break
            tags.append((tag, value))
        else:
            raise NativeAbiError("native_elf_dynamic_table_unterminated")
        strtab = [value for tag, value in tags if tag == DT_STRTAB]
        strsize = [value for tag, value in tags if tag == DT_STRSZ]
        names = [value for tag, value in tags if tag == DT_NEEDED]
        if names:
            if len(strtab) != 1 or len(strsize) != 1:
                raise NativeAbiError("native_elf_dynamic_strings_missing")
            vaddr, size = strtab[0], strsize[0]
            matches = [
                (offset + vaddr - seg_vaddr, size)
                for ptype, offset, seg_vaddr, filesz in headers
                if ptype == PT_LOAD and seg_vaddr <= vaddr
                and size <= filesz - (vaddr - seg_vaddr)
            ]
            if len(matches) != 1:
                raise NativeAbiError("native_elf_dynamic_strings_unmapped")
            table = _slice(binary, *matches[0])
            for start in names:
                if start >= len(table):
                    raise NativeAbiError("native_elf_needed_name_invalid")
                end = table.find(b"\0", start)
                if end < 0:
                    raise NativeAbiError("native_elf_needed_name_invalid")
                try:
                    needed.append(table[start:end].decode("ascii"))
                except UnicodeDecodeError as exc:
                    raise NativeAbiError("native_elf_needed_name_invalid") from exc
    alpine_recipe = bool(re.search(
        r"(?im)^\s*(?:FROM\s+[^\n]*alpine|RUN\s+[^\n]*\bapk\s+add\b)", recipe
    ))
    pyinstaller_archive = b"MEI\x0c\x0b\x0a\x0b\x0e" in binary[-512:]
    blockers = []
    if interpreter:
        blockers.append("external-dynamic-elf-interpreter")
    if needed:
        blockers.append("external-shared-libraries")
    if alpine_recipe:
        blockers.append("alpine-build-provenance")
    if pyinstaller_archive:
        # A static bootloader could still extract dynamically linked .so files.
        # A complete frozen-library closure audit is required independently.
        blockers.append("embedded-python-runtime-closure-unverified")
    if not interp and not dynamic and not pyinstaller_archive and not alpine_recipe:
        # A freestanding/static kernel ABI is a necessary property, not device
        # signature, remote attestation, or a proof of physical compatibility.
        pass
    return {
        "schema": "ordax.native-elf-independence-audit/1",
        "architecture": "x86_64",
        "interpreter": interpreter,
        "needed_libraries": sorted(set(needed)),
        "alpine_build_provenance": alpine_recipe,
        "pyinstaller_archive_present": pyinstaller_archive,
        "independent_base_abi_ready": not blockers,
        "blockers": blockers,
        "installation_authorized": False,
        "device_execution_proven": False,
    }


def audit(binary_path: Path, recipe_path: Path) -> dict:
    if not binary_path.is_file() or binary_path.is_symlink():
        raise NativeAbiError("native_elf_file_unavailable")
    if binary_path.stat().st_size > MAX_BYTES:
        raise NativeAbiError("native_elf_size_invalid")
    if not recipe_path.is_file() or recipe_path.is_symlink():
        raise NativeAbiError("native_build_recipe_unavailable")
    return inspect_elf(
        binary_path.read_bytes(), recipe_path.read_text(encoding="utf-8")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--recipe", type=Path, required=True)
    parser.add_argument("--require-independent", action="store_true")
    args = parser.parse_args()
    try:
        result = audit(args.binary, args.recipe)
    except (NativeAbiError, OSError, UnicodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["independent_base_abi_ready"] or not args.require_independent else 1


if __name__ == "__main__":
    raise SystemExit(main())
