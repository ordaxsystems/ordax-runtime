import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.aleph_actions import ALEPH_PINNED_REF
from ordax_dev_agent.config import AgentConfig


class GameAssetCatalogTests(unittest.TestCase):
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

    def test_provider_catalog_includes_cloud_local_mixamo_and_world_capture_routes(self) -> None:
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ,
            {
                "TRIPO_API_KEY": "tripo-secret",
                "MESHY_API_KEY": "meshy-secret",
                "RODIN_API_KEY": "rodin-secret",
            },
            clear=False,
        ):
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute("game_assets.providers", {"project": "game"})
            self.assertTrue(result.ok, result.summary)
            providers = result.data["providers"]
            self.assertEqual(
                {
                    "adobe_mixamo",
                    "tripo",
                    "meshy",
                    "hyper3d_rodin",
                    "comfyui_local",
                    "alephgeo",
                },
                set(providers),
            )
            self.assertTrue(providers["hyper3d_rodin"]["configured"])
            self.assertEqual("loopback-only", providers["comfyui_local"]["security"])
            self.assertTrue(providers["alephgeo"]["auto_install_on_first_use"])
            self.assertEqual(ALEPH_PINNED_REF, providers["alephgeo"]["pinned_ref"])
            self.assertNotIn("tripo-secret", repr(result.data))
            self.assertNotIn("meshy-secret", repr(result.data))
            self.assertNotIn("rodin-secret", repr(result.data))

    def test_ecosystem_catalog_keeps_heavy_local_models_optional(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute("game_assets.ecosystem_catalog", {"project": "game"})
            self.assertTrue(result.ok, result.summary)
            candidates = result.data["catalog"]["local_model_candidates"]
            self.assertEqual("candidate_not_bundled", candidates["trellis_2"]["integration_state"])
            self.assertEqual("candidate_not_bundled", candidates["hunyuan3d_2_1"]["integration_state"])
            self.assertEqual(["alephgeo"], result.data["catalog"]["integrated"]["world_reference_capture"])
            self.assertIn("terrain_geotiff", result.data["catalog"]["world_generation_pipeline"]["inputs"])
            self.assertIn("DeepMotion".lower(), repr(result.data).lower())


if __name__ == "__main__":
    unittest.main()
