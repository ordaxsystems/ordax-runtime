from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "windows" / "assert-release-provenance.ps1"
SOURCE_LOCK = ROOT / "studio-source.lock.json"


@unittest.skipUnless(shutil.which("powershell"), "Windows PowerShell is required")
class ReleaseProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.lock = json.loads(SOURCE_LOCK.read_text(encoding="utf-8"))
        self.lock_file = self.folder / "studio-source.lock.json"
        self.write_lock()
        self.artifacts = self.folder / "artifacts"
        self.artifacts.mkdir()
        self.name = f"ORDAX-Studio-Setup-{self.lock['version']}-x64.exe"
        self.exe = self.artifacts / self.name
        self.exe.write_bytes(b"ordax-test-installer-bytes")
        self.hash = hashlib.sha256(self.exe.read_bytes()).hexdigest()
        self.checksums = self.artifacts / "SHA256SUMS.txt"
        self.checksums.write_text(f"{self.hash}  {self.name}\n", encoding="ascii")

    def write_lock(self) -> None:
        self.lock_file.write_text(json.dumps(self.lock), encoding="utf-8")

    def verify(self, *, tag: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(SCRIPT),
                "-Tag",
                tag if tag is not None else f"v{self.lock['version']}",
                "-SourceLock",
                str(self.lock_file),
                "-ArtifactDirectory",
                str(self.artifacts),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_correct_version_source_and_checksum_pass(self) -> None:
        result = self.verify()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ORDAX_RELEASE_PROVENANCE_VALID", result.stdout)

    def test_wrong_tag_fails_closed(self) -> None:
        result = self.verify(tag="v0.0.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Release tag does not match", result.stdout + result.stderr)

    def test_checksum_tamper_fails_closed(self) -> None:
        self.exe.write_bytes(b"changed-during-transfer")
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match", result.stdout + result.stderr)

    def test_wrong_installer_name_fails_closed(self) -> None:
        self.exe.rename(self.artifacts / "ORDAX-Studio-Setup-0.0.0-x64.exe")
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exactly the canonical", result.stdout + result.stderr)

    def test_multiple_executables_are_rejected(self) -> None:
        (self.artifacts / "extra.exe").write_bytes(b"unexpected")
        self.assertNotEqual(self.verify().returncode, 0)

    def test_multiple_checksum_entries_are_rejected(self) -> None:
        self.checksums.write_text(f"{self.hash}  {self.name}\n{self.hash}  {self.name}\n", encoding="ascii")
        self.assertNotEqual(self.verify().returncode, 0)

    def test_checksum_cannot_point_to_different_file(self) -> None:
        self.checksums.write_text(f"{self.hash}  other.exe\n", encoding="ascii")
        self.assertNotEqual(self.verify().returncode, 0)

    def test_foreign_source_lock_is_rejected(self) -> None:
        self.lock["repository"] = "untrusted/other"
        self.write_lock()
        result = self.verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("identity/provenance", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
