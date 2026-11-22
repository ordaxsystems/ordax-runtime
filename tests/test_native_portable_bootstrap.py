from __future__ import annotations

import tempfile
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "native/ordax-product-agent"))
from verify_portable_closure import verify
from audit_elf import NativeAbiError


def pretend_elf(data: bytes):
    if data.startswith(b"runtime"):
        return {"interpreter": "/lib/ld-musl-x86_64.so.1",
                "needed_libraries": ["libc.musl-x86_64.so.1", "libz.so.1"]}
    if data.startswith(b"zlib"):
        return {"interpreter": None, "needed_libraries": ["libc.musl-x86_64.so.1"]}
    return {"interpreter": None, "needed_libraries": []}


def pretend_embedded(_binary: bytes, _needed: list[str]):
    return {"native_entry_count": 90, "unprovided_needed_names": ["libc.musl-x86_64.so.1"]}


class PortableBootstrapContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.binary = root / "ordax-product-agent"
        self.binary.write_bytes(b"runtime" + b"\0" * 128)
        self.libs = root / "portable-lib"
        self.libs.mkdir()
        for name, data in (
            ("ld-musl-x86_64.so.1", b"loader" + b"\0" * 128),
            ("libc.musl-x86_64.so.1", b"loader" + b"\0" * 128),
            ("libz.so.1", b"zlib" + b"\0" * 128),
        ):
            (self.libs / name).write_bytes(data)
        self.patches = [patch("verify_portable_closure.inspect_elf", side_effect=pretend_elf),
                        patch("verify_portable_closure.inspect_embedded_elf", side_effect=pretend_embedded)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_exact_loader_and_library_bytes_have_no_install_authority(self):
        receipt = verify(self.binary, self.libs)
        self.assertEqual(receipt["schema"], "ordax.native-portable-bootstrap/1")
        self.assertTrue(receipt["bootstrap_elf_closure_verified"])
        self.assertFalse(receipt["full_runtime_dependency_closure_proven"])
        self.assertFalse(receipt["device_execution_proven"])
        self.assertFalse(receipt["device_install_authorized"])
        self.assertFalse(receipt["stable_activation_authorized"])
        self.assertEqual(len(receipt["libraries"]), 3)

    def test_changed_libc_alias_is_rejected(self):
        (self.libs / "libc.musl-x86_64.so.1").write_bytes(b"foreign" + b"\0" * 128)
        with self.assertRaisesRegex(NativeAbiError, "not_exact_loader_alias"):
            verify(self.binary, self.libs)

    def test_unexpected_library_is_rejected(self):
        (self.libs / "libssl.so.3").write_bytes(b"foreign" + b"\0" * 128)
        with self.assertRaisesRegex(NativeAbiError, "library_set_not_exact"):
            verify(self.binary, self.libs)

    def test_missing_library_is_rejected(self):
        (self.libs / "libz.so.1").unlink()
        with self.assertRaisesRegex(NativeAbiError, "library_set_not_exact"):
            verify(self.binary, self.libs)

    def test_unprovided_transitive_dependency_is_rejected(self):
        def foreign(data: bytes):
            value = pretend_elf(data)
            if data.startswith(b"zlib"):
                value["needed_libraries"] = ["libssl.so.3"]
            return value
        with patch("verify_portable_closure.inspect_elf", side_effect=foreign):
            with self.assertRaisesRegex(NativeAbiError, "unprovided_elf_names"):
                verify(self.binary, self.libs)


if __name__ == "__main__":
    unittest.main()
