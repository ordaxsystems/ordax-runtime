"""Fail-closed provenance preflight. No installs, credentials, or MCP grants."""
import hashlib
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.owner_dev_update_preflight import (
    MainCandidate, _run_gh, verify_owner_dev_preflight,
    stage_owner_dev_candidates,
    audit_legacy_rollback,
)

REPO = "ordaxsystems/ordax-runtime"
WORKFLOW = ".github/workflows/windows-product-build.yml"
TARGET = MainCandidate("39123456789", "a" * 40)
ROLLBACK = MainCandidate("39123450000", "b" * 40)


class FakeGitHub:
    def __init__(self):
        self.calls = []
        self.overrides = {}
        self.unattested_run = None
        self.corrupt_download = False

    def __call__(self, argv):
        argv = list(argv)
        self.calls.append(argv)
        if argv[0] == "attestation":
            if self.unattested_run and self.unattested_run in argv:
                raise RuntimeError("GitHub provenance verification was rejected")
            return "verified"
        if argv[0] == "run" and argv[1] == "download":
            identity = TARGET if argv[2] == TARGET.run_id else ROLLBACK
            version = "0.15.9" if identity == TARGET else "0.15.6"
            folder = Path(argv[argv.index("--dir") + 1])
            filename = f"ORDAX-Studio-Setup-{version}-x64.exe"
            payload = ("downloaded" + version).encode("ascii")
            digest = hashlib.sha256(payload).hexdigest()
            (folder / filename).write_bytes(payload if not self.corrupt_download else b"forged")
            (folder / "SHA256SUMS.txt").write_text(f"{digest}  {filename}\n")
            return ""
        if argv[0] != "api":
            raise AssertionError("unexpected GitHub command")
        url = argv[1]
        run = TARGET if TARGET.run_id in url else ROLLBACK
        if url.endswith("/jobs?per_page=100"):
            jobs = [
                {"name": name, "run_id": int(run.run_id),
                 "status": "completed", "conclusion": "success"}
                for name in ("build-installer", "install-smoke", "attest-owner-dev-candidate")
            ]
            data = {"total_count": len(jobs), "jobs": jobs}
            data.update(self.overrides.get("jobs", {}))
        else:
            data = {
                "id": int(run.run_id), "head_sha": run.commit_sha,
                "head_branch": "main", "event": "push",
                "status": "completed", "conclusion": "success",
                "path": WORKFLOW, "repository": {"full_name": REPO},
            }
            data.update(self.overrides.get("run", {}))
        return json.dumps(data)


class OwnerDevProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {"LOCALAPPDATA": self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.base = Path(self.temp.name)
        self.install = self.base / "Programs" / "ORDAX"
        self.install.mkdir(parents=True)
        self.root = (self.base / "OrdaX" / "updates" /
                     "0.15.6-to-0.15.9" / uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        journal = {
            "schema": "ordax.windows-update-transaction/1",
            "authority": "local-privileged-updater",
            "transactionId": self.root.name,
            "phase": "sha256-verified",
            "installedVersion": "0.15.6",
            "ownerDevSource": {
                "target": {"runId": TARGET.run_id, "commit": TARGET.commit_sha},
                "rollback": {"runId": ROLLBACK.run_id, "commit": ROLLBACK.commit_sha},
            },
        }
        for role, version in (("target", "0.15.9"), ("rollback", "0.15.6")):
            folder = self.root / role
            folder.mkdir()
            installer = f"ORDAX-Studio-Setup-{version}-x64.exe"
            contents = (role + version).encode("ascii")
            sha = hashlib.sha256(contents).hexdigest()
            (folder / installer).write_bytes(contents)
            (folder / "SHA256SUMS.txt").write_text(
                f"{sha}  {installer}\n", encoding="ascii"
            )
            journal[role] = {
                "version": version, "tag": "v" + version, "installer": installer,
                "installerSha256": sha, "checksums": "SHA256SUMS.txt",
            }
        self.journal_path = self.root / "transaction.json"
        self.journal_path.write_text(json.dumps(journal), encoding="utf-8")
        self.gh = FakeGitHub()

    def verify(self, **kwargs):
        return verify_owner_dev_preflight(
            self.root, target=kwargs.pop("target", TARGET),
            rollback=kwargs.pop("rollback", ROLLBACK),
            run=kwargs.pop("run", self.gh),
            installed_product=kwargs.pop(
                "installed_product",
                lambda: {"version": "0.15.6", "installLocation": str(self.install)}
            ),
            **kwargs,
        )

    def _legacy_fixture(self):
        version = "0.15.6"
        source = "ab1ceda55abd226bc5c12b60f47c052276e19e5d"
        manifest = {
            "schema": "ordax.windows-product/1",
            "product": "ORDAX Studio",
            "version": version,
            "runtime_version": "0.4.6",
            "studio_source_commit": source,
        }
        (self.install / "product-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8",
        )
        root = self.base / "OrdaX" / "updates" / "historical-evidence" / "rollback"
        root.mkdir(parents=True)
        name = f"ORDAX-Studio-Setup-{version}-x64.exe"
        installer = root / name
        installer.write_bytes(b"opaque historic installer test bytes")
        digest = hashlib.sha256(installer.read_bytes()).hexdigest()
        (root / "SHA256SUMS.txt").write_text(f"{digest}  {name}\n", encoding="ascii")
        return root, installer, digest, source

    def _audit(self, root, digest, source):
        return audit_legacy_rollback(
            root, expected_installer_sha256=digest,
            expected_studio_source_commit=source,
            installed_product=lambda: {
                "version": "0.15.6", "installLocation": str(self.install),
            },
        )

    def test_legacy_audit_verifies_bytes_but_never_authorizes_rollback(self):
        root, installer, digest, source = self._legacy_fixture()
        original = {p.name: p.read_bytes() for p in root.iterdir()}
        result = self._audit(root, digest, source)
        self.assertEqual(result["state"], "legacy-integrity-verified")
        self.assertEqual(result["installerSha256"], digest)
        self.assertEqual(result["studioSourceCommit"], source)
        self.assertEqual(result["artifactSizeBytes"], installer.stat().st_size)
        self.assertFalse(result["rollbackReady"])
        self.assertFalse(result["applyAllowed"])
        self.assertFalse(result["originalInstallationEquivalenceProven"])
        self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, original)
        self.assertEqual(self.gh.calls, [])

    def test_legacy_audit_rejects_tampered_installer_and_checksum(self):
        root, installer, digest, source = self._legacy_fixture()
        installer.write_bytes(b"altered")
        with self.assertRaisesRegex(ValueError, "installer bytes"):
            self._audit(root, digest, source)
        installer.write_bytes(b"opaque historic installer test bytes")
        checksum = root / "SHA256SUMS.txt"
        checksum.write_text(f"{'0' * 64}  {installer.name}\n")
        with self.assertRaisesRegex(ValueError, "independent CI digest"):
            self._audit(root, digest, source)

