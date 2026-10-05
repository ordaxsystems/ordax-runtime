import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig


class OrdaxPreviewTests(unittest.TestCase):
    def test_preview_status_exposes_latest_image_and_artifact_payload(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "project"
            project.mkdir()
            (project / "index.html").write_text("<h1>demo</h1>", encoding="utf-8")
            state = root / "state"
            artifact = state / "artifacts" / "demo" / "frame.png"
            artifact.parent.mkdir(parents=True)
            Image.new("RGB", (32, 20), (10, 20, 30)).save(artifact)
            config = AgentConfig(
                agent_name="test", poll_seconds=1, state_dir=state,
                agent_repo_path=root / "agent", hordax_path=root / "hordax",
                bridge_path=root / "bridge",
                projects={"demo": {"path": str(project), "apps": []}},
                default_project="demo",
            )
            env = {"ORDAX_MEMORY_DB": str(root / "memory.db")}
            with patch.dict(os.environ, env, clear=False):
                registry = ActionRegistry(config)
                status = registry.execute("project.preview_status", {"project": "demo"})

            self.assertTrue(status.ok)
            self.assertEqual("web", status.data["mode"])
            self.assertIsNone(status.data["url"])
            latest = status.data["latest_image"]
            self.assertEqual("frame.png", latest["relative_path"])
            self.assertEqual({"artifact_name": "frame.png"}, latest["artifact_preview_payload"])

            with patch.dict(os.environ, env, clear=False):
                preview = registry.execute("artifact.preview", {
                    "project": "demo", **latest["artifact_preview_payload"],
                    "thumbnail": True, "max_width": 200, "max_height": 120,
                })
            self.assertTrue(preview.ok)
            self.assertEqual("image/jpeg", preview.data["mime_type"])
            self.assertTrue(preview.data["base64"])


class OrdaxPreviewRuntimeTests(unittest.TestCase):
    def test_static_preview_runtime_start_capture_and_stop(self):
        import socket
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "web"
            project.mkdir()
            (project / "index.html").write_text("<h1>ORDAX live</h1>", encoding="utf-8")
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            config = AgentConfig(
                agent_name="test", poll_seconds=1, state_dir=root / "state",
                agent_repo_path=root / "agent", hordax_path=root / "hordax",
                bridge_path=root / "bridge",
                projects={"web": {"path": str(project), "apps": [], "preview": {"port": port}}},
                default_project="web",
            )
            registry = ActionRegistry(config)
            started = registry.execute("project.preview_start", {"project": "web", "wait_seconds": 10})
            self.assertTrue(started.ok, started.summary)
            try:
                self.assertTrue(started.data["url_ready"])
                status = registry.execute("project.preview_status", {"project": "web"})
                self.assertTrue(status.data["runtime"]["running"])
                self.assertTrue(status.data["runtime"]["ownership_valid"])
                self.assertEqual("running", status.data["runtime"]["state"])
                self.assertEqual(started.data["url"], status.data["url"])

                logs = registry.execute("project.preview_logs", {"project": "web"})
                self.assertTrue(logs.ok, logs.summary)
                self.assertTrue(Path(logs.data["log"]).is_file())
            finally:
                stopped = registry.execute("project.preview_stop", {"project": "web"})
            self.assertTrue(stopped.ok, stopped.summary)
            self.assertFalse(stopped.data["running"])
            self.assertEqual("stopped", stopped.data["state"])
            self.assertTrue((config.state_dir / "previews" / "web.json").is_file())

    def test_stop_refuses_stale_foreign_pid(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = root / "web"
            project.mkdir()
            (project / "index.html").write_text("<h1>safe</h1>", encoding="utf-8")
            config = AgentConfig(
                agent_name="test", poll_seconds=1, state_dir=root / "state",
                agent_repo_path=root / "agent", hordax_path=root / "hordax",
                bridge_path=root / "bridge",
                projects={"web": {"path": str(project), "apps": []}},
                default_project="web",
            )
            registry = ActionRegistry(config)
            state_path = config.state_dir / "previews" / "web.json"
            state_path.parent.mkdir(parents=True)
            state_path.write_text(json.dumps({
                "schema_version": 1, "project": "web", "token": "not-owned",
                "state": "running", "pid": os.getpid(), "url": "http://127.0.0.1:9",
            }), encoding="utf-8")
            stopped = registry.execute("project.preview_stop", {"project": "web"})
            self.assertFalse(stopped.ok)
            self.assertEqual("stale", stopped.data["state"])
            self.assertIn("not owned by ORDAX", stopped.summary)


if __name__ == "__main__":
    unittest.main()


