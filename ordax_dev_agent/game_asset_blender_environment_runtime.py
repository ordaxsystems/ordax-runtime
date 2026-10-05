"""Materialize and audit ordax.visual-environment/1 in an isolated Blender process.

This file is executed by Blender with --disable-autoexec. It intentionally has no
Device Agent imports so the runtime stays deterministic inside Blender's Python.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

import bpy
from mathutils import Vector

ENVIRONMENT_SCHEMA = "ordax.visual-environment/1"
SUN_OBJECT = "OrdaX_Environment_Sun"
OCEAN_OBJECT = "OrdaX_Environment_Ocean"
OCEAN_MATERIAL = "OrdaX_Environment_Ocean_Material"


def _request_path() -> Path:
    raw = sys.argv
    raw = raw[raw.index("--") + 1 :] if "--" in raw else []
    if len(raw) != 1:
        raise RuntimeError("expected exactly one environment request JSON path")
    return Path(raw[0]).resolve()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("environment request must contain an object")
    return value


def _socket(node: Any, *names: str):
    for name in names:
        result = node.inputs.get(name)
        if result is not None:
            return result
    return None


def _set_if(target: Any, name: str, value: Any, evidence: dict[str, Any]) -> None:
    if not hasattr(target, name):
        evidence[name] = {"supported": False}
        return
    try:
        setattr(target, name, value)
        evidence[name] = value
    except (TypeError, ValueError):
        evidence[name] = {"supported": True, "applied": False, "requested": value}


def _kelvin_rgb(kelvin: float) -> tuple[float, float, float]:
    temperature = max(1000.0, min(40000.0, kelvin)) / 100.0
    if temperature <= 66.0:
        red = 255.0
        green = 99.4708025861 * math.log(temperature) - 161.1195681661
        blue = 0.0 if temperature <= 19.0 else 138.5177312231 * math.log(temperature - 10.0) - 305.0447927307
    else:
        red = 329.698727446 * ((temperature - 60.0) ** -0.1332047592)
        green = 288.1221695283 * ((temperature - 60.0) ** -0.0755148492)
        blue = 255.0
    clamp = lambda value: max(0.0, min(255.0, value)) / 255.0
    return clamp(red), clamp(green), clamp(blue)


def _sun_direction(environment: dict[str, Any]) -> Vector:
    elevation = math.radians(float(environment["sun"]["elevation_deg"]))
    azimuth = math.radians(float(environment["sun"]["azimuth_deg"]))
    return Vector(
        (
            math.sin(azimuth) * math.cos(elevation),
            math.cos(azimuth) * math.cos(elevation),
            math.sin(elevation),
        )
    ).normalized()


def _apply_color_management(scene: bpy.types.Scene, environment: dict[str, Any]) -> dict[str, Any]:
    ev100 = float(environment["exposure"]["ev100"])
    tone_mapping = str(environment["exposure"]["tone_mapping"])
    blender_exposure = max(-10.0, min(10.0, 14.0 - ev100))
    evidence: dict[str, Any] = {
        "ev100": ev100,
        "tone_mapping": tone_mapping,
        "requested_blender_exposure": blender_exposure,
    }
    try:
        scene.view_settings.exposure = blender_exposure
        evidence["blender_exposure"] = float(scene.view_settings.exposure)
    except (TypeError, ValueError, AttributeError):
        evidence["blender_exposure"] = None
    if tone_mapping == "agx":
        try:
            scene.view_settings.view_transform = "AgX"
            evidence["view_transform"] = str(scene.view_settings.view_transform)
        except (TypeError, ValueError, AttributeError):
            evidence["view_transform"] = None
    return evidence


def _apply_world(scene: bpy.types.Scene, environment: dict[str, Any]) -> dict[str, Any]:
    world = scene.world or bpy.data.worlds.new("OrdaX_Environment_World")
    scene.world = world
    world.use_nodes = True
    nodes = world.node_tree.nodes
    links = world.node_tree.links
    nodes.clear()

    output = nodes.new("ShaderNodeOutputWorld")
    background = nodes.new("ShaderNodeBackground")
    sky = nodes.new("ShaderNodeTexSky")
    sky.sky_type = "NISHITA"
    links.new(sky.outputs["Color"], background.inputs["Color"])
    links.new(background.outputs["Background"], output.inputs["Surface"])

    sun = environment["sun"]
    values = environment["sky"]
    evidence: dict[str, Any] = {"sky_type": "NISHITA"}
    _set_if(sky, "sun_elevation", math.radians(float(sun["elevation_deg"])), evidence)
    _set_if(sky, "sun_rotation", math.radians(float(sun["azimuth_deg"])), evidence)
    _set_if(sky, "altitude", 0.0, evidence)
    _set_if(sky, "air_density", max(0.1, min(5.0, float(values["rayleigh"]) / 2.4)), evidence)
    _set_if(sky, "dust_density", max(0.0, min(10.0, float(values["mie_coefficient"]) / 0.006)), evidence)
    _set_if(sky, "ozone_density", 1.0, evidence)
    _set_if(sky, "sun_disc", True, evidence)
    _set_if(sky, "sun_intensity", max(0.0, min(10.0, float(sun["intensity_lux"]) / 100000.0)), evidence)
    background.inputs["Strength"].default_value = max(
        0.1,
        min(2.0, 0.9 - (float(values["turbidity"]) - 2.0) * 0.03),
    )
    evidence["background_strength"] = float(background.inputs["Strength"].default_value)

    fog_density = float(environment["atmosphere"]["fog_density"])
    if fog_density > 0.0:
        volume = nodes.new("ShaderNodeVolumePrincipled")
        density = _socket(volume, "Density")
        anisotropy = _socket(volume, "Anisotropy")
        if density is not None:
            density.default_value = max(0.0, min(0.05, fog_density * 0.002))
        if anisotropy is not None:
            anisotropy.default_value = 0.2
        links.new(volume.outputs["Volume"], output.inputs["Volume"])
        evidence["volume_density"] = float(density.default_value) if density is not None else None
    else:
        evidence["volume_density"] = 0.0
    return evidence


def _apply_sun(scene: bpy.types.Scene, environment: dict[str, Any]) -> dict[str, Any]:
    obj = bpy.data.objects.get(SUN_OBJECT)
    if obj is not None and obj.type != "LIGHT":
        raise RuntimeError(f"{SUN_OBJECT} exists but is not a Light object")
    if obj is None:
        light = bpy.data.lights.new(SUN_OBJECT + "_Data", "SUN")
        obj = bpy.data.objects.new(SUN_OBJECT, light)
        scene.collection.objects.link(obj)
    light = obj.data
    light.type = "SUN"
    direction = _sun_direction(environment)
    obj.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()
    lux = float(environment["sun"]["intensity_lux"])
    light.energy = max(0.0, min(10.0, lux / 50000.0))
    light.color = _kelvin_rgb(float(environment["sun"]["color_temperature_k"]))
    if hasattr(light, "angle"):
        light.angle = math.radians(0.53)
    obj["ordax_role"] = "environment_sun"
    obj["ordax_environment_schema"] = ENVIRONMENT_SCHEMA
    return {
        "name": obj.name,
        "energy": float(light.energy),
        "color": [round(float(value), 6) for value in light.color],
        "direction": [round(float(value), 6) for value in direction],
    }


def _water_material(environment: dict[str, Any]) -> bpy.types.Material:
    material = bpy.data.materials.get(OCEAN_MATERIAL) or bpy.data.materials.new(OCEAN_MATERIAL)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    ocean = environment["ocean"]
    deep = ocean["deep_color"]
    base = _socket(bsdf, "Base Color")
    if base is not None:
        base.default_value = (*[float(value) for value in deep], 1.0)
    roughness = _socket(bsdf, "Roughness")
    if roughness is not None:
        roughness.default_value = float(ocean["roughness"])
    metallic = _socket(bsdf, "Metallic")
    if metallic is not None:
        metallic.default_value = 0.0
    ior = _socket(bsdf, "IOR")
    if ior is not None:
        ior.default_value = 1.333
    transmission = _socket(bsdf, "Transmission Weight", "Transmission")
    if transmission is not None:
        transmission.default_value = 0.72
    coat = _socket(bsdf, "Coat Weight", "Clearcoat")
    if coat is not None:
        coat.default_value = 0.18
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
    return material


def _remove_ocean() -> None:
    obj = bpy.data.objects.get(OCEAN_OBJECT)
    if obj is None:
        return
    mesh = obj.data if obj.type == "MESH" else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _apply_ocean(scene: bpy.types.Scene, environment: dict[str, Any]) -> dict[str, Any]:
    ocean = environment["ocean"]
    if not bool(ocean["enabled"]):
        _remove_ocean()
        return {"enabled": False}

    spectrum = ocean.get("spectrum") or {}
    obj = bpy.data.objects.get(OCEAN_OBJECT)
    if obj is not None and obj.type != "MESH":
        raise RuntimeError(f"{OCEAN_OBJECT} exists but is not a Mesh object")
    if obj is None:
        mesh = bpy.data.meshes.new(OCEAN_OBJECT + "_Mesh")
        obj = bpy.data.objects.new(OCEAN_OBJECT, mesh)
        scene.collection.objects.link(obj)
    obj.location.z = float(ocean["sea_level_m"])
    obj["ordax_role"] = "environment_ocean"
    obj["ordax_environment_schema"] = ENVIRONMENT_SCHEMA
    obj["ordax_ocean_profile"] = str(spectrum.get("profile") or "legacy")
    obj["ordax_ocean_spectrum_json"] = json.dumps(spectrum, sort_keys=True)

    modifier = obj.modifiers.get("OrdaX Ocean")
    if modifier is not None and modifier.type != "OCEAN":
        raise RuntimeError("OrdaX Ocean modifier name is occupied by a non-Ocean modifier")
    if modifier is None:
        modifier = obj.modifiers.new("OrdaX Ocean", "OCEAN")

    spread = float(spectrum.get("swell_spread_deg", 35.0))
    wind_direction = float(spectrum.get("wind_wave_direction_deg", environment["wind"]["direction_deg"]))
    short_scale = float(spectrum.get("short_wave_scale_m", max(0.1, float(ocean["significant_wave_height_m"]) * 0.2)))
    shore_foam = float(spectrum.get("shore_foam_amount", 0.0))
    evidence: dict[str, Any] = {
        "enabled": True,
        "object": obj.name,
        "modifier": modifier.name,
        "profile": str(spectrum.get("profile") or "legacy"),
    }
    values = {
        "geometry_mode": "GENERATE",
        "resolution": 8,
        "render_resolution": 9,
        "spatial_size": 1500.0,
        "repeat_x": 4,
        "repeat_y": 4,
        "wave_scale": max(0.01, float(ocean["significant_wave_height_m"]) * 0.5),
        "choppiness": float(ocean["choppiness"]),
        "wind_velocity": float(environment["wind"]["speed_mps"]),
        "wind_direction": math.radians(wind_direction),
        "wave_direction": math.radians(float(ocean["swell_direction_deg"])),
        "wave_alignment": max(0.1, min(1.0, 1.0 - spread / 100.0)),
        "damping": 0.5,
        "smallest_wave": max(0.05, min(2.0, short_scale)),
        "use_foam": float(ocean["foam_amount"]) + shore_foam > 0.001,
        "foam_coverage": max(0.0, min(1.0, float(ocean["foam_amount"]) + shore_foam * 0.35)),
    }
    for key, value in values.items():
        _set_if(modifier, key, value, evidence)

    material = _water_material(environment)
    obj.data.materials.clear()
    obj.data.materials.append(material)
    evidence["material"] = material.name
    evidence["reference_extent_m"] = 6000.0
    evidence["spectrum"] = spectrum
    return evidence


def _audit(scene: bpy.types.Scene) -> dict[str, Any]:
    metadata_raw = scene.get("ordax_visual_environment_json")
    metadata: dict[str, Any] | None = None
    if isinstance(metadata_raw, str):
        try:
            parsed = json.loads(metadata_raw)
            metadata = parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            metadata = None

    sky_nodes: list[str] = []
    volume_nodes: list[str] = []
    world = scene.world
    if world is not None and world.use_nodes and world.node_tree is not None:
        for node in world.node_tree.nodes:
            if node.bl_idname == "ShaderNodeTexSky":
                sky_nodes.append(node.name)
            elif node.bl_idname == "ShaderNodeVolumePrincipled":
                volume_nodes.append(node.name)

    sun = bpy.data.objects.get(SUN_OBJECT)
    ocean = bpy.data.objects.get(OCEAN_OBJECT)
    ocean_modifier = ocean.modifiers.get("OrdaX Ocean") if ocean is not None and ocean.type == "MESH" else None
    spectrum_raw = ocean.get("ordax_ocean_spectrum_json") if ocean is not None else None
    try:
        spectrum = json.loads(spectrum_raw) if isinstance(spectrum_raw, str) else None
    except json.JSONDecodeError:
        spectrum = None
    return {
        "schema": "ordax.blender-environment-audit/2",
        "environment_schema": scene.get("ordax_visual_environment_schema"),
        "environment_metadata_valid": metadata is not None and metadata.get("schema") == ENVIRONMENT_SCHEMA,
        "environment": metadata,
        "world": {
            "present": world is not None,
            "uses_nodes": bool(world is not None and world.use_nodes),
            "sky_nodes": sky_nodes,
            "volume_nodes": volume_nodes,
        },
        "sun": {
            "present": sun is not None,
            "type": sun.type if sun is not None else None,
            "light_type": sun.data.type if sun is not None and sun.type == "LIGHT" else None,
            "role": sun.get("ordax_role") if sun is not None else None,
        },
        "ocean": {
            "present": ocean is not None,
            "type": ocean.type if ocean is not None else None,
            "modifier_present": ocean_modifier is not None,
            "material": ocean.data.materials[0].name if ocean is not None and ocean.type == "MESH" and ocean.data.materials else None,
            "role": ocean.get("ordax_role") if ocean is not None else None,
            "profile": ocean.get("ordax_ocean_profile") if ocean is not None else None,
            "spectrum": spectrum,
        },
    }


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    request = _load(_request_path())
    output = Path(str(request.get("output_path") or "")).resolve()
    report_path = Path(str(request.get("report_path") or "")).resolve()
    try:
        environment = request.get("environment")
        if not isinstance(environment, dict) or environment.get("schema") != ENVIRONMENT_SCHEMA:
            raise RuntimeError(f"environment must use {ENVIRONMENT_SCHEMA}")
        if output.suffix.lower() != ".blend":
            raise RuntimeError("output_path must be a .blend file")
        if report_path.suffix.lower() != ".json":
            raise RuntimeError("report_path must be a .json file")

        scene = bpy.context.scene
        scene.unit_settings.system = "METRIC"
        scene.unit_settings.scale_length = 1.0
        scene["ordax_visual_environment_schema"] = ENVIRONMENT_SCHEMA
        scene["ordax_visual_environment_name"] = str(environment["name"])
        scene["ordax_visual_environment_json"] = json.dumps(environment, sort_keys=True)

        applied = {
            "color_management": _apply_color_management(scene, environment),
            "world": _apply_world(scene, environment),
            "sun": _apply_sun(scene, environment),
            "ocean": _apply_ocean(scene, environment),
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False)

        # Reopen the derivative and audit persisted Blender data without saving again.
        bpy.ops.wm.open_mainfile(filepath=str(output), load_ui=False)
        audit = _audit(bpy.context.scene)
        required_ocean = bool(environment["ocean"]["enabled"])
        valid = bool(
            audit["environment_metadata_valid"]
            and audit["world"]["sky_nodes"]
            and audit["sun"]["present"]
            and (not required_ocean or (audit["ocean"]["present"] and audit["ocean"]["modifier_present"]))
        )
        report = {
            "ok": valid,
            "schema": "ordax.blender-environment-materialization/2",
            "blender_version": ".".join(str(value) for value in bpy.app.version),
            "output_blend": str(output),
            "bytes": output.stat().st_size,
            "environment_schema": ENVIRONMENT_SCHEMA,
            "environment_name": environment["name"],
            "applied": applied,
            "audit": audit,
        }
        _write_report(report_path, report)
        print(json.dumps({"ok": valid, "report": str(report_path)}))
        return 0 if valid else 3
    except Exception as error:
        payload = {"ok": False, "error_type": type(error).__name__, "error": str(error)}
        try:
            _write_report(report_path, payload)
        except Exception:
            pass
        print(json.dumps(payload), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
