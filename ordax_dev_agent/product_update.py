"""Read-only ORDAX Windows product update discovery.

This module never installs, downloads executables, or grants authority.  It only
compares the registered Windows product with a release descriptor that can later
be subjected to checksum, Authenticode and installation smoke gates.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from typing import Any

import httpx

PRODUCT_APP_ID = "{0D31F22D-8451-4CF4-9E34-F0D4D857F55F}_is1"
RELEASE_API = "https://api.github.com/repos/ordaxsystems/ordax-runtime/releases/latest"
_VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def parse_version(value: str) -> tuple[int, int, int]:
    match = _VERSION.fullmatch(str(value).strip())
    if not match:
        raise ValueError("version must be stable MAJOR.MINOR.PATCH")
    return tuple(int(part) for part in match.groups())


def evaluate_release(installed_version: str, release: dict[str, Any]) -> dict[str, Any]:
    installed = parse_version(installed_version)
    if not isinstance(release, dict):
        raise ValueError("release descriptor must be an object")
    if release.get("draft") is True or release.get("prerelease") is True:
        return {"state": "no-production-release", "installedVersion": installed_version}
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not tag.startswith("v"):
        raise ValueError("release tag is invalid")
    available_version = tag[1:]
    available = parse_version(available_version)
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ValueError("release assets are missing")
    names = [item.get("name") for item in assets if isinstance(item, dict)]
    installer = f"ORDAX-Studio-Setup-{available_version}-x64.exe"
    required = {installer, "SHA256SUMS.txt"}
    if not required.issubset(set(names)):
        return {
            "state": "release-incomplete",
            "installedVersion": installed_version,
            "availableVersion": available_version,
            "missingAssets": sorted(required.difference(set(names))),
        }
    relation = "update-available" if available > installed else "current" if available == installed else "installed-newer"
    return {
        "state": relation,
        "installedVersion": installed_version,
        "availableVersion": available_version,
        "tag": tag,
        "installer": installer,
        "verificationRequired": ["sha256", "authenticode-authorized-signer", "install-smoke"],
        "applyAllowed": False,
    }


def installed_windows_product() -> dict[str, str]:
    if sys.platform != "win32":
        raise RuntimeError("installed product discovery is Windows-only")
    import winreg

    key_path = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{PRODUCT_APP_ID}"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        version = str(winreg.QueryValueEx(key, "DisplayVersion")[0])
        location = str(winreg.QueryValueEx(key, "InstallLocation")[0])
    parse_version(version)
    return {"version": version, "installLocation": location}


def fetch_latest_release(url: str = RELEASE_API, timeout: float = 10.0) -> dict[str, Any]:
    if url != RELEASE_API:
        raise ValueError("production update discovery only accepts the canonical release endpoint")
    response = httpx.get(url, timeout=timeout, follow_redirects=False, headers={"Accept": "application/vnd.github+json", "User-Agent": "ORDAX-Runtime-Update-Discovery"})
    response.raise_for_status()
    if len(response.content) > 2_000_000:
        raise ValueError("release descriptor is too large")
    value = response.json()
    if not isinstance(value, dict):
        raise ValueError("release descriptor must be an object")
    return value


def status() -> dict[str, Any]:
    product = installed_windows_product()
    try:
        release = fetch_latest_release()
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 404:
            return {
                "schema": "ordax.windows-update-status/1",
                "channel": "production",
                "authority": "none",
                "state": "no-production-release",
                "installedVersion": product["version"],
                "installLocation": product["installLocation"],
                "applyAllowed": False,
            }
        raise
    result = evaluate_release(product["version"], release)
    return {
        "schema": "ordax.windows-update-status/1",
        "channel": "production",
        "authority": "none",
        "installLocation": product["installLocation"],
        **result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args(argv)
    if not args.status:
        parser.error("--status is required")
    try:
        print(json.dumps(status(), separators=(",", ":")))
        return 0
    except Exception as error:
        print(json.dumps({"schema": "ordax.windows-update-status/1", "channel": "production", "authority": "none", "state": "unavailable", "error": type(error).__name__}, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
