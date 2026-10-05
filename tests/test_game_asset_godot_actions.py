import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.game_asset_engine_export_actions import ENGINE_EXPORT_SCHEMA
from ordax_dev_agent.models import ActionResult


class GameAssetGodotActionsTests(unittest.TestCase):
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

    def make_export(self, root: Path, *, engine: str = "godot") -> tuple[Path, Path, Path]:
        project = root / "project"
        source = project / "staging" / "asset.blend"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"blend-source")
        artifact = project / "exports" / "asset.glb"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"fake-glb-for-cli-mock")
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
                        "format": "glb",
                        "bytes": artifact.stat().st_size,
                        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    },
                    "profile": {"format": "glb", "purpose": "Godot glTF 2.0 import"},
                }
            ),
            encoding="utf-8",
        )
        godot = project / "godot_game"
        godot.mkdir(parents=True, exist_ok=True)
        (godot / "project.godot").write_text("[application]\nconfig/name=\"test\"\n", encoding="utf-8")
        return artifact, manifest, godot

    def semantic_stdout(self, *, mesh_instances=2, skeletons=1, bones=64, animations=3, collision_shapes=1):
        semantic = {
            "packed_scene": True,
            "root_class": "Node3D",
            "nodes": 8,
            "mesh_instances": mesh_instances,
            "mesh_surfaces": 4,
            "materials": 3,
            "blend_shapes": 2,
            "skeletons": skeletons,
            "bones": bones,
            "animation_players": 1,
            "animations": animations,
            "collision_objects": 1 if collision_shapes else 0,
            "collision_shapes": collision_shapes,
        }
        return (
            "ORDAX_GODOT_RESOURCE_OK|res://ordax_generated/asset.glb|PackedScene\n"
            + "ORDAX_GODOT_SEMANTIC_OK|"
            + json.dumps(semantic, separators=(",", ":"))
            + "\n"
        )

    def test_registry_exposes_godot_import_validation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            status = registry.execute("agent.status", {})
            self.assertTrue(status.ok)
            self.assertIn("game_assets.godot_import_validate", status.data["actions"])

    def test_success_runs_headless_import_then_resource_loader_proof(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifact, manifest, godot = self.make_export(root)
            expected_destination = godot / "ordax_generated" / "asset.glb"
            calls = []

            def fake_run(command, *, cwd=None, timeout=0, env=None):
                calls.append((command, cwd, timeout))
                self.assertTrue(Path(cwd).samefile(godot))
                self.assertEqual(444, timeout)
                self.assertEqual("godot", command[0])
                self.assertIn("--headless", command)
                self.assertIn("--recovery-mode", command)
                path_index = command.index("--path")
                self.assertTrue(Path(command[path_index + 1]).samefile(godot))
                if "--import" in command:
                    return ActionResult(True, "command completed", {"stdout": "imported", "stderr": ""})
                self.assertIn("--script", command)
                separator = command.index("--")
                resource_path = command[separator + 1]
                self.assertEqual("res://ordax_generated/asset.glb", resource_path)
                script_path = Path(command[command.index("--script") + 1])
                script = script_path.read_text(encoding="utf-8")
                self.assertIn("MeshInstance3D", script)
                self.assertIn("Skeleton3D", script)
                self.assertIn("AnimationPlayer", script)
                return ActionResult(
                    True,
                    "command completed",
                    {"stdout": self.semantic_stdout(), "stderr": ""},
                )

            registry = ActionRegistry(self.make_config(root))
            with patch(
                "ordax_dev_agent.game_asset_godot_actions._find_godot",
                return_value="godot",
            ), patch(
                "ordax_dev_agent.game_asset_godot_actions._run",
                side_effect=fake_run,
            ):
                result = registry.execute(
                    "game_assets.godot_import_validate",
                    {
                        "project": "game",
                        "artifact_path": "exports/asset.glb",
                        "godot_project_dir": "godot_game",
                        "timeout_seconds": 444,
                        "min_mesh_instances": 2,
                        "min_skeletons": 1,
                        "min_bones": 64,
                        "min_animations": 3,
                        "require_collision": True,
                    },
                )

            self.assertTrue(result.ok, result.summary)
            self.assertEqual(2, len(calls))
            self.assertTrue(expected_destination.is_file())
            self.assertEqual(artifact.read_bytes(), expected_destination.read_bytes())
            destination_manifest = Path(str(expected_destination) + ".ordax.json")
            self.assertTrue(destination_manifest.is_file())
            self.assertEqual(manifest.read_text(encoding="utf-8"), destination_manifest.read_text(encoding="utf-8"))
            self.assertTrue(artifact.is_file())
            self.assertTrue(result.data["engine_validated"])
            self.assertTrue(result.data["semantic_requirements_passed"])
            self.assertEqual("PackedScene", result.data["resource_class"])
            self.assertEqual(64, result.data["semantic"]["bones"])
            self.assertEqual(3, result.data["semantic"]["animations"])
            self.assertEqual("res://ordax_generated/asset.glb", result.data["resource_path"])
            self.assertTrue(result.data["source_preserved"])
            self.assertTrue(result.data["recovery_mode"])

    def test_loaded_scene_can_fail_semantic_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.make_export(root)
            registry = ActionRegistry(self.make_config(root))
            calls = 0

            def fake_run(command, *, cwd=None, timeout=0, env=None):
                nonlocal calls
                calls += 1
                if "--import" in command:
                    return ActionResult(True, "command completed", {"stdout": "imported", "stderr": ""})
                return ActionResult(
                    True,
                    "command completed",
                    {"stdout": self.semantic_stdout(mesh_instances=1, skeletons=0, bones=0, animations=0, collision_shapes=0), "stderr": ""},
                )

            with patch(
                "ordax_dev_agent.game_asset_godot_actions._find_godot",
                return_value="godot",
            ), patch(
                "ordax_dev_agent.game_asset_godot_actions._run",
                side_effect=fake_run,
            ):
                result = registry.execute(
                    "game_assets.godot_import_validate",
                    {
                        "project": "game",
                        "artifact_path": "exports/asset.glb",
                        "godot_project_dir": "godot_game",
                        "min_mesh_instances": 2,
                        "min_skeletons": 1,
                        "min_animations": 1,
                        "require_collision": True,
                    },
                )
            self.assertEqual(2, calls)
            self.assertFalse(result.ok)
            self.assertTrue(result.data["engine_loaded"])
            self.assertFalse(result.data["semantic_requirements_passed"])
            self.assertEqual(4, len(result.data["failures"]))

    def test_destination_must_stay_inside_godot_project(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.make_export(root)
            registry = ActionRegistry(self.make_config(root))
            with patch(
                "ordax_dev_agent.game_asset_godot_actions._find_godot",
                return_value="godot",
            ):
                result = registry.execute(
                    "game_assets.godot_import_validate",
                    {
                        "project": "game",
                        "artifact_path": "exports/asset.glb",
                        "godot_project_dir": "godot_game",
                        "destination_path": "exports/copied.glb",
                    },
                )
            self.assertFalse(result.ok)
            self.assertIn("inside the Godot project", result.summary)

    def test_wrong_engine_provenance_is_rejected_before_godot_execution(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.make_export(root, engine="web")
            registry = ActionRegistry(self.make_config(root))
            with patch("ordax_dev_agent.game_asset_godot_actions._run") as run:
                result = registry.execute(
                    "game_assets.godot_import_validate",
                    {
                        "project": "game",
                        "artifact_path": "exports/asset.glb",
                        "godot_project_dir": "godot_game",
                    },
                )
            self.assertFalse(result.ok)
            self.assertIn("not targeted to Godot", result.summary)
            run.assert_not_called()

    def test_import_failure_keeps_copy_for_diagnostics_and_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifact, _, godot = self.make_export(root)
            registry = ActionRegistry(self.make_config(root))
            with patch(
                "ordax_dev_agent.game_asset_godot_actions._find_godot",
                return_value="godot",
            ), patch(
                "ordax_dev_agent.game_asset_godot_actions._run",
                return_value=ActionResult(
                    False,
                    "command failed",
                    {"stdout": "", "stderr": "import error", "returncode": 1},
                ),
            ) as run:
                result = registry.execute(
                    "game_assets.godot_import_validate",
                    {
                        "project": "game",
                        "artifact_path": "exports/asset.glb",
                        "godot_project_dir": "godot_game",
                    },
                )
            self.assertFalse(result.ok)
            self.assertEqual(1, run.call_count)
            copied = godot / "ordax_generated" / "asset.glb"
            self.assertTrue(copied.is_file())
            self.assertTrue(artifact.is_file())
            self.assertTrue(result.data["source_preserved"])
            self.assertTrue(result.data["copy_retained_for_diagnostics"])

    def test_existing_destination_requires_explicit_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.make_export(root)
            destination = root / "project" / "godot_game" / "ordax_generated" / "asset.glb"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"existing")
            registry = ActionRegistry(self.make_config(root))
            with patch("ordax_dev_agent.game_asset_godot_actions._run") as run:
                result = registry.execute(
                    "game_assets.godot_import_validate",
                    {
                        "project": "game",
                        "artifact_path": "exports/asset.glb",
                        "godot_project_dir": "godot_game",
                    },
                )
            self.assertFalse(result.ok)
            self.assertIn("overwrite=true", result.summary)
            self.assertEqual(b"existing", destination.read_bytes())
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
