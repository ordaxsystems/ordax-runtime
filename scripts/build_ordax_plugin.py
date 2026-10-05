from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "ordax-chatgpt"
JSON_FILES = ("plugin.json", "mcp.json")
PACKAGE_FILES = ("plugin.json", "mcp.json", "assets/ordax.svg")
FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)


def load_manifest() -> dict:
    raw = (PLUGIN_ROOT / "plugin.json").read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("plugin.json must be UTF-8 without BOM")
    manifest = json.loads(raw.decode("utf-8"))
    if manifest.get("name") != "ordax-chatgpt":
        raise ValueError("unexpected ChatGPT connector name")
    return manifest


def validate_package() -> dict:
    manifest = load_manifest()
    for name in PACKAGE_FILES:
        path = PLUGIN_ROOT / name
        if not path.is_file():
            raise FileNotFoundError(path)
    for name in JSON_FILES:
        raw = (PLUGIN_ROOT / name).read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError(f"{name} must be UTF-8 without BOM")
        json.loads(raw.decode("utf-8"))
    return manifest


def build_archive(output_dir: Path) -> tuple[Path, str]:
    manifest = validate_package()
    version = str(manifest.get("version") or "0.0.0")
    output_dir.mkdir(parents=True, exist_ok=True)
    for legacy_stem in ("ordax-studio-plugin", "ordax-dev-plugin"):
        for pattern in (
            f"{legacy_stem}-*.zip",
            f"{legacy_stem}-*.zip.sha256",
        ):
            for legacy in output_dir.glob(pattern):
                legacy.unlink(missing_ok=True)
    archive = output_dir / f"ordax-chatgpt-plugin-{version}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name in sorted(PACKAGE_FILES):
            data = (PLUGIN_ROOT / name).read_bytes()
            info = zipfile.ZipInfo(name.replace("\\", "/"), date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            bundle.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{digest}  {archive.name}\n", encoding="ascii"
    )
    return archive, digest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the portable ORDAX for ChatGPT connector package"
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist" / "plugins")
    args = parser.parse_args()
    archive, digest = build_archive(args.output_dir)
    print(f"ORDAX_CHATGPT_PLUGIN_ARCHIVE={archive}")
    print(f"ORDAX_CHATGPT_PLUGIN_SHA256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
