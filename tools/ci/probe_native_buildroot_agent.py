#!/usr/bin/env python3
"""Probe the ONE native OrdaX agent against an OS-owned private Buildroot Python.

This is a read-only, offline development gate, NOT a builder, installer,
signed runtime candidate, or a proof of device attestation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile

if __name__ == "__main__" and not __package__:
    # Direct file-path invocation places tools/ci on sys.path.
    from audit_independent_python_proof import ProofError, reconcile
else:
    from tools.ci.audit_independent_python_proof import ProofError, reconcile


# Must remain the actual Native entrypoint and Product HTTPX transport,
# not a copied action registry or a second device dispatcher.
_OFFLINE_BOOTSTRAP = r"""
import json
import tempfile
from pathlib import Path

from ordax_dev_agent.native_main import native_config, self_test
from ordax_dev_agent.product_device_control_plane import ProductDeviceControlPlane

proof = self_test()
if not proof.get("ok") or proof.get("network_used") or proof.get("device_credential_used"):
    raise RuntimeError("canonical native offline self-test failed closed")
if proof.get("graphical_actions"):
    raise RuntimeError("graphical device adapter was not proven")

device_id = "22222222-2222-4222-8222-222222222222"
with tempfile.TemporaryDirectory(prefix="ordax-native-agent-") as temporary:
    root = Path(temporary)
    private = root / "private"
    private.mkdir(mode=0o700)
    config = native_config(private, root, device_id)
    control = ProductDeviceControlPlane(
        config,
        device_credential_provider=lambda: {
            "device_id": device_id, "device_token": "a" * 64
        },
    )
    try:
        if control.runtime_kind != "ordax-os":
            raise RuntimeError("wrong canonical runtime")
        if control.product_central_session.status.confirmed:
            raise RuntimeError("offline probe must never claim an attested session")
    finally:
        control.product_central_session.close()
        control.http.close()