    def test_legacy_audit_rejects_unpinned_or_wrong_product_identity(self):
        root, installer, digest, source = self._legacy_fixture()
        with self.assertRaisesRegex(ValueError, "Studio source commit"):
            self._audit(root, digest, "wrong")
        with self.assertRaisesRegex(ValueError, "source"):
            self._audit(root, digest, "a" * 40)
        manifest_path = self.install / "product-manifest.json"
        data = json.loads(manifest_path.read_text())
        data["version"] = "0.15.5"
        manifest_path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "identity"):
            self._audit(root, digest, source)

    def test_legacy_audit_rejects_wrong_root_and_extra_artifacts(self):
        root, installer, digest, source = self._legacy_fixture()
        with self.assertRaisesRegex(ValueError, "canonical private"):
            self._audit(self.base, digest, source)
        (root / "unexpected.bin").write_bytes(b"bad")
        with self.assertRaisesRegex(ValueError, "unexpected artifacts"):
            self._audit(root, digest, source)

    def test_legacy_audit_rejects_symlink_alias(self):
        root, installer, digest, source = self._legacy_fixture()
        checksum = root / "SHA256SUMS.txt"
        original = checksum.read_bytes()
        checksum.unlink()
        sibling = root.parent / "other-checksums.txt"
        sibling.write_bytes(original)
        try:
            checksum.symlink_to(sibling)
        except (OSError, NotImplementedError):
            self.skipTest("symlink privilege not available on Windows")
        with self.assertRaisesRegex(ValueError, "unsafe"):
            self._audit(root, digest, source)

    def test_both_candidates_proven_and_staged_journal_unchanged(self):
        before = self.journal_path.read_bytes()
        out = self.verify()
        self.assertEqual(out["state"], "provenance-verified")
        self.assertFalse(out["applyAllowed"])
        self.assertTrue(out["journalUnchanged"])
        self.assertTrue(out["productionSignatureRequired"])
        self.assertEqual(before, self.journal_path.read_bytes())
        attest = [cmd for cmd in self.gh.calls if cmd[0] == "attestation"]
        self.assertEqual(len(attest), 2)
        self.assertIn(TARGET.commit_sha, attest[0])
        self.assertIn(ROLLBACK.commit_sha, attest[1])
        self.assertTrue(all("--signer-workflow" in cmd for cmd in attest))

    def stage(self):
        return stage_owner_dev_candidates(
            target=TARGET, target_version="0.15.9", rollback=ROLLBACK,
            run=self.gh,
            installed_product=lambda: {
                "version": "0.15.6", "installLocation": str(self.install),
            },
        )

    def test_stage_downloads_exact_ci_artifacts_and_pins_journal(self):
        result = self.stage()
        self.assertFalse(result["applyAllowed"])
        root = Path(result["root"])
        self.assertTrue(root.is_dir())
        journal = json.loads((root / "transaction.json").read_text())
        self.assertEqual(journal["ownerDevSource"]["target"]["runId"], TARGET.run_id)
        self.assertEqual(journal["ownerDevSource"]["rollback"]["commit"], ROLLBACK.commit_sha)
        self.assertEqual(journal["phase"], "sha256-verified")
        downloads = [x for x in self.gh.calls if x[:2] == ["run", "download"]]
        self.assertEqual(len(downloads), 2)
        for item in downloads:
            self.assertEqual(item[item.index("--name") + 1], "ordax-studio-windows-x64")
        proof = verify_owner_dev_preflight(
            root, target=TARGET, rollback=ROLLBACK, run=self.gh,
            installed_product=lambda: {
                "version": "0.15.6", "installLocation": str(self.install),
            },
        )
        self.assertEqual(proof["state"], "provenance-verified")
        self.assertFalse(proof["applyAllowed"])

    def test_corrupt_download_cleans_partial_staging(self):
        self.gh.corrupt_download = True
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            self.stage()
        children = list((self.base / "OrdaX" / "updates" / "0.15.6-to-0.15.9").iterdir())
        self.assertEqual(children, [self.root])
        self.assertFalse(any(x[0] == "attestation" for x in self.gh.calls))

    def test_stage_fails_without_protected_main_or_rollback_attestation(self):
        self.gh.overrides["run"] = {"head_branch": "feature"}
        with self.assertRaisesRegex(ValueError, "canonical main"):
            self.stage()
        self.assertFalse(any(x[0] == "run" for x in self.gh.calls))
        self.gh.overrides.clear()
        self.gh.calls.clear()
        self.gh.unattested_run = ROLLBACK.commit_sha
        with self.assertRaisesRegex(RuntimeError, "provenance"):
            self.stage()
        self.assertFalse(any(x[0] == "attestation" and ROLLBACK.commit_sha in x
                             and x[-1] == "ignored" for x in self.gh.calls))
        self.assertEqual(list((self.base / "OrdaX" / "updates" / "0.15.6-to-0.15.9").iterdir()),
                         [self.root])

    def test_journal_must_pin_both_main_run_identities(self):
        value = json.loads(self.journal_path.read_text())
        value["ownerDevSource"]["rollback"]["runId"] = "9999999"
        self.journal_path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "provenance differs"):
            self.verify()
        self.assertEqual(self.gh.calls, [])

    def test_untrusted_branch_or_pull_request_is_blocked(self):
        for k, value in (("head_branch", "dev"), ("event", "pull_request"),
                         ("conclusion", "failure"), ("path", ".github/workflows/untrusted.yml"),
                         ("head_sha", "c" * 40)):
            with self.subTest(key=k):
                self.gh.overrides["run"] = {k: value}
                with self.assertRaisesRegex(ValueError, "canonical main build"):
                    self.verify()
                self.assertFalse(any(a[0] == "attestation" for a in self.gh.calls))
                self.gh.calls.clear()

    def test_missing_attestation_job_cannot_authorize_installers(self):
        self.gh.overrides["jobs"] = {
            "total_count": 2,
            "jobs": [{"name": n, "run_id": int(TARGET.run_id),
                      "status": "completed", "conclusion": "success"}
                     for n in ("build-installer", "install-smoke")],
        }
        with self.assertRaisesRegex(ValueError, "required Owner/Dev"):
            self.verify()

    def test_rollback_without_attestation_is_rejected(self):
        self.gh.unattested_run = ROLLBACK.commit_sha
        with self.assertRaisesRegex(RuntimeError, "provenance"):
            self.verify()
        self.assertEqual(json.loads(self.journal_path.read_text())["phase"], "sha256-verified")

    def test_staged_target_or_rollback_byte_tamper_is_blocked_before_gh(self):
        for role in ("target", "rollback"):
            with self.subTest(role=role):
                journal = json.loads(self.journal_path.read_text())
                installer = self.root / role / journal[role]["installer"]
                old = installer.read_bytes()
                installer.write_bytes(b"ATTACK")
                with self.assertRaisesRegex(ValueError, "installer integrity changed"):
                    self.verify()
                self.assertEqual(self.gh.calls, [])
                installer.write_bytes(old)

    def test_mismatched_installed_version_or_root_is_blocked(self):
        with self.assertRaisesRegex(ValueError, "installed product changed"):
            self.verify(installed_product=lambda: {
                "version": "0.4.6", "installLocation": str(self.install),
            })
        with self.assertRaisesRegex(ValueError, "not canonical"):
            self.verify(installed_product=lambda: {
                "version": "0.15.6", "installLocation": str(self.base / "other"),
            })

    def test_replay_and_forged_staging_paths_refused(self):
        value = json.loads(self.journal_path.read_text())
        value["phase"] = "handoff-launching"
        self.journal_path.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "unconsumed"):
            self.verify()
        with self.assertRaisesRegex(ValueError, "canonical private"):
            verify_owner_dev_preflight(
                self.base, target=TARGET, rollback=ROLLBACK,
                run=self.gh, installed_product=lambda: {})
        self.assertEqual(self.gh.calls, [])

    def test_identity_validation_rejects_ambiguous_and_reused_builds(self):
        for run, sha in (("0", "a" * 40), ("01", "a" * 40),
                         ("123;echo pwn", "a" * 40), ("123", "wrong")):
            with self.assertRaises(ValueError):
                MainCandidate(run, sha)
        with self.assertRaisesRegex(ValueError, "separately attested"):
            self.verify(target=TARGET, rollback=TARGET)

    def test_github_runner_uses_argument_vector_and_sanitizes_errors(self):
        class Result:
            returncode = 1
            stdout = ""
            stderr = "secret-credentials"
        with patch("ordax_dev_agent.owner_dev_update_preflight.subprocess.run",
                   return_value=Result()) as mocked:
            with self.assertRaisesRegex(RuntimeError, "^GitHub provenance verification was rejected$"):
                _run_gh(["api", "repos/ordaxsystems/ordax-runtime/actions/runs/1"])
            cmd = mocked.call_args.args[0]
            self.assertEqual(cmd[0], "gh")
            self.assertFalse(mocked.call_args.kwargs["shell"])


if __name__ == "__main__":
    unittest.main()
