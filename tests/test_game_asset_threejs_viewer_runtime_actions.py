import hashlib
import json
import shutil
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.game_asset_engine_export_actions import ENGINE_EXPORT_SCHEMA
from ordax_dev_agent.game_asset_threejs_actions import THREE_VERSION, VITE_VERSION
from ordax_dev_agent.models import ActionResult


class GameAssetThreeJsViewerRuntimeTests(unittest.TestCase):
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

    def make_glb(self) -> bytes:
        document = {"asset": {"version": "2.0"}, "scenes": [{}], "nodes": []}
        body = json.dumps(document, separators=(",", ":")).encode("utf-8")
        body += b" " * ((4 - len(body) % 4) % 4)
        total = 12 + 8 + len(body)
        return (
            struct.pack("<4sII", b"glTF", 2, total)
            + struct.pack("<II", len(body), 0x4E4F534A)
            + body
        )

    def prepare_viewer(self, root: Path) -> tuple[ActionRegistry, Path, str]:
        project = root / "project"
        source = project / "staging" / "asset.blend"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"blend-source")
        artifact = project / "exports" / "asset.glb"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(self.make_glb())
        artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
        manifest = Path(str(artifact) + ".ordax.json")
        manifest.write_text(
            json.dumps(
                {
                    "schema": ENGINE_EXPORT_SCHEMA,
                    "project": "game",
                    "engine": "web",
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
                        "sha256": artifact_hash,
                    },
                    "profile": {"format": "glb", "purpose": "Web realtime glTF"},
                }
            ),
            encoding="utf-8",
        )
        registry = ActionRegistry(self.make_config(root))
        prepared = registry.execute(
            "game_assets.threejs_prepare_viewer",
            {
                "project": "game",
                "artifact_path": "exports/asset.glb",
                "viewer_dir": "viewer",
            },
        )
        self.assertTrue(prepared.ok, prepared.summary)
        viewer = project / "viewer"
        self.install_dependencies(viewer)
        return registry, viewer, artifact_hash

    def install_dependencies(self, viewer: Path) -> None:
        three = viewer / "node_modules" / "three"
        vite = viewer / "node_modules" / "vite"
        three.mkdir(parents=True, exist_ok=True)
        vite.mkdir(parents=True, exist_ok=True)
        (three / "package.json").write_text(
            json.dumps({"name": "three", "version": THREE_VERSION}), encoding="utf-8"
        )
        (vite / "package.json").write_text(
            json.dumps({"name": "vite", "version": VITE_VERSION}), encoding="utf-8"
        )

    def fake_build(self, viewer: Path) -> None:
        dist = viewer / "dist"
        if dist.exists():
            shutil.rmtree(dist)
        dist.mkdir(parents=True, exist_ok=True)
        (dist / "index.html").write_text(
            "<html><body><div id='hud'></div></body></html>", encoding="utf-8"
        )
        shutil.copytree(viewer / "public" / "ordax", dist / "ordax")
        assets = dist / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        (assets / "main.js").write_text("console.log('viewer');", encoding="utf-8")

    def fake_png(self, path: Path, *, width: int = 1280, height: int = 720) -> bytes:
        raw = (
            b"\x89PNG\r\n\x1a\n"
            + struct.pack(">I4sIIBBBBB", 13, b"IHDR", width, height, 8, 6, 0, 0, 0)
            + b"\x00\x00\x00\x00"
            + b"evidence" * 8
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return raw

    def screenshot_path(self, command: list[str]) -> Path | None:
        for item in command:
            if item.startswith("--screenshot="):
                return Path(item.split("=", 1)[1])
        return None

    def hud(
        self,
        artifact_hash: str,
        *,
        backend: str = "WebGPU",
        triangles: int = 5000,
        calls: int = 12,
    ) -> str:
        return (
            "<html><body><div id=\"hud\">OrdaX ordax.threejs-viewer/1\n"
            f"backend: {backend}\n"
            "environment: Salvador Clear Noon\n"
            f"model: {artifact_hash[:12]}…\n"
            f"triangles: {triangles}\n"
            f"calls: {calls}</div></body></html>"
        )

    def test_registry_exposes_full_viewer_validation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = ActionRegistry(self.make_config(Path(raw)))
            status = registry.execute("agent.status", {})
            self.assertTrue(status.ok)
            self.assertIn("game_assets.threejs_viewer_validate", status.data["actions"])

    def test_builds_validates_and_persists_visual_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry, viewer, artifact_hash = self.prepare_viewer(root)
            calls = []
            screenshot_bytes = b""

            def fake_run(command, *, cwd=None, timeout=0, env=None):
                nonlocal screenshot_bytes
                calls.append(command)
                self.assertTrue(Path(cwd).samefile(viewer))
                if command[0] == "npm":
                    self.assertEqual(["npm", "run", "build"], command)
                    self.assertEqual("false", env["npm_config_audit"])
                    self.fake_build(viewer)
                    return ActionResult(True, "command completed", {"stdout": "built", "stderr": ""})
                self.assertEqual("chrome", command[0])
                self.assertIn("--headless=new", command)
                self.assertIn("--virtual-time-budget=2500", command)
                self.assertNotIn("--no-sandbox", command)
                self.assertNotIn("--enable-unsafe-swiftshader", command)
                self.assertTrue(command[-1].startswith("http://127.0.0.1:"))
                screenshot = self.screenshot_path(command)
                if screenshot is not None:
                    self.assertIn("--hide-scrollbars", command)
                    screenshot_bytes = self.fake_png(screenshot)
                    return ActionResult(True, "command completed", {"stdout": "screenshot", "stderr": ""})
                self.assertIn("--dump-dom", command)
                return ActionResult(
                    True,
                    "command completed",
                    {"stdout": self.hud(artifact_hash), "stderr": ""},
                )

            with patch(
                "ordax_dev_agent.game_asset_threejs_viewer_runtime_actions._find_npm",
                return_value="npm",
            ), patch(
                "ordax_dev_agent.game_asset_threejs_viewer_runtime_actions._find_chrome",
                return_value="chrome",
            ), patch(
                "ordax_dev_agent.game_asset_threejs_viewer_runtime_actions._run",
                side_effect=fake_run,
            ):
                result = registry.execute(
                    "game_assets.threejs_viewer_validate",
                    {
                        "project": "game",
                        "viewer_dir": "viewer",
                        "min_triangles": 1000,
                        "min_draw_calls": 2,
                        "require_webgpu": True,
                    },
                )

            self.assertTrue(result.ok, result.summary)
            self.assertEqual(3, len(calls))
            self.assertTrue(result.data["viewer_build_validated"])
            self.assertTrue(result.data["viewer_browser_validated"])
            self.assertTrue(result.data["visual_stack_initialized"])
            self.assertTrue(result.data["visual_evidence_captured"])
            self.assertEqual("WebGPU", result.data["backend"])
            self.assertEqual(5000, result.data["render"]["triangles"])
            self.assertEqual(
                {"three": THREE_VERSION, "vite": VITE_VERSION},
                result.data["installed_versions"],
            )
            evidence = result.data["visual_evidence"]
            evidence_path = Path(evidence["path"])
            self.assertTrue(evidence_path.is_file())
            self.assertEqual(1280, evidence["width"])
            self.assertEqual(720, evidence["height"])
            self.assertEqual(len(screenshot_bytes), evidence["bytes"])
            self.assertEqual(hashlib.sha256(screenshot_bytes).hexdigest(), evidence["sha256"])
            self.assertTrue(str(evidence_path).startswith(str(root / "state" / "visual-evidence")))

    def test_webgl_fallback_can_be_rejected_when_webgpu_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry, viewer, artifact_hash = self.prepare_viewer(root)

            def fake_run(command, *, cwd=None, timeout=0, env=None):
                if command[0] == "npm":
                    self.fake_build(viewer)
                    return ActionResult(True, "command completed", {"stdout": "built", "stderr": ""})
                screenshot = self.screenshot_path(command)
                if screenshot is not None:
                    self.fake_png(screenshot)
                    return ActionResult(True, "command completed", {"stdout": "screenshot", "stderr": ""})
                return ActionResult(
                    True,
                    "command completed",
                    {"stdout": self.hud(artifact_hash, backend="WebGL2 fallback"), "stderr": ""},
                )

            with patch(
                "ordax_dev_agent.game_asset_threejs_viewer_runtime_actions._find_npm",
                return_value="npm",
            ), patch(
                "ordax_dev_agent.game_asset_threejs_viewer_runtime_actions._find_chrome",
                return_value="chrome",
            ), patch(
                "ordax_dev_agent.game_asset_threejs_viewer_runtime_actions._run",
                side_effect=fake_run,
            ):
                result = registry.execute(
                    "game_assets.threejs_viewer_validate",
                    {
                        "project": "game",
                        "viewer_dir": "viewer",
                        "require_webgpu": True,
                    },
                )
            self.assertFalse(result.ok)
            self.assertTrue(result.data["viewer_browser_validated"])
            self.assertTrue(result.data["visual_evidence_captured"])
            self.assertIn("WebGL2 fallback", result.data["failures"][0])

    def test_missing_dependencies_refuses_to_run_npm_install_implicitly(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry, viewer, _ = self.prepare_viewer(root)
            shutil.rmtree(viewer / "node_modules")
            with patch("ordax_dev_agent.game_asset_threejs_viewer_runtime_actions._run") as run:
                result = registry.execute(
                    "game_assets.threejs_viewer_validate",
                    {"project": "game", "viewer_dir": "viewer"},
                )
            self.assertFalse(result.ok)
            self.assertIn("run npm install explicitly", result.summary)
            run.assert_not_called()

    def test_tampered_build_script_is_rejected_before_npm_execution(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry, viewer, _ = self.prepare_viewer(root)
            package_path = viewer / "package.json"
            package = json.loads(package_path.read_text(encoding="utf-8"))
            package["scripts"]["build"] = "node arbitrary.js"
            package_path.write_text(json.dumps(package), encoding="utf-8")
            with patch("ordax_dev_agent.game_asset_threejs_viewer_runtime_actions._run") as run:
                result = registry.execute(
                    "game_assets.threejs_viewer_validate",
                    {"project": "game", "viewer_dir": "viewer"},
                )
            self.assertFalse(result.ok)
            self.assertIn("exactly 'vite build'", result.summary)
            run.assert_not_called()

    def test_browser_without_hud_proof_is_retryable_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry, viewer, _ = self.prepare_viewer(root)

            def fake_run(command, *, cwd=None, timeout=0, env=None):
                if command[0] == "npm":
                    self.fake_build(viewer)
                    return ActionResult(True, "command completed", {"stdout": "built", "stderr": ""})
                screenshot = self.screenshot_path(command)
                if screenshot is not None:
                    self.fake_png(screenshot)
                    return ActionResult(True, "command completed", {"stdout": "screenshot", "stderr": ""})
                return ActionResult(
                    True,
                    "command completed",
                    {
                        "stdout": "<html><body><div id='hud'>OrdaX visual runtime</div></body></html>",
                        "stderr": "",
                    },
                )

            with patch(
                "ordax_dev_agent.game_asset_threejs_viewer_runtime_actions._find_npm",
                return_value="npm",
            ), patch(
                "ordax_dev_agent.game_asset_threejs_viewer_runtime_actions._find_chrome",
                return_value="chrome",
            ), patch(
                "ordax_dev_agent.game_asset_threejs_viewer_runtime_actions._run",
                side_effect=fake_run,
            ):
                result = registry.execute(
                    "game_assets.threejs_viewer_validate",
                    {"project": "game", "viewer_dir": "viewer"},
                )
            self.assertFalse(result.ok)
            self.assertTrue(result.data["retryable"])
            self.assertIn("rendered HUD proof", result.summary)

    def test_screenshot_success_without_png_file_is_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry, viewer, artifact_hash = self.prepare_viewer(root)

            def fake_run(command, *, cwd=None, timeout=0, env=None):
                if command[0] == "npm":
                    self.fake_build(viewer)
                    return ActionResult(True, "command completed", {"stdout": "built", "stderr": ""})
                if self.screenshot_path(command) is not None:
                    return ActionResult(True, "command completed", {"stdout": "claimed screenshot", "stderr": ""})
                return ActionResult(
                    True,
                    "command completed",
                    {"stdout": self.hud(artifact_hash), "stderr": ""},
                )

            with patch(
                "ordax_dev_agent.game_asset_threejs_viewer_runtime_actions._find_npm",
                return_value="npm",
            ), patch(
                "ordax_dev_agent.game_asset_threejs_viewer_runtime_actions._find_chrome",
                return_value="chrome",
            ), patch(
                "ordax_dev_agent.game_asset_threejs_viewer_runtime_actions._run",
                side_effect=fake_run,
            ):
                result = registry.execute(
                    "game_assets.threejs_viewer_validate",
                    {"project": "game", "viewer_dir": "viewer"},
                )
            self.assertFalse(result.ok)
            self.assertIn("produced no PNG evidence", result.summary)

    def test_wrong_screenshot_dimensions_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry, viewer, artifact_hash = self.prepare_viewer(root)

            def fake_run(command, *, cwd=None, timeout=0, env=None):
                if command[0] == "npm":
                    self.fake_build(viewer)
                    return ActionResult(True, "command completed", {"stdout": "built", "stderr": ""})
                screenshot = self.screenshot_path(command)
                if screenshot is not None:
                    self.fake_png(screenshot, width=640, height=480)
                    return ActionResult(True, "command completed", {"stdout": "screenshot", "stderr": ""})
                return ActionResult(
                    True,
                    "command completed",
                    {"stdout": self.hud(artifact_hash), "stderr": ""},
                )

            with patch(
                "ordax_dev_agent.game_asset_threejs_viewer_runtime_actions._find_npm",
                return_value="npm",
            ), patch(
                "ordax_dev_agent.game_asset_threejs_viewer_runtime_actions._find_chrome",
                return_value="chrome",
            ), patch(
                "ordax_dev_agent.game_asset_threejs_viewer_runtime_actions._run",
                side_effect=fake_run,
            ):
                result = registry.execute(
                    "game_assets.threejs_viewer_validate",
                    {"project": "game", "viewer_dir": "viewer"},
                )
            self.assertFalse(result.ok)
            self.assertIn("1280x720", result.summary)


if __name__ == "__main__":
    unittest.main()
