import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.models import ActionResult


class GameAssetUnrealSemanticActionsTests(unittest.TestCase):
    def make_config(self, root: Path) -> AgentConfig:
        project = root / "project"
        project.mkdir(parents=True, exist_ok=True)
        unreal = project / "unreal_game"
        unreal.mkdir(parents=True, exist_ok=True)
        (unreal / "Game.uproject").write_text(
            '{"FileVersion":3,"EngineAssociation":"5.8"}', encoding="utf-8"
        )
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

    def test_registry_exposes_unreal_asset_audit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            status = registry.execute("agent.status", {})
            self.assertTrue(status.ok)
            self.assertIn("game_assets.unreal_asset_audit", status.data["actions"])

    def test_static_mesh_semantics_and_requirements_pass(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)

            def fake_run(command, *, cwd=None, timeout=0, env=None):
                self.assertEqual("UnrealEditor-Cmd", command[0])
                self.assertIn("-run=pythonscript", command)
                script_arg = next(item for item in command if item.startswith("-script="))
                script = Path(script_arg.split("=", 1)[1])
                text = script.read_text(encoding="utf-8")
                self.assertIn("get_num_lods", text)
                self.assertIn("get_num_triangles", text)
                self.assertIn("body_setup", text)
                return ActionResult(
                    True,
                    "command completed",
                    {
                        "stdout": (
                            'ORDAX_UNREAL_SEMANTIC_OK|[{"requested_path":"/Game/OrdaX/Generated/asset.asset",'
                            '"object_path":"/Game/OrdaX/Generated/asset.asset","class":"StaticMesh",'
                            '"kind":"static_mesh","lod_count":3,"lods":[{"index":0,"triangles":1000,'
                            '"vertices":700,"sections":2,"texcoords":2},{"index":1,"triangles":500,'
                            '"vertices":380,"sections":2,"texcoords":2},{"index":2,"triangles":120,'
                            '"vertices":100,"sections":1,"texcoords":2}],"material_count":2,'
                            '"collision":{"body_setup":true,"simple_shapes":2,"trace_flag":"CTF_UseDefault"}}]\n'
                        ),
                        "stderr": "",
                    },
                )

            registry = ActionRegistry(config)
            with patch(
                "ordax_dev_agent.game_asset_unreal_semantic_actions._find_unreal_editor",
                return_value="UnrealEditor-Cmd",
            ), patch(
                "ordax_dev_agent.game_asset_unreal_semantic_actions._run",
                side_effect=fake_run,
            ):
                result = registry.execute(
                    "game_assets.unreal_asset_audit",
                    {
                        "project": "game",
                        "uproject_path": "unreal_game/Game.uproject",
                        "asset_paths": ["/Game/OrdaX/Generated/asset.asset"],
                        "expected_classes": ["StaticMesh"],
                        "min_static_mesh_lods": 3,
                        "require_static_collision": True,
                    },
                )

            self.assertTrue(result.ok, result.summary)
            self.assertTrue(result.data["engine_loaded"])
            self.assertTrue(result.data["semantic_requirements_passed"])
            asset = result.data["assets"][0]
            self.assertEqual(3, asset["lod_count"])
            self.assertEqual(1000, asset["lods"][0]["triangles"])
            self.assertEqual(2, asset["collision"]["simple_shapes"])

    def test_loaded_asset_can_fail_semantic_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry = ActionRegistry(self.make_config(root))
            with patch(
                "ordax_dev_agent.game_asset_unreal_semantic_actions._find_unreal_editor",
                return_value="UnrealEditor-Cmd",
            ), patch(
                "ordax_dev_agent.game_asset_unreal_semantic_actions._run",
                return_value=ActionResult(
                    True,
                    "command completed",
                    {
                        "stdout": (
                            'ORDAX_UNREAL_SEMANTIC_OK|[{"requested_path":"/Game/OrdaX/Generated/asset.asset",'
                            '"object_path":"/Game/OrdaX/Generated/asset.asset","class":"StaticMesh",'
                            '"kind":"static_mesh","lod_count":1,"lods":[],"material_count":1,'
                            '"collision":{"body_setup":true,"simple_shapes":0,"trace_flag":"CTF_UseDefault"}}]\n'
                        ),
                        "stderr": "",
                    },
                ),
            ):
                result = registry.execute(
                    "game_assets.unreal_asset_audit",
                    {
                        "project": "game",
                        "uproject_path": "unreal_game/Game.uproject",
                        "asset_paths": "/Game/OrdaX/Generated/asset.asset",
                        "min_static_mesh_lods": 3,
                        "require_static_collision": True,
                    },
                )
            self.assertFalse(result.ok)
            self.assertTrue(result.data["engine_loaded"])
            self.assertFalse(result.data["semantic_requirements_passed"])
            self.assertEqual(2, len(result.data["failures"]))

    def test_skeletal_and_animation_require_skeleton_assignments(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry = ActionRegistry(self.make_config(root))
            stdout = (
                'ORDAX_UNREAL_SEMANTIC_OK|['
                '{"requested_path":"/Game/OrdaX/Hero.Hero","object_path":"/Game/OrdaX/Hero.Hero",'
                '"class":"SkeletalMesh","kind":"skeletal_mesh","lod_count":2,"material_count":2,'
                '"skeleton_path":"/Game/OrdaX/Hero_Skeleton.Hero_Skeleton","bone_count":64,'
                '"morph_target_count":3,"physics_asset_path":"/Game/OrdaX/Hero_PhysicsAsset.Hero_PhysicsAsset"},'
                '{"requested_path":"/Game/OrdaX/Walk.Walk","object_path":"/Game/OrdaX/Walk.Walk",'
                '"class":"AnimSequence","kind":"animation",'
                '"skeleton_path":"/Game/OrdaX/Hero_Skeleton.Hero_Skeleton","play_length_seconds":1.25}]\n'
            )
            with patch(
                "ordax_dev_agent.game_asset_unreal_semantic_actions._find_unreal_editor",
                return_value="UnrealEditor-Cmd",
            ), patch(
                "ordax_dev_agent.game_asset_unreal_semantic_actions._run",
                return_value=ActionResult(True, "command completed", {"stdout": stdout, "stderr": ""}),
            ):
                result = registry.execute(
                    "game_assets.unreal_asset_audit",
                    {
                        "project": "game",
                        "uproject_path": "unreal_game/Game.uproject",
                        "asset_paths": ["/Game/OrdaX/Hero.Hero", "/Game/OrdaX/Walk.Walk"],
                        "expected_classes": ["SkeletalMesh", "AnimSequence"],
                        "min_skeletal_mesh_lods": 2,
                        "require_skeletal_skeleton": True,
                        "require_skeletal_physics_asset": True,
                        "require_animation_skeleton": True,
                    },
                )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(64, result.data["assets"][0]["bone_count"])
            self.assertEqual(1.25, result.data["assets"][1]["play_length_seconds"])

    def test_asset_paths_are_strictly_scoped_to_game_packages(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry = ActionRegistry(self.make_config(root))
            with patch("ordax_dev_agent.game_asset_unreal_semantic_actions._run") as run:
                result = registry.execute(
                    "game_assets.unreal_asset_audit",
                    {
                        "project": "game",
                        "uproject_path": "unreal_game/Game.uproject",
                        "asset_paths": ["/Engine/Injected.Asset"],
                    },
                )
            self.assertFalse(result.ok)
            self.assertIn("/Game", result.summary)
            run.assert_not_called()

    def test_command_success_without_semantic_proof_is_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry = ActionRegistry(self.make_config(root))
            with patch(
                "ordax_dev_agent.game_asset_unreal_semantic_actions._find_unreal_editor",
                return_value="UnrealEditor-Cmd",
            ), patch(
                "ordax_dev_agent.game_asset_unreal_semantic_actions._run",
                return_value=ActionResult(True, "command completed", {"stdout": "normal log", "stderr": ""}),
            ):
                result = registry.execute(
                    "game_assets.unreal_asset_audit",
                    {
                        "project": "game",
                        "uproject_path": "unreal_game/Game.uproject",
                        "asset_paths": ["/Game/OrdaX/Generated/asset.asset"],
                    },
                )
            self.assertFalse(result.ok)
            self.assertIn("without OrdaX semantic audit proof", result.summary)


if __name__ == "__main__":
    unittest.main()
