"""Privileged, fail-closed Windows production update transaction.

Discovery stays in :mod:`ordax_dev_agent.product_update` with ``authority:none``.
This module is a separate local-only execution boundary. It stages immutable
GitHub Release assets, proves SHA-256 and Authenticode against the packaged
trust store, then hands installation to an external Windows PowerShell
bootstrap so the Runtime can safely quiesce itself during upgrade.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.resources
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import httpx

from .product_update import evaluate_release, fetch_latest_release, installed_windows_product, parse_version

REPOSITORY = "ordaxsystems/ordax-runtime"
RELEASE_TAG_API_PREFIX = "https://api.github.com/repos/ordaxsystems/ordax-runtime/releases/tags/"
TRUST_SCHEMA = "ordax.windows-release-trust/1"
TRANSACTION_SCHEMA = "ordax.windows-update-transaction/1"
_MAX_INSTALLER_BYTES = 1024 * 1024 * 1024
_MAX_CHECKSUM_BYTES = 64 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_THUMBPRINT = re.compile(r"^[0-9A-F]{40}$")
_ALLOWED_REDIRECT_HOSTS = {
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
}


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int | None


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".next")
    data = json.dumps(value, separators=(",", ":"), sort_keys=True)
    with pending.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(pending, path)


def load_release_trust(path: str | Path | None = None) -> tuple[str, ...]:
    if path is None:
        resource = importlib.resources.files("ordax_dev_agent").joinpath("assets/release_trust.json")
        text = resource.read_text(encoding="utf-8")
    else:
        candidate = Path(path)
        if candidate.stat().st_size > 16_384:
            raise ValueError("release trust store is too large")
        text = candidate.read_text(encoding="utf-8")
    value = json.loads(text)
    if not isinstance(value, dict) or value.get("schema") != TRUST_SCHEMA:
        raise ValueError("release trust store schema is invalid")
    raw = value.get("authorizedSignerThumbprints")
    if not isinstance(raw, list) or len(raw) > 4:
        raise ValueError("release trust store signer list is invalid")
    signers: list[str] = []
    for item in raw:
        normalized = str(item).replace(" ", "").upper()
        if not _THUMBPRINT.fullmatch(normalized):
            raise ValueError("release trust store contains an invalid signer thumbprint")
        if normalized in signers:
            raise ValueError("release trust store contains duplicate signer thumbprints")
        signers.append(normalized)
    return tuple(signers)


def _required_asset_names(version: str) -> tuple[str, str]:
    parse_version(version)
    return f"ORDAX-Studio-Setup-{version}-x64.exe", "SHA256SUMS.txt"


def _canonical_release_asset_url(version: str, name: str) -> str:
    return f"https://github.com/{REPOSITORY}/releases/download/v{version}/{quote(name)}"


def select_release_assets(release: dict[str, Any], version: str) -> dict[str, ReleaseAsset]:
    installer_name, checksums_name = _required_asset_names(version)
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ValueError("release assets are missing")
    selected: dict[str, ReleaseAsset] = {}
    required = {installer_name, checksums_name}
    for raw in assets:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        if name not in required:
            continue
        if name in selected:
            raise ValueError(f"release contains duplicate asset: {name}")
        url = raw.get("browser_download_url")
        if not isinstance(url, str) or url != _canonical_release_asset_url(version, name):
            raise ValueError(f"release asset URL is not canonical: {name}")
        size = raw.get("size")
        if size is not None and (type(size) is not int or size < 0):
            raise ValueError(f"release asset size is invalid: {name}")
        selected[name] = ReleaseAsset(name=name, url=url, size=size)
    missing = required.difference(selected)
    if missing:
        raise ValueError(f"release is missing required assets: {sorted(missing)}")
    return selected


def _validate_redirect(target_url: str) -> None:
    target = urlparse(target_url)
    if target.scheme != "https" or target.username or target.password or target.fragment:
        raise ValueError("release asset redirect is not HTTPS canonical transport")
    host = (target.hostname or "").lower()
    if host not in _ALLOWED_REDIRECT_HOSTS:
        raise ValueError(f"release asset redirect host is not allowed: {host}")


def download_release_asset(
    asset: ReleaseAsset,
    destination: str | Path,
    *,
    max_size_bytes: int,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    if type(max_size_bytes) is not int or max_size_bytes <= 0:
        raise ValueError("max_size_bytes must be a positive integer")
    if asset.size is not None and asset.size > max_size_bytes:
        raise ValueError(f"release asset is too large: {asset.name}")
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    partial = target.with_suffix(target.suffix + ".partial")
    if partial.exists() or partial.is_symlink():
        raise FileExistsError(partial)

    owned_client = client is None
    session = client or httpx.Client(
        timeout=httpx.Timeout(30.0, read=60.0),
        headers={"User-Agent": "ORDAX-Production-Updater"},
        follow_redirects=False,
    )
    digest = hashlib.sha256()
    written = 0
    current = asset.url
    try:
        with partial.open("xb") as stream:
            for _ in range(6):
                with session.stream("GET", current, follow_redirects=False) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("release asset redirect is missing Location")
                        next_url = urljoin(current, location)
                        _validate_redirect(next_url)
                        current = next_url
                        continue
                    response.raise_for_status()
                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            declared = int(content_length)
                        except ValueError as error:
                            raise ValueError("release asset Content-Length is invalid") from error
                        if declared < 0 or written + declared > max_size_bytes:
                            raise ValueError("release asset exceeds maximum size")
                    for chunk in response.iter_bytes(64 * 1024):
                        if not chunk:
                            continue
                        written += len(chunk)
                        if written > max_size_bytes:
                            raise ValueError("release asset exceeds maximum size")
                        stream.write(chunk)
                        digest.update(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                    break
            else:
                raise ValueError("release asset exceeded redirect limit")
        if asset.size is not None and written != asset.size:
            raise ValueError(f"release asset size mismatch: {asset.name}")
        os.replace(partial, target)
        return {"path": str(target), "sizeBytes": written, "sha256": digest.hexdigest()}
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    finally:
        if owned_client:
            session.close()


def parse_checksum_manifest(text: str, installer_name: str) -> str:
    if "\x00" in text:
        raise ValueError("checksum manifest contains NUL")
    entries: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.fullmatch(r"([0-9A-Fa-f]{64})[ \t]+[*]?([^/\\\r\n]+)", line)
        if not match:
            raise ValueError("checksum manifest contains an invalid line")
        digest = match.group(1).lower()
        name = match.group(2)
        if name in entries:
            raise ValueError(f"checksum manifest contains duplicate entry: {name}")
        entries[name] = digest
    expected = entries.get(installer_name)
    if expected is None:
        raise ValueError("checksum manifest does not cover the expected installer")
    return expected


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_release_by_tag(tag: str, *, timeout: float = 10.0) -> dict[str, Any]:
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise ValueError("release tag is invalid")
    parse_version(tag[1:])
    url = RELEASE_TAG_API_PREFIX + quote(tag, safe="")
    response = httpx.get(
        url,
        timeout=timeout,
        follow_redirects=False,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "ORDAX-Production-Updater"},
    )
    response.raise_for_status()
    if len(response.content) > 2_000_000:
        raise ValueError("release descriptor is too large")
    value = response.json()
    if not isinstance(value, dict):
        raise ValueError("release descriptor must be an object")
    return value


def _default_staging_root() -> Path:
    if sys.platform != "win32":
        raise RuntimeError("production update staging is Windows-only")
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is unavailable")
    return Path(local_app_data) / "OrdaX" / "updates"


def _stage_one_release(
    release: dict[str, Any],
    version: str,
    directory: Path,
    *,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    assets = select_release_assets(release, version)
    installer_name, checksum_name = _required_asset_names(version)
    checksum_result = download_release_asset(
        assets[checksum_name], directory / checksum_name,
        max_size_bytes=_MAX_CHECKSUM_BYTES, client=client,
    )
    manifest_text = Path(checksum_result["path"]).read_text(encoding="ascii")
    expected_sha256 = parse_checksum_manifest(manifest_text, installer_name)
    installer_result = download_release_asset(
        assets[installer_name], directory / installer_name,
        max_size_bytes=_MAX_INSTALLER_BYTES, client=client,
    )
    if installer_result["sha256"] != expected_sha256:
        raise ValueError("installer SHA-256 does not match SHA256SUMS.txt")
    return {
        "version": version,
        "tag": f"v{version}",
        "installer": installer_name,
        "installerSha256": expected_sha256,
        "checksums": checksum_name,
    }


def prepare_update_transaction(
    installed_version: str,
    target_release: dict[str, Any],
    rollback_release: dict[str, Any],
    *,
    staging_root: str | Path | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    target_evaluation = evaluate_release(installed_version, target_release)
    if target_evaluation.get("state") != "update-available":
        raise ValueError("target release is not an applicable production upgrade")
    target_version = str(target_evaluation["availableVersion"])
    rollback_evaluation = evaluate_release(installed_version, rollback_release)
    if rollback_evaluation.get("state") != "current":
        raise ValueError("rollback release does not match the installed production version")

    root = Path(staging_root) if staging_root is not None else _default_staging_root()
    transaction_id = uuid.uuid4().hex
    transaction_root = root / f"{installed_version}-to-{target_version}" / transaction_id
    transaction_root.mkdir(parents=True, exist_ok=False)
    target_dir = transaction_root / "target"
    rollback_dir = transaction_root / "rollback"
    target_dir.mkdir()
    rollback_dir.mkdir()

    try:
        target = _stage_one_release(target_release, target_version, target_dir, client=client)
        rollback = _stage_one_release(rollback_release, installed_version, rollback_dir, client=client)
        journal = {
            "schema": TRANSACTION_SCHEMA,
            "authority": "local-privileged-updater",
            "transactionId": transaction_id,
            "phase": "sha256-verified",
            "installedVersion": installed_version,
            "target": target,
            "rollback": rollback,
        }
        _atomic_json(transaction_root / "transaction.json", journal)
        return {"root": str(transaction_root), **journal}
    except Exception:
        shutil.rmtree(transaction_root, ignore_errors=True)
        raise


def _installed_script(name: str) -> Path:
    if sys.platform != "win32":
        raise RuntimeError("production update verification is Windows-only")
    runtime_root = Path(sys.executable).resolve().parent
    install_root = runtime_root.parent
    script = install_root / "scripts" / "windows" / name
    if not script.is_file():
        raise RuntimeError(f"installed updater script is missing: {name}")
    return script


def _windows_powershell() -> Path:
    system_root = os.environ.get("SystemRoot")
    if not system_root:
        raise RuntimeError("SystemRoot is unavailable")
    candidate = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not candidate.is_file():
        raise RuntimeError("Windows PowerShell is unavailable")
    return candidate


def _read_transaction(root: Path) -> dict[str, Any]:
    path = root / "transaction.json"
    if not path.is_file() or path.stat().st_size > 64 * 1024:
        raise ValueError("update transaction journal is missing or invalid")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != TRANSACTION_SCHEMA:
        raise ValueError("update transaction schema is invalid")
    return value


def _validated_transaction_material(
    root: Path,
    journal: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    installed_version = str(journal.get("installedVersion", ""))
    installed = parse_version(installed_version)
    result: dict[str, dict[str, Any]] = {}
    for role in ("target", "rollback"):
        item = journal.get(role)
        if not isinstance(item, dict):
            raise ValueError(f"transaction {role} descriptor is invalid")
        version = str(item.get("version", ""))
        parsed = parse_version(version)
        if role == "target" and parsed <= installed:
            raise ValueError("transaction target version is not newer than installed version")
        if role == "rollback" and parsed != installed:
            raise ValueError("transaction rollback version does not match installed version")
        expected_installer, expected_checksums = _required_asset_names(version)
        if item.get("tag") != f"v{version}" or item.get("installer") != expected_installer:
            raise ValueError(f"transaction {role} release identity is invalid")
        if item.get("checksums") != expected_checksums:
            raise ValueError(f"transaction {role} checksum identity is invalid")
        expected_sha256 = str(item.get("installerSha256", ""))
        if not _SHA256.fullmatch(expected_sha256):
            raise ValueError(f"transaction {role} SHA-256 is invalid")
        installer = root / role / expected_installer
        checksum_path = root / role / expected_checksums
        expected_parent = (root / role).resolve()
        # The initial SHA-256 verification is not a perpetual authorization:
        # a staged transaction is revalidated immediately before every handoff.
        if (installer.is_symlink() or installer.resolve().parent != expected_parent
                or not installer.is_file()):
            raise ValueError(f"transaction {role} installer path is invalid")
        if (checksum_path.is_symlink() or checksum_path.resolve().parent != expected_parent
                or not checksum_path.is_file()
                or checksum_path.stat().st_size > 16 * 1024):
            raise ValueError(f"transaction {role} checksum manifest path is invalid")
        if _hash_file(installer) != expected_sha256:
            raise ValueError(f"transaction {role} installer integrity changed after staging")
        try:
            staged_checksum = parse_checksum_manifest(
                checksum_path.read_text(encoding="ascii"), expected_installer
            )
        except (OSError, UnicodeError, ValueError) as error:
            raise ValueError(f"transaction {role} checksum manifest changed after staging") from error
        if staged_checksum != expected_sha256:
            raise ValueError(f"transaction {role} checksum manifest changed after staging")
        result[role] = item
    return result["target"], result["rollback"]


def verify_staged_authenticode(
    transaction_root: str | Path,
    *,
    trust_store: str | Path | None = None,
    verification_script: str | Path | None = None,
    powershell: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(transaction_root).resolve()
    journal = _read_transaction(root)
    if journal.get("phase") != "sha256-verified":
        raise ValueError("update transaction is not ready for Authenticode verification")
    signers = load_release_trust(trust_store)
    if not signers:
        raise RuntimeError("production release trust store has no authorized signer")
    target, rollback = _validated_transaction_material(root, journal)
    verifier = Path(verification_script) if verification_script else _installed_script("assert-release-authenticode.ps1")
    shell = Path(powershell) if powershell else _windows_powershell()

    for role, item in (("target", target), ("rollback", rollback)):
        installer = root / role / item["installer"]
        completed = subprocess.run(
            [
                str(shell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", str(verifier), "-Path", str(installer),
                "-TrustedSignerThumbprints", ",".join(signers),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
            shell=False,
        )
        if completed.returncode != 0 or "ORDAX_AUTHENTICODE_VALID" not in completed.stdout:
            raise RuntimeError(f"Authenticode verification failed for {role} installer")
    journal["phase"] = "authenticode-verified"
    journal["authorizedSignerThumbprints"] = list(signers)
    _atomic_json(root / "transaction.json", journal)
    return {"root": str(root), **journal}


def _require_recorded_signers(journal: dict[str, Any], signers: tuple[str, ...]) -> None:
    recorded = journal.get("authorizedSignerThumbprints")
    if not isinstance(recorded, list) or tuple(recorded) != signers:
        raise ValueError("update transaction signer trust changed after verification")


def launch_verified_update(
    transaction_root: str | Path,
    *,
    bootstrap_script: str | Path | None = None,
    verification_script: str | Path | None = None,
    powershell: str | Path | None = None,
) -> dict[str, Any]:
    """Hand off to external PowerShell; final success lives in the journal."""
    if sys.platform != "win32":
        raise RuntimeError("production update application is Windows-only")
    root = Path(transaction_root).resolve()
    journal = _read_transaction(root)
    if journal.get("phase") != "authenticode-verified":
        raise ValueError("update transaction is not verified for application")
    signers = load_release_trust()
    if not signers:
        raise RuntimeError("production release trust store has no authorized signer")
    _require_recorded_signers(journal, signers)
    target, rollback = _validated_transaction_material(root, journal)
    product = installed_windows_product()
    if product["version"] != journal.get("installedVersion"):
        raise RuntimeError("installed product changed after update staging")

    source_bootstrap = Path(bootstrap_script) if bootstrap_script else _installed_script("apply-ordax-production-update.ps1")
    source_verifier = Path(verification_script) if verification_script else _installed_script("assert-release-authenticode.ps1")
    shell = Path(powershell) if powershell else _windows_powershell()
    staged_bootstrap = root / "apply-ordax-production-update.ps1"
    staged_verifier = root / "assert-release-authenticode.ps1"
    shutil.copyfile(source_bootstrap, staged_bootstrap)
    shutil.copyfile(source_verifier, staged_verifier)
    command = [
        str(shell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(staged_bootstrap),
        "-TransactionRoot", str(root),
        "-TargetInstaller", str(root / "target" / target["installer"]),
        "-TargetSha256", target["installerSha256"],
        "-TargetVersion", target["version"],
        "-RollbackInstaller", str(root / "rollback" / rollback["installer"]),
        "-RollbackSha256", rollback["installerSha256"],
        "-RollbackVersion", rollback["version"],
        "-InstallRoot", product["installLocation"],
        "-TrustedSignerThumbprints", ",".join(signers),
        "-VerifierScript", str(staged_verifier),
    ]
    flags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        flags |= subprocess.DETACHED_PROCESS
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        flags |= subprocess.CREATE_NEW_PROCESS_GROUP

    journal["phase"] = "handoff-launching"
    _atomic_json(root / "transaction.json", journal)
    try:
        subprocess.Popen(
            command, cwd=str(root), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, creationflags=flags, shell=False,
        )
    except Exception:
        journal["phase"] = "handoff-failed"
        _atomic_json(root / "transaction.json", journal)
        raise
    return {
        "schema": TRANSACTION_SCHEMA,
        "transactionId": journal["transactionId"],
        "phase": "handoff-launching",
        "root": str(root),
    }


def prepare_current_update() -> dict[str, Any]:
    product = installed_windows_product()
    target = fetch_latest_release()
    evaluation = evaluate_release(product["version"], target)
    if evaluation.get("state") != "update-available":
        return {"state": evaluation.get("state"), "installedVersion": product["version"]}
    rollback = fetch_release_by_tag(f"v{product['version']}")
    return prepare_update_transaction(product["version"], target, rollback)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--verify", metavar="TRANSACTION_ROOT")
    mode.add_argument("--apply", metavar="TRANSACTION_ROOT")
    args = parser.parse_args(argv)
    try:
        if args.prepare:
            result = prepare_current_update()
        elif args.verify:
            result = verify_staged_authenticode(args.verify)
        else:
            result = launch_verified_update(args.apply)
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except Exception as error:
        print(json.dumps({
            "schema": TRANSACTION_SCHEMA,
            "state": "blocked",
            "error": type(error).__name__,
        }, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
