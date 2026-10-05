import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.aleph_actions import ALEPH_PINNED_REF, MAX_CAPTURE_AREA_KM2
from ordax_dev_agent.component_updates import component_catalog, plan_component_update
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.models import ActionResult


class AlephActionsTests(unittest.TestCase):
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
                "world": {
                    "path": str(project),
                    "apps": ["blender", "unity"],
                    "allowed_branches": [],
                }
            },
            default_project="world",
        )

    def test_registry_exposes_managed_aleph_surface(self):
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute("agent.status", {})
            self.assertTrue(result.ok)
            expected = {
                "geo.aleph_status",
                "geo.aleph_ensure",
                "geo.aleph_update",
                "geo.aleph_resolve",
                "geo.aleph_satellite",
                "geo.aleph_streetview",
                "geo.aleph_capture",
                "geo.aleph_capture_resume",
                "geo.aleph_capture_export",
            }
            self.assertTrue(expected.issubset(set(result.data["actions"])))

    def test_status_is_read_only_and_advertises_auto_install(self):
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute("geo.aleph_status", {"project": "world"})
            self.assertTrue(result.ok, result.summary)
            self.assertFalse(result.data["installed"])
            self.assertTrue(result.data["auto_install_on_first_use"])
            self.assertEqual(ALEPH_PINNED_REF, result.data["pinned_ref"])

    def test_component_catalog_tracks_upstream_pin(self):
        catalog = component_catalog()
        aleph = next(item for item in catalog["components"] if item["id"] == "adapter-alephgeo")
        self.assertEqual("Belluxx/Aleph", aleph["upstream"])
        self.assertEqual(ALEPH_PINNED_REF, aleph["upstream_pinned_commit"])
        plan = plan_component_update(["ordax_dev_agent/aleph_actions.py"])
        self.assertEqual(["adapter-alephgeo"], plan["affected_components"])
        self.assertTrue(plan["device_agent_restart_required"])

    def test_satellite_invocation_is_typed_and_project_scoped(self):
        response = ActionResult(
            True,
            "command completed",
            {"stdout": json.dumps({"ok": True, "folder": "capture-1"}), "stderr": "", "returncode": 0},
        )
        with tempfile.TemporaryDirectory() as raw, patch(
            "ordax_dev_agent.aleph_actions.AlephActions._aleph_ready",
            return_value=(Path("alephgeo"), None),
        ), patch("ordax_dev_agent.aleph_actions._run", return_value=response) as run:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute(
                "geo.aleph_satellite",
                {
                    "project": "world",
                    "at": [-12.9714, -38.5014],
                    "size": 500,
                    "zoom": 19,
                    "format": "png",
                    "output_dir": "generated/geo/salvador",
                },
            )
            self.assertTrue(result.ok, result.summary)
            command = run.call_args.args[0]
            self.assertEqual("alephgeo", command[0])
            self.assertIn("satellite", command)
            self.assertIn("--at", command)
            self.assertIn("--satellite-format", command)
            self.assertIn("png", command)
            self.assertIn("--json", command)
            self.assertTrue(Path(result.data["output_root"]).is_dir())

    def test_satellite_exact_tile_selector_is_supported(self):
        response = ActionResult(
            True,
            "command completed",
            {"stdout": json.dumps({"ok": True}), "stderr": "", "returncode": 0},
        )
        with tempfile.TemporaryDirectory() as raw, patch(
            "ordax_dev_agent.aleph_actions.AlephActions._aleph_ready",
            return_value=(Path("alephgeo"), None),
        ), patch("ordax_dev_agent.aleph_actions._run", return_value=response) as run:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute(
                "geo.aleph_satellite",
                {"project": "world", "tile": "19/280337/194891"},
            )
            self.assertTrue(result.ok, result.summary)
            command = run.call_args.args[0]
            self.assertIn("--tile", command)
            self.assertIn("19/280337/194891", command)

    def test_streetview_named_street_is_primary_selector(self):
        response = ActionResult(
            True,
            "command completed",
            {"stdout": json.dumps({"ok": True, "views": 10}), "stderr": "", "returncode": 0},
        )
        with tempfile.TemporaryDirectory() as raw, patch(
            "ordax_dev_agent.aleph_actions.AlephActions._aleph_ready",
            return_value=(Path("alephgeo"), None),
        ), patch("ordax_dev_agent.aleph_actions._run", return_value=response) as run:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute(
                "geo.aleph_streetview",
                {
                    "project": "world",
                    "street": "Avenida Sete de Setembro, Salvador",
                    "best_match": True,
                    "stops": 10,
                    "view": "both",
                },
            )
            self.assertTrue(result.ok, result.summary)
            command = run.call_args.args[0]
            self.assertIn("--street", command)
            self.assertIn("Avenida Sete de Setembro, Salvador", command)
            self.assertIn("--best-match", command)
            self.assertIn("--stops", command)
            self.assertNotIn("--place", command)

    def test_streetview_rejects_multiple_primary_selectors(self):
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute(
                "geo.aleph_streetview",
                {
                    "project": "world",
                    "place": "Pelourinho, Salvador",
                    "street": "Rua Chile, Salvador",
                },
            )
            self.assertFalse(result.ok)
            self.assertIn("exactly one", result.summary)

    def test_output_directory_cannot_escape_registered_project(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry = ActionRegistry(self.make_config(root))
            result = registry.execute(
                "geo.aleph_satellite",
                {
                    "project": "world",
                    "at": [-12.9714, -38.5014],
                    "output_dir": "../outside",
                },
            )
            self.assertFalse(result.ok)
            self.assertIn("outside registered project", result.summary)

    def test_capture_adds_no_plan_and_area_safety_gate(self):
        response = ActionResult(
            True,
            "command completed",
            {"stdout": json.dumps({"ok": True, "folder": "capture-1"}), "stderr": "", "returncode": 0},
        )
        with tempfile.TemporaryDirectory() as raw, patch(
            "ordax_dev_agent.aleph_actions.AlephActions._aleph_ready",
            return_value=(Path("alephgeo"), None),
        ), patch("ordax_dev_agent.aleph_actions._run", return_value=response) as run:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute(
                "geo.aleph_capture",
                {
                    "project": "world",
                    "bbox": [-12.975, -38.506, -12.970, -38.498],
                    "sources": ["osm", "satellite"],
                    "terrain_zoom": 14,
                },
            )
            self.assertTrue(result.ok, result.summary)
            command = run.call_args.args[0]
            self.assertIn("capture", command)
            self.assertIn("create", command)
            self.assertIn("--no-plan", command)
            self.assertLess(result.data["bbox_area_km2"], MAX_CAPTURE_AREA_KM2)

            too_large = registry.execute(
                "geo.aleph_capture",
                {
                    "project": "world",
                    "bbox": [-13.5, -39.0, -12.0, -37.0],
                    "sources": ["osm"],
                },
            )
            self.assertFalse(too_large.ok)
            self.assertIn("allow_large_area=true", too_large.summary)

    def test_resume_requires_project_local_manifest(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            project = root / "project"
            capture = project / "generated" / "aleph" / "captures" / "run"
            capture.mkdir(parents=True)
            registry = ActionRegistry(config)
            missing = registry.execute(
                "geo.aleph_capture_resume",
                {"project": "world", "capture_dir": "generated/aleph/captures/run"},
            )
            self.assertFalse(missing.ok)
            self.assertIn("manifest.json", missing.summary)

    def test_resolve_validates_latitude_without_network(self):
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute(
                "geo.aleph_resolve",
                {"project": "world", "at": [123.0, 10.0]},
            )
            self.assertFalse(result.ok)
            self.assertIn("between -90 and 90", result.summary)


if __name__ == "__main__":
    unittest.main()
