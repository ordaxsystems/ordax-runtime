#!/usr/bin/env python3
"""Read-only Runtime lock reconciliation against OS-owned Buildroot Python proof.

This is NOT a second Buildroot builder or a Runtime release/installation gate.
GitHub artifact authenticity and the source-tree ABI verifier remain OS-owned.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from tools.ci.audit_buildroot_lock import read_requirements

RECEIPT_SCHEMA = "ordax.private-native-python-build-proof/1"
SOURCE_SCHEMA = "prototype-ordax.independent-base-experiment/1"
COMPONENT = "native-product-runtime"
OWNER = "ordaxsystems/ordax-runtime"
SHA = re.compile(r"[0-9a-f]{64}\Z")
VERSION = re.compile(r"3\.\d+\.\d+\Z")
PKG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class ProofError(ValueError):
    pass


def load_json(path: Path, limit: int) -> tuple[dict, str]:
    if not path.is_file() or path.is_symlink() or not 2 <= path.stat().st_size <= limit:
        raise ProofError("private Buildroot proof input missing, unsafe or oversized")
    payload = path.read_bytes()
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProofError("private Buildroot proof JSON invalid") from exc
    if not isinstance(value, dict):
        raise ProofError("private Buildroot proof must be a JSON object")
    return value, hashlib.sha256(payload).hexdigest()


def reconcile(receipt_path: Path, os_source_path: Path, lock_path: Path) -> dict:
    receipt, receipt_sha = load_json(receipt_path, 64 * 1024)
    os_source, source_sha = load_json(os_source_path, 128 * 1024)
    profile = os_source.get("component_profiles", {}).get(COMPONENT)
    buildroot = os_source.get("buildroot")
    if (os_source.get("$schema") != SOURCE_SCHEMA or not isinstance(profile, dict)
            or not isinstance(buildroot, dict)
            or not SHA.fullmatch(str(buildroot.get("sha256", "")))
            or profile.get("source_owner") != OWNER
            or profile.get("base_rootfs_mutation_allowed") is not False
            or profile.get("component_artifact_authorized") is not False
            or profile.get("device_install_authorized") is not False):
        raise ProofError("OS Buildroot component source contract invalid")
    inputs = receipt.get("component_inputs")
    if (receipt.get("$schema") != RECEIPT_SCHEMA
            or receipt.get("status") != "build-proof-only"
            or receipt.get("buildroot_sha256") != buildroot["sha256"]
            or not isinstance(inputs, dict)
            or inputs.get("component") != COMPONENT
            or inputs.get("source_owner") != OWNER
            or inputs.get("buildroot_sha256") != buildroot["sha256"]
            or inputs.get("component_artifact_authorized") is not False
            or inputs.get("device_install_authorized") is not False
            or not all(SHA.fullmatch(str(inputs.get(k, "")))
                       for k in ("base_defconfig_sha256", "fragment_sha256"))):
        raise ProofError("private Python proof is not bound to canonical OS source")
    for name in ("runtime_agent_packaged", "component_artifact_authorized",
                 "device_install_authorized", "base_rootfs_modified"):
        if receipt.get(name) is not False:
            raise ProofError("private Buildroot proof falsely claims promotion authority")
    abi, smoke, built = (receipt.get("python_abi"),
                         receipt.get("private_python_smoke"),
                         receipt.get("httpx_buildroot_source_versions"))
    if not all(isinstance(x, dict) for x in (abi, smoke, built)):
        raise ProofError("private Python receipt lacks ABI, smoke or package versions")
    for name in ("runtime_agent_packaged", "component_install_authorized", "device_execution_proven"):
        if abi.get(name) is not False:
            raise ProofError("private Python ABI falsely claims release authority")
    for name in ("runtime_agent_packaged", "runtime_dependency_versions_reconciled",
                 "ordax_usb_execution_proven"):
        if smoke.get(name) is not False:
            raise ProofError("private Python smoke falsely claims Runtime execution")
    for name in ("all_imports_private", "tls_and_zlib_operational",
                 "httpx_mock_transport_proven", "httpx_offline_request_construction_proven",
                 "linux_kernel_execution_proven"):
        if smoke.get(name) is not True:
            raise ProofError("required private Python host smoke is missing")
    python = smoke.get("python_version")
    if (smoke.get("status") != "kernel-host-smoke-pass"
            or not isinstance(python, str) or not VERSION.fullmatch(python)
            or abi.get("python_executable") != "usr/bin/python" + ".".join(python.split(".")[:2])
            or abi.get("required_interpreter") != "/lib/ld-musl-x86_64.so.1"
            or not all(SHA.fullmatch(str(abi.get(k, "")))
                       for k in ("python_sha256", "elf_manifest_sha256"))
            or type(abi.get("compiled_elf_count")) is not int or abi["compiled_elf_count"] < 1
            or smoke.get("httpx_version") != built.get("httpx")):
        raise ProofError("private Python ABI/smoke identity mismatch")
    if (not 1 <= len(built) <= 64 or
            any(not isinstance(name, str) or not PKG.fullmatch(name)
                or not isinstance(value, str)
                or not re.fullmatch(r"[0-9]+(?:\.[0-9A-Za-z]+)+", value)
                for name, value in built.items())):
        raise ProofError("private Buildroot Python recipe versions invalid")

    pinned, lock_python = read_requirements(lock_path, lock=True)
    build_python = ".".join(python.split(".")[:2])
    mismatches = []
    if lock_python != build_python:
        mismatches.append({"dependency": "python3", "reason": "python-minor-abi-mismatch",
                           "runtime_locked": lock_python, "buildroot_built": build_python})
    for name, version in sorted(built.items()):
        if pinned.get(name) != version:
            mismatches.append({
                "dependency": name,
                "reason": "version-mismatch" if name in pinned else "not-in-runtime-lock",
                "runtime_locked": pinned.get(name), "buildroot_built": version,
            })
    not_proven = sorted(set(pinned) - set(built))
    return {
        "schema": "ordax.native-private-buildroot-lock-reconciliation/1",
        "os_source_sha256": source_sha,
        "receipt_sha256": receipt_sha,
        "buildroot_source_sha256": buildroot["sha256"],
        "private_python_sha256": abi["python_sha256"],
        "private_native_elf_inventory_sha256": abi["elf_manifest_sha256"],
        "runtime_lock_python_minor": lock_python,
        "buildroot_python_minor": build_python,
        "runtime_locked_package_count": len(pinned),
        "buildroot_probed_package_count": len(built),
        "version_mismatches": mismatches,
        "runtime_packages_not_proven_by_private_buildroot": not_proven,
        "version_alignment_proven": not mismatches and not not_proven,
        "full_native_runtime_dependency_closure_proven": False,
        "runtime_agent_packaged": False,
        "device_install_authorized": False,
        "device_execution_proven": False,
        "external_os_artifact_authenticity_verified_here": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--os-source", type=Path, required=True)
    parser.add_argument("--requirements", type=Path,
                        default=Path("native/ordax-product-agent/requirements.lock"))
    parser.add_argument("--require-alignment", action="store_true")
    args = parser.parse_args()
    try:
        result = reconcile(args.receipt, args.os_source, args.requirements)
    except (ProofError, OSError, UnicodeError, ValueError) as exc:
        print(json.dumps({"error": str(exc), "device_install_authorized": False}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 1 if args.require_alignment and not result["version_alignment_proven"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
