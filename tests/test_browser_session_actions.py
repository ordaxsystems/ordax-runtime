from __future__ import annotations

import threading
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.browser_session_actions import BrowserSessionActions
from ordax_dev_agent.browser_capture import find_chromium
from ordax_dev_agent.config import AgentConfig


_HTML = b"""<!doctype html>
<html><head><title>ORDAX Browser Test</title></head>
<body>
  <input id="name" aria-label="Name">
  <button id="go" onclick="document.getElementById('out').textContent='Hello '+document.getElementById('name').value">Go</button>
  <p id="out">Idle</p>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(_HTML)))
        self.end_headers()
        self.wfile.write(_HTML)

    def log_message(self, _format, *_args):
        return


class BrowserProviderGuardTests(unittest.TestCase):
    def test_chatgpt_consumer_pages_are_blocked_from_programmatic_browser_control(self):
        for url in (
            "https://chatgpt.com/",
            "https://chatgpt.com/c/abc",
            "https://sub.chatgpt.com/path",
            "https://chat.openai.com/",
        ):
            with self.subTest(url=url):
                self.assertTrue(BrowserSessionActions._is_protected_provider_url(url))
                with self.assertRaisesRegex(ValueError, "official ORDAX MCP/Web Bridge"):
                    BrowserSessionActions._assert_automation_url_allowed(url)

    def test_regular_websites_remain_automation_eligible(self):
        for url in (
            "https://example.com/",
            "https://github.com/openai/tunnel-client",
            "https://developers.openai.com/api/docs/guides/secure-mcp-tunnels",
        ):
            with self.subTest(url=url):
                self.assertFalse(BrowserSessionActions._is_protected_provider_url(url))
                BrowserSessionActions._assert_automation_url_allowed(url)


@unittest.skipIf(find_chromium() is None, "Chrome/Edge not available on CI runner")
class BrowserSessionActionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.registry = ActionRegistry(
            AgentConfig(
                agent_name="test",
                poll_seconds=0.25,
                state_dir=self.root / "state",
                agent_repo_path=self.root / "agent",
                hordax_path=self.root / "hordax",
                bridge_path=self.root / "bridge",
                projects={"demo": {"path": str(self.project), "apps": []}},
                default_project="demo",
            )
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self._stop_server)
        self.session_id = None

    def _stop_server(self):
        self.server.shutdown()
        self.server_thread.join(timeout=5)
        self.server.server_close()

    def tearDown(self):
        if self.session_id:
            try:
                self.registry.execute(
                    "browser.stop",
                    {"project": "demo", "session_id": self.session_id},
                )
            except Exception:
                pass

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}/"

    def test_headless_browser_snapshot_type_click_screenshot_and_stop(self):
        started = self.registry.execute(
            "browser.start",
            {
                "project": "demo",
                "url": self.url,
                "headless": True,
                "wait_seconds": 12,
            },
        )
        self.assertTrue(started.ok, started.summary)
        self.session_id = started.data["session_id"]
        self.assertTrue(started.data["ownership_valid"])

        snap = self.registry.execute(
            "browser.snapshot",
            {
                "project": "demo",
                "session_id": self.session_id,
                "max_elements": 100,
            },
        )
        self.assertTrue(snap.ok, snap.summary)
        self.assertEqual(snap.data["title"], "ORDAX Browser Test")
        input_node = next(item for item in snap.data["elements"] if item["tag"] == "input")
        button_node = next(item for item in snap.data["elements"] if item["tag"] == "button")

        typed = self.registry.execute(
            "browser.type",
            {
                "project": "demo",
                "session_id": self.session_id,
                "node_id": input_node["id"],
                "text": "ORDAX",
                "clear": True,
            },
        )
        self.assertTrue(typed.ok, typed.summary)
        self.assertEqual(typed.data["value"], "ORDAX")

        clicked = self.registry.execute(
            "browser.click",
            {
                "project": "demo",
                "session_id": self.session_id,
                "node_id": button_node["id"],
            },
        )
        self.assertTrue(clicked.ok, clicked.summary)

        second = self.registry.execute(
            "browser.snapshot",
            {
                "project": "demo",
                "session_id": self.session_id,
                "max_elements": 100,
            },
        )
        self.assertTrue(second.ok, second.summary)
        self.assertIn("Hello ORDAX", second.data["text"])
        input_again = next(item for item in second.data["elements"] if item["tag"] == "input")
        self.assertEqual(input_again["id"], input_node["id"])

        shot = self.registry.execute(
            "browser.screenshot",
            {
                "project": "demo",
                "session_id": self.session_id,
                "width": 1024,
                "height": 720,
            },
        )
        self.assertTrue(shot.ok, shot.summary)
        self.assertGreater(shot.data["size_bytes"], 100)
        artifact = self.root / "state" / "artifacts" / "demo" / shot.data["artifact_name"]
        self.assertTrue(artifact.is_file())
        self.assertTrue(artifact.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"))

        stopped = self.registry.execute(
            "browser.stop",
            {
                "project": "demo",
                "session_id": self.session_id,
            },
        )
        self.assertTrue(stopped.ok, stopped.summary)
        self.session_id = None


if __name__ == "__main__":
    unittest.main()
