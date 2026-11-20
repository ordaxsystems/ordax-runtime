from __future__ import annotations

import threading
import tempfile
import unittest
from unittest.mock import Mock, patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.browser_session_actions import BrowserSessionActions
from ordax_dev_agent.browser_capture import find_chromium
from ordax_dev_agent.config import AgentConfig


_HTML = b"""<!doctype html>
<html><head><title>ORDAX Browser Test</title></head>
<body onload="document.getElementById('saved').textContent='Saved '+(localStorage.getItem('ordax-e2e')||'none')">
  <input id="name" aria-label="Name">
  <button id="go" onclick="document.getElementById('out').textContent='Hello '+document.getElementById('name').value;localStorage.setItem('ordax-e2e',document.getElementById('name').value)">Go</button>
  <p id="out">Idle</p>
  <p id="saved">Saved none</p>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("location", "/")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(_HTML)))
        self.end_headers()
        self.wfile.write(_HTML)

    def log_message(self, _format, *_args):
        return


class BrowserUrlValidationTests(unittest.TestCase):
    def test_chatgpt_pages_reach_the_existing_navigation_adapter(self):
        # Test URL admission and dispatch, not a simulated ChatGPT login or reply.
        actions = BrowserSessionActions()
        actions._project = lambda _payload: object()
        for url in (
            "https://chatgpt.com/",
            "https://chatgpt.com/c/abc",
            "https://sub.chatgpt.com/path",
            "https://chat.openai.com/",
            "https://example.com/",
        ):
            with self.subTest(url=url):
                self.assertEqual(actions._valid_url(url), url)
                with patch.object(
                    actions, "_with_page",
                    return_value={"session_id": "test-session", "url": url, "title": "Browser"},
                ) as dispatch:
                    result = actions.browser_navigate({"session_id": "test-session", "url": url})
                self.assertTrue(result.ok, result.summary)
                dispatch.assert_called_once()

    def test_browser_urls_keep_scheme_validation(self):
        for url in ("javascript:alert(1)", "file:///tmp/secret", "data:text/html,x", ""):
            with self.subTest(url=url), self.assertRaisesRegex(ValueError, "http or https"):
                BrowserSessionActions._valid_url(url)
        self.assertEqual(
            BrowserSessionActions._valid_url("about:blank", allow_blank=True), "about:blank"
        )


@unittest.skipIf(find_chromium() is None, "Chrome/Edge not available on CI runner")
class BrowserShutdownLifecycleTests(unittest.TestCase):
    def test_graceful_close_waits_on_owned_handle_before_child_discovery(self):
        # No real Chromium required: verify the process-handle wait, then only
        # one authoritative child scan after Chrome's parent PID is closed.
        session_id = "11111111-1111-4111-8111-111111111111"
        state = {"session_id": session_id, "pid": 12345, "debug_port": 55555}
        project = Mock(slug="demo")
        actions = BrowserSessionActions()
        handle = Mock()
        handle.pid = 12345
        actions._browser_process_handles = {session_id: handle}
        with (
            patch.object(actions, "_project", return_value=project),
            patch.object(actions, "_load_browser_state", return_value=state),
            patch.object(actions, "_refresh_browser_pid", return_value=state),
            patch.object(actions, "_browser_owned", return_value=True),
            patch.object(actions, "_request_browser_shutdown", return_value=True),
            patch.object(actions, "_pid_running", side_effect=[True, False, False, False]),
            patch.object(actions, "_discover_browser_pid", return_value=None) as discovery,
            patch.object(actions, "_write_browser_state"),
        ):
            result = actions.browser_stop({"project": "demo", "session_id": session_id})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(result.data["shutdown_method"], "cdp")
        self.assertEqual(handle.wait.call_count, 2)
        self.assertGreater(handle.wait.call_args_list[0].kwargs["timeout"], 20)
        self.assertEqual(handle.wait.call_args_list[1].kwargs["timeout"], 5)
        self.assertEqual(discovery.call_count, 3)


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

    def test_initial_navigation_accepts_same_origin_redirect(self):
        started = self.registry.execute(
            "browser.start",
            {
                "project": "demo",
                "url": self.url + "redirect",
                "headless": True,
                "wait_seconds": 15,
            },
        )
        self.assertTrue(started.ok, started.summary)
        self.session_id = started.data["session_id"]
        self.assertEqual(started.data["url"], self.url)
        self.assertTrue(started.data["ownership_valid"])

        blank = self.registry.execute(
            "browser.navigate",
            {"project": "demo", "session_id": self.session_id, "url": "about:blank"},
        )
        self.assertFalse(blank.ok)

    def test_blank_session_can_navigate_to_real_page(self):
        started = self.registry.execute(
            "browser.start", {"project": "demo", "url": "about:blank", "headless": True},
        )
        self.assertTrue(started.ok, started.summary)
        self.session_id = started.data["session_id"]
        navigated = self.registry.execute(
            "browser.navigate",
            {"project": "demo", "session_id": self.session_id, "url": self.url + "redirect"},
        )
        self.assertTrue(navigated.ok, navigated.summary)
        self.assertEqual(navigated.data["url"], self.url)
        self.assertEqual(navigated.data["requested_url"], self.url + "redirect")

    def test_explicit_restart_reuses_only_the_stopped_ordax_profile(self):
        started = self.registry.execute(
            "browser.start",
            {"project": "demo", "url": self.url, "headless": True, "wait_seconds": 15},
        )
        self.assertTrue(started.ok, started.summary)
        self.session_id = started.data["session_id"]
        profile = Path(started.data["profile"])
        original_pid = started.data["pid"]

        # A running profile must never spawn another Chromium against itself.
        denied = self.registry.execute(
            "browser.start", {"project": "demo", "resume_session_id": self.session_id},
        )
        self.assertFalse(denied.ok)
        self.assertIn("still running", denied.summary)

        before = self.registry.execute(
            "browser.snapshot", {"project": "demo", "session_id": self.session_id},
        )
        self.assertTrue(before.ok, before.summary)
        field = next(n for n in before.data["elements"] if n["tag"] == "input")
        button = next(n for n in before.data["elements"] if n["tag"] == "button")
        typed = self.registry.execute(
            "browser.type", {
                "project": "demo", "session_id": self.session_id,
                "node_id": field["id"], "text": "remembered",
            },
        )
        self.assertTrue(typed.ok, typed.summary)
        clicked = self.registry.execute(
            "browser.click", {
                "project": "demo", "session_id": self.session_id,
                "node_id": button["id"],
            },
        )
        self.assertTrue(clicked.ok, clicked.summary)

        stopped = self.registry.execute(
            "browser.stop", {"project": "demo", "session_id": self.session_id},
        )
        self.assertTrue(stopped.ok, stopped.summary)
        self.assertEqual(stopped.data["shutdown_method"], "cdp",
                         "stored login state requires normal Chromium shutdown")
        self.assertTrue(profile.is_dir(), "authorized profile should persist after stop")

        resumed = self.registry.execute(
            "browser.start", {
                "project": "demo", "resume_session_id": self.session_id,
                "url": self.url, "wait_seconds": 15,
            },
        )
        self.assertTrue(resumed.ok, resumed.summary)
        self.assertTrue(resumed.data["resumed"])
        self.assertEqual(resumed.data["session_id"], self.session_id)
        self.assertEqual(resumed.data["profile"], str(profile))
        after = self.registry.execute(
            "browser.snapshot", {"project": "demo", "session_id": self.session_id},
        )
        self.assertTrue(after.ok, after.summary)
        self.assertIn("Saved remembered", after.data["text"])

    def test_resume_rejects_invalid_or_missing_identity_without_new_profile(self):
        for value in ("wrong", "../other", "00000000-0000-0000-0000-000000000000"):
            with self.subTest(value=value):
                result = self.registry.execute(
                    "browser.start", {
                        "project": "demo", "resume_session_id": value,
                        "url": self.url, "headless": True,
                    },
                )
                self.assertFalse(result.ok)

    def test_two_owned_tabs_keep_independent_dom_state(self):
        started = self.registry.execute(
            "browser.start",
            {"project": "demo", "url": self.url, "headless": True, "wait_seconds": 15},
        )
        self.assertTrue(started.ok, started.summary)
        self.session_id = started.data["session_id"]
        primary_id = started.data["tab_id"]

        secondary = self.registry.execute(
            "browser.tab_open",
            {
                "project": "demo", "session_id": self.session_id,
                "url": self.url + "?second=1", "wait_seconds": 15,
            },
        )
        self.assertTrue(secondary.ok, secondary.summary)
        secondary_id = secondary.data["tab_id"]
        self.assertNotEqual(primary_id, secondary_id)

        listed = self.registry.execute(
            "browser.tabs", {"project": "demo", "session_id": self.session_id},
        )
        self.assertTrue(listed.ok, listed.summary)
        self.assertIn(primary_id, {entry["tab_id"] for entry in listed.data["tabs"]})
        self.assertIn(secondary_id, {entry["tab_id"] for entry in listed.data["tabs"]})
        self.assertEqual(sum(int(entry["primary"]) for entry in listed.data["tabs"]), 1)

        second_snapshot = self.registry.execute(
            "browser.snapshot",
            {"project": "demo", "session_id": self.session_id, "tab_id": secondary_id},
        )
        self.assertTrue(second_snapshot.ok, second_snapshot.summary)
        field = next(n for n in second_snapshot.data["elements"] if n["tag"] == "input")
        button = next(n for n in second_snapshot.data["elements"] if n["tag"] == "button")
        typed = self.registry.execute(
            "browser.type",
            {
                "project": "demo", "session_id": self.session_id,
                "tab_id": secondary_id, "node_id": field["id"], "text": "second",
            },
        )
        self.assertTrue(typed.ok, typed.summary)
        clicked = self.registry.execute(
            "browser.click",
            {
                "project": "demo", "session_id": self.session_id,
                "tab_id": secondary_id, "node_id": button["id"],
            },
        )
        self.assertTrue(clicked.ok, clicked.summary)

        secondary_after = self.registry.execute(
            "browser.snapshot",
            {"project": "demo", "session_id": self.session_id, "tab_id": secondary_id},
        )
        primary_after = self.registry.execute(
            "browser.snapshot", {"project": "demo", "session_id": self.session_id},
        )
        self.assertTrue(secondary_after.ok, secondary_after.summary)
        self.assertTrue(primary_after.ok, primary_after.summary)
        self.assertIn("Hello second", secondary_after.data["text"])
        self.assertIn("Idle", primary_after.data["text"])
        self.assertNotIn("Hello second", primary_after.data["text"])

        wrong = self.registry.execute(
            "browser.snapshot",
            {"project": "demo", "session_id": self.session_id,
             "tab_id": "00000000000000000000000000000000"},
        )
        self.assertFalse(wrong.ok, "unknown tabs must not fall back to another conversation")

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
