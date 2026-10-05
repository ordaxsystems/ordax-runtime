"""Capability catalog for the OrdaX game-asset ecosystem."""
from __future__ import annotations

import os
from typing import Any

from .aleph_actions import ALEPH_PINNED_REF
from .models import ActionResult


class GameAssetCatalogActions:
    """Expose integrated providers and researched extension points in one place."""

    def game_assets_providers(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = set(payload) - {"project"}
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        providers = {
            "adobe_mixamo": {
                "mode": "validated_manual_handoff",
                "configured": True,
                "capabilities": [
                    "humanoid_auto_rig",
                    "humanoid_animation_library",
                    "fbx_roundtrip",
                ],
                "actions": [
                    "game_assets.mixamo_handoff",
                    "game_assets.blender_import_fbx",
                ],
                "notes": [
                    "Uses Adobe's documented web workflow; no private API or browser scraping.",
                    "Already-rigged uploads use FBX.",
                ],
            },
            "tripo": {
                "mode": "api",
                "configured": bool(os.environ.get("TRIPO_API_KEY")),
                "credential_env": "TRIPO_API_KEY",
                "capabilities": [
                    "text_to_model",
                    "single_image_to_3d",
                    "four_view_to_3d",
                    "pre_rig_check",
                    "rig",
                    "mixamo_skeleton_spec",
                    "animation_retarget",
                    "smart_lowpoly",
                    "canonical_artifact_download",
                ],
                "actions": [
                    "game_assets.provider_submit",
                    "game_assets.tripo_submit_images",
                    "game_assets.provider_status",
                    "game_assets.provider_download",
                ],
            },
            "meshy": {
                "mode": "api",
                "configured": bool(os.environ.get("MESHY_API_KEY")),
                "credential_env": "MESHY_API_KEY",
                "capabilities": [
                    "text_to_3d",
                    "single_image_to_3d",
                    "multi_image_to_3d",
                    "pbr_refine",
                    "humanoid_rig",
                    "animation",
                    "text_to_motion",
                    "canonical_artifact_download",
                ],
                "actions": [
                    "game_assets.provider_submit",
                    "game_assets.meshy_submit_images",
                    "game_assets.provider_status",
                    "game_assets.provider_download",
                ],
            },
            "hyper3d_rodin": {
                "mode": "api",
                "configured": bool(os.environ.get("RODIN_API_KEY")),
                "credential_env": "RODIN_API_KEY",
                "capabilities": [
                    "text_to_3d",
                    "one_to_five_image_to_3d",
                    "orientation_labels",
                    "pbr",
                    "raw_or_quad_mesh",
                    "face_budget",
                    "ta_pose_conditioning",
                    "glb_fbx_obj_stl_usdz",
                    "canonical_artifact_download",
                ],
                "actions": [
                    "game_assets.rodin_submit_text",
                    "game_assets.rodin_submit_images",
                    "game_assets.rodin_status",
                    "game_assets.rodin_download_manifest",
                    "game_assets.provider_download",
                ],
            },
            "comfyui_local": {
                "mode": "local_loopback_workflow",
                "configured": True,
                "endpoint_env": "ORDAX_COMFYUI_URL",
                "default_endpoint": "http://127.0.0.1:8188",
                "capabilities": [
                    "project_versioned_workflows",
                    "3d_node_discovery",
                    "local_gpu_generation",
                    "workflow_history",
                ],
                "actions": [
                    "game_assets.comfyui_status",
                    "game_assets.comfyui_node_info",
                    "game_assets.comfyui_run_workflow",
                    "game_assets.comfyui_history",
                ],
                "security": "loopback-only",
            },
            "alephgeo": {
                "mode": "managed_external_component",
                "configured": True,
                "auto_install_on_first_use": True,
                "upstream": "Belluxx/Aleph",
                "pinned_ref": ALEPH_PINNED_REF,
                "capabilities": [
                    "place_resolution",
                    "satellite_imagery",
                    "streetview_reference_photos",
                    "osm_buildings_and_roads",
                    "terrain_elevation",
                    "resumable_area_capture",
                    "capture_inspection",
                    "blender_terrain_reconstruction",
                    "blender_osm_building_massing",
                    "blender_road_curves",
                    "blender_streetview_reference_cameras",
                ],
                "actions": [
                    "geo.aleph_status",
                    "geo.aleph_ensure",
                    "geo.aleph_update",
                    "geo.aleph_resolve",
                    "geo.aleph_satellite",
                    "geo.aleph_streetview",
                    "geo.aleph_capture",
                    "geo.aleph_capture_resume",
                    "geo.aleph_capture_export",
                    "geo.aleph_capture_inspect",
                    "geo.aleph_blender_stage",
                ],
                "notes": [
                    "Installed into an isolated Device Agent state-directory virtual environment.",
                    "Upstream explicitly warns that it uses undocumented APIs and may break or hit rate limits.",
                    "Captured data is canonical; Blender scenes are reproducible local-metre derivatives.",
                ],
            },
        }
        return ActionResult(True, "game-asset providers inspected", {"providers": providers})

    def game_assets_ecosystem_catalog(self, payload: dict[str, Any]) -> ActionResult:
        unsupported = set(payload) - {"project"}
        if unsupported:
            return ActionResult(False, f"unsupported fields: {', '.join(sorted(unsupported))}")
        catalog = {
            "integrated": {
                "cloud_generation": ["tripo", "meshy", "hyper3d_rodin"],
                "local_generation_host": ["comfyui_local"],
                "world_reference_capture": ["alephgeo"],
                "world_reconstruction": ["alephgeo", "blender"],
                "rig_animation": ["adobe_mixamo", "tripo", "meshy"],
                "runtime_optimization": ["blender_runtime_audit", "static_lod_generation"],
                "engine_derivation": ["verified_blender_export"],
                "engine_validation": [
                    "unity_verified_handoff",
                    "unity_model_import_audit",
                    "unity_static_lod_prefab",
                ],
                "dcc": ["blender"],
                "engines": ["unity", "unreal_export", "godot_export", "web_gltf"],
            },
            "image_conditioned_generation": {
                "security_model": [
                    "project_local_source_files_only",
                    "jpeg_png_magic_validation",
                    "per_image_size_limits",
                    "no_job_supplied_external_image_urls",
                ],
                "meshy": {
                    "action": "game_assets.meshy_submit_images",
                    "images": "1-4",
                    "single": "image_to_3d",
                    "multiple": "multi_image_to_3d",
                },
                "tripo": {
                    "action": "game_assets.tripo_submit_images",
                    "images": "1 or 4",
                    "four_view_order": ["front", "left", "back", "right"],
                    "upload_tokens_exposed_to_job_result": False,
                },
                "rodin": {
                    "action": "game_assets.rodin_submit_images",
                    "images": "1-5",
                    "orientation_labels": ["F", "FL", "FR", "B", "BL", "BR", "L", "R", "U", "D", "?"],
                },
            },
            "canonical_generated_artifacts": {
                "download_action": "game_assets.provider_download",
                "verify_action": "game_assets.artifact_verify",
                "blender_ingest_action": "game_assets.blender_ingest_generated",
                "providers": ["tripo", "meshy", "rodin"],
                "manifest_schema": "ordax.generated-asset/1",
                "properties": [
                    "provider_status_is_retrieved_server_side",
                    "job_cannot_supply_arbitrary_download_url",
                    "https_only_result_download",
                    "bounded_streaming_download",
                    "atomic_partial_file_replacement",
                    "sha256_content_identity",
                    "sidecar_provenance_manifest",
                    "signed_query_parameters_are_not_persisted",
                    "explicit_overwrite_required",
                    "hash_verified_before_blender_staging",
                    "provider_task_hash_embedded_in_blender_scene_metadata",
                ],
            },
            "runtime_budget_audit": {
                "action": "game_assets.blender_runtime_audit",
                "metrics": [
                    "triangles",
                    "vertices",
                    "material_slots",
                    "materials",
                    "textures",
                    "max_texture_dimension",
                    "texture_pixels",
                    "armatures",
                    "bones",
                    "actions",
                    "shape_keys",
                    "max_vertex_influences",
                ],
                "optional_limits": [
                    "max_triangles",
                    "max_material_slots",
                    "max_texture_dimension",
                    "max_vertex_influences",
                    "max_bones",
                    "max_actions",
                ],
                "static_lod_gate": "lod_safe_static_candidate requires zero armatures and zero shape keys",
            },
            "static_lod_generation": {
                "action": "game_assets.blender_generate_static_lods",
                "schema": "ordax.static-lod/1",
                "default_ratios": [0.5, 0.25, 0.1],
                "safety": [
                    "source_blend_is_never_overwritten",
                    "armature_scenes_are_refused",
                    "shape_key_meshes_are_refused",
                    "source_evaluated_geometry_is_baked_before_decimation",
                    "lod0_and_each_reduced_level_live_in_separate_collections",
                    "actual_triangle_counts_are_reported",
                ],
                "purpose": "safe automatic LODs for static runtime assets; skinned/morph assets require a separate deformation-aware path",
            },
            "verified_engine_exports": {
                "export_action": "game_assets.blender_export_verified",
                "verify_action": "game_assets.engine_export_verify",
                "manifest_schema": "ordax.engine-export/1",
                "properties": [
                    "source_blend_sha256_is_snapshotted_before_export",
                    "export_is_written_to_a_temporary_artifact_first",
                    "source_is_rehashed_before_derivative_promotion",
                    "artifact_and_manifest_are_promoted_only_after_success",
                    "artifact_sha256_and_byte_size_are_persisted",
                    "engine_target_and_export_profile_are_persisted",
                    "historical_export_remains_verifiable_after_source_blend_changes",
                    "source_current_matches_reports_whether_source_still_matches_snapshot",
                    "explicit_overwrite_required",
                ],
            },
            "engine_import_validation": {
                "unity": {
                    "generated_handoff_action": "game_assets.unity_import_generated",
                    "engine_export_handoff_action": "game_assets.unity_import_engine_export",
                    "model_audit_action": "game_assets.unity_model_audit",
                    "static_lod_prefab_action": "game_assets.unity_build_static_lod_prefab",
                    "required_order_for_static_lod": [
                        "game_assets.blender_runtime_audit",
                        "game_assets.blender_generate_static_lods",
                        "game_assets.blender_export_verified",
                        "game_assets.engine_export_verify",
                        "game_assets.unity_import_engine_export",
                        "game_assets.unity_model_audit",
                        "game_assets.unity_build_static_lod_prefab",
                    ],
                    "model_audit_metrics": [
                        "asset_importer_type",
                        "model_importer_presence",
                        "global_scale",
                        "read_write_enabled",
                        "animation_import_enabled",
                        "animation_type",
                        "mesh_compression",
                        "mesh_count",
                        "vertex_count",
                        "triangle_count",
                        "material_count",
                        "animation_clip_count",
                        "bone_count",
                        "blend_shape_count",
                        "lod_group_count",
                    ],
                    "static_lod_prefab_gate": [
                        "zero_bones",
                        "zero_blend_shapes",
                        "zero_animation_clips",
                        "source_has_no_existing_lodgroup",
                        "every_renderer_belongs_to_a_LODn_hierarchy",
                        "lod_levels_are_contiguous_from_LOD0",
                        "transition_heights_are_strictly_descending",
                        "prefab_overwrite_is_explicit",
                    ],
                    "security": [
                        "source_must_be_project_local",
                        "generated_handoff_requires_valid_ordax_provenance",
                        "engine_export_handoff_requires_valid_ordax_engine_export_provenance",
                        "engine_export_target_must_equal_unity",
                        "destination_must_stay_under_assets",
                        "audit_asset_path_must_stay_under_assets",
                        "canonical_source_is_never_deleted_on_editor_failure",
                    ],
                },
            },
            "world_generation_pipeline": {
                "reference_source": "alephgeo",
                "inputs": [
                    "satellite_png_or_geotiff",
                    "terrain_geotiff",
                    "osm_buildings_roads_and_pois",
                    "streetview_reference_photos",
                ],
                "implemented_stages": [
                    "inspect_and_validate_capture",
                    "normalize_to_local_metre_coordinates",
                    "construct_blender_terrain_with_uv",
                    "apply_packed_satellite_reference_material",
                    "extrude_osm_building_massing",
                    "construct_width_classed_road_curves",
                    "create_georeferenced_streetview_reference_cameras",
                    "store_geographic_provenance_metadata",
                ],
                "next_stages": [
                    "architectural_facade_detail",
                    "road_intersection_and_lane_semantics",
                    "vegetation_and_poi_instancing",
                    "collision_navmesh_and_runtime_lod",
                    "engine_specific_world_import_validation",
                ],
                "reliability_note": "Aleph uses undocumented upstream data APIs; capture artifacts should be cached and reproducible locally.",
            },
            "local_model_candidates": {
                "trellis_2": {
                    "route": "ComfyUI workflow or isolated local adapter",
                    "strength": "high-fidelity image-to-3D with PBR materials",
                    "constraint": "upstream documents Linux + NVIDIA GPU >=24GB",
                    "integration_state": "candidate_not_bundled",
                },
                "hunyuan3d_2_1": {
                    "route": "ComfyUI workflow or isolated FastAPI adapter",
                    "strength": "image-to-3D, optional texture, GLB/OBJ API",
                    "constraint": "upstream model code/weights carry Tencent Hunyuan non-commercial terms",
                    "integration_state": "candidate_not_bundled",
                },
                "stable_fast_3d": {
                    "route": "ComfyUI workflow",
                    "strength": "fast single-image 3D asset generation",
                    "constraint": "validate model license and target GPU before deployment",
                    "integration_state": "candidate_not_bundled",
                },
            },
            "motion_candidates": {
                "deepmotion": {
                    "strength": "video-to-3D markerless mocap with custom-character retarget",
                    "route": "future API adapter",
                },
                "rokoko": {
                    "strength": "studio recording/calibration command API and FBX/BVH export workflows",
                    "route": "future workstation adapter",
                },
                "cascadeur": {
                    "strength": "animation cleanup/authoring with Python scripting API",
                    "route": "future workstation adapter",
                },
            },
            "optimization_targets": {
                "canonical_web_exchange": "glTF 2.0 / GLB",
                "texture_delivery": "KTX2/BasisU where the target supports KHR_texture_basisu",
                "implemented": [
                    "static_lod_generation",
                    "verified_engine_export_provenance",
                    "unity_static_lod_prefab",
                ],
                "future_passes": [
                    "deformation_aware_character_lod",
                    "meshopt_or_draco_geometry_compression",
                    "ktx2_texture_compression",
                    "orm_texture_packing",
                    "per_engine_triangle_material_texture_budgets",
                ],
            },
            "quality_gates": [
                "geometry_topology",
                "uv_and_materials",
                "skin_influence_budget",
                "armature_and_actions",
                "origin_scale_axes",
                "runtime_budget_audit",
                "visual_multiview_review",
                "verified_engine_export",
                "engine_import_validation",
            ],
        }
        return ActionResult(True, "game-asset ecosystem catalog", {"catalog": catalog})
