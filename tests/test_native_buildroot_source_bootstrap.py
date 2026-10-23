"""Offline integration gates for the OS-private Python bootstrap probe."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.ci import probe_native_buildroot_agent as target


@unittest.skipUnless(sys.platform.startswith("linux"), "private musl bootstrap requires Linux")
class NativeBuildrootSourceBootstrapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "root"
        self.root.mkdir()
        (self.root / "usr/bin").mkdir(parents=True)
        (self.root / "usr/lib/python3.14").mkdir(parents=True)
        (self.root / "lib").mkdir()
        self.binary = self.root / "usr/bin/python3.14"
        self.binary.write_bytes(b"private-buildroot-python")
        (self.root / "lib/libc.so").write_bytes(b"musl")
        (self.root / "lib/ld-musl-x86_64.so.1").symlink_to("libc.so")
        self.tar = self.base / "native-python-build-input.rootfs.tar"
        self.tar.write_bytes(b"verified-by-canonical-auditor")
        self.receipt = self.base / "native-python-provenance.json"
        self.python_sha = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.receipt.write_text(json.dumps({
            "python_abi": {"python_executable": "usr/bin/python3.14",
                           "python_sha256": self.python_sha}
        }), encoding="utf-8")
        self.verification = patch.object(target, "verify_private_target_tar",
                                         return_value={"python_elf_sha256": self.python_sha})
        self.verification.start()
        self.addCleanup(self.verification.stop)

    @staticmethod
    def completed(*, exit_code=0, proof=None, error=""):
        if proof is None:
            proof = {
                "offline_actions_proven": True,
                "product_httpx_client_constructed": True,
                "registered_action_count": 110,
                "network_used": False,
                "device_credential_used": False,
            }
        return subprocess.CompletedProcess(
            ["native"], exit_code,
            target.MARKER + json.dumps(proof) + "\n" if exit_code == 0 else "",
            error,
        )

    def test_success_is_only_source_bootstrap_not_installed_native_runtime(self):
        with patch.object(target.subprocess, "run", return_value=self.completed()) as run:
            report = target.probe(self.receipt, self.tar, self.root)
        self.assertTrue(report["offline_bootstrap_proven"])
        self.assertFalse(report["native_runtime_agent_packaged"])
        self.assertFalse(report["full_native_runtime_dependency_closure_proven"])
        self.assertFalse(report["device_install_authorized"])
        self.assertFalse(report["device_execution_proven"])
        args, kwargs = run.call_args
        self.assertEqual(args[0][0], str(self.root / "lib/libc.so"))
        self.assertIn("-I", args[0])
        self.assertIn("socket.socket.connect = network_forbidden", args[0][5])
        self.assertNotIn("ORDAX_DEVICE_TOKEN", kwargs["env"])
        self.assertEqual(kwargs["env"]["PYTHONHOME"], str(self.root / "usr"))
        self.assertEqual(kwargs["timeout"], 60)

    def test_missing_import_is_reported_without_leaking_traceback(self):
        result = self.completed(exit_code=1, error=(
            "Traceback (most recent call last):\n"
            "ModuleNotFoundError: No module named 'pydantic_core'\n"
        ))
        with patch.object(target.subprocess, "run", return_value=result):
            report = target.probe(self.receipt, self.tar, self.root)
        self.assertFalse(report["offline_bootstrap_proven"])
        self.assertEqual(report["failure"], "native_missing_import")
        self.assertEqual(report["missing_module"], "pydantic_core")
        self.assertNotIn("Traceback", json.dumps(report))

    def test_mismatched_extracted_interpreter_is_rejected_before_execution(self):
        self.binary.write_bytes(b"another-executable")
        with patch.object(target.subprocess, "run") as run:
            with self.assertRaisesRegex(target.ProofError, "differs from verified TAR"):
                target.probe(self.receipt, self.tar, self.root)
        run.assert_not_called()

    def test_symlinked_extraction_root_is_rejected(self):
        alias = self.base / "root-link"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(target.ProofError, "extraction root missing/unsafe"):
            target.probe(self.receipt, self.tar, alias)

    def test_subprocess_success_without_offline_proof_remains_failure(self):
        with patch.object(target.subprocess, "run", return_value=self.completed(proof={
            "offline_actions_proven": True,
            "product_httpx_client_constructed": True,
            "registered_action_count": 110,
            "network_used": True,
            "device_credential_used": False,
        })):
            report = target.probe(self.receipt, self.tar, self.root)
        self.assertEqual(report["failure"], "native_bootstrap_proof_invalid")
        self.assertFalse(report["device_install_authorized"])


if __name__ == "__main__":
    unittest.main()
