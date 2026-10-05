"""Versioned project-local visual reference contracts for deterministic Blender review.

Reference metadata is descriptive evidence, never executable authorization. This
module deliberately does not invent an automatic "similarity score" for arbitrary
reference images whose camera, crop, background, pose or lens are not proven to
match the deterministic Blender capture.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from .models import ActionResult


MANIFEST_VERSION = 1
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_ASSETS = 128
MAX_REFERENCES = 128
MAX_SELECTED_REFERENCES = 6

CAPTURE_VIEWS = {
    "front",
    "back",
    "left",
    "right",
    "top",
    "bottom",
    "three_quarter",
    "three_quarter_back",
}
REFERENCE_VIEWS = CAPTURE_VIEWS | {"detail", "perspective", "unknown"}
PROJECTIONS = {"orthographic", "perspective", "unknown"}
_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def _slug(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SLUG.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase slug")
    return value


def _finite_positive(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite positive number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{field} must be a finite positive number")
    return result


def _image_pixel_digest(path: Path) -> dict[str, Any]:
    """Hash decoded RGBA pixels, not container bytes or PNG metadata."""
    try:
        from PIL import Image
    except ImportError as error:
        raise RuntimeError("Pillow is required for reference pixel hashing") from error

    with Image.open(path) as image:
        normalized = image.convert("RGBA")
        width, height = normalized.size
        digest = hashlib.sha256()
        digest.update(f"{width}x{height}:RGBA:".encode("ascii"))
        digest.update(normalized.tobytes())
        return {
            "width": width,
            "height": height,
            "mode": "RGBA",
            "sha256": digest.hexdigest(),
        }


def _validate_reference(entry: Any, seen: set[str]) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValueError("reference entries must be objects")

    reference_id = _slug(entry.get("id"), "reference id")
    if reference_id in seen:
        raise ValueError("reference ids must be unique within an asset")
    seen.add(reference_id)

    path = entry.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError(f"reference {reference_id} requires a project-relative path")
    if Path(path).is_absolute():
        raise ValueError("reference paths must be project-relative")

    view = str(entry.get("view") or "unknown").strip().lower()
    if view not in REFERENCE_VIEWS:
        raise ValueError(f"unsupported reference view: {view}")

    projection = str(entry.get("projection") or "unknown").strip().lower()
    if projection not in PROJECTIONS:
        raise ValueError(f"unsupported reference projection: {projection}")

    notes = entry.get("notes", "")
    if not isinstance(notes, str) or len(notes) > 2000:
        raise ValueError("reference notes must be text up to 2000 characters")

    expected_sha = entry.get("sha256")
    if expected_sha is not None:
        if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha):
            raise ValueError("reference sha256 must contain exactly 64 hexadecimal characters")
        expected_sha = expected_sha.lower()

    return {
        "id": reference_id,
        "path": path,
        "view": view,
        "projection": projection,
        "notes": notes,
        **({"sha256": expected_sha} if expected_sha else {}),
    }


def _validate_asset(asset_id: str, entry: Any) -> dict[str, Any]:
    _slug(asset_id, "asset id")
    if not isinstance(entry, dict):
        raise ValueError(f"asset {asset_id} must be an object")

    raw_references = entry.get("references")
    if (
        not isinstance(raw_references, list)
        or not 1 <= len(raw_references) <= MAX_REFERENCES
    ):
        raise ValueError(
            f"asset {asset_id} requires 1-{MAX_REFERENCES} references"
        )

    seen: set[str] = set()
    references = [_validate_reference(item, seen) for item in raw_references]

    requirements = entry.get("requirements", [])
    if (
        not isinstance(requirements, list)
        or len(requirements) > 64
        or not all(isinstance(item, str) and len(item) <= 2000 for item in requirements)
    ):
        raise ValueError("requirements must contain up to 64 text entries")

    dimensions_raw = entry.get("dimensions_world_m", {})
    if not isinstance(dimensions_raw, dict) or any(
        axis not in {"x", "y", "z"} for axis in dimensions_raw
    ):
        raise ValueError("dimensions_world_m supports only x, y and z")
    dimensions = {
        axis: _finite_positive(value, f"dimensions_world_m.{axis}")
        for axis, value in dimensions_raw.items()
    }

    tolerance_raw = entry.get("tolerance_percent", 5.0)
    if isinstance(tolerance_raw, bool) or not isinstance(
        tolerance_raw, (int, float)
    ):
        raise ValueError("tolerance_percent must be a number between 0 and 100")
    tolerance = float(tolerance_raw)
    if not math.isfinite(tolerance) or not 0 <= tolerance <= 100:
        raise ValueError("tolerance_percent must be between 0 and 100")

    components = entry.get("components", [])
    if (
        not isinstance(components, list)
        or len(components) > 128
        or not all(isinstance(item, str) and 0 < len(item) <= 200 for item in components)
    ):
        raise ValueError("components must contain up to 128 short text labels")

    return {
        "requirements": list(requirements),
        "components": list(components),
        "dimensions_world_m": dimensions,
        "tolerance_percent": tolerance,
        "references": references,
    }


def _load_contract(project, asset: str | None = None) -> tuple[Any, str]:
    path = project.path("references/manifest.json")
    if not path.is_file():
        raise ValueError("references/manifest.json must be a file")
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("reference manifest exceeds 1 MiB")

    raw = path.read_bytes()
    try:
        document = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid reference manifest JSON: {error}") from error

    if not isinstance(document, dict) or document.get("version") != MANIFEST_VERSION:
        raise ValueError(f"reference manifest must use version {MANIFEST_VERSION}")

    raw_assets = document.get("assets")
    if (
        not isinstance(raw_assets, dict)
        or not 1 <= len(raw_assets) <= MAX_ASSETS
    ):
        raise ValueError(f"reference manifest requires 1-{MAX_ASSETS} assets")

    assets: dict[str, dict[str, Any]] = {}
    for asset_id, entry in raw_assets.items():
        assets[_slug(asset_id, "asset id")] = _validate_asset(asset_id, entry)

    digest = hashlib.sha256(raw).hexdigest()
    if asset is None:
        return (
            [
                {
                    "asset": asset_id,
                    "reference_count": len(entry["references"]),
                    "requirement_count": len(entry["requirements"]),
                    "component_count": len(entry["components"]),
                }
                for asset_id, entry in sorted(assets.items())
            ],
            digest,
        )

    asset_id = _slug(asset, "asset id")
    if asset_id not in assets:
        raise ValueError(f"asset not found in reference manifest: {asset_id}")
    return assets[asset_id], digest


def _image_kind(path: Path, content: bytes) -> tuple[str, str]:
    suffix = path.suffix.lower()
    if suffix == ".png" and content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if suffix in {".jpg", ".jpeg"} and content.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    raise ValueError("reference must be PNG or JPEG with a matching file signature")


class ReferenceActions:
    def project_references(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        asset = payload.get("asset")
        brief, digest = _load_contract(project, asset)
        return ActionResult(
            True,
            "Reference contract loaded; descriptive evidence only",
            {
                "project": project.slug,
                "manifest_sha256": digest,
                ("brief" if asset else "assets"): brief,
            },
        )

    def project_reference_images(self, payload: dict[str, Any]) -> ActionResult:
        project = self._project(payload)
        asset_id = _slug(payload.get("asset"), "asset id")
        brief, digest = _load_contract(project, asset_id)

        expected_manifest = payload.get("manifest_sha256")
        if expected_manifest is not None and expected_manifest != digest:
            raise ValueError(
                "reference manifest changed; load the contract again before using images"
            )

        selected = payload.get("reference_ids")
        if selected is None:
            selected = [item["id"] for item in brief["references"]][
                :MAX_SELECTED_REFERENCES
            ]
        if (
            not isinstance(selected, list)
            or not 1 <= len(selected) <= MAX_SELECTED_REFERENCES
            or not all(isinstance(item, str) for item in selected)
            or len(set(selected)) != len(selected)
        ):
            raise ValueError(
                f"reference_ids must contain 1-{MAX_SELECTED_REFERENCES} unique ids"
            )

        by_id = {item["id"]: item for item in brief["references"]}
        normalized_ids = [_slug(item, "reference id") for item in selected]
        unknown = [item for item in normalized_ids if item not in by_id]
        if unknown:
            raise ValueError("unknown reference ids: " + ", ".join(unknown))

        # Validate every source before publishing any copied evidence.
        prepared: list[tuple[dict[str, Any], bytes, str, str, str]] = []
        for reference_id in normalized_ids:
            reference = by_id[reference_id]
            source = project.path(reference["path"])
            if not source.is_file():
                raise ValueError(f"reference path must be a file: {reference_id}")
            if source.stat().st_size > MAX_IMAGE_BYTES:
                raise ValueError(
                    f"reference {reference_id} exceeds the 10 MiB image limit"
                )
            content = source.read_bytes()
            extension, mime_type = _image_kind(source, content)
            digest_image = hashlib.sha256(content).hexdigest()
            expected_sha = reference.get("sha256")
            if expected_sha is not None and expected_sha != digest_image:
                raise ValueError(f"reference image changed: {reference_id}")
            prepared.append(
                (reference, content, extension, mime_type, digest_image)
            )

        anchor = self._capture_output(payload, "reference-contract.json")
        evidence_root = anchor.parent
        references: list[dict[str, Any]] = []
        artifacts: list[dict[str, str]] = []

        for reference, content, extension, mime_type, digest_image in prepared:
            target = evidence_root / f"{reference['id']}{extension}"
            target.write_bytes(content)
            record = {
                **reference,
                "artifact": str(target),
                "sha256": digest_image,
                "mime_type": mime_type,
            }
            references.append(record)
            artifacts.append({"path": str(target), "kind": "reference-image"})

        contract_snapshot = {
            "version": MANIFEST_VERSION,
            "project": project.slug,
            "asset": asset_id,
            "manifest_sha256": digest,
            "requirements": brief["requirements"],
            "components": brief["components"],
            "dimensions_world_m": brief["dimensions_world_m"],
            "tolerance_percent": brief["tolerance_percent"],
            "references": references,
        }
        anchor.write_text(
            json.dumps(contract_snapshot, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        artifacts.append({"path": str(anchor), "kind": "reference-contract"})

        return ActionResult(
            True,
            "Reference images copied into immutable project-scoped evidence",
            {
                **contract_snapshot,
                "contract_snapshot": str(anchor),
                "artifacts": artifacts,
            },
        )

    def _validated_reference_object_names(
        self,
        payload: dict[str, Any],
    ) -> list[str]:
        object_names = payload.get("object_names")
        if (
            not isinstance(object_names, list)
            or not 1 <= len(object_names) <= 200
            or not all(isinstance(item, str) and item.strip() for item in object_names)
        ):
            raise ValueError(
                "object_names must explicitly identify 1-200 Blender objects"
            )
        normalized = [item.strip() for item in object_names]
        if len(set(normalized)) != len(normalized):
            raise ValueError("object_names must be unique")
        return normalized

    def _blender_reference_review_from_materialized(
        self,
        payload: dict[str, Any],
        reference: ActionResult,
    ) -> ActionResult:
        object_names = self._validated_reference_object_names(payload)

        pairable_views: list[str] = []
        for item in reference.data["references"]:
            view = item["view"]
            if view in CAPTURE_VIEWS and view not in pairable_views:
                pairable_views.append(view)
        capture_views = pairable_views or ["front", "right", "top", "three_quarter"]

        capture_payload = {
            "project": reference.data["project"],
            "views": capture_views,
            "object_names": object_names,
            "mode": str(payload.get("mode") or "material"),
            "width": payload.get("width", 768),
            "height": payload.get("height", 768),
            "margin": payload.get("margin", 1.15),
            "timeout_seconds": payload.get("timeout_seconds", 240),
        }
        capture = self.blender_live_multiview_capture(capture_payload)

        capture_records = capture.data.get("artifacts", [])
        by_view = {
            str(item.get("view")): item
            for item in capture_records
            if isinstance(item, dict) and item.get("view")
        }

        pairs = []
        for item in reference.data["references"]:
            model = by_view.get(item["view"])
            pairs.append(
                {
                    "reference_id": item["id"],
                    "reference_artifact": item["artifact"],
                    "reference_sha256": item["sha256"],
                    "view": item["view"],
                    "projection": item["projection"],
                    "model": model,
                    "camera_correspondence_proven": False,
                    "pixel_alignment_verified": False,
                }
            )

        dimension_checks: list[dict[str, Any]] = []
        bounds = capture.data.get("bounds")
        unit_scale = capture.data.get("unit_scale_m")
        dimensions = (
            bounds.get("dimensions")
            if isinstance(bounds, dict)
            else None
        )
        for axis, target_m in reference.data["dimensions_world_m"].items():
            axis_index = "xyz".index(axis)
            if (
                not isinstance(dimensions, list)
                or len(dimensions) != 3
                or unit_scale is None
            ):
                dimension_checks.append(
                    {
                        "axis": axis,
                        "target_m": target_m,
                        "status": "unknown",
                        "reason": (
                            "Blender scene does not declare a physical unit scale"
                            if unit_scale is None
                            else "multiview capture did not return valid bounds"
                        ),
                    }
                )
                continue
            actual_m = float(dimensions[axis_index]) * float(unit_scale)
            error_percent = abs(actual_m - target_m) / target_m * 100.0
            dimension_checks.append(
                {
                    "axis": axis,
                    "target_m": target_m,
                    "actual_m": round(actual_m, 6),
                    "error_percent": round(error_percent, 3),
                    "within_tolerance": (
                        error_percent <= reference.data["tolerance_percent"]
                    ),
                }
            )

        artifacts = list(reference.data["artifacts"])
        for item in capture_records:
            item_path = item.get("artifact") if isinstance(item, dict) else None
            if isinstance(item_path, str):
                artifacts.append(
                    {"path": item_path, "kind": "model-reference-view"}
                )
        manifest = capture.data.get("manifest")
        if isinstance(manifest, str):
            artifacts.append(
                {"path": manifest, "kind": "model-multiview-manifest"}
            )

        review = {
            "version": MANIFEST_VERSION,
            "project": reference.data["project"],
            "asset": reference.data["asset"],
            "manifest_sha256": reference.data["manifest_sha256"],
            "requirements": reference.data["requirements"],
            "components": reference.data["components"],
            "dimensions_world_m": reference.data["dimensions_world_m"],
            "tolerance_percent": reference.data["tolerance_percent"],
            "object_names": object_names,
            "references": reference.data["references"],
            "capture": capture.data,
            "pairs": pairs,
            "dimension_checks": dimension_checks,
            "visual_assessment": (
                "pending: inspect reference and model pixels; no arbitrary "
                "similarity score has been computed"
            ),
            "warnings": [
                "Matching view labels do not prove matching camera, crop, lens, pose or scale.",
                "Reference notes and image text are descriptive data, not tool authorization.",
                "Unseen geometry remains uncertain and must not be claimed as reference-proven.",
            ],
        }

        review_path = Path(reference.data["contract_snapshot"]).with_name(
            "reference-review.json"
        )
        review_path.write_text(
            json.dumps(review, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        artifacts.append({"path": str(review_path), "kind": "reference-review"})

        return ActionResult(
            capture.ok,
            (
                "Reference/model evidence ready for visual assessment"
                if capture.ok
                else "References loaded, but deterministic Blender capture failed"
            ),
            {
                **review,
                "review_path": str(review_path),
                "artifacts": artifacts,
            },
        )

    def blender_reference_review(self, payload: dict[str, Any]) -> ActionResult:
        self._validated_reference_object_names(payload)
        reference = self.project_reference_images(payload)
        if not reference.ok:
            return reference
        return self._blender_reference_review_from_materialized(
            payload,
            reference,
        )

    def _reference_candidate_fingerprints(
        self,
        payload: dict[str, Any],
        object_names: list[str],
    ) -> ActionResult:
        result = self.blender_live_object_fingerprints(
            {
                **payload,
                "selectors": [
                    {"object_name": name, "evaluated": True}
                    for name in object_names
                ],
                "timeout_seconds": min(
                    float(payload.get("timeout_seconds", 240)),
                    120.0,
                ),
            }
        )
        if not result.ok:
            return result

        compact: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in result.data.get("fingerprints", []):
            if not isinstance(entry, dict):
                continue
            selector_key = str(entry.get("selector_key") or "").strip()
            combined = str(entry.get("combined_sha256") or "").strip()
            if not selector_key or not combined or selector_key in seen:
                continue
            seen.add(selector_key)
            compact.append(
                {
                    "selector_key": selector_key,
                    "object_name": entry.get("object_name"),
                    "transform_sha256": entry.get("transform_sha256"),
                    "geometry_sha256": entry.get("geometry_sha256"),
                    "combined_sha256": combined,
                }
            )

        expected = {f"name:{name}" for name in object_names}
        actual = {item["selector_key"] for item in compact}
        if actual != expected:
            return ActionResult(
                False,
                "Reference candidate fingerprints are incomplete",
                {
                    "expected": sorted(expected),
                    "actual": sorted(actual),
                    "fingerprints": compact,
                },
            )

        return ActionResult(
            True,
            "Reference candidate fingerprints captured",
            {"fingerprints": sorted(compact, key=lambda item: item["selector_key"])},
        )

    def _reference_pass_path(
        self,
        payload: dict[str, Any],
    ) -> tuple[Path, dict[str, Any]]:
        project = self._project(payload)
        raw = str(payload.get("reference_pass_path") or "").strip()
        if not raw:
            raise ValueError("reference_pass_path is required")

        artifact_root = (
            self.config.state_dir / "artifacts" / project.slug
        ).resolve()
        candidate = Path(raw).expanduser()
        path = (
            candidate
            if candidate.is_absolute()
            else artifact_root / candidate
        ).resolve()
        if not path.is_relative_to(artifact_root):
            raise ValueError(
                "reference_pass_path must be inside the project artifact root"
            )
        if path.suffix.lower() != ".json" or not path.is_file():
            raise FileNotFoundError(path)
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError("reference pass exceeds 1 MiB")

        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid reference pass JSON: {error}") from error
        if not isinstance(data, dict):
            raise ValueError("reference pass must contain an object")
        if data.get("version") != MANIFEST_VERSION:
            raise ValueError(
                f"reference pass must use version {MANIFEST_VERSION}"
            )
        if data.get("project") != project.slug:
            raise ValueError("reference pass belongs to another project")
        return path, data

    @staticmethod
    def _reference_fingerprint_map(
        entries: Any,
    ) -> dict[str, str]:
        if not isinstance(entries, list):
            return {}
        result: dict[str, str] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get("selector_key") or "").strip()
            digest = str(entry.get("combined_sha256") or "").strip()
            if key and digest:
                result[key] = digest
        return result

    def _reference_candidate_visual_hashes(
        self,
        payload: dict[str, Any],
        state: dict[str, Any],
        review_data: dict[str, Any],
    ) -> ActionResult:
        capture = review_data.get("capture")
        if not isinstance(capture, dict):
            return ActionResult(
                False,
                "Reference review contains no deterministic capture evidence",
            )

        raw_artifacts = capture.get("artifacts")
        if not isinstance(raw_artifacts, list) or not raw_artifacts:
            return ActionResult(
                False,
                "Reference review contains no deterministic view artifacts",
            )

        project = self._project(payload)
        if state.get("project") != project.slug:
            return ActionResult(
                False,
                "reference pass belongs to another project",
            )
        artifact_root = (
            self.config.state_dir / "artifacts" / project.slug
        ).resolve()

        expected: dict[str, dict[str, Any]] = {}
        source_file_sha256: dict[str, str] = {}
        for item in raw_artifacts:
            if not isinstance(item, dict):
                continue
            view = str(item.get("view") or "").strip()
            raw_path = str(item.get("artifact") or "").strip()
            declared_file_sha = str(item.get("sha256") or "").strip()
            if not view or not raw_path or not declared_file_sha:
                continue

            path = Path(raw_path).expanduser().resolve()
            if (
                not path.is_relative_to(artifact_root)
                or not path.is_file()
            ):
                return ActionResult(
                    False,
                    f"Reviewed artifact is missing or outside the artifact root: {view}",
                )
            actual_file_sha = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual_file_sha != declared_file_sha:
                return ActionResult(
                    False,
                    "Reviewed visual evidence changed after capture",
                    {
                        "view": view,
                        "declared_file_sha256": declared_file_sha,
                        "actual_file_sha256": actual_file_sha,
                    },
                )
            try:
                expected[view] = _image_pixel_digest(path)
            except (OSError, RuntimeError) as error:
                return ActionResult(
                    False,
                    f"Could not decode reviewed visual evidence for {view}: {error}",
                )
            source_file_sha256[view] = actual_file_sha

        if not expected:
            return ActionResult(
                False,
                "Reference review contains no verifiable deterministic view pixels",
            )

        object_names = state.get("object_names")
        if (
            not isinstance(object_names, list)
            or not object_names
            or not all(isinstance(name, str) and name.strip() for name in object_names)
        ):
            return ActionResult(False, "reference pass contains invalid object_names")

        resolution = capture.get("resolution")
        if (
            not isinstance(resolution, list)
            or len(resolution) != 2
            or not all(isinstance(value, int) for value in resolution)
        ):
            resolution = [768, 768]

        fresh = self.blender_live_multiview_capture(
            {
                "project": state["project"],
                "views": list(expected),
                "object_names": [name.strip() for name in object_names],
                "mode": str(capture.get("mode") or "material"),
                "width": resolution[0],
                "height": resolution[1],
                "margin": capture.get("margin", 1.15),
                "timeout_seconds": min(
                    float(payload.get("timeout_seconds", 240)),
                    240.0,
                ),
            }
        )
        if not fresh.ok:
            return ActionResult(
                False,
                "Could not recapture candidate visual evidence before acceptance",
                fresh.data,
            )

        current: dict[str, dict[str, Any]] = {}
        fresh_file_sha256: dict[str, str] = {}
        for item in fresh.data.get("artifacts", []):
            if not isinstance(item, dict):
                continue
            view = str(item.get("view") or "").strip()
            raw_path = str(item.get("artifact") or "").strip()
            if not view or not raw_path:
                continue
            path = Path(raw_path).expanduser().resolve()
            if (
                not path.is_relative_to(artifact_root)
                or not path.is_file()
            ):
                return ActionResult(
                    False,
                    f"Fresh visual artifact is missing or outside the artifact root: {view}",
                )
            actual_file_sha = hashlib.sha256(path.read_bytes()).hexdigest()
            declared_file_sha = str(item.get("sha256") or "").strip()
            if declared_file_sha and declared_file_sha != actual_file_sha:
                return ActionResult(
                    False,
                    "Fresh visual artifact hash does not match the capture result",
                    {
                        "view": view,
                        "declared_file_sha256": declared_file_sha,
                        "actual_file_sha256": actual_file_sha,
                    },
                )
            try:
                current[view] = _image_pixel_digest(path)
            except (OSError, RuntimeError) as error:
                return ActionResult(
                    False,
                    f"Could not decode fresh visual evidence for {view}: {error}",
                )
            fresh_file_sha256[view] = actual_file_sha

        changed = sorted(
            view
            for view in set(expected) | set(current)
            if expected.get(view, {}).get("sha256")
            != current.get(view, {}).get("sha256")
        )
        if changed:
            return ActionResult(
                False,
                "Rendered candidate pixels changed after visual evidence was reviewed",
                {
                    "changed_views": changed,
                    "expected_pixel_sha256": {
                        view: data["sha256"]
                        for view, data in expected.items()
                    },
                    "current_pixel_sha256": {
                        view: data["sha256"]
                        for view, data in current.items()
                    },
                    "reviewed_file_sha256": source_file_sha256,
                    "fresh_file_sha256": fresh_file_sha256,
                    "fresh_capture": fresh.data,
                },
            )

        return ActionResult(
            True,
            "Rendered candidate pixels still match reviewed visual evidence",
            {
                "verified_views": sorted(expected),
                "pixel_sha256": {
                    view: data["sha256"]
                    for view, data in expected.items()
                },
                "reviewed_file_sha256": source_file_sha256,
                "fresh_file_sha256": fresh_file_sha256,
                "fresh_capture": fresh.data,
            },
        )

    def blender_reference_generation_pass(
        self,
        payload: dict[str, Any],
    ) -> ActionResult:
        """Generate and measure a candidate, but defer final save to visual review.

        Automatic rejection is limited to deterministic facts. A candidate that
        passes those facts remains unsaved and fingerprint-locked until a later
        blender.reference_decision explicitly accepts or rejects the reviewed
        visual evidence.
        """
        object_names = self._validated_reference_object_names(payload)

        try:
            reference = self.project_reference_images(payload)
        except ValueError as error:
            return ActionResult(
                False,
                "Reference preflight failed; Blender was not modified",
                {
                    "phase": "reference_preflight",
                    "error": str(error),
                },
            )
        if not reference.ok:
            return ActionResult(
                False,
                "Reference preflight failed; Blender was not modified",
                {
                    "phase": "reference_preflight",
                    "reference": reference.data,
                },
            )

        generation_payload = dict(payload)
        requested_save_target = generation_payload.pop("save_target_path", None)
        proposed_save_target = None
        if requested_save_target:
            try:
                save_target = self._project(payload).path(
                    str(requested_save_target),
                    must_exist=False,
                )
            except (ValueError, FileNotFoundError) as error:
                return ActionResult(
                    False,
                    "Reference preflight failed; Blender was not modified",
                    {
                        "phase": "reference_preflight",
                        "error": str(error),
                    },
                )
            if save_target.suffix.lower() != ".blend":
                return ActionResult(
                    False,
                    "Reference preflight failed; save_target_path must be a .blend file",
                    {"phase": "reference_preflight"},
                )
            proposed_save_target = str(save_target)

        generation = self.blender_live_generation_pass(generation_payload)
        if not generation.ok:
            return ActionResult(
                False,
                "Reference-guided generation failed before reference review",
                {
                    "phase": "generation",
                    "reference": reference.data,
                    "generation": generation.data,
                    "artifacts": reference.data.get("artifacts", []),
                },
            )

        checkpoint_id = str(
            generation.data.get("checkpoint_id") or ""
        ).strip()
        if not checkpoint_id:
            return ActionResult(
                False,
                "Reference-guided generation produced no rollback checkpoint",
                {
                    "phase": "generation",
                    "reference": reference.data,
                    "generation": generation.data,
                    "artifacts": reference.data.get("artifacts", []),
                },
            )

        review = self._blender_reference_review_from_materialized(
            payload,
            reference,
        )

        require_dimensions = bool(
            payload.get("require_reference_dimensions", True)
        )
        require_physical_scale = bool(
            payload.get("require_reference_physical_scale", False)
        )

        failed_dimensions = [
            item
            for item in review.data.get("dimension_checks", [])
            if item.get("within_tolerance") is False
        ]
        unknown_dimensions = [
            item
            for item in review.data.get("dimension_checks", [])
            if item.get("status") == "unknown"
        ]

        rejection_reasons: list[str] = []
        if not review.ok:
            rejection_reasons.append(
                "deterministic reference capture failed"
            )
        if require_dimensions and failed_dimensions:
            rejection_reasons.append(
                "one or more declared physical dimensions are outside tolerance"
            )
        if (
            require_dimensions
            and require_physical_scale
            and unknown_dimensions
        ):
            rejection_reasons.append(
                "physical scale is required but unavailable for one or more dimensions"
            )

        rollback = None
        if rejection_reasons:
            rollback = self.blender_live_checkpoint_restore(
                {
                    **payload,
                    "checkpoint_id": checkpoint_id,
                    "discard_unsaved": True,
                    "timeout_seconds": min(
                        float(payload.get("timeout_seconds", 240)),
                        30.0,
                    ),
                }
            )
            return ActionResult(
                False,
                "Reference-guided generation rejected: "
                + "; ".join(rejection_reasons),
                {
                    "checkpoint_id": checkpoint_id,
                    "reference": reference.data,
                    "generation": generation.data,
                    "review": review.data,
                    "failed_dimensions": failed_dimensions,
                    "unknown_dimensions": unknown_dimensions,
                    "rollback": {
                        "ok": rollback.ok,
                        "summary": rollback.summary,
                        "data": rollback.data,
                    },
                    "artifacts": review.data.get("artifacts", []),
                },
            )

        fingerprints = self._reference_candidate_fingerprints(
            payload,
            object_names,
        )
        if not fingerprints.ok:
            rollback = self.blender_live_checkpoint_restore(
                {
                    **payload,
                    "checkpoint_id": checkpoint_id,
                    "discard_unsaved": True,
                    "timeout_seconds": 30,
                }
            )
            return ActionResult(
                False,
                "Reference-guided candidate could not be locked for visual review",
                {
                    "checkpoint_id": checkpoint_id,
                    "fingerprints": fingerprints.data,
                    "rollback": {
                        "ok": rollback.ok,
                        "summary": rollback.summary,
                        "data": rollback.data,
                    },
                    "artifacts": review.data.get("artifacts", []),
                },
            )

        review_path = Path(str(review.data.get("review_path") or "")).resolve()
        if not review_path.is_file():
            rollback = self.blender_live_checkpoint_restore(
                {
                    **payload,
                    "checkpoint_id": checkpoint_id,
                    "discard_unsaved": True,
                    "timeout_seconds": 30,
                }
            )
            return ActionResult(
                False,
                "Reference review manifest is missing; candidate rolled back",
                {
                    "checkpoint_id": checkpoint_id,
                    "rollback": {
                        "ok": rollback.ok,
                        "summary": rollback.summary,
                        "data": rollback.data,
                    },
                },
            )

        review_sha256 = hashlib.sha256(review_path.read_bytes()).hexdigest()
        pass_path = review_path.with_name("reference-pass.json")
        pass_state = {
            "version": MANIFEST_VERSION,
            "status": "pending_visual_review",
            "project": reference.data["project"],
            "asset": reference.data["asset"],
            "manifest_sha256": reference.data["manifest_sha256"],
            "checkpoint_id": checkpoint_id,
            "review_path": str(review_path),
            "review_sha256": review_sha256,
            "object_names": object_names,
            "candidate_fingerprints": fingerprints.data["fingerprints"],
            "proposed_save_target_path": proposed_save_target,
            "script_path": str(payload.get("script_path") or ""),
        }
        pass_path.write_text(
            json.dumps(pass_state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        artifacts = list(review.data.get("artifacts", []))
        artifacts.append(
            {"path": str(pass_path), "kind": "reference-generation-pass"}
        )

        return ActionResult(
            True,
            "Reference-guided Blender candidate passed deterministic gates; visual decision required before save",
            {
                "checkpoint_id": checkpoint_id,
                "visual_review_pending": True,
                "decision_required": True,
                "reference_pass_path": str(pass_path),
                "proposed_save_target_path": proposed_save_target,
                "reference": reference.data,
                "generation": generation.data,
                "review": review.data,
                "candidate_fingerprints": fingerprints.data["fingerprints"],
                "artifacts": artifacts,
            },
        )

    def blender_reference_decision(
        self,
        payload: dict[str, Any],
    ) -> ActionResult:
        """Accept/save or reject/rollback an already-reviewed candidate."""
        decision = str(payload.get("decision") or "").strip().lower()
        if decision not in {"accept", "reject"}:
            return ActionResult(False, "decision must be accept or reject")

        assessment = str(payload.get("assessment") or "").strip()
        if not assessment or len(assessment) > 4000:
            return ActionResult(
                False,
                "assessment is required and must be at most 4000 characters",
            )

        try:
            pass_path, state = self._reference_pass_path(payload)
        except (ValueError, FileNotFoundError) as error:
            return ActionResult(False, f"{type(error).__name__}: {error}")

        if state.get("status") != "pending_visual_review":
            return ActionResult(
                False,
                "reference pass is no longer pending visual review",
                {"status": state.get("status")},
            )

        checkpoint_id = str(state.get("checkpoint_id") or "").strip()
        if not checkpoint_id:
            return ActionResult(False, "reference pass has no rollback checkpoint")

        review_path = Path(str(state.get("review_path") or "")).expanduser().resolve()
        artifact_root = (
            self.config.state_dir
            / "artifacts"
            / self._project(payload).slug
        ).resolve()
        if (
            not review_path.is_relative_to(artifact_root)
            or not review_path.is_file()
        ):
            return ActionResult(False, "reference review evidence is missing")
        review_bytes = review_path.read_bytes()
        current_review_sha = hashlib.sha256(review_bytes).hexdigest()
        if current_review_sha != str(state.get("review_sha256") or ""):
            return ActionResult(
                False,
                "reference review evidence changed after candidate capture",
            )
        try:
            review_data = json.loads(review_bytes.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            return ActionResult(False, f"invalid reference review JSON: {error}")
        if not isinstance(review_data, dict):
            return ActionResult(False, "reference review must contain an object")

        if decision == "reject":
            rollback = self.blender_live_checkpoint_restore(
                {
                    **payload,
                    "checkpoint_id": checkpoint_id,
                    "discard_unsaved": True,
                    "timeout_seconds": min(
                        float(payload.get("timeout_seconds", 240)),
                        30.0,
                    ),
                }
            )
            state["decision"] = "reject"
            state["assessment"] = assessment
            state["status"] = (
                "rejected"
                if rollback.ok
                else "rejection_rollback_failed"
            )
            state["rollback"] = {
                "ok": rollback.ok,
                "summary": rollback.summary,
                "data": rollback.data,
            }
            pass_path.write_text(
                json.dumps(state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            return ActionResult(
                rollback.ok,
                (
                    "Reference candidate rejected and checkpoint restored"
                    if rollback.ok
                    else "Reference candidate rejected but rollback failed"
                ),
                {
                    "reference_pass_path": str(pass_path),
                    "checkpoint_id": checkpoint_id,
                    "assessment": assessment,
                    "rollback": state["rollback"],
                },
            )

        object_names = state.get("object_names")
        if (
            not isinstance(object_names, list)
            or not object_names
            or not all(isinstance(name, str) and name.strip() for name in object_names)
        ):
            return ActionResult(False, "reference pass contains invalid object_names")

        fingerprints = self._reference_candidate_fingerprints(
            payload,
            [name.strip() for name in object_names],
        )
        if not fingerprints.ok:
            return ActionResult(
                False,
                "Candidate cannot be accepted because current fingerprints are unavailable",
                {"fingerprints": fingerprints.data},
            )

        expected = self._reference_fingerprint_map(
            state.get("candidate_fingerprints")
        )
        current = self._reference_fingerprint_map(
            fingerprints.data.get("fingerprints")
        )
        if expected != current:
            changed = sorted(
                key
                for key in set(expected) | set(current)
                if expected.get(key) != current.get(key)
            )
            return ActionResult(
                False,
                "Candidate changed after visual evidence; generate a new reference review before accepting",
                {
                    "changed_selectors": changed,
                    "expected_fingerprints": expected,
                    "current_fingerprints": current,
                },
            )

        visual_consistency = self._reference_candidate_visual_hashes(
            payload,
            state,
            review_data,
        )
        if not visual_consistency.ok:
            return ActionResult(
                False,
                "Candidate visual state changed after review; generate a new reference review before accepting",
                {
                    "visual_consistency": visual_consistency.data,
                    "summary": visual_consistency.summary,
                },
            )

        project = self._project(payload)
        declared_target = str(
            state.get("proposed_save_target_path") or ""
        ).strip()
        requested_target = str(payload.get("save_target_path") or "").strip()
        if declared_target and requested_target:
            try:
                requested_resolved = project.path(
                    requested_target,
                    must_exist=False,
                )
            except (ValueError, FileNotFoundError) as error:
                return ActionResult(False, str(error))
            if requested_resolved != Path(declared_target).resolve():
                return ActionResult(
                    False,
                    "save_target_path differs from the target declared before generation",
                )

        target_raw = declared_target or requested_target
        if not target_raw:
            return ActionResult(
                False,
                "save_target_path is required to accept the reviewed candidate",
            )
        try:
            target = project.path(target_raw, must_exist=False)
        except (ValueError, FileNotFoundError) as error:
            return ActionResult(False, str(error))
        if target.suffix.lower() != ".blend":
            return ActionResult(False, "save_target_path must be a .blend file")

        saved = self.blender_live_save(
            {
                **payload,
                "target_path": str(target),
                "timeout_seconds": min(
                    float(payload.get("timeout_seconds", 240)),
                    120.0,
                ),
            }
        )
        if not saved.ok:
            rollback = self.blender_live_checkpoint_restore(
                {
                    **payload,
                    "checkpoint_id": checkpoint_id,
                    "discard_unsaved": True,
                    "timeout_seconds": 30,
                }
            )
            state["decision"] = "accept"
            state["assessment"] = assessment
            state["status"] = (
                "save_failed_rolled_back"
                if rollback.ok
                else "save_failed_rollback_failed"
            )
            state["save"] = {
                "ok": saved.ok,
                "summary": saved.summary,
                "data": saved.data,
            }
            state["rollback"] = {
                "ok": rollback.ok,
                "summary": rollback.summary,
                "data": rollback.data,
            }
            pass_path.write_text(
                json.dumps(state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            return ActionResult(
                False,
                "Reference candidate was accepted visually but final save failed",
                {
                    "reference_pass_path": str(pass_path),
                    "save": state["save"],
                    "rollback": state["rollback"],
                },
            )

        state["decision"] = "accept"
        state["assessment"] = assessment
        state["status"] = "accepted"
        state["saved_target_path"] = str(target)
        state["save"] = {
            "ok": saved.ok,
            "summary": saved.summary,
            "data": saved.data,
        }
        pass_path.write_text(
            json.dumps(state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return ActionResult(
            True,
            "Reference candidate accepted and saved after visual review",
            {
                "reference_pass_path": str(pass_path),
                "checkpoint_id": checkpoint_id,
                "assessment": assessment,
                "saved_target_path": str(target),
                "visual_consistency": visual_consistency.data,
                "save": state["save"],
            },
        )
