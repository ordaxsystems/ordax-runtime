"""Read-only Runtime consumption of the OS-owned private Buildroot target TAR."""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools.ci.audit_independent_python_proof import (
    ProofError, verify_private_target_tar,
)

PYTHON = "usr/bin/python3.14"
NAME = "native-python-build-input.rootfs.tar"


class NativePrivateBuildrootTargetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.target = self.folder / NAME
        self.binary = b"\x7fELF" + b"verified private CPython" * 4
        self.abi = {
            "python_executable": PYTHON,
            "python_sha256": hashlib.sha256(self.binary).hexdigest(),
        }
        self.write_target()
        self.receipt = {
            "python_abi": dict(self.abi),
            "private_target_build_input": self.provenance(),
        }

    def write_target(self, *, add_loader=True, duplicate_python=False):
        with tarfile.open(self.target, "w") as tar:
            entries = [
                (PYTHON, self.binary),
                ("etc/ssl/certs/ca-certificates.crt", b"private CAs"),
            ]
            if add_loader:
                entries.append(("lib/ld-musl-x86_64.so.1", b"musl loader"))
            if duplicate_python:
                entries.append(("./usr/bin/python3.14", b"untrusted duplicate"))
            for name, payload in entries:
                item = tarfile.TarInfo(name)
                item.size = len(payload)
                tar.addfile(item, io.BytesIO(payload))

    def provenance(self):
        return {
            "schema": "ordax.private-buildroot-python-build-input/1",
            "file_name": NAME,
            "sha256": hashlib.sha256(self.target.read_bytes()).hexdigest(),
            "size_bytes": self.target.stat().st_size,
            "python_sha256": self.abi["python_sha256"],
            "purpose": "native-runtime-build-input-only",
            "runtime_agent_packaged": False,
            "device_install_authorized": False,
        }

    def test_real_target_tar_is_tied_to_private_python_and_noninstallable_receipt(self):
        report = verify_private_target_tar(self.target, self.receipt)
        self.assertTrue(report["os_private_build_input_verified"])
        self.assertEqual(report["python_elf_sha256"], self.abi["python_sha256"])
        self.assertEqual(report["private_target_tar_sha256"], self.provenance()["sha256"])
        self.assertFalse(report["runtime_agent_packaged"])
        self.assertFalse(report["device_install_authorized"])

    def test_mismatch_between_claimed_archive_and_bytes_is_rejected(self):
        changed = {
            **self.receipt,
            "private_target_build_input": {
                **self.provenance(), "sha256": "0" * 64,
            },
        }
        with self.assertRaisesRegex(ProofError, "does not match OS-owned"):
            verify_private_target_tar(self.target, changed)

    def test_python_executable_sha_mismatch_is_rejected(self):
        changed = {
            **self.receipt,
            "python_abi": {**self.abi, "python_sha256": "0" * 64},
        }
        with self.assertRaisesRegex(ProofError, "Python bytes do not match"):
            verify_private_target_tar(self.target, changed)

    def test_missing_loader_and_duplicate_entry_rejected(self):
        self.write_target(add_loader=False)
        self.receipt["private_target_build_input"] = self.provenance()
        with self.assertRaisesRegex(ProofError, "lacks interpreter/loader"):
            verify_private_target_tar(self.target, self.receipt)
        self.write_target(duplicate_python=True)
        self.receipt["private_target_build_input"] = self.provenance()
        with self.assertRaisesRegex(ProofError, "duplicate required entry"):
            verify_private_target_tar(self.target, self.receipt)

    def test_symlinked_target_is_rejected(self):
        original = self.folder / "original.tar"
        self.target.rename(original)
        self.target.symlink_to(original)
        with self.assertRaisesRegex(ProofError, "unavailable or unsafe"):
            verify_private_target_tar(self.target, self.receipt)

    def test_replace_archive_between_digest_and_parse_fails_closed(self):
        intruder = self.folder / "intruder.tar"
        intruder.write_bytes(b"untrusted")
        real_open = tarfile.open
        def swap(*args, **kwargs):
            os.replace(intruder, self.target)
            return real_open(*args, **kwargs)
        with patch("tarfile.open", side_effect=swap):
            with self.assertRaisesRegex(ProofError, "changed during audit"):
                verify_private_target_tar(self.target, self.receipt)
        self.assertEqual(self.target.read_bytes(), b"untrusted")

    def test_without_os_issued_provenance_fails_closed(self):
        for receipt in ({}, {"python_abi": self.abi}, {"private_target_build_input": self.provenance()}):
            with self.subTest(receipt=receipt), self.assertRaisesRegex(ProofError, "no OS-issued"):
                verify_private_target_tar(self.target, receipt)

    def test_strict_cli_does_not_treat_missing_archive_as_success(self):
        process = subprocess.run(
            [sys.executable, "tools/ci/audit_independent_python_proof.py",
             "--receipt", str(self.folder / "absent.json"),
             "--os-source", str(self.folder / "absent-source.json"),
             "--require-private-target"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertIn("private Buildroot target TAR required", process.stdout)


if __name__ == "__main__":
    unittest.main()
