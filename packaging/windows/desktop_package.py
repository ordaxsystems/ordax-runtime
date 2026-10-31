"""Produce the OS-owned component-channel v1 format; no signing authority."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import stat
import zipfile


def encode(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")


def package(binary: Path, source: str, version: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("Exact source commit required")
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.[0-9]+\.[0-9]+", version):
        raise ValueError("Canonical Runtime version required")
    if binary.is_symlink() or not binary.is_file() or not 0 < binary.stat().st_size <= 28 << 20:
        raise ValueError("Invalid component executable")
    raw = binary.read_bytes()
    entry = "system/components/product-runtime/ordax-product-agent.exe"
    component = {"id": "product-runtime", "title": "Product Runtime", "kind": "service",
                 "version": version, "releaseMode": "component-slot", "criticality": "optional",
                 "failureDomain": "service", "restartScope": "component", "healthMode": "runtime",
                 "owner": "ordaxsystems/ordax-runtime", "dependencies": []}
    schema = "prototype-ordax.runtime-component-package/1"
    manifest = {"$schema": schema, "status": "candidate", "component": component,
                "source_commit": source, "entrypoint": entry, "target_platform": "windows-amd64",
                "self_contained_source_graph": True, "remote_runtime_dependencies": False,
                "activation_allowed": False, "signature_required_before_activation": True,
                "native_adapters_packaged": False, "composition_packaged": False,
                "files": [{"path": entry, "sha256": hashlib.sha256(raw).hexdigest(),
                           "size": len(raw), "executable": True}]}
    manifest_bytes = encode(manifest)
    archive = binary.with_name("product-runtime.zip")
    with zipfile.ZipFile(archive, "w") as out:
        for name, content, mode in (("component-package.json", manifest_bytes, 0o444),
                                    (entry, raw, 0o555)):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_STORED
            out.writestr(info, content)
    release = {"$schema": "prototype-ordax.runtime-component-release/1",
               "source_repository": "ordaxsystems/ordax-runtime", "source_commit": source,
               "created_from_ci_recipe": "runtime-component/package/1",
               "component": {"id": "product-runtime", "version": version,
                             "release_mode": "component-slot", "package_schema": schema},
               "package": {"name": archive.name, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                           "size": archive.stat().st_size,
                           "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest()},
               "activation": {"direct_activation_allowed": False, "pending_health_required": True}}
    binary.with_name("runtime-component-release.json").write_bytes(encode(release))
    return release
