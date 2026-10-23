"""Test private Buildroot runtime probing without needing a live OS or USB."""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools.ci.probe_native_buildroot_agent import _stage_verified_target, offline_probe
from tools.ci.audit_independent_python_proof import ProofError


class BuildrootOfflineProbeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.tar = self.root / "private.tar"
        self.destination = self.root / "extract"
        self.make_tar([("usr/bin/python3.14", b"verified-private-python")])
        self.digest = hashlib.sha256(self.tar.read_bytes()).hexdigest()

    def make_tar(self, entries):
        with tarfile.open(self.tar, mode="w") as archive:
            for path, content in entries:
                info = tarfile.TarInfo(path)
                if isinstance(content, tuple):
                    info.type = tarfile.SYMTYPE
                    info.linkname = content[1]
                else:
                    info.size = len(content)
                archive.addfile(info, None if isinstance(content, tuple)
                                else io.BytesIO(content))

    def test_extracts_only_into_new_temporary_directory(self):
        _stage_verified_target(self.tar, self.digest, self.destination)
        self.assertEqual((self.destination / "usr/bin/python3.14").read_bytes(),
                         b"verified-private-python")

    def test_rejects_tampered_bytes_even_with_same_filename(self):
        with self.assertRaisesRegex(ProofError, "changed since OS receipt"):
            _stage_verified_target(self.tar, "0" * 64, self.destination)

    def test_never_overwrites_existing_target(self):
        self.destination.mkdir()
        with self.assertRaisesRegex(ProofError, "already exists"):
            _stage_verified_target(self.tar, self.digest, self.destination)

    def test_rejects_path_traversal(self):
        self.make_tar([("../escape", b"never-write")])
        hash_value = hashlib.sha256(self.tar.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ProofError, "path is unsafe"):
            _stage_verified_target(self.tar, hash_value, self.destination)
        self.assertFalse((self.root / "escape").exists())

    def test_rejects_absolute_symlink_outside_staging(self):
        self.make_tar([("lib/ld-musl-x86_64.so.1", ("link", "/etc/passwd"))])
        hash_value = hashlib.sha256(self.tar.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ProofError, "extraction was rejected"):
            _stage_verified_target(self.tar, hash_value, self.destination)
        self.assertFalse((self.destination / "lib/ld-musl-x86_64.so.1").exists())

    def test_rejects_duplicate_entries(self):
        self.make_tar([("usr/bin/python3.14", b"legitimate"),
                       ("./usr/bin/python3.14", b"spoofed")])
        digest = hashlib.sha256(self.tar.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ProofError, "duplicated"):
            _stage_verified_target(self.tar, digest, self.destination)

    def test_rejects_special_device_entries(self):
        with tarfile.open(self.tar, mode="w") as archive:
            item = tarfile.TarInfo("usr/dev/unsafe")
            item.type = tarfile.CHRTYPE
            item.devmajor = 1
            item.devminor = 3
            archive.addfile(item)
        digest = hashlib.sha256(self.tar.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ProofError, "special files"):
            _stage_verified_target(self.tar, digest, self.destination)

    def test_requires_canonical_checkout_before_any_probe(self):
        with self.assertRaisesRegex(ProofError, "complete canonical Runtime source"):
            offline_probe(self.root / "no.json", self.root / "no-source",
                          self.root / "lock", self.tar, self.root)

    def test_does_not_claim_install_authority_without_verified_receipt(self):
        source = self.root / "canonical"
        for name in ("ordax_dev_agent/native_main.py",
                     "ordax_dev_agent/product_device_control_plane.py",
                     "ordax_core/__init__.py",
                     "ordax_connector/capabilities.py",
                     "mcp_blender_unity/__init__.py"):
            f = source / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("# fixture\n")
        with self.assertRaises(ProofError), patch(
                "tools.ci.probe_native_buildroot_agent.reconcile",
                side_effect=ProofError("unverified OS proof")):
            offline_probe(self.root / "no.json", self.root / "no-source",
                          self.root / "lock", self.tar, source)


if __name__ == "__main__":
    unittest.main()
