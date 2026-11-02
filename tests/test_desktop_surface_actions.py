from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from ordax_dev_agent.desktop_surface_actions import desktop_surface_status, desktop_surface_open


TOKEN = "d" * 64


class _NativeHost(BaseHTTPRequestHandler):
    response_payload = {
        "schema": "ordax.desktop-surface-state/1",
        "host": "ordax-desktop",
        "surfaceReady": True,
        "activeAppId": "system",
        "ageMs": 4,
    }
    def do_GET(self):
        if (self.path != "/__ordax/native/desktop-surface-state" or
                self.headers.get("X-OrdaX-Product-Bridge-Token") != TOKEN):
            self.send_error(401)
            return
        payload = json.dumps(self.response_payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload)

    command_payload = {
        "schema": "ordax.desktop-surface-command/1",
        "requestId": "f" * 32,
        "appId": "settings",
        "activeAppId": "settings",
        "state": "applied",
    }

    def do_POST(self):
        if (self.path != "/__ordax/native/desktop-surface-command" or
                self.headers.get("X-OrdaX-Product-Bridge-Token") != TOKEN):
            self.send_error(401)
            return
        supplied = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        if (supplied != {"schema": "ordax.desktop-surface-command/1", "appId": "settings"}):
            self.send_error(400)
            return
        response = json.dumps(self.command_payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(response)
    def log_message(self, *_args):
        pass


class DesktopSurfaceActionsTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _NativeHost)
        self.server.response_payload = _NativeHost.response_payload.copy()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.server.shutdown)
        self.environment = {
            "ORDAX_RUNTIME_HOST_PROFILE": "ordax-desktop",
            "ORDAX_DESKTOP_SURFACE_BRIDGE_URL":
                "http://127.0.0.1:" + str(self.server.server_port) +
                "/__ordax/native/desktop-surface-state",
            "ORDAX_DESKTOP_SURFACE_BRIDGE_TOKEN": TOKEN,
        }

    def invoke(self, payload=None, **overrides):
        values = dict(self.environment)
        values.update(overrides)
        with patch.dict(os.environ, values):
            return desktop_surface_status({} if payload is None else payload)

    def test_native_app_open_requires_actual_confirmed_surface_receipt(self):
        with patch.dict(os.environ, self.environment):
            result = desktop_surface_open({"appId": "settings"})
        self.assertTrue(result.ok)
        self.assertEqual(result.data, {
            "schema": "ordax.desktop-surface-command/1",
            "requestId": "f" * 32,
            "appId": "settings",
            "activeAppId": "settings",
            "state": "applied",
        })

    def test_uninstalled_and_windows_destinations_cannot_be_opened(self):
        with patch.dict(os.environ, self.environment):
            for data in ({}, {"appId": "files"}, {"appId": "shell"},
                         {"appId": "settings", "extra": True},
                         {"appId": "SYSTEM"}, {"appId": None}):
                self.assertFalse(desktop_surface_open(data).ok)
            self.assertFalse(desktop_surface_open({"appId": "system"}).ok)

    def test_false_receipts_never_count_as_success(self):
        original = _NativeHost.command_payload
        try:
            for fields in ({"activeAppId": "system"},
                           {"state": "pending"},
                           {"requestId": "bad"},
                           {"schema": "unverified"}):
                _NativeHost.command_payload = {**original, **fields}
                with patch.dict(os.environ, self.environment):
                    self.assertFalse(desktop_surface_open({"appId": "settings"}).ok)
        finally:
            _NativeHost.command_payload = original

    def test_windows_mcp_cannot_use_native_app_action(self):
        values = {**self.environment, "ORDAX_RUNTIME_HOST_PROFILE": "windows"}
        with patch.dict(os.environ, values):
            self.assertFalse(desktop_surface_open({"appId": "settings"}).ok)
    def test_real_native_status_is_read_only_and_device_scoped(self):
        result = self.invoke()
        self.assertTrue(result.ok)
        self.assertEqual(result.data, {
            "schema": "ordax.desktop-surface-state/1",
            "surfaceReady": True, "activeAppId": "system", "ageMs": 4,
        })
        self.assertNotIn("allowed_roots", result.data)

    def test_other_windows_agent_has_no_native_surface_authority(self):
        self.assertFalse(self.invoke(
            ORDAX_RUNTIME_HOST_PROFILE="windows").ok)

    def test_payload_and_untrusted_host_or_token_are_refused(self):
        self.assertFalse(self.invoke({"appId": "system"}).ok)
        self.assertFalse(self.invoke(
            ORDAX_DESKTOP_SURFACE_BRIDGE_URL="http://localhost:80/anything").ok)
        self.assertFalse(self.invoke(
            ORDAX_DESKTOP_SURFACE_BRIDGE_TOKEN="bad-token").ok)

    def test_native_surface_stale_and_malformed_reports_fail_closed(self):
        original = _NativeHost.response_payload
        try:
            for invalid in (
                {**original, "ageMs": 15001},
                {**original, "host": "windows-agent"},
                {**original, "activeAppId": "../../Windows"},
                {**original, "surfaceReady": False},
                {**original, "ageMs": True},
            ):
                _NativeHost.response_payload = invalid
                self.assertFalse(self.invoke().ok)
        finally:
            _NativeHost.response_payload = original


if __name__ == "__main__":
    unittest.main()
