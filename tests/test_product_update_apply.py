import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from ordax_dev_agent.product_update_apply import (
    ReleaseAsset,
    _require_recorded_signers,
    _validated_transaction_material,
    download_release_asset,
    load_release_trust,
    parse_checksum_manifest,
    prepare_update_transaction,
    select_release_assets,
    verify_staged_authenticode,
)


class ProductUpdateApplyTests(unittest.TestCase):
    def release(self, version: str, payload: bytes) -> dict:
        installer = f"ORDAX-Studio-Setup-{version}-x64.exe"
        checksum = hashlib.sha256(payload).hexdigest()
        checksum_payload = f"{checksum}  {installer}\n".encode("ascii")
        base = f"https://github.com/ordaxsystems/ordax-runtime/releases/download/v{version}/"
        return {
            "tag_name": f"v{version}",
            "draft": False,
            "prerelease": False,
            "assets": [
                {
                    "name": installer,
                    "browser_download_url": base + installer,
                    "size": len(payload),
                },
                {
                    "name": "SHA256SUMS.txt",
                    "browser_download_url": base + "SHA256SUMS.txt",
                    "size": len(checksum_payload),
                },
            ],
        }

    def client(self, payloads: dict[str, bytes]) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "github.com":
                location = "https://release-assets.githubusercontent.com" + request.url.path
                return httpx.Response(302, headers={"location": location}, request=request)
            if request.url.host != "release-assets.githubusercontent.com":
                return httpx.Response(404, request=request)
            path = request.url.path
            for version, payload in payloads.items():
                if f"/v{version}/" not in path:
                    continue
                installer = f"ORDAX-Studio-Setup-{version}-x64.exe"
                if path.endswith("/" + installer):
                    return httpx.Response(
                        200,
                        content=payload,
                        headers={"content-length": str(len(payload))},
                        request=request,
                    )
                if path.endswith("/SHA256SUMS.txt"):
                    checksum = hashlib.sha256(payload).hexdigest()
                    body = f"{checksum}  {installer}\n".encode("ascii")
                    return httpx.Response(
                        200,
                        content=body,
                        headers={"content-length": str(len(body))},
                        request=request,
                    )
            return httpx.Response(404, request=request)

        return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)

    def test_packaged_trust_store_is_fail_closed_until_signer_is_provisioned(self):
        self.assertEqual(load_release_trust(), ())

    def test_trust_store_rejects_invalid_and_duplicate_thumbprints(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trust.json"
            path.write_text(json.dumps({
                "schema": "ordax.windows-release-trust/1",
                "authorizedSignerThumbprints": ["not-a-thumbprint"],
            }), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_release_trust(path)
            valid = "A" * 40
            path.write_text(json.dumps({
                "schema": "ordax.windows-release-trust/1",
                "authorizedSignerThumbprints": [valid, valid],
            }), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_release_trust(path)

    def test_release_assets_must_use_exact_canonical_repository_url(self):
        release = self.release("0.15.2", b"target")
        release["assets"][0]["browser_download_url"] = "https://evil.example/installer.exe"
        with self.assertRaises(ValueError):
            select_release_assets(release, "0.15.2")

    def test_checksum_manifest_rejects_paths_duplicates_and_malformed_lines(self):
        name = "ORDAX-Studio-Setup-0.15.2-x64.exe"
        digest = "a" * 64
        self.assertEqual(parse_checksum_manifest(f"{digest}  {name}\n", name), digest)
        for text in (
            f"{digest}  ../{name}\n",
            f"{digest}  {name}\n{digest}  {name}\n",
            f"not-a-hash  {name}\n",
        ):
            with self.assertRaises(ValueError):
                parse_checksum_manifest(text, name)

    def test_download_rejects_redirect_outside_github_asset_storage(self):
        asset = ReleaseAsset(
            name="x",
            url="https://github.com/ordaxsystems/ordax-runtime/releases/download/v0.1.0/x",
            size=1,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "https://evil.example/x"}, request=request)

        with tempfile.TemporaryDirectory() as tmp, httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(ValueError):
                download_release_asset(asset, Path(tmp) / "x", max_size_bytes=10, client=client)

    def test_transaction_stages_target_and_matching_rollback_before_apply(self):
        target_payload = b"signed-later-target"
        rollback_payload = b"signed-later-rollback"
        target = self.release("0.15.2", target_payload)
        rollback = self.release("0.15.1", rollback_payload)
        with tempfile.TemporaryDirectory() as tmp, self.client({
            "0.15.2": target_payload,
            "0.15.1": rollback_payload,
        }) as client:
            result = prepare_update_transaction(
                "0.15.1", target, rollback, staging_root=tmp, client=client
            )
            root = Path(result["root"])
            self.assertEqual(result["phase"], "sha256-verified")
            self.assertEqual(result["rollback"]["version"], "0.15.1")
            self.assertEqual(result["target"]["version"], "0.15.2")
            self.assertTrue((root / "target" / result["target"]["installer"]).is_file())
            self.assertTrue((root / "rollback" / result["rollback"]["installer"]).is_file())
            journal = json.loads((root / "transaction.json").read_text(encoding="utf-8"))
            self.assertEqual(journal["authority"], "local-privileged-updater")
            self.assertEqual(journal["phase"], "sha256-verified")

            # Empty packaged trust is an intentional hard stop. It must block
            # before any PowerShell verifier or installer can be launched.
            with self.assertRaisesRegex(RuntimeError, "no authorized signer"):
                verify_staged_authenticode(root)
            journal_after = json.loads((root / "transaction.json").read_text(encoding="utf-8"))
            self.assertEqual(journal_after["phase"], "sha256-verified")

    def test_handoff_revalidates_journal_material_and_signer_trust(self):
        target_payload = b"target"
        rollback_payload = b"rollback"
        target = self.release("0.15.2", target_payload)
        rollback = self.release("0.15.1", rollback_payload)
        with tempfile.TemporaryDirectory() as tmp, self.client({
            "0.15.2": target_payload,
            "0.15.1": rollback_payload,
        }) as client:
            result = prepare_update_transaction(
                "0.15.1", target, rollback, staging_root=tmp, client=client
            )
            root = Path(result["root"])
            journal = json.loads((root / "transaction.json").read_text(encoding="utf-8"))
            _validated_transaction_material(root, journal)
            signers = ("A" * 40,)
            journal["authorizedSignerThumbprints"] = list(signers)
            _require_recorded_signers(journal, signers)

            journal["authorizedSignerThumbprints"] = ["B" * 40]
            with self.assertRaisesRegex(ValueError, "signer trust changed"):
                _require_recorded_signers(journal, signers)

            journal["authorizedSignerThumbprints"] = list(signers)
            journal["target"]["installer"] = "../../outside.exe"
            with self.assertRaisesRegex(ValueError, "release identity"):
                _validated_transaction_material(root, journal)

    def test_rollback_release_must_match_installed_version(self):
        target = self.release("0.15.2", b"target")
        wrong_rollback = self.release("0.14.9", b"rollback")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "rollback release"):
                prepare_update_transaction(
                    "0.15.1", target, wrong_rollback, staging_root=tmp
                )


if __name__ == "__main__":
    unittest.main()
