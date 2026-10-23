#!/usr/bin/env python3
"""Run the canonical Native agent against a verified private Buildroot Python.

Diagnostic only. The OS owns the Python/TAR; this never installs to a device,
creates credentials, grants MCP access or certifies the full Runtime closure.
Run inside a disposable Linux CI container with networking disabled.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile

from tools.ci.audit_independent_python_proof import (
    ProofError,
    load_json,
    verify_private_target_tar,
)

ROOT = Path(__file__).resolve().parents[2]
RESULT_SCHEMA = "ordax.native-buildroot-agent-bootstrap-probe/1"
MARKER = "ORDAX_NATIVE_BOOTSTRAP_PROOF="
MISSING_MODULE = re.compile(r"ModuleNotFoundError: No module named ['\"]([^'\"]+)['\"]")
# This subprocess is intentionally NOT an OS action service. No device state,
# MCP tokens or Product network endpoints may be supplied to it.
CHILD = """
import json
import socket
import sys
import tempfile
from pathlib import Path

def network_forbidden(*args, **kwargs):
    raise RuntimeError("native_bootstrap_network_forbidden")

socket.socket.connect = network_forbidden
socket.socket.connect_ex = network_forbidden
socket.create_connection = network_forbidden

sys.path.insert(0, sys.argv[1])
from ordax_dev_agent.native_main import native_config, self_test
from ordax_dev_agent.product_device_control_plane import ProductDeviceControlPlane

proof = self_test()
if not proof.get("ok") or proof.get("network_used") or proof.get("device_credential_used"):
    raise RuntimeError("native_offline_self_test_not_proven")
with tempfile.TemporaryDirectory(prefix="ordax-native-device-probe-") as temporary:
    root = Path(temporary)
    private = root / "private"
    private.mkdir(mode=0o700)
    device_id = "22222222-2222-4222-8222-222222222222"
    config = native_config(private, root, device_id)
    control = ProductDeviceControlPlane(
        config,
        device_credential_provider=lambda: {
            "device_id": device_id, "device_token": "a" * 64
        },
    )
    try:
        if control.runtime_kind != "ordax-os" or control.device_id != device_id:
            raise RuntimeError("native_product_transport_identity_mismatch")
        if control.product_central_session.status.confirmed:
            raise RuntimeError("native_offline_probe_claimed_remote_attestation")
    finally:
        control.product_central_session.close()
        control.http.close()
print("ORDAX_NATIVE_BOOTSTRAP_PROOF=" + json.dumps({
    "offline_actions_proven": True,
    "product_httpx_client_constructed": True,
    "registered_action_count": proof["registered_action_count"],
    "network_used": False,
    "device_credential_used": False,
}, sort_keys=True))
"""


def checked_file(path: Path, *, limit: int) -> Path:
    if not path.is_file() or path.is_symlink():
        raise ProofError("private Buildroot executable or loader missing/unsafe")
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or not 0 < st.st_size <= limit:
        raise ProofError("private Buildroot executable or loader size/type invalid")
    return path


def probe(receipt_path: Path, target_tar: Path, extracted_root: Path) -> dict:
    if os.name != "posix" or not Path("/proc/self").exists():
        raise ProofError("Native Buildroot smoke requires a disposable Linux host")
    if extracted_root.is_symlink() or not extracted_root.is_dir():
        raise ProofError("private Buildroot extraction root missing/unsafe")
    receipt, _ = load_json(receipt_path, 64 * 1024)
    # The existing reconciler is the sole verifier of the OS-issued TAR.
    tar_proof = verify_private_target_tar(target_tar, receipt)
    python_rel = receipt["python_abi"]["python_executable"]
    root = extracted_root.resolve(strict=True)
    binary = checked_file(root / python_rel, limit=128 * 1024 * 1024)
    actual = hashlib.sha256(binary.read_bytes()).hexdigest()
    if actual != tar_proof["python_elf_sha256"]:
        raise ProofError("extracted Buildroot Python differs from verified TAR")
    loader = root / "lib/ld-musl-x86_64.so.1"
    if not loader.exists() or loader.is_symlink() and loader.resolve().parent != (root / "lib"):
        raise ProofError("private Buildroot loader missing or outside target root")
    loader = checked_file(loader.resolve(), limit=16 * 1024 * 1024)
    for directory in (root / "usr/lib/python3.14", root / "usr/lib", root / "lib"):
        if not directory.is_dir() or directory.is_symlink():
            raise ProofError("private Buildroot stdlib/library root unavailable")
    if not (ROOT / "ordax_dev_agent/native_main.py").is_file():
        raise ProofError("canonical checked-out Native agent source unavailable")
    with tempfile.TemporaryDirectory(prefix="ordax-native-bootstrap-execution-") as scratch:
        # -I isolates Python from user/site environment. Interpreter/stdlib/libs
        # are the OS private target, never the host's Python packages.
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": scratch,
            "LANG": "C",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHOME": str(root / "usr"),
            "LD_LIBRARY_PATH": str(root / "lib") + ":" + str(root / "usr/lib"),
        }
        command = [str(loader), str(binary), "-I", "-B", "-c", CHILD, str(ROOT)]
        try:
            execution = subprocess.run(
                command, cwd=scratch, env=env,
                text=True, capture_output=True, timeout=60, check=False,
            )
        except subprocess.TimeoutExpired:
            execution = subprocess.CompletedProcess(command, 124, "", "timeout")
    summary = {
        "schema": RESULT_SCHEMA,
        "private_python_sha256": actual,
        "os_target_tar_verified": True,
        "canonical_runtime_source": True,
        "offline_bootstrap_proven": False,
        "full_native_runtime_dependency_closure_proven": False,
        "native_runtime_agent_packaged": False,
        "device_install_authorized": False,
        "device_execution_proven": False,
    }
    if execution.returncode:
        missing = MISSING_MODULE.search(execution.stderr)
        summary["failure"] = (
            "native_missing_import" if missing else
            "native_bootstrap_timeout" if execution.returncode == 124 else
            "native_bootstrap_failed"
        )
        if missing:
            summary["missing_module"] = missing.group(1)
        return summary
    lines = [line.removeprefix(MARKER) for line in execution.stdout.splitlines()
             if line.startswith(MARKER)]
    if len(lines) != 1:
        summary["failure"] = "native_bootstrap_proof_missing"
        return summary
    try:
        proof = json.loads(lines[0])
    except json.JSONDecodeError:
        summary["failure"] = "native_bootstrap_proof_invalid"
        return summary
    if (proof.get("offline_actions_proven") is not True
            or proof.get("product_httpx_client_constructed") is not True
            or proof.get("network_used") is not False
            or proof.get("device_credential_used") is not False
            or type(proof.get("registered_action_count")) is not int
            or proof["registered_action_count"] < 100):
        summary["failure"] = "native_bootstrap_proof_invalid"
        return summary
    summary["offline_bootstrap_proven"] = True
    summary["registered_action_count"] = proof["registered_action_count"]
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--private-target-tar", type=Path, required=True)
    parser.add_argument("--extracted-private-root", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = probe(args.receipt, args.private_target_tar, args.extracted_private_root)
    except (ProofError, OSError, ValueError) as exc:
        print(json.dumps({
            "schema": RESULT_SCHEMA, "failure": str(exc),
            "offline_bootstrap_proven": False, "device_install_authorized": False
        }, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["offline_bootstrap_proven"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
