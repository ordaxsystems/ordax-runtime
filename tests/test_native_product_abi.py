"""Kernel-only ABI contract tests independent of binutils and Alpine."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import struct
import unittest

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
        artifact.extend(b"\\0" * 2048)
        artifact[-1800:-1792] = b"MEI\\x0c\\x0b\\x0a\\x0b\\x0e"
        result = module.inspect_elf(bytes(artifact), "FROM scratch\\n")
        self.assertFalse(result["independent_base_abi_ready"])
        self.assertIn("embedded-python-runtime-closure-unverified", result["blockers"])

    def test_recipe_declaring_pyinstaller_requires_closure_verification(self):
        result = module.inspect_elf(fake_elf(), "FROM scratch\\nRUN python -m PyInstaller app.py\\n")
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

    def test_wrong_architecture_is_not_independent(self):
        artifact = bytearray(fake_elf())
        struct.pack_into("<H", artifact, 18, 183)
        with self.assertRaisesRegex(module.NativeAbiError, "architecture"):
            module.inspect_elf(bytes(artifact))


if __name__ == "__main__":
    unittest.main()
