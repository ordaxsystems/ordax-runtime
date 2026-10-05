import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.game_asset_unity_actions import _default_prefab_path, _lod_thresholds
from ordax_dev_agent.models import ActionResult


class GameAssetUnityLodActionsTests(unittest.TestCase):
    def make_config(self, root: Path) -> AgentConfig:
        project = root / "project"
        (project / "Assets" / "Models").mkdir(parents=True, exist_ok=True)
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

    def static_audit(self, **updates) -> ActionResult:
        data = {
            "assetPath": "Assets/Models/prop.fbx",
            "modelMeshCount": 4,
            "modelVertexCount": 25000,
            "modelTriangleCount": 16000,
            "modelMaterialCount": 2,
            "modelAnimationClipCount": 0,
            "modelBoneCount": 0,
            "modelBlendShapeCount": 0,
            "modelLodGroupCount": 0,
        }
        data.update(updates)
        return ActionResult(True, "Unity model import audit passed", data)

    def test_registry_exposes_static_lod_prefab_action(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            status = registry.execute("agent.status", {})
            self.assertTrue(status.ok)
            self.assertIn("game_assets.unity_build_static_lod_prefab", status.data["actions"])

    def test_threshold_validation_and_default_prefab_path(self) -> None:
        self.assertIsNone(_lod_thresholds(None))
        self.assertEqual([0.6, 0.3, 0.12], _lod_thresholds([0.6, 0.3, 0.12]))
        self.assertEqual(
            "Assets/Models/prop_LOD.prefab",
            _default_prefab_path("Assets/Models/prop.fbx"),
        )
        for invalid in ([0.3, 0.6], [0.6], [1.0, 0.5], [0.6, 0.6]):
            with self.assertRaises(ValueError):
                _lod_thresholds(invalid)

    def test_static_lod_prefab_audits_before_building(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            (root / "project" / "Assets" / "Models" / "prop.fbx").write_bytes(b"fbx")
            registry = ActionRegistry(config)

            class FakeEditor:
                def __init__(self, audit):
                    self.audit = audit
                    self.calls = []

                def request(self, action, payload, *, timeout_seconds):
                    self.calls.append((action, payload, timeout_seconds))
                    if action == "asset_model_audit":
                        return self.audit
                    if action == "asset_lod_prefab_build":
                        return ActionResult(
                            True,
                            "Static Unity LODGroup prefab created",
                            {
                                "assetPath": payload["assetPath"],
                                "prefabPath": payload["prefabPath"],
                                "lodLevelCount": 4,
                                "lodRendererCount": 4,
                                "lodTransitionHeights": payload["lodThresholds"],
                            },
                        )
                    raise AssertionError(action)

            editor = FakeEditor(self.static_audit())
            with patch.object(registry, "_editor", return_value=editor):
                result = registry.execute(
                    "game_assets.unity_build_static_lod_prefab",
                    {
                        "project": "game",
                        "asset_path": "Assets/Models/prop.fbx",
                        "lod_thresholds": [0.6, 0.3, 0.15, 0.05],
                        "timeout_seconds": 120,
                    },
                )

            self.assertTrue(result.ok, result.summary)
            self.assertEqual("Assets/Models/prop_LOD.prefab", result.data["prefab_path"])
            self.assertEqual(
                ["asset_model_audit", "asset_lod_prefab_build"],
                [call[0] for call in editor.calls],
            )
            build = editor.calls[1]
            self.assertEqual("Assets/Models/prop_LOD.prefab", build[1]["prefabPath"])
            self.assertEqual([0.6, 0.3, 0.15, 0.05], build[1]["lodThresholds"])
            self.assertFalse(build[1]["overwrite"])
            self.assertEqual(120.0, build[2])

    def test_static_lod_prefab_refuses_deformation_before_build_request(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            (root / "project" / "Assets" / "Models" / "character.fbx").write_bytes(b"fbx")
            registry = ActionRegistry(config)

            class FakeEditor:
                def __init__(self):
                    self.calls = []

                def request(self, action, payload, *, timeout_seconds):
                    self.calls.append(action)
                    return self_result

            self_result = self.static_audit(
                assetPath="Assets/Models/character.fbx",
                modelBoneCount=42,
            )
            editor = FakeEditor()
            with patch.object(registry, "_editor", return_value=editor):
                result = registry.execute(
                    "game_assets.unity_build_static_lod_prefab",
                    {"project": "game", "asset_path": "Assets/Models/character.fbx"},
                )
            self.assertFalse(result.ok)
            self.assertIn("bones", result.summary)
            self.assertEqual(["asset_model_audit"], editor.calls)

    def test_static_lod_prefab_requires_new_companion_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            (root / "project" / "Assets" / "Models" / "prop.fbx").write_bytes(b"fbx")
            registry = ActionRegistry(config)
            old_data = self.static_audit().data.copy()
            old_data.pop("modelBlendShapeCount")

            class FakeEditor:
                def request(self, action, payload, *, timeout_seconds):
                    return ActionResult(True, "old audit", old_data)

            with patch.object(registry, "_editor", return_value=FakeEditor()):
                result = registry.execute(
                    "game_assets.unity_build_static_lod_prefab",
                    {"project": "game", "asset_path": "Assets/Models/prop.fbx"},
                )
            self.assertFalse(result.ok)
            self.assertIn("too old", result.summary)
            self.assertTrue(result.data["retryable"])


if __name__ == "__main__":
    unittest.main()
