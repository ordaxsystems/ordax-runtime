"""Audit native ELF requirements against OrdaX Independent Base (kernel-only).

No dependency on ldd, readelf, Alpine, a target rootfs or executable probing.
This tool reads exact ELF headers and the canonical build recipe; it never
executes the binary or treats a PyInstaller archive as static Linux proof.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import gzip
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import stat
import struct
import tarfile
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
    magic, package_size, toc_offset, toc_size, python_abi_number, library_field = struct.unpack_from(
        CARCHIVE_COOKIE, binary, marker
    )
    # The embedded interpreter must agree with the exact PyInstaller cookie.
    # This is descriptive ABI evidence, not proof of target-rootfs closure.
    if b"\0" not in library_field:
        raise NativeAbiError("native_carchive_python_abi_invalid")
    raw_library, padding = library_field.split(b"\0", 1)
    if any(padding):
        raise NativeAbiError("native_carchive_python_abi_invalid")
    try:
        python_library = raw_library.decode("ascii")
    except UnicodeDecodeError as exc:
        raise NativeAbiError("native_carchive_python_abi_invalid") from exc
    match = re.fullmatch(r"libpython(3)\.(\d{1,2})(?:t)?\.so(?:\.\d+)*", python_library)
    if not match or python_abi_number != int(match.group(1)) * 100 + int(match.group(2)):
        raise NativeAbiError("native_carchive_python_abi_invalid")
    python_version = f"{match.group(1)}.{match.group(2)}"
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
        "embedded_python_version": python_version,
        "embedded_python_library": python_library,
        "inventory_sha256": fingerprint,
        "needed_libraries": sorted(needed),
        "unprovided_needed_names": sorted(needed - provided),
        "embedded_interpreters": sorted(interpreters),
        "read_only_audit": True,
        "independent_base_compatibility_proven": False,
        "install_authorized": False,
    }



# The OS owns Buildroot and its exact rootfs. This is a read-only name/ELF
# diagnostic for *that* verified rootfs, not another Buildroot implementation.
# Importantly, it never extracts or executes an archive member.
ROOTFS_LIBRARY_DIRS = ("lib", "lib64", "usr/lib", "usr/lib64")
ROOTFS_MAX_TAR_BYTES = 2 * 1024 * 1024 * 1024
ROOTFS_MAX_ENTRIES = 100_000
ROOTFS_MAX_LIBRARY_BYTES = 64 * 1024 * 1024
ROOTFS_MAX_LIBRARIES = 512
ROOTFS_MAX_CPIO_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
ROOTFS_MAX_CPIO_LIB_BYTES = 192 * 1024 * 1024
ROOTFS_CPIO_HEADER_BYTES = 110


class _CpioMember:
    """Bounded, in-memory *library* metadata; other OS files are discarded."""
    def __init__(self, kind: int, data: bytes = b"", linkname: str = ""):
        self.kind = kind
        self.data = data
        self.linkname = linkname
        self.size = len(data)

    def isfile(self) -> bool:
        return self.kind == stat.S_IFREG

    def isdir(self) -> bool:
        return self.kind == stat.S_IFDIR

    def issym(self) -> bool:
        return self.kind == stat.S_IFLNK


def _cpio_loader_search_path(name: str) -> bool:
    # Ignore Buildroot firmware in /lib/firmware and all unrelated files.
    # Only the standard root library directories and their direct entries
    # participate in this auditor's explicitly limited loader-name search.
    return any(name == root or
               (name.startswith(root + "/") and
                "/" not in name[len(root) + 1:])
               for root in ROOTFS_LIBRARY_DIRS)


def _read_cpio_libraries(source) -> dict:
    """Index standard-library paths in OS-owned newc CPIO, without extraction.

    The OrdaX Buildroot QEMU artifact publishes rootfs.cpio.gz but not its
    rootfs.tar. Reuse the *same* ELF and dependency graph after this small
    newc archive adapter; never build another OS rootfs or install anything.
    """
    entries = {}
    expanded = 0
    library_bytes = 0
    count = 0
    with gzip.GzipFile(fileobj=source, mode="rb") as stream:
        def take(length: int, *, retain: bool = False) -> bytes:
            nonlocal expanded
            if length < 0 or length > ROOTFS_MAX_CPIO_EXPANDED_BYTES - expanded:
                raise NativeAbiError("native_rootfs_cpio_unpacked_limit")
            expanded += length
            chunks = [] if retain else None
            remaining = length
            while remaining:
                data = stream.read(min(remaining, 1 << 20))
                if not data:
                    raise NativeAbiError("native_rootfs_cpio_truncated")
                remaining -= len(data)
                if chunks is not None:
                    chunks.append(data)
            return b"".join(chunks) if chunks is not None else b""

        while True:
            header = take(ROOTFS_CPIO_HEADER_BYTES, retain=True)
            if header[:6] != b"070701":
                raise NativeAbiError("native_rootfs_cpio_format_invalid")
            try:
                fields = [int(header[6+i*8:14+i*8], 16) for i in range(13)]
            except ValueError as exc:
                raise NativeAbiError("native_rootfs_cpio_header_invalid") from exc
            mode, size, namesize = fields[1], fields[6], fields[11]
            if not 1 <= namesize <= 4096:
                raise NativeAbiError("native_rootfs_cpio_name_size_invalid")
            raw_name = take(namesize, retain=True)
            if raw_name[-1] != 0 or b"\0" in raw_name[:-1]:
                raise NativeAbiError("native_rootfs_cpio_name_invalid")
            try:
                name = raw_name[:-1].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise NativeAbiError("native_rootfs_cpio_name_invalid") from exc
            if name == "TRAILER!!!":
                if size or mode:
                    raise NativeAbiError("native_rootfs_cpio_trailer_invalid")
                take(-(ROOTFS_CPIO_HEADER_BYTES + namesize) % 4)
                # newc permits zero padding after its trailer.
                while True:
                    rest = stream.read(1 << 20)
                    if not rest:
                        break
                    if len(rest) > ROOTFS_MAX_CPIO_EXPANDED_BYTES - expanded:
                        raise NativeAbiError("native_rootfs_cpio_unpacked_limit")
                    expanded += len(rest)
                    if any(rest):
                        raise NativeAbiError("native_rootfs_cpio_trailing_data")
                return entries
            count += 1
            if count > ROOTFS_MAX_ENTRIES:
                raise NativeAbiError("native_rootfs_member_count_exceeded")
            normalized = None
            if name not in (".", "./"):
                normalized = _tar_member_name(name)
            take(-(ROOTFS_CPIO_HEADER_BYTES + namesize) % 4)
            is_library = normalized is not None and _cpio_loader_search_path(normalized)
            kind = stat.S_IFMT(mode)
            if is_library:
                if normalized in entries:
                    raise NativeAbiError("native_rootfs_duplicate_library_path")
                if kind not in (stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
                    raise NativeAbiError("native_rootfs_unsupported_library_entry")
                if size > ROOTFS_MAX_LIBRARY_BYTES:
                    raise NativeAbiError("native_rootfs_library_too_large")
                if library_bytes + size > ROOTFS_MAX_CPIO_LIB_BYTES:
                    raise NativeAbiError("native_rootfs_cpio_library_limit")
                library_bytes += size
            data = take(size, retain=is_library and kind != stat.S_IFDIR)
            take(-size % 4)
            if is_library:
                link = ""
                if kind == stat.S_IFLNK:
                    try:
                        link = data.decode("utf-8")
                    except UnicodeDecodeError as exc:
                        raise NativeAbiError("native_rootfs_unsafe_symlink_target") from exc
                    if len(data) > 4096:
                        raise NativeAbiError("native_rootfs_unsafe_symlink_target")
                if kind == stat.S_IFDIR and size:
                    raise NativeAbiError("native_rootfs_cpio_directory_payload")
                entries[normalized] = _CpioMember(kind, data if kind == stat.S_IFREG else b"", link)


LIB_NAME = re.compile(r"[A-Za-z0-9_.+-]{1,255}\Z")


def _tar_member_name(raw: str) -> str:
    name = raw
    while name.startswith("./"):
        name = name[2:]
    if (not name or name.startswith("/") or "\\" in name or "\0" in name
            or any(part in ("", ".", "..") for part in name.rstrip("/").split("/"))):
        raise NativeAbiError("native_rootfs_unsafe_member_name")
    return name.rstrip("/")


def _rootfs_target(parent: str, target: str) -> str:
    if not target or "\0" in target or "\\" in target:
        raise NativeAbiError("native_rootfs_unsafe_symlink_target")
    value = (target.lstrip("/") if target.startswith("/")
             else posixpath.join(posixpath.dirname(parent), target))
    normalized = posixpath.normpath(value)
    if normalized in (".", "..") or normalized.startswith("../"):
        raise NativeAbiError("native_rootfs_symlink_escape")
    return normalized


def _library_namespace(name: str) -> bool:
    return any(name == root or name.startswith(root + "/")
               for root in ROOTFS_LIBRARY_DIRS)


def _resolve_rootfs_member(name: str, entries: dict) -> tuple[str, object] | None:
    """Resolve symbolic links in-memory, including parent directory aliases."""
    if name.startswith("/") or not _library_namespace(name):
        return None
    seen = set()
    for _ in range(32):
        if name in seen:
            raise NativeAbiError("native_rootfs_symlink_cycle")
        seen.add(name)
        parts = name.split("/")
        changed = False
        for index in range(1, len(parts) + 1):
            segment = "/".join(parts[:index])
            member = entries.get(segment)
            if member is None or not member.issym():
                continue
            target = _rootfs_target(segment, member.linkname)
            name = posixpath.join(target, *parts[index:])
            if not _library_namespace(name):
                raise NativeAbiError("native_rootfs_symlink_outside_library_namespace")
            changed = True
            break
        if not changed:
            entry = entries.get(name)
            return (name, entry) if entry is not None and entry.isfile() else None
    raise NativeAbiError("native_rootfs_symlink_depth_exceeded")


def inspect_rootfs_abi(binary: bytes, rootfs_tar: Path, recipe: str = "",
                       *, archive_format: str = "tar") -> dict:
    """Compare real target-rootfs ELF files against the frozen Runtime names.

    Basename search through default loader directories cannot prove glibc/musl
    symbol versions, dlopen calls, PyInstaller extraction/import behavior,
    exact loader search policy or device execution. Positive results must NOT
    lift the Independent Base release gate.
    """
    head = inspect_elf(binary, recipe)
    embedded = (inspect_embedded_elf(binary, head["needed_libraries"])
                if head["pyinstaller_archive_present"] else None)
    needed = (embedded["unprovided_needed_names"] if embedded
              else head["needed_libraries"])
    if any(not LIB_NAME.fullmatch(name) for name in needed):
        raise NativeAbiError("native_rootfs_unsafe_needed_name")
    if archive_format not in ("tar", "cpio"):
        raise NativeAbiError("native_rootfs_archive_format_invalid")
    # The OS authenticated archive must be exactly the same descriptor
    # inspected for ELF closure, even if its pathname is replaced mid-check.
    try:
        descriptor = os.open(
            rootfs_tar,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise NativeAbiError("native_rootfs_tar_unavailable") from exc
    try:
        with ExitStack() as stack:
            source = stack.enter_context(os.fdopen(descriptor, "rb"))
            before = os.fstat(source.fileno())
            if (not stat.S_ISREG(before.st_mode)
                    or not 0 < before.st_size <= ROOTFS_MAX_TAR_BYTES):
                raise NativeAbiError("native_rootfs_tar_unavailable")
            digest = hashlib.sha256()
            for block in iter(lambda: source.read(1 << 20), b""):
                digest.update(block)
            source.seek(0)
            entries = {}
            tar = None
            if archive_format == "cpio":
                entries = _read_cpio_libraries(source)
            else:
                tar = stack.enter_context(tarfile.open(fileobj=source, mode="r:*"))
                for count, member in enumerate(tar, 1):
                    if count > ROOTFS_MAX_ENTRIES:
                        raise NativeAbiError("native_rootfs_member_count_exceeded")
                    if member.name in (".", "./"):
                        continue
                    name = _tar_member_name(member.name)
                    if not _library_namespace(name):
                        continue
                    if name in entries:
                        raise NativeAbiError("native_rootfs_duplicate_library_path")
                    if not (member.isfile() or member.isdir() or member.issym()):
                        raise NativeAbiError("native_rootfs_unsupported_library_entry")
                    if member.isfile() and member.size > ROOTFS_MAX_LIBRARY_BYTES:
                        raise NativeAbiError("native_rootfs_library_too_large")
                    entries[name] = member

            def load_elf(name: str) -> tuple[str, dict, str] | None:
                match = _resolve_rootfs_member(name, entries)
                if match is None:
                    return None
                target, entry = match
                if isinstance(entry, _CpioMember):
                    data = entry.data
                else:
                    source = tar.extractfile(entry)
                    if source is None:
                        raise NativeAbiError("native_rootfs_library_unreadable")
                    with source:
                        data = source.read(ROOTFS_MAX_LIBRARY_BYTES + 1)
                if len(data) != entry.size or len(data) > ROOTFS_MAX_LIBRARY_BYTES:
                    raise NativeAbiError("native_rootfs_library_size_changed")
                try:
                    info = inspect_elf(data)
                except NativeAbiError as exc:
                    raise NativeAbiError("native_rootfs_library_not_valid_elf") from exc
                return target, info, hashlib.sha256(data).hexdigest()

            interpreter = head["interpreter"]
            loader = None
            if interpreter:
                if (not interpreter.startswith("/") or
                        not _library_namespace(interpreter.lstrip("/"))):
                    raise NativeAbiError("native_rootfs_interpreter_not_library_path")
                loader = load_elf(interpreter.lstrip("/"))

            checked = {}
            unresolved = set()
            queue = sorted(set(needed))
            while queue:
                name = queue.pop(0)
                if name in checked or name in unresolved:
                    continue
                if len(checked) + len(unresolved) >= ROOTFS_MAX_LIBRARIES:
                    raise NativeAbiError("native_rootfs_dependency_graph_too_large")
                matches = []
                for root in ROOTFS_LIBRARY_DIRS:
                    candidate = load_elf(root + "/" + name)
                    if candidate is not None:
                        matches.append(candidate)
                if not matches:
                    unresolved.add(name)
                    continue
                # Different files for one SONAME can yield a loader-path-
                # dependent answer. Never silently select the first match.
                if len({x[2] for x in matches}) > 1:
                    raise NativeAbiError("native_rootfs_ambiguous_library_name")
                target, info, sha = matches[0]
                checked[name] = {"target_path": target, "sha256": sha}
                for sub in info["needed_libraries"]:
                    if not LIB_NAME.fullmatch(sub):
                        raise NativeAbiError("native_rootfs_unsafe_transitive_name")
                    if sub not in checked and sub not in unresolved:
                        queue.append(sub)
            after = os.fstat(source.fileno())
            if ((before.st_dev, before.st_ino, before.st_size,
                 before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_dev, after.st_ino, after.st_size,
                 after.st_mtime_ns, after.st_ctime_ns)):
                raise NativeAbiError("native_rootfs_source_changed_during_audit")
    except (tarfile.TarError, EOFError) as exc:
        raise NativeAbiError("native_rootfs_tar_unreadable") from exc

    return {
        "schema": "ordax.native-target-rootfs-elf-preflight/1",
        "target_rootfs_tar_sha256": digest.hexdigest() if archive_format == "tar" else None,
        "target_rootfs_cpio_sha256": digest.hexdigest() if archive_format == "cpio" else None,
        "interpreter": head["interpreter"],
        "interpreter_found": loader is not None if head["interpreter"] else None,
        "required_dynamic_names": sorted(set(needed)),
        "resolved_rootfs_libraries": dict(sorted(checked.items())),
        "unresolved_rootfs_libraries": sorted(unresolved),
        "default_directory_name_resolution_complete": (
            not unresolved and (loader is not None if head["interpreter"] else True)
        ),
        "full_runtime_abi_compatible": False,
        "native_dependency_closure_proven": False,
        "device_install_authorized": False,
        "device_execution_proven": False,
        "read_only": True,
    }


def audit(binary_path: Path, recipe_path: Path, rootfs_tar: Path | None = None,
          rootfs_cpio: Path | None = None,
          expected_rootfs_sha256: str | None = None) -> dict:
    if expected_rootfs_sha256 is not None:
        if not re.fullmatch(r"[0-9a-f]{64}", expected_rootfs_sha256):
            raise NativeAbiError("native_rootfs_expected_sha256_invalid")
        if rootfs_tar is None and rootfs_cpio is None:
            raise NativeAbiError("native_rootfs_expected_sha256_requires_archive")
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
    if rootfs_tar is not None and rootfs_cpio is not None:
        raise NativeAbiError("native_rootfs_sources_mutually_exclusive")
    if rootfs_tar is not None or rootfs_cpio is not None:
        report["target_rootfs_preflight"] = inspect_rootfs_abi(
            binary, rootfs_cpio if rootfs_cpio is not None else rootfs_tar,
            recipe_path.read_text(encoding="utf-8"),
            archive_format="cpio" if rootfs_cpio is not None else "tar",
        )
        if expected_rootfs_sha256 is not None:
            hash_field = ("target_rootfs_cpio_sha256" if rootfs_cpio is not None
                          else "target_rootfs_tar_sha256")
            if report["target_rootfs_preflight"][hash_field] != expected_rootfs_sha256:
                raise NativeAbiError("native_rootfs_source_sha256_mismatch")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--recipe", type=Path, required=True)
    parser.add_argument("--require-independent", action="store_true")
    archives = parser.add_mutually_exclusive_group()
    archives.add_argument("--rootfs-tar", type=Path,
                          help="Exact OS-owned Buildroot rootfs.tar for read-only ELF preflight")
    archives.add_argument("--rootfs-cpio", type=Path,
                          help="Exact OS-owned published rootfs.cpio.gz for read-only ELF preflight")
    parser.add_argument("--expected-rootfs-sha256",
                        help="Expected SHA-256 of exact OS-verified rootfs archive; fail closed on mismatch")
    parser.add_argument("--require-rootfs-name-resolution", action="store_true",
                        help="Fail if any required rootfs loader or DT_NEEDED name is unresolved")
    args = parser.parse_args()
    try:
        result = audit(args.binary, args.recipe, args.rootfs_tar, args.rootfs_cpio,
                       args.expected_rootfs_sha256)
    except (NativeAbiError, OSError, UnicodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 2
    if args.require_rootfs_name_resolution and args.rootfs_tar is None and args.rootfs_cpio is None:
        print(json.dumps({"ok": False, "error": "rootfs_tar_required"}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    if args.require_independent and not result["independent_base_abi_ready"]:
        return 1
    if args.require_rootfs_name_resolution and not result[
        "target_rootfs_preflight"
    ]["default_directory_name_resolution_complete"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
