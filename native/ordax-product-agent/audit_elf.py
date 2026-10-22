"""Audit native ELF requirements against OrdaX Independent Base (kernel-only).

No dependency on ldd, readelf, Alpine, a target rootfs or executable probing.
This tool reads exact ELF headers and the canonical build recipe; it never
executes the binary or treats a PyInstaller archive as static Linux proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import zlib

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
    pyinstaller_archive = (
        b"MEI\x0c\x0b\x0a\x0b\x0e" in binary[-16384:]
        or bool(re.search(r"(?i)\bpyinstaller\b", recipe))
    )
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


# PyInstaller CArchive cookie and TOC use a versioned, big-endian on-disk
# format. This is only a read-only native-file inventory; it is not a loader.
CARCHIVE_MAGIC = b"MEI\x0c\x0b\x0a\x0b\x0e"
CARCHIVE_COOKIE = "!8sIIII64s"
CARCHIVE_TOC_RECORD = "!iIIIBc"
MAX_CARCHIVE_ENTRIES = 4096
MAX_CARCHIVE_MEMBER = 128 * 1024 * 1024
MAX_CARCHIVE_TOTAL = 512 * 1024 * 1024


def inspect_embedded_elf(binary: bytes, outer_needed=()) -> dict:
    """Audit the *actual* frozen native entries without extracting or executing.

    DT_NEEDED basename matching only identifies candidates not present in the
    PyInstaller archive. It is not dynamic-loader resolution, RPATH validation,
    ABI compatibility with Buildroot, or proof of runtime extraction.
    """
    if len(binary) < 128 or len(binary) > MAX_BYTES:
        raise NativeAbiError("native_carchive_size_invalid")
    cookie_size = struct.calcsize(CARCHIVE_COOKIE)
    marker = binary.rfind(CARCHIVE_MAGIC, max(0, len(binary) - 65536))
    if marker < 0 or marker + cookie_size > len(binary):
        raise NativeAbiError("native_carchive_cookie_missing")
    magic, package_size, toc_offset, toc_size, _, _ = struct.unpack_from(
        CARCHIVE_COOKIE, binary, marker
    )
    package_start = marker + cookie_size - package_size
    toc_start = package_start + toc_offset
    toc_end = toc_start + toc_size
    if (magic != CARCHIVE_MAGIC or package_start < 64 or package_size > len(binary)
            or toc_size < 19 or toc_end != marker or toc_start < package_start):
        raise NativeAbiError("native_carchive_layout_invalid")

    cursor = toc_start
    header_size = struct.calcsize(CARCHIVE_TOC_RECORD)
    seen = set()
    inventory = []
    provided = set()
    needed = set(outer_needed)
    interpreters = set()
    total_size = 0
    count = 0
    while cursor < toc_end:
        count += 1
        if count > MAX_CARCHIVE_ENTRIES or cursor + header_size > toc_end:
            raise NativeAbiError("native_carchive_toc_invalid")
        entry_size, position, packed, size, compression, kind = struct.unpack_from(
            CARCHIVE_TOC_RECORD, binary, cursor
        )
        if (entry_size <= header_size or entry_size > 1024 or cursor + entry_size > toc_end
                or compression not in (0, 1) or size > MAX_CARCHIVE_MEMBER
                or position + packed > toc_offset or packed > MAX_BYTES):
            raise NativeAbiError("native_carchive_entry_invalid")
        raw_name = binary[cursor + header_size:cursor + entry_size]
        if not raw_name.endswith(b"\0"):
            raise NativeAbiError("native_carchive_name_invalid")
        try:
            name = raw_name.rstrip(b"\0").decode("utf-8")
        except UnicodeDecodeError as exc:
            raise NativeAbiError("native_carchive_name_invalid") from exc
        if (not name or "\0" in name or name in seen or name.startswith("/")
                or "\\" in name or any(part in ("", ".", "..") for part in name.split("/"))):
            raise NativeAbiError("native_carchive_name_invalid")
        seen.add(name)
        cursor += entry_size
        # Linux PyInstaller stores executables and shared objects as 'b'.
        # Fail closed if it mislabels a .so as another member type.
        if kind != b"b":
            if re.search(r"\.so(?:\.[0-9.]+)?$", name) and kind == b"x":
                raise NativeAbiError("native_carchive_shared_object_misclassified")
            continue
        total_size += size
        if total_size > MAX_CARCHIVE_TOTAL or size < 64:
            raise NativeAbiError("native_carchive_native_bytes_invalid")
        payload = _slice(binary, package_start + position, packed)
        if compression:
            decoder = zlib.decompressobj()
            try:
                inflated = decoder.decompress(payload, size + 1)
            except zlib.error as exc:
                raise NativeAbiError("native_carchive_decompression_invalid") from exc
            if (not decoder.eof or decoder.unconsumed_tail or decoder.unused_data
                    or len(inflated) != size):
                raise NativeAbiError("native_carchive_decompression_invalid")
            payload = inflated
        if len(payload) != size or payload[:4] != b"\x7fELF":
            raise NativeAbiError("native_carchive_non_elf_binary")
        result = inspect_elf(payload)
        digest = hashlib.sha256(payload).hexdigest()
        inventory.append((name, digest, result["interpreter"], result["needed_libraries"]))
        provided.add(name.rsplit("/", 1)[-1])
        needed.update(result["needed_libraries"])
        if result["interpreter"]:
            interpreters.add(result["interpreter"])
    if cursor != toc_end or not inventory:
        raise NativeAbiError("native_carchive_native_inventory_missing")

    # Summarize the exact binary hashes and DT_NEEDED binding without placing
    # all 90+ native modules or extracted code in the candidate manifest.
    fingerprint = hashlib.sha256(json.dumps(
        sorted(inventory), separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")).hexdigest()
    return {
        "schema": "ordax.native-embedded-elf-inventory/1",
        "native_entry_count": len(inventory),
        "native_uncompressed_bytes": total_size,
        "inventory_sha256": fingerprint,
        "needed_libraries": sorted(needed),
        "unprovided_needed_names": sorted(needed - provided),
        "embedded_interpreters": sorted(interpreters),
        "read_only_audit": True,
        "independent_base_compatibility_proven": False,
        "install_authorized": False,
    }


def audit(binary_path: Path, recipe_path: Path) -> dict:
    if not binary_path.is_file() or binary_path.is_symlink():
        raise NativeAbiError("native_elf_file_unavailable")
    if binary_path.stat().st_size > MAX_BYTES:
        raise NativeAbiError("native_elf_size_invalid")
    if not recipe_path.is_file() or recipe_path.is_symlink():
        raise NativeAbiError("native_build_recipe_unavailable")
    binary = binary_path.read_bytes()
    report = inspect_elf(binary, recipe_path.read_text(encoding="utf-8"))
    if report["pyinstaller_archive_present"]:
        report["embedded_elf_inventory"] = inspect_embedded_elf(
            binary, report["needed_libraries"]
        )
    return report


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
