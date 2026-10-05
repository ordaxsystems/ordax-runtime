import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.game_asset_engine_export_actions import ENGINE_EXPORT_SCHEMA
from ordax_dev_agent.game_asset_threejs_actions import (
    OCEAN_RUNTIME,
    THREEJS_VIEWER_SCHEMA,
    THREE_VERSION,
    VITE_VERSION,
)


JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942


def make_glb(document: dict, binary: bytes = b"\x00\x00\x00\x00") -> bytes:
    json_bytes = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
    binary += b"\x00" * ((4 - len(binary) % 4) % 4)
    chunks = struct.pack("<II", len(json_bytes), JSON_CHUNK) + json_bytes
    if binary:
        chunks += struct.pack("<II", len(binary), BIN_CHUNK) + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks


class GameAssetThreeJsActionTests(unittest.TestCase):
    def make_config(self, root: Path) -> AgentConfig:
        project = root / "project"
        project.mkdir(parents=True, exist_ok=True)
        return AgentConfig(
            agent_name="test-agent",
            poll_seconds=1.0,
            state_dir=root / "state",
            agent_repo_path=root / "agent",
            hordax_path=project,
            bridge_path=root / "bridge",
            projects={
                "salvador": {
                    "path": str(project),
                    "apps": ["blender", "unity"],
                    "allowed_branches": [],
                }
            },
            default_project="salvador",
        )

    def write_export(self, root: Path, *, engine: str = "web") -> Path:
        project = root / "project"
        source = project / "staging" / "salvador.blend"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"salvador-blend-source")
        artifact = project / "exports" / "salvador.glb"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(
            make_glb(
                {
                    "asset": {"version": "2.0", "generator": "ordax-test"},
                    "scenes": [{"nodes": [0]}],
                    "nodes": [{"mesh": 0}],
                    "meshes": [{"primitives": []}],
                    "materials": [{}],
                    "buffers": [{"byteLength": 4}],
                }
            )
        )
        manifest = Path(str(artifact) + ".ordax.json")
        manifest.write_text(
            json.dumps(
                {
                    "schema": ENGINE_EXPORT_SCHEMA,
                    "project": "salvador",
                    "engine": engine,
                    "created_at": "2026-09-25T00:00:00+00:00",
                    "source": {
                        "path": source.relative_to(project).as_posix(),
                        "bytes": source.stat().st_size,
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    },
                    "artifact": {
                        "path": artifact.relative_to(project).as_posix(),
                        "format": "glb",
                        "bytes": artifact.stat().st_size,
                        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    },
                    "profile": {"format": "glb", "purpose": "web realtime"},
                }
            ),
            encoding="utf-8",
        )
        return artifact

    def test_registry_exposes_threejs_actions(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            status = registry.execute("agent.status", {})
            self.assertTrue(status.ok)
            self.assertIn("game_assets.threejs_prepare_viewer", status.data["actions"])
            self.assertIn("game_assets.threejs_runtime_audit", status.data["actions"])

    def test_prepare_and_audit_viewer(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.write_export(root)
            registry = ActionRegistry(self.make_config(root))
            prepared = registry.execute(
                "game_assets.threejs_prepare_viewer",
                {
                    "project": "salvador",
                    "artifact_path": "exports/salvador.glb",
                    "viewer_dir": "web/salvador-viewer",
                    "environment": {"preset": "salvador_golden_hour"},
                },
            )
            self.assertTrue(prepared.ok, prepared.summary)
            self.assertEqual(THREEJS_VIEWER_SCHEMA, prepared.data["schema"])
            self.assertEqual(OCEAN_RUNTIME["model"], prepared.data["ocean_runtime"]["model"])
            viewer = root / "project" / "web" / "salvador-viewer"
            package = json.loads((viewer / "package.json").read_text(encoding="utf-8"))
            self.assertEqual(THREE_VERSION, package["dependencies"]["three"])
            self.assertEqual(VITE_VERSION, package["devDependencies"]["vite"])

            environment = json.loads(
                (viewer / "public" / "ordax" / "environment.json").read_text(encoding="utf-8")
            )
            self.assertEqual("bay", environment["ocean"]["spectrum"]["profile"])
            runtime = json.loads(
                (viewer / "public" / "ordax" / "runtime.json").read_text(encoding="utf-8")
            )
            self.assertEqual("gerstner-tsl-4band", runtime["ocean_runtime"]["model"])

            main_js = (viewer / "src" / "main.js").read_text(encoding="utf-8")
            self.assertIn("WebGPURenderer", main_js)
            self.assertIn("SkyMesh", main_js)
            self.assertIn("WaterMesh", main_js)
            self.assertIn("from 'three/tsl'", main_js)
            self.assertIn("buildGerstnerSpectrum", main_js)
            self.assertIn("material.positionNode", main_js)
            self.assertIn("crest_foam_threshold", main_js)
            self.assertIn("PlaneGeometry(4096, 4096, 256, 256)", main_js)
            self.assertIn("water.rotation.x = -Math.PI / 2", main_js)
            self.assertIn("foam.rotation.x = -Math.PI / 2", main_js)
            self.assertIn("intensity_lux / 50000", main_js)
            self.assertNotIn("PCFSoftShadowMap", main_js)

            audited = registry.execute(
                "game_assets.threejs_runtime_audit",
                {"project": "salvador", "viewer_dir": "web/salvador-viewer"},
            )
            self.assertTrue(audited.ok, audited.summary)
            self.assertEqual(THREEJS_VIEWER_SCHEMA, audited.data["schema"])
            self.assertEqual(THREE_VERSION, audited.data["three_version"])
            self.assertEqual("bay", audited.data["ocean_profile"])
            self.assertEqual("gerstner-tsl-4band", audited.data["ocean_runtime"]["model"])
            self.assertTrue(audited.data["glb"]["self_contained"])

    def test_custom_ocean_spectrum_reaches_viewer_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.write_export(root)
            registry = ActionRegistry(self.make_config(root))
            prepared = registry.execute(
                "game_assets.threejs_prepare_viewer",
                {
                    "project": "salvador",
                    "artifact_path": "exports/salvador.glb",
                    "environment": {
                        "ocean": {
                            "spectrum": {
                                "profile": "coastal",
                                "wind_wave_height_m": 0.48,
                                "short_wave_strength": 0.77,
                            }
                        }
                    },
                },
            )
            self.assertTrue(prepared.ok, prepared.summary)
            spectrum = prepared.data["environment"]["ocean"]["spectrum"]
            self.assertEqual("coastal", spectrum["profile"])
            self.assertEqual(0.48, spectrum["wind_wave_height_m"])
            self.assertEqual(0.77, spectrum["short_wave_strength"])

    def test_non_web_provenance_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.write_export(root, engine="godot")
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.threejs_prepare_viewer",
                {
                    "project": "salvador",
                    "artifact_path": "exports/salvador.glb",
                },
            )
            self.assertFalse(result.ok)
            self.assertIn("not targeted to the web runtime", result.summary)

    def test_runtime_audit_detects_tampered_model(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.write_export(root)
            registry = ActionRegistry(self.make_config(root))
            prepared = registry.execute(
                "game_assets.threejs_prepare_viewer",
                {
                    "project": "salvador",
                    "artifact_path": "exports/salvador.glb",
                },
            )
            self.assertTrue(prepared.ok, prepared.summary)
            model = root / "project" / "ordax" / "threejs-viewer" / "public" / "ordax" / "model.glb"
            model.write_bytes(model.read_bytes() + b"tampered")
            result = registry.execute(
                "game_assets.threejs_runtime_audit",
                {"project": "salvador"},
            )
            self.assertFalse(result.ok)
            self.assertIn("hash does not match", result.summary)


if __name__ == "__main__":
    unittest.main()
