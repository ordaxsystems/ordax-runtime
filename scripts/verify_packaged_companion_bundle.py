#!/usr/bin/env python3
"""Verify that a built wheel contains the complete Blender companion bundle."""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path


PACKAGE_PREFIX = "ordax_dev_agent/assets/"
MANIFEST_NAME = PACKAGE_PREFIX + "blender_companion_bundle.json"


def _wheel_from(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if path.is_file() and path.suffix == ".whl":
        return path
    if path.is_dir():
        wheels = sorted(path.glob("*.whl"))
        if len(wheels) == 1:
            return wheels[0]
        raise SystemExit(
            f"expected exactly one wheel in {path}, found {len(wheels)}"
        )
    raise SystemExit(f"wheel path does not exist: {path}")


def verify(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        if MANIFEST_NAME not in names:
            raise SystemExit(
                f"wheel is missing Blender companion manifest: {MANIFEST_NAME}"
            )

        manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8-sig"))
        if not isinstance(manifest, dict) or manifest.get("version") != 1:
            raise SystemExit("Blender companion manifest version must be 1")

        raw_files = manifest.get("files")
        if (
            not isinstance(raw_files, list)
            or not raw_files
            or not all(isinstance(name, str) and name.strip() for name in raw_files)
        ):
            raise SystemExit("Blender companion manifest files are invalid")

        missing: list[str] = []
        for raw in raw_files:
            name = raw.strip().replace("\\", "/")
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise SystemExit(f"invalid companion bundle path in wheel: {name}")
            member = PACKAGE_PREFIX + name
            if member not in names:
                missing.append(member)

        if missing:
            raise SystemExit(
                "wheel is missing Blender companion bundle files: "
                + ", ".join(sorted(missing))
            )

        print(
            f"wheel bundle OK: {wheel.name} "
            f"({len(raw_files)} runtime files + manifest)"
        )


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(
            "usage: verify_packaged_companion_bundle.py <wheel-or-wheel-dir>"
        )
    verify(_wheel_from(sys.argv[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
