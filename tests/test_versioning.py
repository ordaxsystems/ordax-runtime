import tempfile
import tomllib
import unittest
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from unittest.mock import patch

from mcp_blender_unity import __version__ as bridge_package_version
from ordax_dev_agent import __version__ as dev_agent_version
from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.blender_live_bridge import (
    BUNDLE_FORMAT_VERSION,
    EXPECTED_PROTOCOL_VERSION,
)
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.references import MANIFEST_VERSION
from ordax_dev_agent.versioning import component_versions, runtime_package_version
from ordax_dev_agent.capability_contracts import capability_contracts


class ComponentVersioningTests(unittest.TestCase):
    def make_config(self, root: Path) -> AgentConfig:
        return AgentConfig(
            agent_name="version-test",
            poll_seconds=5.0,
            state_dir=root / "state",
            agent_repo_path=root / "agent",
            hordax_path=root / "hordax",
            bridge_path=root / "bridge",
        )

    def test_distribution_metadata_matches_runtime_package_version(self) -> None:
        root = Path(__file__).resolve().parents[1]
        metadata = tomllib.loads(
            (root / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            component_versions()["runtime_package"],
            metadata["project"]["version"],
        )
        self.assertEqual(metadata["project"]["name"], "ordax-runtime")

    def test_component_versions_are_independent_and_explicit(self) -> None:
        versions = component_versions()
        self.assertEqual(bridge_package_version, versions["bridge_package"])
        self.assertEqual(dev_agent_version, versions["dev_agent"])
        self.assertEqual(
            EXPECTED_PROTOCOL_VERSION,
            versions["blender_live_protocol"],
        )
        self.assertEqual(
            BUNDLE_FORMAT_VERSION,
            versions["blender_companion_bundle_format"],
        )
        self.assertEqual(
            MANIFEST_VERSION,
            versions["reference_contract"],
        )
        self.assertNotEqual(
            versions["bridge_package"],
            versions["dev_agent"],
        )

    def test_installed_runtime_uses_its_own_distribution_metadata(self) -> None:
        with patch("ordax_dev_agent.versioning.Path.is_file", return_value=False), patch(
            "ordax_dev_agent.versioning.version", return_value="1.2.3",
        ) as installed:
            self.assertEqual(runtime_package_version(), "1.2.3")
        installed.assert_called_once_with("ordax-runtime")

    def test_missing_distribution_does_not_invent_a_runtime_version(self) -> None:
        with patch("ordax_dev_agent.versioning.Path.is_file", return_value=False), patch(
            "ordax_dev_agent.versioning.version", side_effect=PackageNotFoundError("ordax-runtime"),
        ):
            self.assertIsNone(runtime_package_version())

    def test_agent_status_exposes_component_versions(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "hordax").mkdir()
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute("agent.status", {})

        self.assertTrue(result.ok)
        self.assertEqual(component_versions(), result.data["versions"])

    def test_capability_contracts_expose_promoted_blender_modeling_actions(self) -> None:
        contracts = capability_contracts()
        modeling = contracts["blender_modeling"]

        self.assertEqual([], modeling["pending_operations"])
        self.assertEqual(
            [
                "blender.live_add_modifier",
                "blender.live_boolean_cut_cancel",
                "blender.live_boolean_cut_commit",
                "blender.live_boolean_cut_preview",
                "blender.live_boundary_hole_fill_cancel",
                "blender.live_boundary_hole_fill_commit",
                "blender.live_boundary_hole_fill_preview",
                "blender.live_create_primitive",
                "blender.live_degenerate_repair_cancel",
                "blender.live_degenerate_repair_commit",
                "blender.live_degenerate_repair_preview",
                "blender.live_merge_by_distance_cancel",
                "blender.live_merge_by_distance_commit",
                "blender.live_merge_by_distance_preview",
                "blender.live_mesh_cleanup",
                "blender.live_object_transform",
                "blender.live_surface_scatter",
            ],
            modeling["available_actions"],
        )
        self.assertEqual(
            "available",
            modeling["operations"]["create_primitive"]["status"],
        )
        self.assertEqual(
            "blender.live_create_primitive",
            modeling["operations"]["create_primitive"]["action"],
        )
        self.assertEqual(
            "available",
            modeling["operations"]["add_modifier"]["status"],
        )
        self.assertEqual(
            {"commit": "blender.live_boolean_cut_commit", "cancel": "blender.live_boolean_cut_cancel"},
            modeling["operations"]["boolean_cut_preview"]["workflow_actions"],
        )
        self.assertEqual("available", modeling["operations"]["mesh_cleanup"]["status"])
        self.assertEqual("blender.live_mesh_cleanup", modeling["operations"]["mesh_cleanup"]["action"])
        self.assertEqual(
            "available",
            modeling["operations"]["degenerate_repair_preview"]["status"],
        )
        self.assertEqual(
            {"commit": "blender.live_degenerate_repair_commit", "cancel": "blender.live_degenerate_repair_cancel"},
            modeling["operations"]["degenerate_repair_preview"]["workflow_actions"],
        )
        self.assertEqual(
            "available",
            modeling["operations"]["merge_by_distance_preview"]["status"],
        )
        self.assertEqual(
            {"commit": "blender.live_merge_by_distance_commit", "cancel": "blender.live_merge_by_distance_cancel"},
            modeling["operations"]["merge_by_distance_preview"]["workflow_actions"],
        )
        self.assertEqual(
            "available",
            modeling["operations"]["boundary_hole_fill_preview"]["status"],
        )
        self.assertEqual(
            {"commit": "blender.live_boundary_hole_fill_commit", "cancel": "blender.live_boundary_hole_fill_cancel"},
            modeling["operations"]["boundary_hole_fill_preview"]["workflow_actions"],
        )
        self.assertEqual(
            8,
            modeling["operations"]["add_modifier"]["runtime_guards"][
                "max_modifier_stack"
            ],
        )

    def test_agent_status_exposes_capability_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "hordax").mkdir()
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute("agent.status", {})

        self.assertTrue(result.ok)
        self.assertEqual(
            capability_contracts(),
            result.data["capability_contracts"],
        )


if __name__ == "__main__":
    unittest.main()
