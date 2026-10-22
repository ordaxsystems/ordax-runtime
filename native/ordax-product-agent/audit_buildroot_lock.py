"""Compare pinned native Runtime requirements with the actual OrdaX Buildroot.

Buildroot is owned by ordax-os. This read-only source audit takes its
already-verified, extracted tree as input; it never downloads wheels,
installs anything, changes Kconfig, or replaces the canonical Runtime.

An Alpine/PyInstaller lock resolved for CPython 3.12 is NOT automatically
compatible with a Buildroot CPython 3.14 component. Buildroot package names
and versions must be checked rather than guessed or silently substituted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
DEFAULT_LOCK = HERE / "requirements.lock"
DEFAULT_BOOTSTRAP_LOCK = HERE / "bootstrap.lock"
DEFAULT_BUILD_REQUIREMENTS = HERE / "build-requirements.in"
PIN = re.compile(r"(?m)^([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][^\s\\]*)\s*\\?")
LOCK_PYTHON = re.compile(r"--python-version\s+(\d+\.\d+)")
VERSION = re.compile(r"(?m)^\s*PYTHON_([A-Z0-9_]+)_VERSION\s*=\s*([^\s#]+)\s*$")
PY_VERSION = re.compile(r"(?m)^\s*PYTHON3_VERSION\s*=\s*([^\s#]+)")
CANONICAL_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class RuntimeBuildrootAuditError(ValueError):
    pass


def normalize(name: str) -> str:
    canonical = re.sub(r"[-_.]+", "-", name.lower())
    if not CANONICAL_NAME.fullmatch(canonical):
        raise RuntimeBuildrootAuditError("invalid native dependency name")
    return canonical


def read_requirements(path: Path, *, lock: bool) -> tuple[dict[str, str], str | None]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeBuildrootAuditError("canonical requirement file unavailable")
    data = path.read_text(encoding="utf-8")
    requirements = {}
    entries = list(PIN.finditer(data))
    for i, match in enumerate(entries):
        name, version = normalize(match.group(1)), match.group(2)
        if name in requirements:
            raise RuntimeBuildrootAuditError("duplicate requirement: " + name)
        block = data[match.end():entries[i+1].start() if i+1 < len(entries) else len(data)]
        if lock and not re.search(r"--hash=sha256:[0-9a-f]{64}\b", block):
            raise RuntimeBuildrootAuditError("unhashed requirement: " + name)
        requirements[name] = version
    if not requirements:
        raise RuntimeBuildrootAuditError("canonical requirement file is empty")
    target = LOCK_PYTHON.search(data)
    if lock and not target:
        raise RuntimeBuildrootAuditError("requirement lock has no Python ABI target")
    return requirements, target.group(1) if target else None


def buildroot_python_version(buildroot: Path) -> str:
    file = buildroot / "package/python3/python3.mk"
    if not file.is_file() or file.is_symlink():
        raise RuntimeBuildrootAuditError("Buildroot Python3 package unavailable")
    hit = PY_VERSION.search(file.read_text(encoding="utf-8"))
    if not hit or not re.fullmatch(r"\d+\.\d+\.\d+", hit.group(1)):
        raise RuntimeBuildrootAuditError("Buildroot Python3 version is not pinned")
    return hit.group(1)


def buildroot_package(buildroot: Path, name: str) -> dict:
    directory = buildroot / "package" / ("python-" + name)
    mk = directory / ("python-" + name + ".mk")
    config = directory / "Config.in"
    if not mk.is_file() or mk.is_symlink() or not config.is_file() or config.is_symlink():
        return {"status": "missing", "buildroot_version": None}
    variable = name.replace("-", "_").upper()
    if not re.search(r"(?m)^\s*config\s+BR2_PACKAGE_PYTHON_" + variable + r"\s*$",
                     config.read_text(encoding="utf-8")):
        return {"status": "no-target-option", "buildroot_version": None}
    matches = [value for key, value in VERSION.findall(mk.read_text(encoding="utf-8"))
               if key == variable]
    if len(matches) != 1 or not re.fullmatch(r"[0-9]+(?:\.[0-9A-Za-z]+)*(?:[._-][0-9A-Za-z]+)*", matches[0]):
        return {"status": "version-not-pinned", "buildroot_version": None}
    return {"status": "found", "buildroot_version": matches[0]}


def audit(
    buildroot: Path,
    requirements: Path = DEFAULT_LOCK,
    bootstrap: Path = DEFAULT_BOOTSTRAP_LOCK,
    build_requirements: Path = DEFAULT_BUILD_REQUIREMENTS,
) -> dict:
    if not buildroot.is_dir() or buildroot.is_symlink():
        raise RuntimeBuildrootAuditError("verified Buildroot source tree unavailable")
    pinned, lock_python = read_requirements(requirements, lock=True)
    boot, _ = read_requirements(bootstrap, lock=True)
    direct_build, _ = read_requirements(build_requirements, lock=False)
    builders = set(boot) | set(direct_build)
    # Keep the original authoritative lock and report exclusions explicitly.
    # A build tool appearing in a runtime import chain needs a separate
    # package-graph proof: this inventory itself cannot authorize execution.
    runtime_names = sorted(set(pinned) - builders)
    if not runtime_names:
        raise RuntimeBuildrootAuditError("no native Runtime dependencies identified")
    py_version = buildroot_python_version(buildroot)
    buildroot_minor = ".".join(py_version.split(".")[:2])
    issues = []
    if lock_python != buildroot_minor:
        issues.append({
            "package": "python3", "reason": "lock-python-abi-mismatch",
            "locked": lock_python, "buildroot": buildroot_minor,
        })
    packages = []
    for name in runtime_names:
        info = buildroot_package(buildroot, name)
        version = info["buildroot_version"]
        status = ("exact" if info["status"] == "found" and version == pinned[name]
                  else "version-mismatch" if info["status"] == "found"
                  else info["status"])
        packages.append({
            "name": name,
            "locked_version": pinned[name],
            "buildroot_version": version,
            "status": status,
        })
        if status != "exact":
            issues.append({
                "package": name, "reason": status,
                "locked": pinned[name], "buildroot": version,
            })
    return {
        "schema": "ordax.native-runtime-buildroot-dependency-audit/1",
        "requirements_sha256": hashlib.sha256(requirements.read_bytes()).hexdigest(),
        "bootstrap_sha256": hashlib.sha256(bootstrap.read_bytes()).hexdigest(),
        "lock_python_minor": lock_python,
        "buildroot_python_version": py_version,
        "build_tool_only_names": sorted(builders & set(pinned)),
        "runtime_packages_examined": len(runtime_names),
        "packages": packages,
        "issues": issues,
        "source_version_compatible": not issues,
        "native_dependency_closure_proven": False,
        "runtime_agent_packaged": False,
        "device_install_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--buildroot", type=Path, required=True,
                        help="Already SHA256-verified and extracted official Buildroot")
    parser.add_argument("--requirements", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--bootstrap", type=Path, default=DEFAULT_BOOTSTRAP_LOCK)
    parser.add_argument("--build-requirements", type=Path, default=DEFAULT_BUILD_REQUIREMENTS)
    parser.add_argument("--require-compatible", action="store_true")
    args = parser.parse_args()
    try:
        report = audit(args.buildroot, args.requirements, args.bootstrap, args.build_requirements)
    except (OSError, UnicodeError, RuntimeBuildrootAuditError) as exc:
        print(json.dumps({"error": str(exc), "source_version_compatible": False}, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    if args.require_compatible and not report["source_version_compatible"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
