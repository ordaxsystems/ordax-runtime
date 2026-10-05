import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.assets.aleph_capture_preprocess import _building_height, _load_osm, _road_width
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.models import ActionResult


class AlephSceneActionsTests(unittest.TestCase):
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

    def write_capture(self, project: Path) -> Path:
        capture = project / "generated" / "aleph" / "capture-1"
        (capture / "streetview").mkdir(parents=True, exist_ok=True)
        manifest = {
            "format": "aleph-python",
            "version": 3,
            "bounds": [-12.975, -38.506, -12.970, -38.498],
            "state": "complete",
            "options": {"include": ["osm", "satellite", "streetview"]},
            "stages": [
                {"mode": "satellite", "grid": {"rows": 2, "columns": 3}, "results": [{}, {}]},
                {"mode": "osm", "grid": {"rows": 1, "columns": 2}, "results": [{}, {}, {}]},
                {"mode": "streetview", "samples": [{}, {}], "results": [{}, {}, {}, {}]},
            ],
        }
        (capture / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (capture / "map.osm").write_text("<osm version='0.6'/>", encoding="utf-8")
        (capture / "terrain.tif").write_bytes(b"terrain")
        (capture / "satellite.png").write_bytes(b"png")
        (capture / "streetview" / "photos.geojson").write_text("{}", encoding="utf-8")
        return capture

    def test_registry_exposes_capture_inspect_and_blender_stage(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            result = registry.execute("agent.status", {})
            self.assertTrue(result.ok)
            self.assertIn("geo.aleph_capture_inspect", result.data["actions"])
            self.assertIn("geo.aleph_blender_stage", result.data["actions"])

    def test_capture_inspect_reports_local_sources_and_progress(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            capture = self.write_capture(root / "project")
            registry = ActionRegistry(config)
            result = registry.execute(
                "geo.aleph_capture_inspect",
                {"project": "world", "capture_dir": str(capture.relative_to(root / "project"))},
            )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(3, result.data["version"])
            self.assertTrue(result.data["capabilities"]["terrain_mesh"])
            self.assertTrue(result.data["capabilities"]["osm_buildings"])
            stages = {item["mode"]: item for item in result.data["stages"]}
            self.assertEqual(6, stages["satellite"]["expected"])
            self.assertEqual(3, stages["osm"]["expected"])
            self.assertEqual(4, stages["streetview"]["expected"])

    def test_osm_parser_keeps_way_nodes_tags_and_building_relations(self) -> None:
        xml = """<osm version='0.6'>
          <node id='1' lat='-12.9710' lon='-38.5010'/>
          <node id='2' lat='-12.9710' lon='-38.5009'/>
          <node id='3' lat='-12.9709' lon='-38.5009'/>
          <way id='10'>
            <nd ref='1'/><nd ref='2'/><nd ref='3'/><nd ref='1'/>
            <tag k='building' v='yes'/><tag k='building:levels' v='4'/>
          </way>
          <way id='20'>
            <nd ref='1'/><nd ref='2'/><tag k='highway' v='residential'/>
          </way>
          <relation id='30'>
            <member type='way' ref='10' role='outer'/>
            <tag k='type' v='multipolygon'/><tag k='building' v='yes'/>
          </relation>
        </osm>"""
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "map.osm"
            path.write_text(xml, encoding="utf-8")
            nodes, ways, relations = _load_osm(path)
        self.assertEqual(3, len(nodes))
        self.assertEqual([1, 2, 3, 1], ways[10][0])
        self.assertEqual("yes", ways[10][1]["building"])
        self.assertEqual("residential", ways[20][1]["highway"])
        self.assertEqual(1, len(relations))
        self.assertEqual(("way", 10, "outer"), relations[0]["members"][0])

    def test_osm_height_and_road_width_contracts_are_bounded(self) -> None:
        minimum, height = _building_height({"height": "40 ft", "min_height": "3 m"})
        self.assertAlmostEqual(12.192, height, places=3)
        self.assertEqual(3.0, minimum)
        self.assertEqual(4.5, _road_width({"highway": "residential"}))
        self.assertEqual(30.0, _road_width({"highway": "primary", "width": "200 m"}))

    def test_blender_stage_runs_preprocess_then_blender_with_project_scoping(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            project = root / "project"
            capture = self.write_capture(project)
            managed = root / "managed"
            managed.mkdir()
            fake_python = managed / ("python.exe" if os.name == "nt" else "python")
            fake_python.write_bytes(b"python")
            fake_blender = managed / ("blender.exe" if os.name == "nt" else "blender")
            fake_blender.write_bytes(b"blender")
            commands = []

            def fake_run(command, *, cwd=None, timeout=0, env=None):
                commands.append(command)
                if "--staging" in command:
                    staging = Path(command[command.index("--staging") + 1])
                    report = staging / "preprocess.json"
                    report.write_text(
                        json.dumps(
                            {
                                "schema": "ordax.aleph-preprocess/1",
                                "capture": str(capture),
                                "aleph_format_version": 3,
                                "bounds": [-12.975, -38.506, -12.970, -38.498],
                                "origin_epsg3857": [0, 0],
                                "size_m": [800, 550],
                                "assets": {},
                                "warnings": [],
                            }
                        ),
                        encoding="utf-8",
                    )
                    return ActionResult(True, "preprocessed", {})
                output = Path(command[command.index("--output") + 1])
                report = Path(command[command.index("--report") + 1])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"blend")
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(
                    json.dumps({"ok": True, "schema": "ordax.aleph-blender-scene/1", "objects": 1}),
                    encoding="utf-8",
                )
                return ActionResult(True, "built", {})

            registry = ActionRegistry(config)
            component_paths = {
                "root": managed,
                "repo": managed / "repo",
                "venv": managed,
                "python": fake_python,
                "executable": managed / "alephgeo",
                "manifest": managed / "component.json",
            }
            with patch.object(ActionRegistry, "_aleph_ready", return_value=(component_paths["executable"], None)), patch(
                "ordax_dev_agent.aleph_scene_actions._component_paths", return_value=component_paths
            ), patch("ordax_dev_agent.aleph_scene_actions.find_blender", return_value=fake_blender), patch(
                "ordax_dev_agent.aleph_scene_actions._run", side_effect=fake_run
            ):
                result = registry.execute(
                    "geo.aleph_blender_stage",
                    {
                        "project": "world",
                        "capture_dir": str(capture.relative_to(project)),
                        "output_blend": "generated/world.blend",
                        "terrain_samples": 96,
                        "include_roads": False,
                        "use_satellite": False,
                    },
                )
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(2, len(commands))
            self.assertIn("--terrain-samples", commands[0])
            self.assertIn("96", commands[0])
            self.assertIn("--no-roads", commands[0])
            self.assertIn("--no-satellite", commands[1])
            self.assertTrue((project / "generated" / "world.blend").is_file())
            self.assertIsNone(result.data["staging_dir"])

    def test_blender_stage_requires_explicit_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.make_config(root)
            project = root / "project"
            capture = self.write_capture(project)
            output = project / "generated" / "world.blend"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"existing")
            registry = ActionRegistry(config)
            result = registry.execute(
                "geo.aleph_blender_stage",
                {
                    "project": "world",
                    "capture_dir": str(capture.relative_to(project)),
                    "output_blend": "generated/world.blend",
                },
            )
            self.assertFalse(result.ok)
            self.assertIn("overwrite=true", result.summary)


if __name__ == "__main__":
    unittest.main()
