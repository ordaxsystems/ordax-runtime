import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.game_asset_engine_export_actions import ENGINE_EXPORT_SCHEMA
from ordax_dev_agent.models import ActionResult


class GameAssetWebRuntimeActionsTests(unittest.TestCase):
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

    def make_glb(self, document: dict) -> bytes:
        body = json.dumps(document, separators=(",", ":")).encode("utf-8")
        body += b" " * ((4 - len(body) % 4) % 4)
        total = 12 + 8 + len(body)
        return (
            struct.pack("<4sII", b"glTF", 2, total)
            + struct.pack("<II", len(body), 0x4E4F534A)
            + body
        )

    def make_export(self, root: Path, *, engine: str = "web", document: dict | None = None):
        project = root / "project"
        source = project / "staging" / "asset.blend"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"blend-source")
        artifact = project / "exports" / "asset.glb"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(
            self.make_glb(document or {"asset": {"version": "2.0"}, "scenes": [{}], "nodes": []})
        )
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
                    "profile": {"format": "glb", "purpose": "Web realtime glTF"},
                }
            ),
            encoding="utf-8",
        )
        return artifact

    def make_three_project(self, root: Path) -> Path:
        web = root / "project" / "web"
        three = web / "node_modules" / "three"
        (three / "build").mkdir(parents=True, exist_ok=True)
        (three / "examples" / "jsm" / "loaders").mkdir(parents=True, exist_ok=True)
        (three / "build" / "three.module.js").write_text("export const REVISION='186';", encoding="utf-8")
        (three / "examples" / "jsm" / "loaders" / "GLTFLoader.js").write_text(
            "export class GLTFLoader {}", encoding="utf-8"
        )
        (three / "package.json").write_text('{"name":"three","version":"0.186.0"}', encoding="utf-8")
        return web

    def runtime_stats(self, *, visible_ratio: float = 0.22, meshes: int = 2) -> dict:
        return {
            "three_revision": "186",
            "meshes": meshes,
            "skinned_meshes": 1,
            "bones": 64,
            "vertices": 2400,
            "triangles": 1200,
            "materials": 3,
            "textures": 4,
            "animations": 2,
            "bounds": {"x": 2.0, "y": 3.0, "z": 1.5, "radius": 2.0},
            "visible_pixels": int(65536 * visible_ratio),
            "visible_pixel_ratio": visible_ratio,
            "luminance_range": 180,
            "render_calls": 2,
            "rendered_triangles": 1200,
            "webgl2": True,
        }

    def browser_dom(self, stats: dict) -> str:
        return "<html><body><pre>ORDAX_THREE_RUNTIME_OK|" + json.dumps(stats) + "</pre></body></html>"

    def test_registry_exposes_threejs_browser_validation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            status = registry.execute("agent.status", {})
            self.assertTrue(status.ok)
            self.assertIn("game_assets.threejs_browser_validate", status.data["actions"])

    def test_success_uses_local_three_and_sandboxed_headless_chrome(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifact = self.make_export(root)
            web = self.make_three_project(root)

            def fake_run(command, *, cwd=None, timeout=0, env=None):
                self.assertEqual("chrome", command[0])
                self.assertIn("--headless=new", command)
                self.assertIn("--dump-dom", command)
                self.assertNotIn("--no-sandbox", command)
                self.assertNotIn("--enable-unsafe-swiftshader", command)
                self.assertNotIn("--use-angle=swiftshader", command)
                self.assertTrue(command[-1].startswith("http://127.0.0.1:"))
                self.assertTrue(Path(cwd).samefile(web))
                self.assertGreaterEqual(timeout, 15)
                return ActionResult(
                    True,
                    "command completed",
                    {"stdout": self.browser_dom(self.runtime_stats()), "stderr": ""},
                )

            registry = ActionRegistry(self.make_config(root))
            with patch(
                "ordax_dev_agent.game_asset_web_runtime_actions._find_chrome",
                return_value="chrome",
            ), patch(
                "ordax_dev_agent.game_asset_web_runtime_actions._run",
                side_effect=fake_run,
            ):
                result = registry.execute(
                    "game_assets.threejs_browser_validate",
                    {
                        "project": "game",
                        "artifact_path": "exports/asset.glb",
                        "web_project_dir": "web",
                        "min_meshes": 2,
                        "min_triangles": 1000,
                        "min_animations": 1,
                        "require_skinned_mesh": True,
                        "min_visible_pixel_ratio": 0.1,
                    },
                )

            self.assertTrue(result.ok, result.summary)
            self.assertTrue(result.data["validated_in_browser"])
            self.assertTrue(result.data["render_validated"])
            self.assertTrue(result.data["semantic_requirements_passed"])
            self.assertEqual("three.js", result.data["runtime"])
            self.assertEqual("0.186.0", result.data["three_package_version"])
            self.assertEqual(1200, result.data["runtime_stats"]["triangles"])
            self.assertTrue(artifact.is_file())
            self.assertTrue(result.data["source_preserved"])

    def test_browser_can_render_but_fail_visible_pixel_quality_gate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.make_export(root)
            self.make_three_project(root)
            with patch(
                "ordax_dev_agent.game_asset_web_runtime_actions._find_chrome",
                return_value="chrome",
            ), patch(
                "ordax_dev_agent.game_asset_web_runtime_actions._run",
                return_value=ActionResult(
                    True,
                    "command completed",
                    {"stdout": self.browser_dom(self.runtime_stats(visible_ratio=0.0001)), "stderr": ""},
                ),
            ):
                result = ActionRegistry(self.make_config(root)).execute(
                    "game_assets.threejs_browser_validate",
                    {
                        "project": "game",
                        "artifact_path": "exports/asset.glb",
                        "web_project_dir": "web",
                        "min_visible_pixel_ratio": 0.05,
                    },
                )
            self.assertFalse(result.ok)
            self.assertTrue(result.data["validated_in_browser"])
            self.assertTrue(result.data["render_validated"])
            self.assertFalse(result.data["semantic_requirements_passed"])
            self.assertIn("visible-pixel ratio", result.data["failures"][0])

    def test_external_glb_dependencies_are_rejected_before_browser_execution(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.make_export(
                root,
                document={
                    "asset": {"version": "2.0"},
                    "scenes": [{}],
                    "buffers": [{"uri": "external.bin", "byteLength": 4}],
                },
            )
            self.make_three_project(root)
            with patch("ordax_dev_agent.game_asset_web_runtime_actions._run") as run:
                result = ActionRegistry(self.make_config(root)).execute(
                    "game_assets.threejs_browser_validate",
                    {
                        "project": "game",
                        "artifact_path": "exports/asset.glb",
                        "web_project_dir": "web",
                    },
                )
            self.assertFalse(result.ok)
            self.assertIn("self-contained", result.summary)
            run.assert_not_called()

    def test_three_package_must_be_project_local(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.make_export(root)
            web = root / "project" / "web"
            web.mkdir(parents=True, exist_ok=True)
            with patch("ordax_dev_agent.game_asset_web_runtime_actions._run") as run:
                result = ActionRegistry(self.make_config(root)).execute(
                    "game_assets.threejs_browser_validate",
                    {
                        "project": "game",
                        "artifact_path": "exports/asset.glb",
                        "web_project_dir": "web",
                    },
                )
            self.assertFalse(result.ok)
            self.assertIn("Three.js package not found", result.summary)
            run.assert_not_called()

    def test_wrong_engine_provenance_is_rejected_before_browser_execution(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.make_export(root, engine="godot")
            self.make_three_project(root)
            with patch("ordax_dev_agent.game_asset_web_runtime_actions._run") as run:
                result = ActionRegistry(self.make_config(root)).execute(
                    "game_assets.threejs_browser_validate",
                    {
                        "project": "game",
                        "artifact_path": "exports/asset.glb",
                        "web_project_dir": "web",
                    },
                )
            self.assertFalse(result.ok)
            self.assertIn("targeted to web", result.summary)
            run.assert_not_called()

    def test_browser_failure_is_retryable_and_source_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            artifact = self.make_export(root)
            self.make_three_project(root)
            with patch(
                "ordax_dev_agent.game_asset_web_runtime_actions._find_chrome",
                return_value="chrome",
            ), patch(
                "ordax_dev_agent.game_asset_web_runtime_actions._run",
                return_value=ActionResult(
                    False,
                    "command failed",
                    {"stdout": "", "stderr": "WebGL unavailable", "returncode": 1},
                ),
            ):
                result = ActionRegistry(self.make_config(root)).execute(
                    "game_assets.threejs_browser_validate",
                    {
                        "project": "game",
                        "artifact_path": "exports/asset.glb",
                        "web_project_dir": "web",
                    },
                )
            self.assertFalse(result.ok)
            self.assertTrue(result.data["retryable"])
            self.assertTrue(result.data["source_preserved"])
            self.assertTrue(artifact.is_file())


if __name__ == "__main__":
    unittest.main()
