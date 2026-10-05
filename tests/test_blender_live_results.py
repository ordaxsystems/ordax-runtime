import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ordax_dev_agent.blender_live_bridge import BlenderLiveBridge
from ordax_dev_agent.projects import Project


class BlenderLiveResultTests(unittest.TestCase):
    def make_bridge(self, root: Path) -> BlenderLiveBridge:
        project_root = root / "project"
        (project_root / "automation" / "blender").mkdir(parents=True)
        project = Project(
            slug="demo",
            root=project_root,
            apps=("blender",),
            blender={"scripts_dir": "automation/blender"},
        )
        config = SimpleNamespace(state_dir=root / "state")
        return BlenderLiveBridge(config, project)

    def test_result_survives_without_live_presence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bridge = self.make_bridge(Path(raw))
            bridge._ensure_dirs()
            command_id = uuid.uuid4().hex
            (bridge.results / f"{command_id}.json").write_text(
                json.dumps(
                    {
                        "id": command_id,
                        "ok": True,
                        "summary": "done",
                        "value": 42,
                    }
                ),
                encoding="utf-8",
            )

            result = bridge.result(command_id)

            self.assertTrue(result.ok)
            self.assertEqual("done", result.summary)
            self.assertTrue(result.data["durable_result"])
            self.assertEqual(42, result.data["value"])

    def test_missing_result_is_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bridge = self.make_bridge(Path(raw))
            result = bridge.result(uuid.uuid4().hex)

            self.assertFalse(result.ok)
            self.assertTrue(result.data["retryable"])

    def test_noncanonical_command_id_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bridge = self.make_bridge(Path(raw))
            result = bridge.result(str(uuid.uuid4()))

            self.assertFalse(result.ok)
            self.assertIn("canonical", result.summary)

    def test_missing_result_reports_inflight_command(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bridge = self.make_bridge(Path(raw))
            bridge._ensure_dirs()
            command_id = uuid.uuid4().hex
            (bridge.inflight / f"{command_id}.json").write_text(
                json.dumps({"id": command_id, "operation": "run_script"}),
                encoding="utf-8",
            )

            result = bridge.result(command_id)

            self.assertFalse(result.ok)
            self.assertTrue(result.data["retryable"])
            self.assertTrue(result.data["in_progress"])

    def test_presence_fresh_accepts_live_pid_and_rejects_dead_pid(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bridge = self.make_bridge(Path(raw))
            bridge._ensure_dirs()
            bridge.presence.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
            with patch("ordax_dev_agent.blender_live_bridge._process_is_running", return_value=True):
                self.assertTrue(bridge.presence_is_fresh())

            with patch("ordax_dev_agent.blender_live_bridge._process_is_running", return_value=False):
                self.assertFalse(bridge.presence_is_fresh())

    def test_presence_fresh_tolerates_small_future_mtime_but_not_large_skew(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bridge = self.make_bridge(Path(raw))
            bridge._ensure_dirs()
            bridge.presence.write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
            now = bridge.presence.stat().st_mtime

            os.utime(bridge.presence, (now + 1.0, now + 1.0))
            with (
                patch("ordax_dev_agent.blender_live_bridge.time.time", return_value=now),
                patch("ordax_dev_agent.blender_live_bridge._process_is_running", return_value=True),
            ):
                self.assertTrue(bridge.presence_is_fresh())

            os.utime(bridge.presence, (now + 3.0, now + 3.0))
            with (
                patch("ordax_dev_agent.blender_live_bridge.time.time", return_value=now),
                patch("ordax_dev_agent.blender_live_bridge._process_is_running", return_value=True),
            ):
                self.assertFalse(bridge.presence_is_fresh())

    def test_status_retries_transient_presence_permission_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bridge = self.make_bridge(Path(raw))
            bridge._ensure_dirs()
            bridge.presence.write_text("{}", encoding="utf-8")
            presence = json.dumps(
                {
                    "protocol_version": 9,
                    "companion_fingerprint": "demo",
                    "capabilities": ["ping"],
                }
            )
            original_read_text = Path.read_text
            calls = {"count": 0}

            def flaky_read_text(path, *args, **kwargs):
                if path == bridge.presence and calls["count"] < 2:
                    calls["count"] += 1
                    raise PermissionError(13, "sharing violation")
                if path == bridge.presence:
                    calls["count"] += 1
                    return presence
                return original_read_text(path, *args, **kwargs)

            with patch.object(Path, "read_text", flaky_read_text):
                status = bridge.status()

            self.assertGreaterEqual(calls["count"], 3)
            self.assertTrue(status["protocol_compatible"])
            self.assertNotIn("presence_error", status)

    def test_status_lists_inflight_commands(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bridge = self.make_bridge(Path(raw))
            bridge._ensure_dirs()
            command_id = uuid.uuid4().hex
            (bridge.inflight / f"{command_id}.json").write_text("{}", encoding="utf-8")

            status = bridge.status()

            self.assertIn(command_id, status["inflight_commands"])
            self.assertEqual(str(bridge.inflight), status["inflight_root"])


    def test_request_retries_transient_response_permission_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bridge = self.make_bridge(Path(raw))
            bridge._ensure_dirs()
            command_id = "b" * 32
            response_path = bridge.responses / f"{command_id}.json"
            response_path.write_text(
                json.dumps(
                    {
                        "id": command_id,
                        "ok": True,
                        "summary": "done",
                    }
                ),
                encoding="utf-8",
            )
            original_read_text = Path.read_text
            calls = {"count": 0}

            def flaky_read_text(path, *args, **kwargs):
                if path == response_path and calls["count"] < 2:
                    calls["count"] += 1
                    raise PermissionError(13, "sharing violation")
                if path == response_path:
                    calls["count"] += 1
                return original_read_text(path, *args, **kwargs)

            current = {
                "protocol_compatible": True,
                "companion_current": True,
                "capabilities": ["ping"],
            }
            with (
                patch.object(bridge, "presence_is_fresh", return_value=True),
                patch.object(bridge, "status", return_value=current),
                patch("ordax_dev_agent.blender_live_bridge.uuid.uuid4", return_value=SimpleNamespace(hex=command_id)),
                patch.object(Path, "read_text", flaky_read_text),
            ):
                result = bridge.request("ping", timeout_seconds=1)

            self.assertTrue(result.ok)
            self.assertEqual("done", result.summary)
            self.assertGreaterEqual(calls["count"], 3)

    def test_result_retries_transient_permission_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bridge = self.make_bridge(Path(raw))
            bridge._ensure_dirs()
            command_id = uuid.uuid4().hex
            result_path = bridge.results / f"{command_id}.json"
            result_path.write_text(
                json.dumps(
                    {
                        "id": command_id,
                        "ok": True,
                        "summary": "durable",
                    }
                ),
                encoding="utf-8",
            )
            original_read_text = Path.read_text
            calls = {"count": 0}

            def flaky_read_text(path, *args, **kwargs):
                if path == result_path and calls["count"] < 2:
                    calls["count"] += 1
                    raise PermissionError(13, "sharing violation")
                if path == result_path:
                    calls["count"] += 1
                return original_read_text(path, *args, **kwargs)

            with patch.object(Path, "read_text", flaky_read_text):
                result = bridge.result(command_id)

            self.assertTrue(result.ok)
            self.assertEqual("durable", result.summary)
            self.assertGreaterEqual(calls["count"], 3)

    def test_outdated_companion_allows_advertised_maintenance_operation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bridge = self.make_bridge(Path(raw))
            bridge._ensure_dirs()
            command_id = "a" * 32
            (bridge.responses / f"{command_id}.json").write_text(
                json.dumps({"id": command_id, "ok": True, "summary": "saved"}),
                encoding="utf-8",
            )
            outdated = {
                "protocol_compatible": False,
                "companion_current": False,
                "capabilities": ["save", "quit"],
            }
            with (
                patch.object(bridge, "presence_is_fresh", return_value=True),
                patch.object(bridge, "status", return_value=outdated),
                patch("ordax_dev_agent.blender_live_bridge.uuid.uuid4", return_value=SimpleNamespace(hex=command_id)),
            ):
                result = bridge.request("save", {"target_path": "demo.blend"}, timeout_seconds=1)

            self.assertTrue(result.ok)
            self.assertEqual("saved", result.summary)

    def test_outdated_companion_blocks_nonmaintenance_operation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            bridge = self.make_bridge(Path(raw))
            outdated = {
                "protocol_compatible": False,
                "companion_current": False,
                "capabilities": ["run_script"],
            }
            with (
                patch.object(bridge, "presence_is_fresh", return_value=True),
                patch.object(bridge, "status", return_value=outdated),
            ):
                result = bridge.request("run_script", {"script_path": "demo.py"})

            self.assertFalse(result.ok)
            self.assertIn("protocol is outdated", result.summary)

    def test_companion_uses_shared_transform_contract(self) -> None:
        root = Path(__file__).resolve().parents[1]
        companion = (
            root / "ordax_dev_agent" / "assets" / "blender_live_companion.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            '"blender_modeling_contracts.py"',
            companion,
        )
        self.assertIn(
            "_normalize_transform_request = "
            "_MODELING_CONTRACTS.normalize_transform_request",
            companion,
        )
        self.assertIn(
            'transport_fields={"id", "operation"}',
            companion,
        )
        self.assertNotIn("def _coerce_vector(", companion)

    def test_validated_modeling_mutations_are_advertised_and_use_normal_dispatch(self) -> None:
        root = Path(__file__).resolve().parents[1]
        companion = (
            root / "ordax_dev_agent" / "assets" / "blender_live_companion.py"
        ).read_text(encoding="utf-8")
        benchmark = (root / "scripts" / "blender_benchmark.py").read_text(
            encoding="utf-8"
        )

        start = companion.index("CAPABILITIES = [")
        end = companion.index("_LAST_PRESENCE_AT", start)
        capabilities = companion[start:end]
        self.assertIn('"create_primitive"', capabilities)
        self.assertIn('"add_modifier"', capabilities)
        self.assertNotIn("__smoke_create_primitive", companion)
        self.assertNotIn("__smoke_add_modifier", companion)
        self.assertNotIn("smoke_only_unregistered", companion)

        self.assertIn("def _modeling_create_primitive(", companion)
        self.assertIn("def _modeling_add_modifier(", companion)
        self.assertIn('operation == "create_primitive"', companion)
        self.assertIn('operation == "add_modifier"', companion)
        self.assertIn('"operation": "create_primitive"', companion)
        self.assertIn('"operation": "add_modifier"', companion)

        self.assertIn('"smoke-model-create.json"', benchmark)
        self.assertIn('"smoke-model-modifier.json"', benchmark)
        self.assertIn('"smoke-model-create-duplicate.json"', benchmark)
        self.assertIn('"smoke-model-modifier-duplicate.json"', benchmark)

    def test_multiview_uses_eevee_for_material_and_workbench_for_silhouette(self) -> None:
        root = Path(__file__).resolve().parents[1]
        companion = (
            root / "ordax_dev_agent" / "assets" / "blender_live_companion.py"
        ).read_text(encoding="utf-8")

        self.assertIn(
            'candidates = ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE")',
            companion,
        )
        self.assertIn(
            'candidates = ("BLENDER_WORKBENCH_NEXT", "BLENDER_WORKBENCH")',
            companion,
        )
        self.assertIn("mode=mode", companion)
        self.assertIn("def _multiview_add_material_lights(", companion)
        self.assertIn("material_lights = _multiview_add_material_lights(", companion)


    def test_blenderbench_fixture_uses_script_file_not_python_expr(self) -> None:
        root = Path(__file__).resolve().parents[1]
        benchmark = (root / "scripts" / "blender_benchmark.py").read_text(
            encoding="utf-8"
        )

        create_start = benchmark.index("def _create_scene(")
        create_end = benchmark.index("\n\ndef _run_companion_smoke(", create_start)
        create_scene = benchmark[create_start:create_end]

        self.assertIn('fixture_script = scene.with_suffix(".fixture.py")', create_scene)
        self.assertIn('"--disable-autoexec"', create_scene)
        self.assertIn('"--python"', create_scene)
        self.assertIn("fixture_script.unlink()", create_scene)
        self.assertNotIn('"--python-expr"', create_scene)


    def test_extract_region_handles_stale_root_world_matrix(self) -> None:
        root = Path(__file__).resolve().parents[1]
        companion = (
            root / "ordax_dev_agent" / "assets" / "blender_live_companion.py"
        ).read_text(encoding="utf-8")

        self.assertIn("bpy.context.view_layer.update()", companion)
        self.assertIn("local_world_delta = abs(local_coordinate - world_coordinate)", companion)
        self.assertIn("bounds_world_delta = abs(coordinate - world_coordinate)", companion)
        self.assertIn("coordinate = local_coordinate", companion)
        self.assertIn("obj.location = obj.location + delta", companion)



if __name__ == "__main__":
    unittest.main()
