"""Closed-world visual environment contract shared across 3D runtimes."""
from __future__ import annotations

import copy
import math
from typing import Any

ENVIRONMENT_SCHEMA = "ordax.visual-environment/1"

_TONE_MAPPINGS = {"agx", "aces", "neutral"}
_OCEAN_PROFILES = {"bay", "coastal", "open_ocean"}


def _number(value: Any, field: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return result


def _rgb(value: Any, field: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"{field} must be an RGB list")
    return [_number(item, field, 0.0, 1.0) for item in value]


def _object(value: Any, field: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"unsupported {field} field(s): {', '.join(unknown)}")
    return value


def _bounded_name(value: Any, field: str = "name") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    result = value.strip()
    if not result or len(result.encode("utf-8")) > 96:
        raise ValueError(f"{field} must be a non-empty string up to 96 UTF-8 bytes")
    return result


def _ocean_spectrum_defaults(
    *,
    profile: str,
    swell_spread_deg: float,
    wind_wave_height_m: float,
    wind_wave_period_s: float,
    wind_wave_direction_deg: float,
    short_wave_strength: float,
    short_wave_scale_m: float,
    crest_foam_threshold: float,
    shore_foam_amount: float,
) -> dict[str, Any]:
    return {
        "profile": profile,
        "swell_spread_deg": swell_spread_deg,
        "wind_wave_height_m": wind_wave_height_m,
        "wind_wave_period_s": wind_wave_period_s,
        "wind_wave_direction_deg": wind_wave_direction_deg,
        "short_wave_strength": short_wave_strength,
        "short_wave_scale_m": short_wave_scale_m,
        "crest_foam_threshold": crest_foam_threshold,
        "shore_foam_amount": shore_foam_amount,
    }


PRESETS: dict[str, dict[str, Any]] = {
    "salvador_clear_noon": {
        "schema": ENVIRONMENT_SCHEMA,
        "name": "Salvador Clear Noon",
        "sun": {
            "elevation_deg": 72.0,
            "azimuth_deg": 35.0,
            "intensity_lux": 105000.0,
            "color_temperature_k": 5700.0,
        },
        "sky": {
            "turbidity": 4.0,
            "rayleigh": 2.4,
            "mie_coefficient": 0.006,
            "mie_directional_g": 0.82,
            "cloud_coverage": 0.18,
            "cloud_density": 0.24,
        },
        "atmosphere": {
            "horizon_haze": 0.28,
            "visibility_km": 45.0,
            "fog_density": 0.015,
            "fog_height_m": 180.0,
        },
        "wind": {"speed_mps": 5.5, "direction_deg": 110.0},
        "ocean": {
            "enabled": True,
            "sea_level_m": 0.0,
            "significant_wave_height_m": 0.7,
            "swell_period_s": 7.5,
            "swell_direction_deg": 125.0,
            "choppiness": 0.75,
            "foam_amount": 0.12,
            "deep_color": [0.015, 0.16, 0.24],
            "shallow_color": [0.03, 0.33, 0.38],
            "roughness": 0.18,
            "absorption": 0.45,
            "spectrum": _ocean_spectrum_defaults(
                profile="bay",
                swell_spread_deg=18.0,
                wind_wave_height_m=0.24,
                wind_wave_period_s=2.8,
                wind_wave_direction_deg=110.0,
                short_wave_strength=0.58,
                short_wave_scale_m=0.85,
                crest_foam_threshold=0.64,
                shore_foam_amount=0.18,
            ),
        },
        "exposure": {"ev100": 14.2, "tone_mapping": "agx"},
    },
    "salvador_golden_hour": {
        "schema": ENVIRONMENT_SCHEMA,
        "name": "Salvador Golden Hour",
        "sun": {
            "elevation_deg": 8.0,
            "azimuth_deg": 268.0,
            "intensity_lux": 18000.0,
            "color_temperature_k": 3900.0,
        },
        "sky": {
            "turbidity": 5.5,
            "rayleigh": 2.7,
            "mie_coefficient": 0.012,
            "mie_directional_g": 0.84,
            "cloud_coverage": 0.28,
            "cloud_density": 0.32,
        },
        "atmosphere": {
            "horizon_haze": 0.52,
            "visibility_km": 28.0,
            "fog_density": 0.028,
            "fog_height_m": 220.0,
        },
        "wind": {"speed_mps": 4.0, "direction_deg": 120.0},
        "ocean": {
            "enabled": True,
            "sea_level_m": 0.0,
            "significant_wave_height_m": 0.55,
            "swell_period_s": 8.5,
            "swell_direction_deg": 130.0,
            "choppiness": 0.62,
            "foam_amount": 0.1,
            "deep_color": [0.018, 0.12, 0.18],
            "shallow_color": [0.035, 0.27, 0.31],
            "roughness": 0.22,
            "absorption": 0.52,
            "spectrum": _ocean_spectrum_defaults(
                profile="bay",
                swell_spread_deg=14.0,
                wind_wave_height_m=0.18,
                wind_wave_period_s=3.1,
                wind_wave_direction_deg=120.0,
                short_wave_strength=0.46,
                short_wave_scale_m=1.0,
                crest_foam_threshold=0.68,
                shore_foam_amount=0.14,
            ),
        },
        "exposure": {"ev100": 11.2, "tone_mapping": "agx"},
    },
}


def environment_schema() -> dict[str, Any]:
    return {
        "schema": ENVIRONMENT_SCHEMA,
        "presets": sorted(PRESETS),
        "sections": {
            "sun": ["elevation_deg", "azimuth_deg", "intensity_lux", "color_temperature_k"],
            "sky": ["turbidity", "rayleigh", "mie_coefficient", "mie_directional_g", "cloud_coverage", "cloud_density"],
            "atmosphere": ["horizon_haze", "visibility_km", "fog_density", "fog_height_m"],
            "wind": ["speed_mps", "direction_deg"],
            "ocean": [
                "enabled",
                "sea_level_m",
                "significant_wave_height_m",
                "swell_period_s",
                "swell_direction_deg",
                "choppiness",
                "foam_amount",
                "deep_color",
                "shallow_color",
                "roughness",
                "absorption",
                "spectrum",
            ],
            "ocean.spectrum": [
                "profile",
                "swell_spread_deg",
                "wind_wave_height_m",
                "wind_wave_period_s",
                "wind_wave_direction_deg",
                "short_wave_strength",
                "short_wave_scale_m",
                "crest_foam_threshold",
                "shore_foam_amount",
            ],
            "exposure": ["ev100", "tone_mapping"],
        },
    }


def environment_preset(name: str) -> dict[str, Any]:
    key = name.strip().lower() if isinstance(name, str) else ""
    if key not in PRESETS:
        raise ValueError(f"unknown environment preset: {name}")
    return copy.deepcopy(PRESETS[key])


def normalize_environment(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("environment must be an object")
    allowed = {"schema", "preset", "name", "sun", "sky", "atmosphere", "wind", "ocean", "exposure"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError("unsupported environment field(s): " + ", ".join(unknown))

    raw_schema = payload.get("schema")
    if raw_schema is not None and raw_schema != ENVIRONMENT_SCHEMA:
        raise ValueError(f"environment schema must be {ENVIRONMENT_SCHEMA}")

    preset_name = payload.get("preset", "salvador_clear_noon")
    base = environment_preset(str(preset_name))
    base.pop("preset", None)
    if payload.get("name") is not None:
        base["name"] = _bounded_name(payload["name"])

    for section in ("sun", "sky", "atmosphere", "wind", "ocean", "exposure"):
        if section not in payload:
            continue
        if not isinstance(payload[section], dict):
            raise ValueError(f"{section} must be an object")
        if section == "ocean" and "spectrum" in payload[section]:
            ocean_update = dict(payload[section])
            spectrum_update = ocean_update.pop("spectrum")
            if not isinstance(spectrum_update, dict):
                raise ValueError("ocean.spectrum must be an object")
            base["ocean"].update(ocean_update)
            base["ocean"]["spectrum"].update(spectrum_update)
        else:
            base[section].update(payload[section])

    sun = _object(base["sun"], "sun", {"elevation_deg", "azimuth_deg", "intensity_lux", "color_temperature_k"})
    sky = _object(base["sky"], "sky", {"turbidity", "rayleigh", "mie_coefficient", "mie_directional_g", "cloud_coverage", "cloud_density"})
    atmosphere = _object(base["atmosphere"], "atmosphere", {"horizon_haze", "visibility_km", "fog_density", "fog_height_m"})
    wind = _object(base["wind"], "wind", {"speed_mps", "direction_deg"})
    ocean = _object(
        base["ocean"],
        "ocean",
        {
            "enabled",
            "sea_level_m",
            "significant_wave_height_m",
            "swell_period_s",
            "swell_direction_deg",
            "choppiness",
            "foam_amount",
            "deep_color",
            "shallow_color",
            "roughness",
            "absorption",
            "spectrum",
        },
    )
    spectrum = _object(
        ocean.get("spectrum"),
        "ocean.spectrum",
        {
            "profile",
            "swell_spread_deg",
            "wind_wave_height_m",
            "wind_wave_period_s",
            "wind_wave_direction_deg",
            "short_wave_strength",
            "short_wave_scale_m",
            "crest_foam_threshold",
            "shore_foam_amount",
        },
    )
    exposure = _object(base["exposure"], "exposure", {"ev100", "tone_mapping"})

    enabled = ocean.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("ocean.enabled must be boolean")
    profile = spectrum.get("profile")
    if not isinstance(profile, str) or profile.strip().lower() not in _OCEAN_PROFILES:
        raise ValueError("ocean.spectrum.profile must be one of bay, coastal, open_ocean")
    tone_mapping = exposure.get("tone_mapping")
    if not isinstance(tone_mapping, str) or tone_mapping.lower() not in _TONE_MAPPINGS:
        raise ValueError("exposure.tone_mapping must be one of agx, aces, neutral")

    return {
        "schema": ENVIRONMENT_SCHEMA,
        "name": _bounded_name(base["name"]),
        "sun": {
            "elevation_deg": _number(sun["elevation_deg"], "sun.elevation_deg", -8.0, 90.0),
            "azimuth_deg": _number(sun["azimuth_deg"], "sun.azimuth_deg", 0.0, 360.0),
            "intensity_lux": _number(sun["intensity_lux"], "sun.intensity_lux", 0.0, 200000.0),
            "color_temperature_k": _number(sun["color_temperature_k"], "sun.color_temperature_k", 1000.0, 20000.0),
        },
        "sky": {
            "turbidity": _number(sky["turbidity"], "sky.turbidity", 1.0, 20.0),
            "rayleigh": _number(sky["rayleigh"], "sky.rayleigh", 0.0, 8.0),
            "mie_coefficient": _number(sky["mie_coefficient"], "sky.mie_coefficient", 0.0, 0.1),
            "mie_directional_g": _number(sky["mie_directional_g"], "sky.mie_directional_g", 0.0, 0.999),
            "cloud_coverage": _number(sky["cloud_coverage"], "sky.cloud_coverage", 0.0, 1.0),
            "cloud_density": _number(sky["cloud_density"], "sky.cloud_density", 0.0, 1.0),
        },
        "atmosphere": {
            "horizon_haze": _number(atmosphere["horizon_haze"], "atmosphere.horizon_haze", 0.0, 1.0),
            "visibility_km": _number(atmosphere["visibility_km"], "atmosphere.visibility_km", 0.1, 200.0),
            "fog_density": _number(atmosphere["fog_density"], "atmosphere.fog_density", 0.0, 1.0),
            "fog_height_m": _number(atmosphere["fog_height_m"], "atmosphere.fog_height_m", 0.0, 5000.0),
        },
        "wind": {
            "speed_mps": _number(wind["speed_mps"], "wind.speed_mps", 0.0, 60.0),
            "direction_deg": _number(wind["direction_deg"], "wind.direction_deg", 0.0, 360.0),
        },
        "ocean": {
            "enabled": enabled,
            "sea_level_m": _number(ocean["sea_level_m"], "ocean.sea_level_m", -1000.0, 1000.0),
            "significant_wave_height_m": _number(ocean["significant_wave_height_m"], "ocean.significant_wave_height_m", 0.0, 20.0),
            "swell_period_s": _number(ocean["swell_period_s"], "ocean.swell_period_s", 1.0, 30.0),
            "swell_direction_deg": _number(ocean["swell_direction_deg"], "ocean.swell_direction_deg", 0.0, 360.0),
            "choppiness": _number(ocean["choppiness"], "ocean.choppiness", 0.0, 4.0),
            "foam_amount": _number(ocean["foam_amount"], "ocean.foam_amount", 0.0, 1.0),
            "deep_color": _rgb(ocean["deep_color"], "ocean.deep_color"),
            "shallow_color": _rgb(ocean["shallow_color"], "ocean.shallow_color"),
            "roughness": _number(ocean["roughness"], "ocean.roughness", 0.0, 1.0),
            "absorption": _number(ocean["absorption"], "ocean.absorption", 0.0, 10.0),
            "spectrum": {
                "profile": profile.strip().lower(),
                "swell_spread_deg": _number(spectrum["swell_spread_deg"], "ocean.spectrum.swell_spread_deg", 0.0, 90.0),
                "wind_wave_height_m": _number(spectrum["wind_wave_height_m"], "ocean.spectrum.wind_wave_height_m", 0.0, 10.0),
                "wind_wave_period_s": _number(spectrum["wind_wave_period_s"], "ocean.spectrum.wind_wave_period_s", 0.5, 20.0),
                "wind_wave_direction_deg": _number(spectrum["wind_wave_direction_deg"], "ocean.spectrum.wind_wave_direction_deg", 0.0, 360.0),
                "short_wave_strength": _number(spectrum["short_wave_strength"], "ocean.spectrum.short_wave_strength", 0.0, 1.0),
                "short_wave_scale_m": _number(spectrum["short_wave_scale_m"], "ocean.spectrum.short_wave_scale_m", 0.05, 20.0),
                "crest_foam_threshold": _number(spectrum["crest_foam_threshold"], "ocean.spectrum.crest_foam_threshold", 0.0, 1.0),
                "shore_foam_amount": _number(spectrum["shore_foam_amount"], "ocean.spectrum.shore_foam_amount", 0.0, 1.0),
            },
        },
        "exposure": {
            "ev100": _number(exposure["ev100"], "exposure.ev100", -16.0, 24.0),
            "tone_mapping": tone_mapping.lower(),
        },
    }
