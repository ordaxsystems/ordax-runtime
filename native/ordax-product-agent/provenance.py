"""Bind an offline-tested native candidate to its exact CI source and bytes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import tomllib

from audit_elf import audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch("[0-9a-f]{40}", args.source_commit):
        parser.error("exact source SHA required")
    binary = args.output / "ordax-product-agent"
    raw = binary.read_bytes()
    if raw[:5] != b"\x7fELF\x02" or raw[18:20] != b"\x3e\x00":
        raise ValueError("candidate is not an amd64 ELF executable")
    proof = json.loads((args.output / "self-test.json").read_text())
    if (proof.get("ok") is not True or proof.get("network_used") is not False
        or proof.get("device_credential_used") is not False
        or proof.get("graphical_actions") != []
        or proof.get("probes") != ["filesystem", "terminal", "supervised-process"]
        or proof.get("supervised_process_actions") != [
            "start", "status", "list", "write_stdin", "logs", "stop"]):
        raise ValueError("offline executable proof is missing or invalid")
    root = Path(__file__).resolve().parents[2]
    digest = hashlib.sha256(raw).hexdigest()
    # The provenance is also the one authoritative ABI disclosure for this
    # candidate. Offline tests in Alpine cannot prove Independent Base support.
    abi = audit(binary, Path(__file__).with_name("Dockerfile"))
    manifest = {
        "schema": "ordax.native-product-runtime-candidate/1",
        "source_repository": "ordaxsystems/ordax-runtime",
        "source_commit": args.source_commit,
        "product_version": tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"],
        "platform": "linux-amd64-musl", "artifact": binary.name,
        "size_bytes": len(raw), "sha256": digest,
        "recipe_sha256": hashlib.sha256((Path(__file__).with_name("Dockerfile")).read_bytes()).hexdigest(),
        "requirements_sha256": hashlib.sha256((Path(__file__).with_name("requirements.lock")).read_bytes()).hexdigest(),
        "python_required_on_device": False, "graphical_control_implemented": False,
        "independent_base_abi_ready": abi["independent_base_abi_ready"],
        "independent_base_abi_blockers": abi["blockers"],
        "elf_interpreter": abi["interpreter"],
        "elf_needed_libraries": abi["needed_libraries"],
        "embedded_elf_inventory": abi["embedded_elf_inventory"],
        "signature_present": False, "installation_authorized_by_candidate": False,
        "stable_activation_authorized": False,
    }
    (args.output / "candidate.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (args.output / "SHA256SUMS").write_text(f"{digest}  {binary.name}\n")


if __name__ == "__main__":
    main()
