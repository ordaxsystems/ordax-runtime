"""Integrity verification and Blender staging for generated 3D artifacts."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from mcp_blender_unity.config import find_blender

from .models import ActionResult
from .process_runner import run_command as _run


_SUPPORTED_3D = frozenset({".glb", ".gltf", ".fbx", ".obj"})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(f"cannot parse generated-asset manifest: {error}") from error
    if not isinstance(raw, dict) or raw.get("schema") != "ordax.generated-asset/1":
        raise ValueError("manifest is not ordax.generated-asset/1")
    artifact = raw.get("artifact")
    if not isinstance(artifact, dict):
        raise ValueError("manifest has no artifact object")
    expected = artifact.get("sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError("manifest has no valid SHA-256")
    return raw


def _verification(project, artifact_path: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = _load_manifest(manifest_path)
    artifact = manifest["artifact"]
    declared_path = artifact.get("path")
    if not isinstance(declared_path, str) or not declared_path:
        raise ValueError("manifest artifact path is invalid")
    declared = project.path(declared_path)
    if declared.resolve() != artifact_path.resolve():
        raise ValueError("manifest artifact path does not match requested artifact")
    actual_bytes = artifact_path.stat().st_size
    declared_bytes = artifact.get("bytes")
    if isinstance(declared_bytes, int) and declared_bytes != actual_bytes:
        raise ValueError("artifact byte size no longer matches provenance manifest")
    actual_hash = _sha256(artifact_path)
    expected_hash = str(artifact["sha256"]).lower()
    if actual_hash != expected_hash:
        raise ValueError("artifact SHA-256 no longer matches provenance manifest")
    return {
        "ok": True,
        "artifact_path": str(artifact_path),
        "manifest_path": str(manifest_path),
        "bytes": actual_bytes,
        "sha256": actual_hash,
        "format": artifact.get("format"),
        "provenance": manifest.get("provenance") if isinstance(manifest.get("provenance"), dict) else {},
    }


class GeneratedAssetActions:
    """Verify immutable evidence and build editable Blender staging files."""

    def game_assets_artifact_verify(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = set(payload) - {"project", "artifact_path", "manifest_path"}
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        try:
            artifact = project.path(str(payload.get("artifact_path") or ""))
            if not artifact.is_file():
                raise ValueError("artifact_path must be a file")
            manifest_raw = payload.get("manifest_path")
            manifest = (
                project.path(str(manifest_raw))
                if manifest_raw is not None
                else artifact.with_name(artifact.name + ".ordax.json")
            )
            verification = _verification(project, artifact, manifest)
        except (ValueError, OSError, FileNotFoundError) as error:
            return ActionResult(False, str(error))
        return ActionResult(True, "generated artifact integrity verified", verification)

    def game_assets_blender_ingest_generated(self, payload: dict[str, Any]) -> ActionResult:
        supported = {
            "project",
            "artifact_path",
            "manifest_path",
            "output_blend",
            "overwrite",
            "require_provenance",
            "timeout_seconds",
        }
        unsupported = set(payload) - supported
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        project = self._project(payload)
        try:
            artifact = project.path(str(payload.get("artifact_path") or ""))
            if artifact.suffix.lower() not in _SUPPORTED_3D:
                raise ValueError("artifact_path must be GLB, glTF, FBX, or OBJ")
            if not artifact.is_file():
                raise ValueError("artifact_path must be a file")
            output = project.path(str(payload.get("output_blend") or ""), must_exist=False)
            if output.suffix.lower() != ".blend":
                raise ValueError("output_blend must end in .blend")
            overwrite = bool(payload.get("overwrite", False))
            if output.exists() and not overwrite:
                raise ValueError("output_blend already exists; set overwrite=true explicitly")
            require_provenance = bool(payload.get("require_provenance", True))
            manifest_raw = payload.get("manifest_path")
            manifest = (
                project.path(str(manifest_raw))
                if manifest_raw is not None
                else artifact.with_name(artifact.name + ".ordax.json")
            )
            verification: dict[str, Any] | None = None
            if manifest.exists():
                verification = _verification(project, artifact, manifest)
            elif require_provenance:
                raise ValueError("generated artifact provenance manifest is required")
            timeout = payload.get("timeout_seconds", 1200)
            if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 30 or timeout > 3600:
                raise ValueError("timeout_seconds must be an integer between 30 and 3600")
            blender = find_blender()
            if blender is None:
                raise ValueError("Blender executable not found")
            output.parent.mkdir(parents=True, exist_ok=True)
            artifact_dir = self.config.state_dir / "artifacts" / project.slug / "game-assets"
            artifact_dir.mkdir(parents=True, exist_ok=True)
            report_path = artifact_dir / f"generated-ingest-{uuid.uuid4().hex}.json"
            script = Path(__file__).resolve().parent / "assets" / "blender_generated_asset_ingest.py"
            command = [
                str(blender),
                "--background",
                "--factory-startup",
                "--python",
                str(script),
                "--",
                "--input",
                str(artifact),
                "--output",
                str(output),
                "--report",
                str(report_path),
            ]
            if manifest.exists():
                command.extend(["--provenance", str(manifest)])
            result = _run(command, cwd=project.root, timeout=timeout)
            if not result.ok:
                return result
            if not output.is_file():
                return ActionResult(False, "Blender ingestion completed without output .blend")
            if not report_path.is_file():
                return ActionResult(False, "Blender ingestion completed without report")
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                return ActionResult(False, f"cannot read Blender ingestion report: {error}")
            if not isinstance(report, dict) or not report.get("ok"):
                return ActionResult(False, "Blender ingestion report indicates failure", {"report": report})
        except (ValueError, OSError, FileNotFoundError) as error:
            return ActionResult(False, str(error))
        return ActionResult(
            True,
            "generated asset ingested into Blender staging",
            {
                "artifact_path": str(artifact),
                "output_blend": str(output),
                "output_bytes": output.stat().st_size,
                "integrity": verification,
                "report_path": str(report_path),
                "report": report,
            },
        )
