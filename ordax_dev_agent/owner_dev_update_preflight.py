"""Read-only Owner/Dev provenance preflight for the canonical Windows updater.

Production Authenticode policy and the sole installation/rollback executor are
unchanged. A successful result is *not* permission to install unsigned code.
Both target and rollback must have independently attested protected-main builds.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .product_update import installed_windows_product, parse_version
from .product_update_apply import (
    TRANSACTION_SCHEMA,
    _read_transaction,
    _validated_transaction_material,
    _atomic_json,
    _required_asset_names,
    _hash_file,
    parse_checksum_manifest,
)

REPOSITORY = "ordaxsystems/ordax-runtime"
WORKFLOW = ".github/workflows/windows-product-build.yml"
SIGNER_WORKFLOW = f"{REPOSITORY}/{WORKFLOW}"
PREFLIGHT_SCHEMA = "ordax.owner-dev-provenance-preflight/1"
ARTIFACT_NAME = "ordax-studio-windows-x64"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_RUN_ID = re.compile(r"[1-9][0-9]{0,18}\Z")
_TRANSACTION_ID = re.compile(r"[0-9a-f]{32}\Z")
_REQUIRED_JOBS = frozenset({
    "build-installer", "install-smoke", "attest-owner-dev-candidate",
})


@dataclass(frozen=True)
class MainCandidate:
    run_id: str
    commit_sha: str

    def __post_init__(self) -> None:
        if not _RUN_ID.fullmatch(self.run_id):
            raise ValueError("invalid immutable CI run ID")
        if not _SHA.fullmatch(self.commit_sha):
            raise ValueError("invalid protected-main source SHA")


def _run_gh(args: Sequence[str], *, timeout: int = 45) -> str:
    # Fixed host/commands and argument vectors; never use shell interpolation.
    try:
        result = subprocess.run(
            ["gh", *args], check=False, shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("GitHub provenance verification is unavailable") from error
    if result.returncode != 0:
        # Do not leak CLI stderr: it can include URLs, environment or credentials.
        raise RuntimeError("GitHub provenance verification was rejected")
    if len(result.stdout) > 512_000:
        raise RuntimeError("GitHub provenance response exceeded its limit")
    return result.stdout


def _github_json(
    args: Sequence[str], run: Callable[..., str],
) -> dict[str, Any]:
    data = json.loads(run(args))
    if not isinstance(data, dict):
        raise ValueError("invalid GitHub API response")
    return data


def _assert_protected_main_run(
    candidate: MainCandidate, run: Callable[..., str],
) -> None:
    base = f"repos/{REPOSITORY}/actions/runs/{candidate.run_id}"
    info = _github_json(["api", base], run)
    if (
        info.get("id") != int(candidate.run_id)
        or info.get("head_sha") != candidate.commit_sha
        or info.get("head_branch") != "main"
        or info.get("event") != "push"
        or info.get("status") != "completed"
        or info.get("conclusion") != "success"
        or info.get("path") not in (WORKFLOW, f"{REPOSITORY}/{WORKFLOW}")
        or (info.get("repository") or {}).get("full_name") != REPOSITORY
    ):
        raise ValueError("CI candidate is not a successful canonical main build")

    jobs = _github_json(["api", f"{base}/jobs?per_page=100"], run)
    if not isinstance(jobs.get("total_count"), int) or jobs["total_count"] > 100:
        raise ValueError("CI job evidence is incomplete")
    listing = jobs.get("jobs")
    if not isinstance(listing, list) or len(listing) != jobs["total_count"]:
        raise ValueError("CI job inventory is incomplete")
    passed = {
        job.get("name") for job in listing
        if isinstance(job, dict)
        and job.get("run_id") == int(candidate.run_id)
        and job.get("status") == "completed"
        and job.get("conclusion") == "success"
    }
    if not _REQUIRED_JOBS.issubset(passed):
        raise ValueError("required Owner/Dev build, installation or attestation did not pass")


def _assert_attested_bytes(
    candidate: MainCandidate, installer: Path, run: Callable[..., str],
) -> None:
    # A digest in SHA256SUMS.txt proves integrity, not build provenance.
    # gh attestation verify checks issuer, exact digest and main source commit.
    run([
        "attestation", "verify", str(installer),
        "--repo", REPOSITORY,
        "--source-digest", candidate.commit_sha,
        "--signer-workflow", SIGNER_WORKFLOW,
    ])


def _canonical_transaction_root(root: Path) -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("Owner/Dev preflight requires a Windows user profile")
    resolved = root.resolve(strict=True)
    updates = (Path(local) / "OrdaX" / "updates").resolve()
    if (
        root.is_symlink()
        or resolved.parent.parent != updates
        or not _TRANSACTION_ID.fullmatch(resolved.name)
        or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+-to-[0-9]+\.[0-9]+\.[0-9]+", resolved.parent.name)
    ):
        raise ValueError("transaction root is not in canonical private update storage")
    return resolved



def stage_owner_dev_candidates(
    *,
    target: MainCandidate,
    target_version: str,
    rollback: MainCandidate,
    run: Callable[..., str] = _run_gh,
    installed_product: Callable[[], dict[str, str]] = installed_windows_product,
) -> dict[str, Any]:
    """Stage canonical protected-main CI bytes, without installing anything.

    This reuses the production transaction layout and integrity helpers.
    No new installer, rollback engine, or signer trust root is introduced.
    """
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("Owner/Dev staging requires a Windows user profile")
    installed = installed_product()
    current_version = str(installed.get("version", ""))
    if parse_version(target_version) <= parse_version(current_version):
        raise ValueError("target is not newer than installed product")
    if Path(installed.get("installLocation", "")).resolve() != (
        Path(local) / "Programs" / "ORDAX"
    ).resolve():
        raise ValueError("installed product location is not canonical")
    if target == rollback or target.run_id == rollback.run_id:
        raise ValueError("target and rollback require separate source runs")

    # Check exact provenance before writing any files.
    for candidate in (target, rollback):
        _assert_protected_main_run(candidate, run)

    base = Path(local) / "OrdaX" / "updates"
    root = base / f"{current_version}-to-{target_version}" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    records: dict[str, dict[str, Any]] = {}
    try:
        for role, version, candidate in (
            ("target", target_version, target),
            ("rollback", current_version, rollback),
        ):
            folder = root / role
            folder.mkdir()
            # The *only* CI artifact name is locked in the GitHub workflow.
            run(["run", "download", candidate.run_id, "--repo", REPOSITORY,
                 "--name", ARTIFACT_NAME, "--dir", str(folder)])
            installer_name, checksum_name = _required_asset_names(version)
            if {p.name for p in folder.iterdir()} != {installer_name, checksum_name}:
                raise ValueError(f"{role} CI artifact inventory is not canonical")
            installer = folder / installer_name
            checksum = folder / checksum_name
            if (installer.is_symlink() or checksum.is_symlink()
                or not installer.is_file() or not checksum.is_file()
                or installer.stat().st_size > 1024 * 1024 * 1024
                or checksum.stat().st_size > 16 * 1024):
                raise ValueError(f"{role} installer inventory is not safe")
            digest = parse_checksum_manifest(
                checksum.read_text(encoding="ascii"), installer_name,
            )
            if _hash_file(installer) != digest:
                raise ValueError(f"{role} CI installer digest mismatch")
            _assert_attested_bytes(candidate, installer, run)
            records[role] = {
                "version": version,
                "tag": f"v{version}",
                "installer": installer_name,
                "installerSha256": digest,
                "checksums": checksum_name,
            }
        journal = {
            "schema": TRANSACTION_SCHEMA,
            "authority": "local-privileged-updater",
            "transactionId": root.name,
            "phase": "sha256-verified",
            "installedVersion": current_version,
            "target": records["target"],
            "rollback": records["rollback"],
            "ownerDevSource": {
                "target": {"runId": target.run_id, "commit": target.commit_sha},
                "rollback": {"runId": rollback.run_id, "commit": rollback.commit_sha},
            },
        }
        _atomic_json(root / "transaction.json", journal)
        # Final validation ensures staged files and journal remain coherent.
        _validated_transaction_material(root, journal)
        return {"state": "staged", "root": str(root),
                "transactionId": root.name, "applyAllowed": False}
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise


def verify_owner_dev_preflight(
    transaction_root: str | Path,
    *,
    target: MainCandidate,
    rollback: MainCandidate,
    run: Callable[..., str] = _run_gh,
    installed_product: Callable[[], dict[str, str]] = installed_windows_product,
) -> dict[str, Any]:
    """Validate a complete *pair* of staged builds. Never write/apply grants."""
    root = _canonical_transaction_root(Path(transaction_root))
    journal = _read_transaction(root)
    if (
        journal.get("schema") != TRANSACTION_SCHEMA
        or journal.get("authority") != "local-privileged-updater"
        or journal.get("phase") != "sha256-verified"
        or journal.get("transactionId") != root.name
    ):
        raise ValueError("transaction journal is not an unconsumed canonical staging record")
    target_record, rollback_record = _validated_transaction_material(root, journal)
    if target == rollback or target.run_id == rollback.run_id:
        raise ValueError("target and rollback must be separately attested candidates")
    expected_source = {
        "target": {"runId": target.run_id, "commit": target.commit_sha},
        "rollback": {"runId": rollback.run_id, "commit": rollback.commit_sha},
    }
    if journal.get("ownerDevSource") != expected_source:
        raise ValueError("Owner/Dev candidate provenance differs from durable journal")
    expected_dir = f"{journal['installedVersion']}-to-{target_record['version']}"
    if root.parent.name != expected_dir:
        raise ValueError("transaction directory and version transition differ")
    current = installed_product()
    if current.get("version") != journal["installedVersion"]:
        raise ValueError("installed product changed since staging")
    local = os.environ["LOCALAPPDATA"]
    expected_install = (Path(local) / "Programs" / "ORDAX").resolve()
    if Path(current.get("installLocation", "")).resolve() != expected_install:
        raise ValueError("installed product location is not canonical")

    # Validate both source runs and *both* executable byte streams. An old
    # un-attested rollback is deliberately rejected, even if its hash matches.
    for role, candidate, record in (
        ("target", target, target_record),
        ("rollback", rollback, rollback_record),
    ):
        _assert_protected_main_run(candidate, run)
        _assert_attested_bytes(candidate, root / role / record["installer"], run)

    # The result is advisory and read-only: it MUST NOT change the production
    # journal's phase to authenticode-verified or grant unsigned apply rights.
    return {
        "schema": PREFLIGHT_SCHEMA,
        "state": "provenance-verified",
        "transactionId": root.name,
        "targetRunId": target.run_id,
        "targetCommit": target.commit_sha,
        "rollbackRunId": rollback.run_id,
        "rollbackCommit": rollback.commit_sha,
        "applyAllowed": False,
        "productionSignatureRequired": True,
        "journalUnchanged": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Owner/Dev protected-main installer provenance")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--stage", action="store_true", help="download and verify CI artifacts only")
    mode.add_argument("--transaction-root", help="verify a previously staged transaction")
    parser.add_argument("--target-version", help="required for staging, e.g. 0.15.9")
    parser.add_argument("--target-run", required=True)
    parser.add_argument("--target-commit", required=True)
    parser.add_argument("--rollback-run", required=True)
    parser.add_argument("--rollback-commit", required=True)
    args = parser.parse_args(argv)
    try:
        target = MainCandidate(args.target_run, args.target_commit)
        rollback = MainCandidate(args.rollback_run, args.rollback_commit)
        if args.stage:
            if not args.target_version:
                parser.error("--target-version is required with --stage")
            result = stage_owner_dev_candidates(
                target=target, target_version=args.target_version, rollback=rollback,
            )
        else:
            if args.target_version:
                parser.error("--target-version is only accepted with --stage")
            result = verify_owner_dev_preflight(
                args.transaction_root, target=target, rollback=rollback,
            )
    except (ValueError, RuntimeError, OSError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"schema": PREFLIGHT_SCHEMA, "state": "blocked",
                          "reason": type(error).__name__, "applyAllowed": False}))
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
