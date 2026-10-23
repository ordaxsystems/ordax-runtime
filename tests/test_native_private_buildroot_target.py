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
    ProofError, SDK_ARCHIVE_NAME, SDK_PREFIX,
    verify_private_target_tar, verify_private_sdk_tar,
)

PYTHON = "usr/bin/python3.14"
NAME = "native-python-build-input.rootfs.tar"
DIST_METADATA = "usr/lib/python3.14/site-packages/httpx-0.28.1.dist-info/METADATA"
DIST_BODY = b"Metadata-Version: 2.4\nName: httpx\nVersion: 0.28.1\n\n"


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
            "httpx_buildroot_source_versions": {"httpx": "0.28.1"},
            "private_target_build_input": self.provenance(),
        }

    def write_target(self, *, add_loader=True, duplicate_python=False,
                     add_metadata=True, duplicate_metadata=False,
                     metadata_body=DIST_BODY):
        with tarfile.open(self.target, "w") as tar:
            entries = [
                (PYTHON, self.binary),
                ("etc/ssl/certs/ca-certificates.crt", b"private CAs"),
            ]
            if add_metadata:
                entries.append((DIST_METADATA, metadata_body))
            if duplicate_metadata:
                entries.append(("./" + DIST_METADATA, metadata_body))
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
        self.assertEqual(report["source_claimed_package_metadata_verified"], 1)
        self.assertFalse(report["full_native_runtime_dependency_closure_proven"])

    def test_missing_claimed_distribution_metadata_rejected_even_with_matching_archive_digest(self):
        self.write_target(add_metadata=False)
        self.receipt["private_target_build_input"] = self.provenance()
        with self.assertRaisesRegex(ProofError, "lacks interpreter/loader/certificates"):
            verify_private_target_tar(self.target, self.receipt)

    def test_untrusted_distribution_version_rejected_even_with_matching_archive_digest(self):
        self.write_target(metadata_body=b"Metadata-Version: 2.4\nName: httpx\nVersion: 9.9.9\n\n")
        self.receipt["private_target_build_input"] = self.provenance()
        with self.assertRaisesRegex(ProofError, "metadata disagrees"):
            verify_private_target_tar(self.target, self.receipt)

    def test_duplicate_distribution_metadata_rejected(self):
        self.write_target(duplicate_metadata=True)
        self.receipt["private_target_build_input"] = self.provenance()
        with self.assertRaisesRegex(ProofError, "duplicate required entry"):
            verify_private_target_tar(self.target, self.receipt)

    def test_unjustified_package_claim_rejected(self):
        self.receipt["httpx_buildroot_source_versions"] = {
            "httpx": "0.28.1", "anyio": "4.13.0",
        }
        with self.assertRaisesRegex(ProofError, "lacks interpreter/loader/certificates"):
            verify_private_target_tar(self.target, self.receipt)

    def test_invalid_package_receipt_name_rejected(self):
        self.receipt["httpx_buildroot_source_versions"] = {"../httpx": "0.28.1"}
        with self.assertRaisesRegex(ProofError, "package receipt invalid"):
            verify_private_target_tar(self.target, self.receipt)

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

    def write_sdk(self, *, compiler=True, header=True, duplicate=False,
                  unsafe_path=False):
        archive = self.folder / SDK_ARCHIVE_NAME
        names = [
            SDK_PREFIX + "/relocate-sdk.sh",
            SDK_PREFIX + "/share/buildroot/sdk-location",
        ]
        if compiler:
            names.append(SDK_PREFIX + "/bin/x86_64-buildroot-linux-musl-gcc")
        if header:
            names.append(SDK_PREFIX
                         + "/x86_64-buildroot-linux-musl/sysroot/usr/include/stdio.h")
        if duplicate:
            names.append("./" + SDK_PREFIX + "/relocate-sdk.sh")
        if unsafe_path:
            names.append("../" + SDK_PREFIX + "/bin/intruder")
        with tarfile.open(archive, "w:gz") as tar:
            for name in names:
                payload = b"OS-owned same-build private SDK proof bytes"
                item = tarfile.TarInfo(name)
                item.size = len(payload)
                tar.addfile(item, io.BytesIO(payload))
        return archive

    def sdk_receipt(self, archive):
        return {
            "schema": "ordax.private-buildroot-runtime-sdk-input/1",
            "file_name": SDK_ARCHIVE_NAME,
            "sdk_prefix": SDK_PREFIX,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "size_bytes": archive.stat().st_size,
            "python_sha256": self.abi["python_sha256"],
            "purpose": "native-runtime-cross-compile-input-only",
            "runtime_agent_packaged": False,
            "device_install_authorized": False,
        }

    def test_private_sdk_verifies_same_os_target_python_and_never_authorizes_install(self):
        archive = self.write_sdk()
        self.receipt["private_sdk_build_input"] = self.sdk_receipt(archive)
        result = verify_private_sdk_tar(archive, self.receipt)
        self.assertTrue(result["same_build_private_sdk_verified"])
        self.assertEqual(result["python_elf_sha256"], self.abi["python_sha256"])
        self.assertEqual(result["private_sdk_tar_sha256"],
                         self.receipt["private_sdk_build_input"]["sha256"])
        self.assertFalse(result["native_dependency_closure_proven"])
        self.assertFalse(result["runtime_agent_packaged"])
        self.assertFalse(result["device_install_authorized"])

    def test_private_sdk_rejects_changed_receipt_even_if_target_is_valid(self):
        archive = self.write_sdk()
        proof = self.sdk_receipt(archive)
        self.receipt["private_sdk_build_input"] = {**proof, "sha256": "0" * 64}
        with self.assertRaisesRegex(ProofError, "does not match OS-owned"):
            verify_private_sdk_tar(archive, self.receipt)

    def test_private_sdk_rejects_other_python_even_with_matching_archive_bytes(self):
        archive = self.write_sdk()
        self.receipt["private_sdk_build_input"] = {
            **self.sdk_receipt(archive), "python_sha256": "f" * 64,
        }
        with self.assertRaisesRegex(ProofError, "Python identity mismatch"):
            verify_private_sdk_tar(archive, self.receipt)

    def test_private_sdk_rejects_missing_compiler_or_sysroot(self):
        for missing in ({"compiler": False}, {"header": False}):
            with self.subTest(missing=missing):
                archive = self.write_sdk(**missing)
                self.receipt["private_sdk_build_input"] = self.sdk_receipt(archive)
                with self.assertRaisesRegex(ProofError, "compiler/sysroot/relocation"):
                    verify_private_sdk_tar(archive, self.receipt)

    def test_private_sdk_rejects_duplicate_required_path_and_tar_traversal(self):
        for changed, reason in (
            ({"duplicate": True}, "duplicate required"),
            ({"unsafe_path": True}, "path is noncanonical"),
        ):
            with self.subTest(changed=changed):
                archive = self.write_sdk(**changed)
                self.receipt["private_sdk_build_input"] = self.sdk_receipt(archive)
                with self.assertRaisesRegex(ProofError, reason):
                    verify_private_sdk_tar(archive, self.receipt)

    def test_private_sdk_symlink_and_missing_target_receipt_fail_closed(self):
        archive = self.write_sdk()
        self.receipt["private_sdk_build_input"] = self.sdk_receipt(archive)
        self.receipt.pop("private_target_build_input")
        with self.assertRaisesRegex(ProofError, "OS-issued target and SDK"):
            verify_private_sdk_tar(archive, self.receipt)
        self.receipt["private_target_build_input"] = self.provenance()
        link = self.folder / "untrusted-sdk.tar.gz"
        link.symlink_to(archive)
        with self.assertRaisesRegex(ProofError, "missing or unsafe"):
            verify_private_sdk_tar(link, self.receipt)

    def test_private_sdk_cli_requires_exact_target_and_sdk_pair(self):
        for args, expected in (
            (["--require-private-sdk"], "private Buildroot SDK TAR required"),
            (["--private-sdk-tar", "absent.tar.gz"],
             "private SDK requires verified same-build target TAR"),
        ):
            with self.subTest(args=args):
                process = subprocess.run([
                    sys.executable, "-m", "tools.ci.audit_independent_python_proof",
                    "--receipt", str(self.folder / "absent.json"),
                    "--os-source", str(self.folder / "absent-source.json"),
                    *args,
                ], capture_output=True, text=True, timeout=15)
                self.assertEqual(process.returncode, 2, process.stderr)
                self.assertIn(expected, process.stdout)

    def test_strict_module_cli_does_not_treat_missing_archive_as_success(self):
        process = subprocess.run(
            [sys.executable, "-m", "tools.ci.audit_independent_python_proof",
             "--receipt", str(self.folder / "absent.json"),
             "--os-source", str(self.folder / "absent-source.json"),
             "--require-private-target"],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(process.returncode, 2, process.stderr)
        self.assertIn("private Buildroot target TAR required", process.stdout)

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
