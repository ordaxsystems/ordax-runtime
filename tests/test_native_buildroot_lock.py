"""Buildroot-vs-ordax-runtime lock drift regression and fail-closed gate."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/ci/audit_buildroot_lock.py"
spec = importlib.util.spec_from_file_location("ordax_buildroot_lock_audit", SCRIPT)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
H = "a" * 64


class BuildrootNativeLockTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.br = self.folder / "buildroot"
        self.req = self.folder / "requirements.lock"
        self.boot = self.folder / "bootstrap.lock"
        self.build = self.folder / "build-requirements.in"
        self.write_python("3.14.7")
        self.package("httpx", "0.28.1")
        self.package("websockets", "16.0")
        self.lock(self.req, {
            "httpx": "0.28.1", "websockets": "15.0.1",
            "mcp": "1.30.0", "hatchling": "1.32.4",
        })
        self.lock(self.boot, {"hatchling": "1.32.4"})
        self.build.write_text("hatchling>=1.27,<2\n")

    def write_python(self, version):
        p = self.br / "package/python3"
        p.mkdir(parents=True, exist_ok=True)
        (p / "python3.mk").write_text("PYTHON3_VERSION = " + version + "\n")

    def package(self, name, version):
        package = self.br / ("package/python-" + name)
        package.mkdir(parents=True, exist_ok=True)
        variable = name.replace("-", "_").upper()
        (package / "Config.in").write_text("config BR2_PACKAGE_PYTHON_" + variable + "\n")
        (package / ("python-" + name + ".mk")).write_text(
            "PYTHON_" + variable + "_VERSION = " + version + "\n"
        )

    @staticmethod
    def lock(path, entries, python="3.12"):
        path.write_text(
            "# uv pip compile --python-version " + python + " --generate-hashes\n"
            + "".join(name + "==" + version + " \\\n    --hash=sha256:" + H + "\n"
                      for name, version in entries.items())
        )

    def check(self):
        return audit.audit(self.br, self.req, self.boot, self.build)

    def test_incompatible_3_12_lock_vs_3_14_and_missing_packages(self):
        report = self.check()
        self.assertFalse(report["source_version_compatible"])
        self.assertFalse(report["native_dependency_closure_proven"])
        self.assertFalse(report["runtime_agent_packaged"])
        self.assertFalse(report["device_install_authorized"])
        self.assertEqual(report["build_tool_overlap_names"], ["hatchling"])
        self.assertEqual(report["runtime_packages_examined"], 4)
        issues = {issue["package"]: issue["reason"] for issue in report["issues"]}
        self.assertEqual(issues, {
            "python3": "lock-python-abi-mismatch",
            "hatchling": "missing",
            "mcp": "missing",
            "websockets": "version-mismatch",
        })
        self.assertEqual(report["packages"][-1]["buildroot_version"], "16.0")

    def test_exactly_version_compatible_is_still_not_executable_proof(self):
        self.write_python("3.14.7")
        self.package("mcp", "1.30.0")
        self.package("hatchling", "1.32.4")
        self.package("websockets", "15.0.1")
        self.lock(self.req, {
            "mcp": "1.30.0", "httpx": "0.28.1", "websockets": "15.0.1",
            "hatchling": "1.32.4",
        }, python="3.14")
        report = self.check()
        self.assertEqual(report["issues"], [])
        self.assertTrue(report["source_version_compatible"])
        self.assertFalse(report["native_dependency_closure_proven"])

    def test_missing_target_option_cannot_pass_by_version_alone(self):
        target = self.br / "package/python-httpx/Config.in"
        target.write_text("# the package exists but has no target symbol\n")
        report = self.check()
        issues = {x["package"]: x["reason"] for x in report["issues"]}
        self.assertEqual(issues["httpx"], "no-target-option")

    def test_unexpanded_or_missing_version_cannot_pass(self):
        mk = self.br / "package/python-httpx/python-httpx.mk"
        mk.write_text("PYTHON_HTTPX_VERSION = $(EXTERNAL_VERSION)\n")
        report = self.check()
        issues = {x["package"]: x["reason"] for x in report["issues"]}
        self.assertEqual(issues["httpx"], "version-not-pinned")

    def test_unhashed_or_duplicate_pin_fails_closed(self):
        self.req.write_text("httpx==0.28.1\n# uv --python-version 3.14\n")
        with self.assertRaisesRegex(audit.RuntimeBuildrootAuditError, "unhashed"):
            self.check()
        self.lock(self.req, {"httpx": "0.28.1"})
        with self.req.open("a") as handle:
            handle.write("httpx==0.29.0 \\\n    --hash=sha256:" + H + "\n")
        with self.assertRaisesRegex(audit.RuntimeBuildrootAuditError, "duplicate"):
            self.check()

    def test_reject_unparsed_lock_requirements_and_download_directives(self):
        for injected in (
            "untracked-package>=2.0",
            "--extra-index-url https://packages.invalid/simple",
            "-r extra-requirements.lock",
            "httpx==0.28.1;python_version>='3.14'",
            "    --hash=sha512:" + H,
        ):
            with self.subTest(injected=injected):
                self.lock(self.req, {"httpx": "0.28.1"})
                with self.req.open("a") as stream:
                    stream.write(injected + "\n")
                with self.assertRaisesRegex(
                    audit.RuntimeBuildrootAuditError, "unrecognized requirement lock line"
                ):
                    self.check()

    def test_hash_comment_does_not_satisfy_pinned_requirement(self):
        self.req.write_text(
            "# uv --python-version 3.12\n"
            "httpx==0.28.1\n"
            "# --hash=sha256:" + H + "\n"
        )
        with self.assertRaisesRegex(audit.RuntimeBuildrootAuditError, "unhashed"):
            self.check()

    def test_conflicting_python_targets_fail_closed(self):
        self.lock(self.req, {"httpx": "0.28.1"})
        with self.req.open("a") as stream:
            stream.write("# extra uv --python-version 3.14\n")
        with self.assertRaisesRegex(audit.RuntimeBuildrootAuditError, "conflicting Python ABI"):
            self.check()

    def test_runtime_lock_is_parsed_without_guessing_native_wheels(self):
        lock = ROOT / "native/ordax-product-agent/requirements.lock"
        bootstrap = ROOT / "native/ordax-product-agent/bootstrap.lock"
        packages, python = audit.read_requirements(lock, lock=True)
        boot, _ = audit.read_requirements(bootstrap, lock=True)
        self.assertEqual(python, "3.12")
        self.assertEqual(packages["mcp"], "1.30.0")
        self.assertEqual(packages["httpx"], "0.28.1")
        self.assertIn("pyinstaller", packages)
        self.assertIn("hatchling", boot)
        self.assertGreaterEqual(len(packages), 35)


if __name__ == "__main__":
    unittest.main()
