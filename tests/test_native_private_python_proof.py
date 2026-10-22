"""The Buildroot private Python proof cannot silently bless the Runtime lock."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/ci/audit_independent_python_proof.py"
spec = importlib.util.spec_from_file_location("ordax_private_python_lock", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)

HASH = "a" * 64


class IndependentPythonProofTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.os_source = self.root / "source.json"
        self.receipt_path = self.root / "native-python-provenance.json"
        self.lock = self.root / "requirements.lock"
        self.source = {
            "$schema": audit.SOURCE_SCHEMA,
            "buildroot": {"sha256": HASH},
            "component_profiles": {"native-product-runtime": {
                "source_owner": "ordaxsystems/ordax-runtime",
                "base_rootfs_mutation_allowed": False,
                "component_artifact_authorized": False,
                "device_install_authorized": False,
            }},
        }
        self.proof = {
            "$schema": audit.RECEIPT_SCHEMA,
            "status": "build-proof-only",
            "buildroot_sha256": HASH,
            "component_inputs": {"component": "native-product-runtime",
                                 "source_owner": "ordaxsystems/ordax-runtime",
                                 "base_defconfig_sha256": HASH,
                                 "fragment_sha256": HASH},
            "runtime_agent_packaged": False,
            "component_artifact_authorized": False,
            "device_install_authorized": False,
            "base_rootfs_modified": False,
            "python_abi": {
                "python_executable": "usr/bin/python3.14",
                "required_interpreter": "/lib/ld-musl-x86_64.so.1",
                "python_sha256": HASH,
                "elf_manifest_sha256": HASH,
                "compiled_elf_count": 56,
                "runtime_agent_packaged": False,
                "component_install_authorized": False,
                "device_execution_proven": False,
            },
            "private_python_smoke": {
                "status": "kernel-host-smoke-pass",
                "python_version": "3.14.7",
                "httpx_version": "0.28.1",
                "all_imports_private": True,
                "tls_and_zlib_operational": True,
                "httpx_mock_transport_proven": True,
                "httpx_offline_request_construction_proven": True,
                "linux_kernel_execution_proven": True,
                "runtime_agent_packaged": False,
                "runtime_dependency_versions_reconciled": False,
                "ordax_usb_execution_proven": False,
            },
            "httpx_buildroot_source_versions": {
                "anyio": "4.13.0", "certifi": "2026.4.22", "h11": "0.16.0",
                "httpcore": "1.0.9", "httpx": "0.28.1", "idna": "3.11",
            },
        }
        self.pins = {
            "anyio": "4.15.1", "certifi": "2026.10.19", "h11": "0.16.0",
            "httpcore": "1.0.9", "httpx": "0.28.1", "idna": "3.20",
            "mcp": "1.30.0",
        }
        self.write()

    def write(self, python="3.12"):
        self.os_source.write_text(json.dumps(self.source), encoding="utf-8")
        self.receipt_path.write_text(json.dumps(self.proof), encoding="utf-8")
        self.lock.write_text(
            "# uv pip compile --python-version " + python + " --generate-hashes\n"
            + "".join(name + "==" + version + " \\\n    --hash=sha256:" + HASH + "\n"
                      for name, version in self.pins.items()),
            encoding="utf-8",
        )

    def check(self):
        return audit.reconcile(self.receipt_path, self.os_source, self.lock)

    def test_actual_buildroot_314_vs_python_312_style_lock_has_exact_blockers(self):
        result = self.check()
        self.assertEqual(
            [x["dependency"] for x in result["version_mismatches"]],
            ["python3", "anyio", "certifi", "idna"],
        )
        self.assertEqual(result["runtime_packages_not_proven_by_private_buildroot"], ["mcp"])
        self.assertFalse(result["version_alignment_proven"])
        self.assertFalse(result["runtime_agent_packaged"])
        self.assertFalse(result["device_install_authorized"])
        self.assertFalse(result["external_os_artifact_authenticity_verified_here"])

    def test_versions_matching_do_not_authorize_execution(self):
        self.pins = dict(self.proof["httpx_buildroot_source_versions"])
        self.write(python="3.14")
        result = self.check()
        self.assertTrue(result["version_alignment_proven"])
        self.assertFalse(result["full_native_runtime_dependency_closure_proven"])
        self.assertFalse(result["device_execution_proven"])
        self.assertFalse(result["device_install_authorized"])

    def test_forged_device_or_runtime_grant_fails_closed(self):
        for root, key in (("proof", "device_install_authorized"),
                          ("proof", "base_rootfs_modified"),
                          ("python_abi", "device_execution_proven"),
                          ("private_python_smoke", "runtime_dependency_versions_reconciled"),
                          ("private_python_smoke", "ordax_usb_execution_proven")):
            with self.subTest(key=key):
                entry = self.proof if root == "proof" else self.proof[root]
                entry[key] = True
                self.write()
                with self.assertRaisesRegex(audit.ProofError, "falsely|authority"):
                    self.check()
                entry[key] = False

    def test_different_buildroot_digest_does_not_pass(self):
        self.proof["buildroot_sha256"] = "b" * 64
        self.write()
        with self.assertRaisesRegex(audit.ProofError, "not bound"):
            self.check()

    def test_unapproved_external_component_owner_cannot_pass(self):
        self.source["component_profiles"]["native-product-runtime"]["source_owner"] = "external/agent"
        self.write()
        with self.assertRaisesRegex(audit.ProofError, "source contract"):
            self.check()

    def test_symlink_proof_is_rejected(self):
        other = self.root / "receipt.json"
        self.receipt_path.rename(other)
        self.receipt_path.symlink_to(other)
        with self.assertRaisesRegex(audit.ProofError, "unsafe"):
            self.check()


if __name__ == "__main__":
    unittest.main()
