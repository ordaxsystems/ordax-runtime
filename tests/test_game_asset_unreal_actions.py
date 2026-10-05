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


class GameAssetUnrealActionsTests(unittest.TestCase):
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

    def make_export(self, root: Path, *, engine: str = "unreal") -> tuple[Path, Path, Path]:
        project = root / "project"
        source = project / "staging" / "asset.blend"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"blend-source")
        artifact = project / "exports" / "asset.fbx"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"fake-fbx-for-cli-mock")
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
                        "format": "fbx",
                        "bytes": artifact.stat().st_size,
                        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    },
                    "profile": {"format": "fbx", "purpose": "Unreal FBX import"},
                }
            ),
            encoding="utf-8",
        )
        unreal = project / "unreal_game"
        unreal.mkdir(parents=True, exist_ok=True)
        uproject = unreal / "Game.uproject"
        uproject.write_text('{"FileVersion":3,"EngineAssociation":"5.8"}', encoding="utf-8")
        return artifact, manifest, uproject

    def test_registry_exposes_unreal_import_validation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            status = registry.execute("agent.status", {})
            self.assertTrue(status.ok)
            self.assertIn("game_assets.unreal_import_validate", status.data["actions"])

    def test_success_runs_python_commandlet_and_requires_import_proof(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifact, _, uproject = self.make_export(root)

            def fake_run(command, *, cwd=None, timeout=0, env=None):
                self.assertEqual("UnrealEditor-Cmd", command[0])
                self.assertTrue(Path(command[1]).samefile(uproject))
                self.assertIn("-unattended", command)
                self.assertIn("-nop4", command)
                self.assertIn("-nosplash", command)
                self.assertIn("-nullrhi", command)
                self.assertIn("-run=pythonscript", command)
                script_arg = next(item for item in command if item.startswith("-script="))
                script_path = Path(script_arg.split("=", 1)[1])
                self.assertTrue(script_path.is_file())
                text = script_path.read_text(encoding="utf-8")
                self.assertIn("asset.fbx", text)
                self.assertIn("/Game/OrdaX/Generated", text)
                self.assertIn('set_editor_property("save", True)', text)
                self.assertTrue(Path(cwd).samefile(uproject.parent))
                self.assertEqual(777, timeout)
                return ActionResult(
                    True,
                    "command completed",
                    {
                        "stdout": 'ORDAX_UNREAL_IMPORT_OK|{"paths":["/Game/OrdaX/Generated/asset.asset"],"classes":["StaticMesh"]}\n',
                        "stderr": "",
                    },
                )

            registry = ActionRegistry(self.make_config(root))
            with patch(
                "ordax_dev_agent.game_asset_unreal_actions._find_unreal_editor",
                return_value="UnrealEditor-Cmd",
            ), patch(
                "ordax_dev_agent.game_asset_unreal_actions._run",
                side_effect=fake_run,
            ):
                result = registry.execute(
                    "game_assets.unreal_import_validate",
                    {
                        "project": "game",
                        "artifact_path": "exports/asset.fbx",
                        "uproject_path": "unreal_game/Game.uproject",
                        "timeout_seconds": 777,
                    },
                )

            self.assertTrue(result.ok, result.summary)
            self.assertTrue(result.data["engine_validated"])
            self.assertEqual(["/Game/OrdaX/Generated/asset.asset"], result.data["object_paths"])
            self.assertEqual(["StaticMesh"], result.data["asset_classes"])
            self.assertTrue(result.data["source_preserved"])
            self.assertTrue(result.data["unattended"])
            self.assertTrue(result.data["null_rhi"])

    def test_wrong_engine_provenance_is_rejected_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.make_export(root, engine="unity")
            registry = ActionRegistry(self.make_config(root))
            with patch("ordax_dev_agent.game_asset_unreal_actions._run") as run:
                result = registry.execute(
                    "game_assets.unreal_import_validate",
                    {
                        "project": "game",
                        "artifact_path": "exports/asset.fbx",
                        "uproject_path": "unreal_game/Game.uproject",
                    },
                )
            self.assertFalse(result.ok)
            self.assertIn("not targeted to Unreal", result.summary)
            run.assert_not_called()

    def test_uproject_must_be_project_local_and_exist(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.make_export(root)
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "game_assets.unreal_import_validate",
                {
                    "project": "game",
                    "artifact_path": "exports/asset.fbx",
                    "uproject_path": "unreal_game/missing.uproject",
                },
            )
            self.assertFalse(result.ok)
            self.assertTrue("does not exist" in result.summary or ".uproject" in result.summary)

    def test_destination_package_path_is_strictly_validated(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.make_export(root)
            registry = ActionRegistry(self.make_config(root))
            with patch("ordax_dev_agent.game_asset_unreal_actions._run") as run:
                result = registry.execute(
                    "game_assets.unreal_import_validate",
                    {
                        "project": "game",
                        "artifact_path": "exports/asset.fbx",
                        "uproject_path": "unreal_game/Game.uproject",
                        "destination_path": "/Engine/Injected",
                    },
                )
            self.assertFalse(result.ok)
            self.assertIn("/Game/", result.summary)
            run.assert_not_called()

    def test_destination_rejects_dot_segments(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.make_export(root)
            registry = ActionRegistry(self.make_config(root))
            with patch("ordax_dev_agent.game_asset_unreal_actions._run") as run:
                result = registry.execute(
                    "game_assets.unreal_import_validate",
                    {
                        "project": "game",
                        "artifact_path": "exports/asset.fbx",
                        "uproject_path": "unreal_game/Game.uproject",
                        "destination_path": "/Game/../Injected",
                    },
                )
            self.assertFalse(result.ok)
            self.assertIn("dot path segments", result.summary)
            run.assert_not_called()

    def test_proof_must_stay_under_requested_destination(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.make_export(root)
            registry = ActionRegistry(self.make_config(root))
            with patch(
                "ordax_dev_agent.game_asset_unreal_actions._find_unreal_editor",
                return_value="UnrealEditor-Cmd",
            ), patch(
                "ordax_dev_agent.game_asset_unreal_actions._run",
                return_value=ActionResult(
                    True,
                    "command completed",
                    {
                        "stdout": 'ORDAX_UNREAL_IMPORT_OK|{"paths":["/Game/Other/asset.asset"],"classes":["StaticMesh"]}\n',
                        "stderr": "",
                    },
                ),
            ):
                result = registry.execute(
                    "game_assets.unreal_import_validate",
                    {
                        "project": "game",
                        "artifact_path": "exports/asset.fbx",
                        "uproject_path": "unreal_game/Game.uproject",
                    },
                )
            self.assertFalse(result.ok)
            self.assertIn("without OrdaX import/load proof", result.summary)

    def test_success_without_ordax_proof_is_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.make_export(root)
            registry = ActionRegistry(self.make_config(root))
            with patch(
                "ordax_dev_agent.game_asset_unreal_actions._find_unreal_editor",
                return_value="UnrealEditor-Cmd",
            ), patch(
                "ordax_dev_agent.game_asset_unreal_actions._run",
                return_value=ActionResult(True, "command completed", {"stdout": "normal log", "stderr": ""}),
            ):
                result = registry.execute(
                    "game_assets.unreal_import_validate",
                    {
                        "project": "game",
                        "artifact_path": "exports/asset.fbx",
                        "uproject_path": "unreal_game/Game.uproject",
                    },
                )
            self.assertFalse(result.ok)
            self.assertIn("without OrdaX import/load proof", result.summary)


if __name__ == "__main__":
    unittest.main()
