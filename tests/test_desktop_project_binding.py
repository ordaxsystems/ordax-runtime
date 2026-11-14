from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from ordax_dev_agent.desktop_project_binding import read_desktop_project_links


TOKEN = "d" * 64
PROJECT = "33333333-3333-4333-8333-333333333333"
SPACE = "44444444-4444-4444-8444-444444444444"


class _NativeHost(BaseHTTPRequestHandler):
    payload = {
        "schema": "ordax.desktop-project-links/1",
        "host": "ordax-desktop",
        "persistence": "device",
        "links": [{
            "localProjectId": "project-3",
            "cloudProjectId": PROJECT,
            "spaceId": SPACE,
        }],
    }

    def do_GET(self):
        if (
            self.path != "/__ordax/native/desktop-project-links"
            or self.headers.get("X-OrdaX-Product-Bridge-Token") != TOKEN
        ):
            self.send_error(401)
            return
        payload = json.dumps(type(self).payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


class DesktopProjectBindingTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _NativeHost)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join, 2)
        self.environment = {
            "ORDAX_RUNTIME_HOST_PROFILE": "ordax-desktop",
            "ORDAX_DESKTOP_SURFACE_BRIDGE_URL":
                "http://127.0.0.1:" + str(self.server.server_port)
                + "/__ordax/native/desktop-surface-state",
            "ORDAX_DESKTOP_SURFACE_BRIDGE_TOKEN": TOKEN,
        }

    def invoke(self, **overrides):
        values = dict(self.environment)
        values.update(overrides)
        with patch.dict(os.environ, values, clear=True):
            return read_desktop_project_links()

    def test_reads_only_explicit_durable_os_project_links(self):
        self.assertEqual(self.invoke(), _NativeHost.payload)

    def test_session_or_extra_authority_fields_are_rejected(self):
        original = _NativeHost.payload
        try:
            for payload in (
                {**original, "persistence": "session"},
                {**original, "repository_full_name": "ordaxsystems/guess"},
                {**original, "links": [{
                    **original["links"][0], "path": "C:/untrusted",
                }]},
                {**original, "links": [{
                    **original["links"][0], "localProjectId": "repo-slug",
                }]},
                {**original, "links": [
                    original["links"][0],
                    {**original["links"][0], "localProjectId": "project-4"},
                ]},
            ):
                _NativeHost.payload = payload
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    self.invoke()
        finally:
            _NativeHost.payload = original

    def test_only_exact_per_launch_loopback_bridge_is_accepted(self):
        for url in (
            "http://localhost:1234/__ordax/native/desktop-surface-state",
            "http://127.0.0.1:1234/__ordax/native/desktop-project-links",
            "https://127.0.0.1:1234/__ordax/native/desktop-surface-state",
            "http://127.0.0.1:1234/__ordax/native/desktop-surface-state?x=1",
        ):
            with self.subTest(url=url):
                self.assertIsNone(self.invoke(ORDAX_DESKTOP_SURFACE_BRIDGE_URL=url))
        self.assertIsNone(self.invoke(ORDAX_RUNTIME_HOST_PROFILE="windows"))
        self.assertIsNone(self.invoke(ORDAX_DESKTOP_SURFACE_BRIDGE_TOKEN="bad"))


if __name__ == "__main__":
    unittest.main()
