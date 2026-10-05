import tempfile
import unittest
from pathlib import Path

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.visual_environment import ENVIRONMENT_SCHEMA, normalize_environment


class VisualEnvironmentTests(unittest.TestCase):
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

    def test_salvador_default_normalizes(self) -> None:
        result = normalize_environment({})
        self.assertEqual(ENVIRONMENT_SCHEMA, result["schema"])
        self.assertEqual("Salvador Clear Noon", result["name"])
        self.assertTrue(result["ocean"]["enabled"])
        self.assertEqual("bay", result["ocean"]["spectrum"]["profile"])
        self.assertGreater(result["ocean"]["spectrum"]["wind_wave_height_m"], 0.0)
        self.assertGreater(result["sun"]["intensity_lux"], 50000)

    def test_nested_unknown_field_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported ocean field"):
            normalize_environment({"ocean": {"magic_waves": True}})

    def test_spectrum_unknown_field_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported ocean.spectrum field"):
            normalize_environment({"ocean": {"spectrum": {"magic_fft": True}}})

    def test_partial_spectrum_override_preserves_preset_defaults(self) -> None:
        result = normalize_environment(
            {
                "preset": "salvador_golden_hour",
                "ocean": {
                    "spectrum": {
                        "profile": "coastal",
                        "wind_wave_height_m": 0.42,
                    }
                },
            }
        )
        spectrum = result["ocean"]["spectrum"]
        self.assertEqual("coastal", spectrum["profile"])
        self.assertEqual(0.42, spectrum["wind_wave_height_m"])
        self.assertEqual(3.1, spectrum["wind_wave_period_s"])
        self.assertEqual(1.0, spectrum["short_wave_scale_m"])

    def test_invalid_spectrum_profile_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "profile must be one of"):
            normalize_environment({"ocean": {"spectrum": {"profile": "pool"}}})

    def test_out_of_range_wave_height_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "significant_wave_height_m"):
            normalize_environment({"ocean": {"significant_wave_height_m": 50.0}})

    def test_out_of_range_short_wave_scale_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "short_wave_scale_m"):
            normalize_environment({"ocean": {"spectrum": {"short_wave_scale_m": 0.001}}})

    def test_registry_exposes_visual_environment_actions(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            status = registry.execute("agent.status", {})
            self.assertTrue(status.ok)
            self.assertIn("visual.environment_schema", status.data["actions"])
            self.assertIn("visual.environment_preset", status.data["actions"])
            self.assertIn("visual.environment_write", status.data["actions"])

    def test_write_environment_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "visual.environment_write",
                {
                    "project": "salvador",
                    "environment": {
                        "preset": "salvador_golden_hour",
                        "ocean": {
                            "foam_amount": 0.2,
                            "spectrum": {"wind_wave_height_m": 0.3},
                        },
                    },
                    "output_path": "scene/environment.json",
                },
            )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(ENVIRONMENT_SCHEMA, result.data["environment"]["schema"])
            self.assertEqual(0.2, result.data["environment"]["ocean"]["foam_amount"])
            self.assertEqual(
                0.3,
                result.data["environment"]["ocean"]["spectrum"]["wind_wave_height_m"],
            )
            self.assertTrue((root / "project" / "scene" / "environment.json").is_file())


if __name__ == "__main__":
    unittest.main()
