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
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from .product_update import installed_windows_product
from .product_update_apply import (
    TRANSACTION_SCHEMA,
    _read_transaction,
    _validated_transaction_material,
)

REPOSITORY = "ordaxsystems/ordax-runtime"
WORKFLOW = ".github/workflows/windows-product-build.yml"
SIGNER_WORKFLOW = f"{REPOSITORY}/{WORKFLOW}"
PREFLIGHT_SCHEMA = "ordax.owner-dev-provenance-preflight/1"
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
    if target == rollback or target.run_id == rollback.run_id:
        raise ValueError("target and rollback must be separately attested candidates")

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
    parser = argparse.ArgumentParser(description="Read-only Owner/Dev installer provenance check")
    parser.add_argument("--transaction-root", required=True)
    parser.add_argument("--target-run", required=True)
    parser.add_argument("--target-commit", required=True)
    parser.add_argument("--rollback-run", required=True)
    parser.add_argument("--rollback-commit", required=True)
    args = parser.parse_args(argv)
    try:
        result = verify_owner_dev_preflight(
            args.transaction_root,
            target=MainCandidate(args.target_run, args.target_commit),
            rollback=MainCandidate(args.rollback_run, args.rollback_commit),
        )
    except (ValueError, RuntimeError, OSError, TypeError, json.JSONDecodeError) as error:
        print(json.dumps({"schema": PREFLIGHT_SCHEMA, "state": "blocked",
                          "reason": type(error).__name__, "applyAllowed": False}))
        return 2
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