print(json.dumps({
    "native_self_test_ok": True,
    "action_count": proof["registered_action_count"],
    "product_httpx_client_initialized": True,
    "network_used": False,
    "device_credential_used": False,
    "attested_session": False,
    "runtime_agent_packaged": False,
    "device_install_authorized": False,
}, sort_keys=True))
"""


def _stage_verified_target(target_tar: Path, expected_sha256: str, destination: Path) -> None:
    """Extract only into a fresh private staging directory using Python's data filter.

    Never extract directly to an OS base, host /, component slot or USB.
    Rehash from the SAME opened descriptor before and after extraction. Existing
    receipt/target/package metadata verification happens before this function.
    """
    if destination.exists():
        raise ProofError("private probe staging directory already exists")
    destination.mkdir(mode=0o700)
    try:
        descriptor = os.open(
            target_tar, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
        )
    except OSError as exc:
        raise ProofError("private probe target is missing or unsafe") from exc
    with os.fdopen(descriptor, "rb") as source:
        first = os.fstat(source.fileno())
        if not stat.S_ISREG(first.st_mode) or not 0 < first.st_size <= 1024 * 1024 * 1024:
            raise ProofError("private probe target is not a bounded regular file")
        h = hashlib.sha256()
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
        if h.hexdigest() != expected_sha256:
            raise ProofError("private probe target bytes changed since OS receipt")
        source.seek(0)
        try:
            with tarfile.open(fileobj=source, mode="r:") as archive:
                # Fail before extraction on specials, duplicates and path aliasing.
                seen = set()
                count = 0
                for member in archive:
                    count += 1
                    if count > 200000:
                        raise ProofError("private probe archive entry limit exceeded")
                    # Buildroot rootfs.tar begins with a canonical '.' directory.
                    # A root file/link remains forbidden; only this directory
                    # header is allowed, with no destination path created.
                    if member.name in (".", "./"):
                        if not member.isdir():
                            raise ProofError("private probe root TAR member is not a directory")
                        continue
                    name = member.name.removeprefix("./").rstrip("/")
                    if (not name or name in seen or name.startswith("/")
                            or any(part in ("", ".", "..") for part in name.split("/"))):
                        raise ProofError("private probe archive path is unsafe or duplicated")
                    if not (member.isfile() or member.isdir() or member.issym()):
                        raise ProofError("private probe refuses special files and hard links")
                    seen.add(name)
                source.seek(0)
                with tarfile.open(fileobj=source, mode="r:") as archive:
                    # "data" rejects outside-root links, devices, unsafe permissions
                    # and traversal; unlike fully_trusted it is safe for untrusted tar.
                    archive.extractall(destination, filter="data")
        except ProofError:
            # Preserve an actionable canonical policy rejection; it must not
            # be disguised as an unrelated low-level TAR extraction failure.
            raise
        except (tarfile.TarError, OSError, ValueError) as exc:
            raise ProofError("private probe target extraction was rejected") from exc
        source.seek(0)
        after_digest = hashlib.sha256()
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            after_digest.update(chunk)
        last = os.fstat(source.fileno())
        if (after_digest.hexdigest() != expected_sha256
                or (first.st_dev, first.st_ino, first.st_size,
                    first.st_mtime_ns, first.st_ctime_ns) !=
                   (last.st_dev, last.st_ino, last.st_size,
                    last.st_mtime_ns, last.st_ctime_ns)):
            raise ProofError("private probe archive changed during staging")


def offline_probe(receipt: Path, os_source: Path, lock: Path, target_tar: Path,
                  source_tree: Path, sdk_tar: Path | None = None) -> dict:
    """Return a precise, non-installable diagnostic or fail closed."""
    required = (
        "ordax_dev_agent/native_main.py",
        "ordax_dev_agent/product_device_control_plane.py",
        "ordax_core/__init__.py",
        "ordax_connector/capabilities.py",
        "mcp_blender_unity/__init__.py",
    )
    source_tree = source_tree.resolve(strict=True)
    if any(not (source_tree / name).is_file() for name in required):
        raise ProofError("native probe needs complete canonical Runtime source checkout")
    verified = reconcile(
        receipt, os_source, lock,
        private_target_tar=target_tar, private_sdk_tar=sdk_tar,
    )
    target_proof = verified.get("private_os_build_input_verification")
    if not isinstance(target_proof, dict) or target_proof.get("os_private_build_input_verified") is not True:
        raise ProofError("private target has not passed canonical Runtime verification")
    if sdk_tar is not None:
        sdk = verified.get("private_os_sdk_build_input_verification")
        if not isinstance(sdk, dict) or sdk.get("same_build_private_sdk_verified") is not True:
            raise ProofError("private SDK has not passed canonical Runtime verification")
    receipt_data = json.loads(receipt.read_text(encoding="utf-8"))
    python_relative = receipt_data["python_abi"]["python_executable"]
    if not python_relative.startswith("usr/bin/python3."):
        raise ProofError("private Buildroot executable identity is invalid")
    with tempfile.TemporaryDirectory(prefix="ordax-native-buildroot-probe-") as temporary:
        base = Path(temporary)
        staged = base / "target"
        _stage_verified_target(target_tar, target_proof["private_target_tar_sha256"], staged)
        loader = staged / "lib/ld-musl-x86_64.so.1"
        python = staged / python_relative
        if not loader.is_file() or not python.is_file():
            raise ProofError("private Buildroot interpreter or musl loader unavailable")
        version = python.name.removeprefix("python")
        env = os.environ.copy()
        for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONINSPECT",
                    "LD_PRELOAD", "LD_AUDIT", "LD_LIBRARY_PATH"):
            env.pop(key, None)
        env.update({
            "PYTHONHOME": str(staged / "usr"),
            "PYTHONPATH": os.pathsep.join((
                str(source_tree),
                str(staged / "usr/lib" / ("python" + version) / "site-packages"),
            )),
            "LD_LIBRARY_PATH": str(staged / "lib") + ":" + str(staged / "usr/lib"),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TMPDIR": str(base),
            "HOME": str(base),
        })
        completed = subprocess.run(
            [str(loader), str(python), "-S", "-s", "-B", "-c", _OFFLINE_BOOTSTRAP],
            cwd=base, env=env, capture_output=True, text=True, timeout=90,
            check=False,
        )
        if completed.returncode:
            detail = (completed.stderr + completed.stdout)[-2400:]
            raise ProofError("canonical Native Buildroot bootstrap failed: " + detail)
        try:
            probe = json.loads(completed.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError) as exc:
            raise ProofError("canonical Native Buildroot probe did not return JSON") from exc
        if (probe.get("native_self_test_ok") is not True
                or probe.get("product_httpx_client_initialized") is not True
                or any(probe.get(x) is not False
                       for x in ("network_used", "device_credential_used", "attested_session",
                                 "runtime_agent_packaged", "device_install_authorized"))):
            raise ProofError("canonical Native Buildroot bootstrap report is unsafe")
    return {
        "schema": "ordax.native-buildroot-offline-agent-probe/1",
        "private_python_abi": receipt_data["python_abi"]["python_executable"],
        "private_target_sha256": target_proof["private_target_tar_sha256"],
        "native_import_and_self_test_proven": True,
        "agent_observation": probe,
        "full_native_runtime_dependency_closure_proven": False,
        "ordax_usb_execution_proven": False,
        "device_install_authorized": False,
        "attested_session": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--os-source", type=Path, required=True)
    parser.add_argument("--requirements", type=Path,
                        default=Path("native/ordax-product-agent/requirements.lock"))
    parser.add_argument("--private-target-tar", type=Path, required=True)
    parser.add_argument("--private-sdk-tar", type=Path)
    parser.add_argument("--source-tree", type=Path, default=Path("."))
    args = parser.parse_args()
    try:
        report = offline_probe(args.receipt, args.os_source, args.requirements,
                               args.private_target_tar, args.source_tree,
                               sdk_tar=args.private_sdk_tar)
    except (ProofError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"error": str(exc), "device_install_authorized": False}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
