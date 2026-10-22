"""Kernel-only ABI contract tests independent of binutils and Alpine."""
from __future__ import annotations

import importlib.util
import gzip
import hashlib
import io
from pathlib import Path
import stat
import struct
import tarfile
import tempfile
import unittest
import zlib

SCRIPT = Path(__file__).resolve().parents[1] / "native/ordax-product-agent/audit_elf.py"
spec = importlib.util.spec_from_file_location("ordax_native_abi", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

ALPINE_RECIPE = (
    "FROM python:3.12.12-alpine3.22@sha256:abc AS build\n"
    "RUN apk add --no-cache musl-dev zlib-dev\n"
    "FROM alpine:3.22.5\n"
)


def fake_elf(*, interpreter=None, needed=()):
    """Build a small syntactically valid ELF64 program-header graph."""
    size = 512
    buf = bytearray(size)
    ident = b"\x7fELF\x02\x01\x01" + b"\0" * 9
    count = 1 + bool(interpreter) + bool(needed)
    struct.pack_into("<16sHHIQQQIHHHHHH", buf, 0,
                     ident, 2, 62, 1, 0, 64, 0, 0, 64, 56, count, 0, 0, 0)
    struct.pack_into("<IIQQQQQQ", buf, 64,
                     1, 5, 0, 0, 0, size, size, 4096)
    index = 1
    if interpreter:
        encoded = interpreter.encode() + b"\0"
        buf[256:256 + len(encoded)] = encoded
        struct.pack_into("<IIQQQQQQ", buf, 64 + index * 56,
                         3, 4, 256, 256, 0, len(encoded), len(encoded), 1)
        index += 1
    if needed:
        table = b"\0" + b"".join(x.encode() + b"\0" for x in needed)
        buf[400:400 + len(table)] = table
        offset = 1
        entries = [(5, 400), (10, len(table))]
        for name in needed:
            entries.append((1, offset))
            offset += len(name) + 1
        entries.append((0, 0))
        for row, (tag, value) in enumerate(entries):
            struct.pack_into("<qQ", buf, 320 + row * 16, tag, value)
        struct.pack_into("<IIQQQQQQ", buf, 64 + index * 56,
                         2, 4, 320, 320, 0, len(entries) * 16, len(entries) * 16, 8)
    return bytes(buf)


def fake_carchive(*, name="libsample.so", payload=None, compression=1, kind=b"b",
                  suffix=b""):
    """Append one authentic PyInstaller CArchive-formatted TOC and cookie."""
    module = fake_elf() if payload is None else payload
    body = zlib.compress(module) if compression else module
    encoded = name.encode("utf-8") + b"\0"
    record = struct.pack("!iIIIBc", 18 + len(encoded), 0, len(body),
                         len(module), compression, kind) + encoded
    cookie = struct.pack("!8sIIII64s", module_abi.CARCHIVE_MAGIC,
                         len(body) + len(record) + 88,
                         len(body), len(record), 312, b"libpython3.12.so.1.0")
    return fake_elf() + body + record + cookie + suffix


# The same canonical parser is used for the bootloader and frozen ELF members.
module_abi = module


def make_buildroot_rootfs(entries):
    """Build small target rootfs.tar with regular library files and links."""
    temp = tempfile.TemporaryDirectory()
    path = Path(temp.name) / "rootfs.tar"
    with tarfile.open(path, "w") as archive:
        root = tarfile.TarInfo("./")
        root.type = tarfile.DIRTYPE
        archive.addfile(root)
        for name, body in entries:
            member = tarfile.TarInfo(name)
            if isinstance(body, str):
                member.type = tarfile.SYMTYPE
                member.linkname = body
                archive.addfile(member)
            else:
                member.size = len(body)
                member.mode = 0o755
                archive.addfile(member, io.BytesIO(body))
    return temp, path


def make_buildroot_cpio(entries):
    """Canonical newc stream with 4-byte alignment, compressed as OS rootfs."""
    temporary = tempfile.TemporaryDirectory()
    path = Path(temporary.name) / "rootfs.cpio.gz"
    with gzip.open(path, "wb", mtime=None) as archive:
        def record(name, payload, mode):
            raw_name = name.encode() + b"\0"
            fields = [
                1, mode, 0, 0, 1, 0, len(payload),
                0, 0, 0, 0, len(raw_name), 0,
            ]
            archive.write(b"070701" + b"".join(f"{v:08x}".encode() for v in fields))
            archive.write(raw_name)
            archive.write(b"\0" * (-(110 + len(raw_name)) % 4))
            archive.write(payload)
            archive.write(b"\0" * (-len(payload) % 4))
        record(".", b"", stat.S_IFDIR | 0o755)
        for name, value in entries:
            mode = stat.S_IFLNK | 0o777 if isinstance(value, str) else stat.S_IFREG | 0o755
            body = value.encode() if isinstance(value, str) else value
            record(name, body, mode)
        record("TRAILER!!!", b"", 0)
    return temporary, path


class IndependentNativeAbiTests(unittest.TestCase):
    def test_current_alpine_musl_executable_cannot_claim_independence(self):
        result = module.inspect_elf(
            fake_elf(interpreter="/lib/ld-musl-x86_64.so.1",
                     needed=("libz.so.1", "libc.musl-x86_64.so.1")),
            ALPINE_RECIPE,
        )
        self.assertFalse(result["independent_base_abi_ready"])
        self.assertEqual(result["interpreter"], "/lib/ld-musl-x86_64.so.1")
        self.assertEqual(result["needed_libraries"],
                         ["libc.musl-x86_64.so.1", "libz.so.1"])
        self.assertEqual(result["blockers"], [
            "external-dynamic-elf-interpreter",
            "external-shared-libraries",
            "alpine-build-provenance",
        ])
        self.assertFalse(result["installation_authorized"])
        self.assertFalse(result["device_execution_proven"])

    def test_static_non_pyinstaller_executable_can_pass_abi_preflight_only(self):
        result = module.inspect_elf(fake_elf(), "FROM scratch\n")
        self.assertTrue(result["independent_base_abi_ready"])
        self.assertEqual(result["blockers"], [])
        self.assertFalse(result["installation_authorized"])
        self.assertFalse(result["device_execution_proven"])

    def test_pyinstaller_onefile_requires_shared_object_closure_proof(self):
        artifact = bytearray(fake_elf())
        artifact[-24:-16] = b"MEI\x0c\x0b\x0a\x0b\x0e"
        result = module.inspect_elf(bytes(artifact), "FROM scratch\n")
        self.assertFalse(result["independent_base_abi_ready"])
        self.assertEqual(result["blockers"], [
            "embedded-python-runtime-closure-unverified"
        ])

    def test_pyinstaller_cookie_may_be_farther_than_last_512_bytes(self):
        artifact = bytearray(fake_elf())
        artifact.extend(b"\0" * 2048)
        artifact[-1800:-1792] = b"MEI\x0c\x0b\x0a\x0b\x0e"
        result = module.inspect_elf(bytes(artifact), "FROM scratch\n")
        self.assertFalse(result["independent_base_abi_ready"])
        self.assertIn("embedded-python-runtime-closure-unverified", result["blockers"])

    def test_recipe_declaring_pyinstaller_requires_closure_verification(self):
        result = module.inspect_elf(fake_elf(), "FROM scratch\nRUN python -m PyInstaller app.py\n")
        self.assertFalse(result["independent_base_abi_ready"])
        self.assertIn("embedded-python-runtime-closure-unverified", result["blockers"])

    def test_corrupt_program_header_must_never_count_as_static(self):
        artifact = bytearray(fake_elf())
        struct.pack_into("<H", artifact, 54, 55)
        with self.assertRaisesRegex(module.NativeAbiError, "program_headers"):
            module.inspect_elf(bytes(artifact))

    def test_unsafe_needed_string_mapping_fails_closed(self):
        artifact = bytearray(fake_elf(needed=("libz.so.1",)))
        struct.pack_into("<Q", artifact, 320 + 8, 100000)
        with self.assertRaisesRegex(module.NativeAbiError, "dynamic_strings_unmapped"):
            module.inspect_elf(bytes(artifact))

    def test_dynamic_interpreter_from_non_alpine_is_still_dependency(self):
        result = module.inspect_elf(fake_elf(interpreter="/lib64/ld-linux-x86-64.so.2"),
                                   "FROM debian:bookworm\n")
        self.assertFalse(result["independent_base_abi_ready"])
        self.assertFalse(result["alpine_build_provenance"])
        self.assertEqual(result["blockers"], ["external-dynamic-elf-interpreter"])

    def test_actual_pyinstaller_archive_entries_have_bound_inventory(self):
        result = module.inspect_embedded_elf(
            fake_carchive(payload=fake_elf(needed=("libz.so.1",)),
                          suffix=b"appended-unsigned-bytes"),
            outer_needed=("libc.musl-x86_64.so.1",),
        )
        self.assertEqual(result["native_entry_count"], 1)
        self.assertEqual(result["embedded_python_version"], "3.12")
        self.assertEqual(result["embedded_python_library"], "libpython3.12.so.1.0")
        self.assertEqual(result["needed_libraries"],
                         ["libc.musl-x86_64.so.1", "libz.so.1"])
        self.assertEqual(result["unprovided_needed_names"],
                         ["libc.musl-x86_64.so.1", "libz.so.1"])
        self.assertEqual(len(result["inventory_sha256"]), 64)
        self.assertFalse(result["independent_base_compatibility_proven"])
        self.assertFalse(result["install_authorized"])

    def test_carchive_python_version_and_library_must_agree(self):
        candidate = bytearray(fake_carchive())
        marker = candidate.rfind(module.CARCHIVE_MAGIC)
        struct.pack_into("!I", candidate, marker + 20, 313)
        with self.assertRaisesRegex(module.NativeAbiError, "python_abi_invalid"):
            module.inspect_embedded_elf(bytes(candidate))

    def test_carchive_python_library_must_be_bounded_and_unambiguous(self):
        candidate = bytearray(fake_carchive())
        marker = candidate.rfind(module.CARCHIVE_MAGIC)
        candidate[marker + 24:marker + 88] = b"libpython3.12.so.1.0" + b"\0" * 43 + b"x"
        with self.assertRaisesRegex(module.NativeAbiError, "python_abi_invalid"):
            module.inspect_embedded_elf(bytes(candidate))

    def test_carchive_python_metadata_is_reported_from_real_cookie(self):
        candidate = bytearray(fake_carchive())
        marker = candidate.rfind(module.CARCHIVE_MAGIC)
        struct.pack_into("!I", candidate, marker + 20, 313)
        candidate[marker + 24:marker + 88] = (
            b"libpython3.13.so.1.0".ljust(64, b"\0")
        )
        result = module.inspect_embedded_elf(bytes(candidate))
        self.assertEqual(result["embedded_python_version"], "3.13")
        self.assertEqual(result["embedded_python_library"], "libpython3.13.so.1.0")
        self.assertFalse(result["independent_base_compatibility_proven"])

    def test_embedded_elf_inventory_rejects_missing_cookie(self):
        with self.assertRaisesRegex(module.NativeAbiError, "cookie_missing"):
            module.inspect_embedded_elf(fake_elf())

    def test_embedded_elf_inventory_rejects_unsafe_native_name(self):
        with self.assertRaisesRegex(module.NativeAbiError, "name_invalid"):
            module.inspect_embedded_elf(fake_carchive(name="../libz.so"))

    def test_embedded_elf_inventory_rejects_non_elf_native_member(self):
        with self.assertRaisesRegex(module.NativeAbiError, "non_elf_binary"):
            module.inspect_embedded_elf(fake_carchive(payload=b"x" * 512))

    def test_embedded_elf_inventory_rejects_incorrect_toc_size(self):
        candidate = bytearray(fake_carchive())
        cookie = candidate.rfind(module.CARCHIVE_MAGIC)
        struct.pack_into("!I", candidate, cookie + 16, 1)
        with self.assertRaisesRegex(module.NativeAbiError, "layout_invalid"):
            module.inspect_embedded_elf(bytes(candidate))

    def test_embedded_elf_inventory_rejects_wrong_member_type(self):
        with self.assertRaisesRegex(module.NativeAbiError, "misclassified"):
            module.inspect_embedded_elf(fake_carchive(kind=b"x"))

    def test_real_os_cpio_format_uses_the_same_elf_dependency_auditor(self):
        temp, rootfs = make_buildroot_cpio([
            ("lib/ld-musl-x86_64.so.1", "../usr/lib/libc.musl-x86_64.so.1"),
            ("usr/lib/libc.musl-x86_64.so.1", fake_elf()),
            ("usr/lib/libz.so.1", fake_elf(needed=("libc.musl-x86_64.so.1",))),
            ("usr/bin/ignored-by-elf-auditor", b"not-an-ELF"),
        ])
        self.addCleanup(temp.cleanup)
        candidate = fake_elf(
            interpreter="/lib/ld-musl-x86_64.so.1",
            needed=("libz.so.1", "libc.musl-x86_64.so.1"),
        )
        proof = module.inspect_rootfs_abi(candidate, rootfs, archive_format="cpio")
        self.assertEqual(proof["target_rootfs_cpio_sha256"],
                         hashlib.sha256(rootfs.read_bytes()).hexdigest())
        self.assertIsNone(proof["target_rootfs_tar_sha256"])
        self.assertTrue(proof["interpreter_found"])
        self.assertEqual(proof["unresolved_rootfs_libraries"], [])
        self.assertTrue(proof["default_directory_name_resolution_complete"])
        self.assertEqual(proof["resolved_rootfs_libraries"]["libz.so.1"]["target_path"],
                         "usr/lib/libz.so.1")
        self.assertFalse(proof["full_runtime_abi_compatible"])
        self.assertFalse(proof["device_install_authorized"])

    def test_real_os_cpio_without_dynamic_loader_remains_fail_closed(self):
        temp, rootfs = make_buildroot_cpio([
            ("usr/bin/isolated-static-tool", fake_elf()),
            ("lib/not-a-dynamic-linker", fake_elf()),
        ])
        self.addCleanup(temp.cleanup)
        proof = module.inspect_rootfs_abi(
            fake_elf(interpreter="/lib/ld-musl-x86_64.so.1",
                     needed=("libz.so.1",)),
            rootfs, archive_format="cpio",
        )
        self.assertFalse(proof["interpreter_found"])
        self.assertEqual(proof["unresolved_rootfs_libraries"], ["libz.so.1"])
        self.assertFalse(proof["default_directory_name_resolution_complete"])

    def test_cpio_duplicate_library_and_truncated_payload_rejected(self):
        temp, rootfs = make_buildroot_cpio([
            ("./lib/libz.so.1", fake_elf()),
            ("lib/libz.so.1", fake_elf()),
        ])
        self.addCleanup(temp.cleanup)
        with self.assertRaisesRegex(module.NativeAbiError, "duplicate_library_path"):
            module.inspect_rootfs_abi(
                fake_elf(needed=("libz.so.1",)), rootfs, archive_format="cpio"
            )
        with open(rootfs, "rb") as stream:
            data = stream.read()
        rootfs.write_bytes(data[:max(10, len(data)//2)])
        with self.assertRaises((EOFError, OSError, module.NativeAbiError)):
            module.inspect_rootfs_abi(
                fake_elf(needed=("libz.so.1",)), rootfs, archive_format="cpio"
            )

    def test_cpio_library_symlink_escape_and_cycle_rejected(self):
        fixtures = [
            ([("lib/libz.so.1", "../../../outside.so")], "symlink_escape"),
            ([("lib/libz.so.1", "loop.so"),
              ("lib/loop.so", "libz.so.1")], "symlink_cycle"),
        ]
        for entries, diagnostic in fixtures:
            with self.subTest(diagnostic=diagnostic):
                temp, rootfs = make_buildroot_cpio(entries)
                self.addCleanup(temp.cleanup)
                with self.assertRaisesRegex(module.NativeAbiError, diagnostic):
                    module.inspect_rootfs_abi(
                        fake_elf(needed=("libz.so.1",)), rootfs,
                        archive_format="cpio",
                    )

    def test_buildroot_rootfs_resolves_real_loader_and_transitive_libraries(self):
        # The dynamic loader and libz are actually in this exact rootfs, but
        # pathname resolution alone cannot certify Python's frozen ABI.
        temp, rootfs = make_buildroot_rootfs([
            ("usr/lib/libc.musl-x86_64.so.1", fake_elf()),
            ("usr/lib/libz.so.1", fake_elf(needed=("libc.musl-x86_64.so.1",))),
            ("lib/ld-musl-x86_64.so.1", "../usr/lib/libc.musl-x86_64.so.1"),
            ("lib/libz.so.1", "../usr/lib/libz.so.1"),
        ])
        self.addCleanup(temp.cleanup)
        binary = fake_elf(
            interpreter="/lib/ld-musl-x86_64.so.1",
            needed=("libz.so.1", "libc.musl-x86_64.so.1"),
        )
        report = module.inspect_rootfs_abi(binary, rootfs)
        self.assertTrue(report["interpreter_found"])
        self.assertEqual(report["unresolved_rootfs_libraries"], [])
        self.assertTrue(report["default_directory_name_resolution_complete"])
        self.assertEqual(
            report["resolved_rootfs_libraries"]["libz.so.1"]["target_path"],
            "usr/lib/libz.so.1",
        )
        self.assertEqual(
            report["target_rootfs_tar_sha256"],
            hashlib.sha256(rootfs.read_bytes()).hexdigest(),
        )
        self.assertFalse(report["native_dependency_closure_proven"])
        self.assertFalse(report["full_runtime_abi_compatible"])
        self.assertFalse(report["device_install_authorized"])

    def test_static_buildroot_rootfs_cannot_satisfy_dynamic_candidate(self):
        temp, rootfs = make_buildroot_rootfs([
            ("lib/libstandalone.a", b"static-archive"),
        ])
        self.addCleanup(temp.cleanup)
        report = module.inspect_rootfs_abi(
            fake_elf(
                interpreter="/lib/ld-musl-x86_64.so.1",
                needed=("libz.so.1",),
            ), rootfs,
        )
        self.assertFalse(report["interpreter_found"])
        self.assertEqual(report["unresolved_rootfs_libraries"], ["libz.so.1"])
        self.assertFalse(report["default_directory_name_resolution_complete"])

    def test_rootfs_symlink_may_not_escape_its_namespace(self):
        temp, rootfs = make_buildroot_rootfs([
            ("lib/libz.so.1", "../../../outside.so"),
        ])
        self.addCleanup(temp.cleanup)
        with self.assertRaisesRegex(module.NativeAbiError, "symlink_escape"):
            module.inspect_rootfs_abi(fake_elf(needed=("libz.so.1",)), rootfs)

    def test_rootfs_symlink_cycle_fails_closed(self):
        temp, rootfs = make_buildroot_rootfs([
            ("lib/libz.so.1", "libother.so"),
            ("lib/libother.so", "libz.so.1"),
        ])
        self.addCleanup(temp.cleanup)
        with self.assertRaisesRegex(module.NativeAbiError, "symlink_cycle"):
            module.inspect_rootfs_abi(fake_elf(needed=("libz.so.1",)), rootfs)

    def test_duplicate_canonical_tar_library_name_is_rejected(self):
        temp, rootfs = make_buildroot_rootfs([
            ("./lib/libz.so.1", fake_elf()),
            ("lib/libz.so.1", fake_elf()),
        ])
        self.addCleanup(temp.cleanup)
        with self.assertRaisesRegex(module.NativeAbiError, "duplicate_library_path"):
            module.inspect_rootfs_abi(fake_elf(needed=("libz.so.1",)), rootfs)

    def test_rootfs_matching_non_elf_library_is_not_mistaken_for_abi(self):
        temp, rootfs = make_buildroot_rootfs([
            ("lib/libz.so.1", b"not-an-elf"),
        ])
        self.addCleanup(temp.cleanup)
        with self.assertRaisesRegex(module.NativeAbiError, "library_not_valid_elf"):
            module.inspect_rootfs_abi(fake_elf(needed=("libz.so.1",)), rootfs)

    def test_ambiguous_library_names_cannot_implicitly_pick_one_loader_path(self):
        temp, rootfs = make_buildroot_rootfs([
            ("lib/libz.so.1", fake_elf()),
            ("usr/lib/libz.so.1", fake_elf(needed=("libc.so",))),
        ])
        self.addCleanup(temp.cleanup)
        with self.assertRaisesRegex(module.NativeAbiError, "ambiguous_library_name"):
            module.inspect_rootfs_abi(fake_elf(needed=("libz.so.1",)), rootfs)

    def test_wrong_architecture_is_not_independent(self):
        artifact = bytearray(fake_elf())
        struct.pack_into("<H", artifact, 18, 183)
        with self.assertRaisesRegex(module.NativeAbiError, "architecture"):
            module.inspect_elf(bytes(artifact))


if __name__ == "__main__":
    unittest.main()
