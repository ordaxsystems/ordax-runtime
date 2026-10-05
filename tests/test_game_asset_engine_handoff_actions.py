import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.game_asset_engine_export_actions import ENGINE_EXPORT_SCHEMA


class GameAssetEngineHandoffTests(unittest.TestCase):
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
                "game": {
                    "path": str(project),
                    "apps": ["blender", "unity"],
                    "allowed_branches": [],
                }
            },
            default_project="game",
        )

    def make_export(self, root: Path, *, engine: str, extension: str, profile_format: str):
        project = root / "project"
        source = project / "staging" / "asset.blend"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"blend-source")
        artifact = project / "exports" / f"asset.{extension}"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"engine-artifact")
        manifest = Path(str(artifact) + ".ordax.json")
        manifest.write_text(
            json.dumps(
                {
                    "schema": ENGINE_EXPORT_SCHEMA,
                    "project": "game",
                    "engine": engine,
                    "created_at": "2026-09-25T00:00:00+00:00",
                    "source": {
                        "path": source.relative_to(project).as_posix(),
                        "bytes": source.stat().st_size,
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    },
                    "artifact": {
                        "path": artifact.relative_to(project).as_posix(),
                        "format": extension,
                        "bytes": artifact.stat().st_size,
                        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    },
                    "profile": {"format": profile_format, "purpose": "test"},
                }
            ),
            encoding="utf-8",
        )
        return source, artifact, manifest

    def test_registry_exposes_engine_handoff_audit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            status = registry.execute("agent.status", {})
            self.assertTrue(status.ok)
            self.assertIn("game_assets.engine_handoff_audit", status.data["actions"])

    def test_unreal_fbx_routes_to_real_import_and_semantic_gates(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _, artifact, _ = self.make_export(
                root, engine="unreal", extension="fbx", profile_format="fbx"
            )
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.engine_handoff_audit",
                {"project": "game", "artifact_path": "exports/asset.fbx"},
            )
            self.assertTrue(result.ok, result.summary)
            self.assertTrue(result.data["ready_for_engine_import"])
            self.assertFalse(result.data["validated_in_engine"])
            self.assertTrue(result.data["engine_validation_available"])
            self.assertEqual("game_assets.unreal_import_validate", result.data["next_gate"])
            self.assertEqual("game_assets.unreal_asset_audit", result.data["semantic_gate"])
            self.assertTrue(Path(result.data["artifact_path"]).samefile(artifact))

    def test_godot_glb_routes_to_integrated_import_semantic_gate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.make_export(root, engine="godot", extension="glb", profile_format="glb")
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.engine_handoff_audit",
                {"project": "game", "artifact_path": "exports/asset.glb"},
            )
            self.assertTrue(result.ok, result.summary)
            self.assertTrue(result.data["engine_validation_available"])
            self.assertEqual("game_assets.godot_import_validate", result.data["next_gate"])
            self.assertEqual("game_assets.godot_import_validate", result.data["semantic_gate"])

    def test_web_glb_routes_to_structural_audit_without_claiming_browser_validation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.make_export(root, engine="web", extension="glb", profile_format="glb")
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.engine_handoff_audit",
                {"project": "game", "artifact_path": "exports/asset.glb"},
            )
            self.assertTrue(result.ok, result.summary)
            self.assertFalse(result.data["engine_validation_available"])
            self.assertEqual("game_assets.web_glb_audit", result.data["next_gate"])
            self.assertIsNone(result.data["semantic_gate"])

    def test_godot_rejects_fbx_even_when_manifest_hashes_are_valid(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.make_export(root, engine="godot", extension="fbx", profile_format="fbx")
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.engine_handoff_audit",
                {"project": "game", "artifact_path": "exports/asset.fbx"},
            )
            self.assertFalse(result.ok)
            self.assertIn("godot handoff requires", result.summary)

    def test_profile_format_must_match_artifact_extension(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.make_export(root, engine="web", extension="glb", profile_format="gltf")
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.engine_handoff_audit",
                {"project": "game", "artifact_path": "exports/asset.glb"},
            )
            self.assertFalse(result.ok)
            self.assertIn("profile format", result.summary)

    def test_current_source_can_be_required_for_strict_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source, _, _ = self.make_export(
                root, engine="unreal", extension="fbx", profile_format="fbx"
            )
            source.write_bytes(b"changed-after-export")
            registry = ActionRegistry(self.make_config(root))
            permissive = registry.execute(
                "game_assets.engine_handoff_audit",
                {"project": "game", "artifact_path": "exports/asset.fbx"},
            )
            self.assertTrue(permissive.ok, permissive.summary)
            self.assertFalse(permissive.data["source_current_matches"])

            strict = registry.execute(
                "game_assets.engine_handoff_audit",
                {
                    "project": "game",
                    "artifact_path": "exports/asset.fbx",
                    "require_current_source": True,
                },
            )
            self.assertFalse(strict.ok)
            self.assertIn("fresh derivative", strict.summary)


if __name__ == "__main__":
    unittest.main()
