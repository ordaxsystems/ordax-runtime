from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from ordax_dev_agent.desktop_intelligence_actions import desktop_intelligence_respond

TOKEN = "c" * 64


class NativeIntelligenceHost(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        if (self.path != "/__ordax/native/desktop-intelligence-request"
                or self.headers.get("X-OrdaX-Product-Bridge-Token") != TOKEN):
            self.send_error(401)
            return
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        self.server.seen.append(req)
        kind = getattr(self.server, "mode", "complete")
        if kind == "redirect":
            self.send_response(307)
            self.send_header("Location", "https://example.invalid/attack")
            self.end_headers()
            return
        if kind == "unknown":
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"outcome": "unknown"}).encode("utf-8"))
            return
        response = {
            "schema": "ordax.desktop-intelligence-reply/1",
            "requestId": "a" * 32,
            "outcome": "completed",
            "response": {
                "schema": "ordax.intelligence-response/1",
                "text": "Resposta do mesmo OS Intelligence utilizado pela Aura",
                "engineId": "shared-engine",
                "modelId": "shared-model",
                "authority": "none" if kind != "forged" else "admin",
            },
        }
        payload = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args):
        pass


class DesktopNativeIntelligenceActionsTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), NativeIntelligenceHost)
        self.server.seen = []
        self.server.mode = "complete"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join, 2)
        self.addCleanup(self.server.shutdown)
        self.context = patch.dict(os.environ, {
            "ORDAX_RUNTIME_HOST_PROFILE": "ordax-desktop",
            "ORDAX_DESKTOP_SURFACE_BRIDGE_URL":
                f"http://127.0.0.1:{self.server.server_port}/__ordax/native/desktop-surface-state",
            "ORDAX_DESKTOP_SURFACE_BRIDGE_TOKEN": TOKEN,
        }, clear=False)
        self.context.start()
        self.addCleanup(self.context.stop)

    def test_peer_consultation_uses_exact_current_native_grant_and_only_os_memory(self):
        result = desktop_intelligence_respond({
            "prompt": "O que Aura sabe deste projeto?", "intent": "summarize",
            "maxTokens": 96,
        })
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(result.data["text"],
                         "Resposta do mesmo OS Intelligence utilizado pela Aura")
        self.assertEqual(result.data["modelId"], "shared-model")
        self.assertEqual(result.data["authority"], "none")
        self.assertEqual(len(self.server.seen), 1)
        self.assertEqual(self.server.seen[0], {
            "schema": "ordax.desktop-intelligence-request/1",
            "request": {
                "intent": "summarize",
                "prompt": "O que Aura sabe deste projeto?",
                "context": [],
                "maxTokens": 96,
            },
        })

    def test_remote_cannot_supply_model_provider_context_identity_or_other_fields(self):
        for extra in ("context", "modelId", "subjectId", "spaceId", "provider", "url"):
            with self.subTest(extra=extra):
                value = desktop_intelligence_respond({"prompt": "hi", extra: "untrusted"})
                self.assertFalse(value.ok)
        self.assertEqual(self.server.seen, [])

    def test_absent_desktop_host_profile_fails_closed(self):
        os.environ["ORDAX_RUNTIME_HOST_PROFILE"] = "windows-agent"
        result = desktop_intelligence_respond({"prompt": "hello"})
        self.assertFalse(result.ok)
        self.assertEqual(self.server.seen, [])

    def test_unknown_outcome_does_not_claim_success_or_retry(self):
        self.server.mode = "unknown"
        result = desktop_intelligence_respond({"prompt": "sumario"})
        self.assertFalse(result.ok)
        self.assertEqual(result.data["outcome"], "unknown")
        self.assertEqual(len(self.server.seen), 1)

    def test_renderer_cannot_assign_tool_authority_through_response(self):
        self.server.mode = "forged"
        result = desktop_intelligence_respond({"prompt": "diagnose"})
        self.assertFalse(result.ok)
        self.assertEqual(result.data["outcome"], "unknown")

    def test_redirects_to_cloud_are_refused_without_external_request(self):
        self.server.mode = "redirect"
        result = desktop_intelligence_respond({"prompt": "do not egress"})
        self.assertFalse(result.ok)
        self.assertEqual(result.data["outcome"], "unknown")
        self.assertEqual(len(self.server.seen), 1)


if __name__ == "__main__":
    unittest.main()
