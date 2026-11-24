from __future__ import annotations

import hashlib
import io
import base64
import threading
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from ordax_dev_agent.artifact_actions import ArtifactActions
from ordax_dev_agent.project_local_service import (
    LocalServiceError, LocalServicePolicy, LocalServiceRoute, read_local_service_batch,
)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.server.calls.append(self.path)
        path = urlsplit(self.path).path
        if path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/json")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/unauthorized":
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b'{"ok":1}'
        media = "application/json"
        if path == "/image":
            body = b"RIFF\x04\x00\x00\x00WEBP"
            media = "image/webp"
        if path.startswith("/thumb/") or path in ("/visual", "/wrong-format"):
            from PIL import Image
            buffer = io.BytesIO()
            color = "blue" if parse_qs(urlsplit(self.path).query).get("id") == ["2"] else "red"
            Image.new("RGB", (96, 64), color).save(buffer, format="PNG")
            body = buffer.getvalue()
            media = "image/webp" if path == "/wrong-format" else "image/png"
        if path == "/large":
            body = b"x" * 4096
        if path == "/bad-media":
            media = "text/html"
        self.send_response(200)
        self.send_header("Content-Type", media)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LocalServiceTransportTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.calls = []
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.thread.join, 3)
        self.addCleanup(self.server.shutdown)
        json_media = frozenset({"application/json"})
        self.policy = LocalServicePolicy("127.0.0.1", self.server.server_port, (
            LocalServiceRoute("/json", frozenset({"page"}), json_media),
            LocalServiceRoute("/image", frozenset({"id"}), frozenset({"image/webp"})),
            LocalServiceRoute("/visual", frozenset({"id"}), frozenset({"image/png"})),
            LocalServiceRoute("/thumb/{id}", frozenset({"w"}), frozenset({"image/png"})),
            LocalServiceRoute("/wrong-format", frozenset({"id"}), frozenset({"image/webp"})),
            *tuple(LocalServiceRoute(path, frozenset(), json_media)
                   for path in ("/redirect", "/large", "/bad-media", "/unauthorized")),
        ))

    def test_reads_json_and_images_in_one_bounded_batch_without_files(self):
        values = read_local_service_batch(self.policy, [
            {"route": "/json", "query": {"page": "1&other=2"}},
            {"route": "/image", "query": {"id": 42}},
        ])
        self.assertEqual([v.media_type for v in values], ["application/json", "image/webp"])
        self.assertEqual(values[0].body, b'{"ok":1}')
        self.assertEqual(values[1].sha256, hashlib.sha256(values[1].body).hexdigest())
        self.assertEqual(parse_qs(urlsplit(self.server.calls[0]).query), {"page": ["1&other=2"]})
        self.assertEqual(len(self.server.calls), 2)

    def test_local_service_sheet_uses_shared_renderer_and_response_identity(self):
        from PIL import Image
        host = ArtifactActions()
        result = host.local_service_image_sheet(self.policy, [
            {"route": "/visual", "query": {"id": 1}},
            {"route": "/visual", "query": {"id": 2}},
            {"route": "/visual", "query": {"id": 1}},
        ], max_width=800, max_height=600)
        self.assertTrue(result.ok, result.summary)
        data = result.data
        self.assertEqual("local_service_response", data["source_kind"])
        self.assertEqual(3, data["image_count"])
        self.assertEqual(2, data["unique_image_count"])
        self.assertEqual([0, 1, 2], [item["request_index"] for item in data["items"]])
        self.assertEqual(["I001", "I002", "I001"], [item["tile_id"] for item in data["items"]])
        self.assertEqual(data["items"][0]["response_sha256"], data["items"][2]["response_sha256"])
        self.assertTrue(all("source_sha256" not in item and "project_artifact_path" not in item
                            for item in data["items"]))
        self.assertTrue(data["visual_candidates"]["review_only"])
        with Image.open(io.BytesIO(base64.b64decode(data["base64"]))) as sheet:
            self.assertEqual("JPEG", sheet.format)
            self.assertEqual(tuple(data["thumbnail_size"]), sheet.size)
        self.assertEqual(len(self.server.calls), 3)

    def test_local_service_sheet_rejects_non_image_and_mislabeled_image(self):
        host = ArtifactActions()
        for route in ("/json", "/wrong-format", "/image"):
            result = host.local_service_image_sheet(self.policy, [{"route": route}])
            self.assertFalse(result.ok)
            self.assertNotIn("base64", result.data)

    def test_public_artifact_preview_cannot_inherit_local_service_authority(self):
        host = ArtifactActions()
        for field in ("local_service_requests", "local_service_policy", "service_responses"):
            result = host.artifact_preview({field: []})
            self.assertFalse(result.ok)
            self.assertIn("separate verified authority", result.summary)
        self.assertEqual(self.server.calls, [])

    def test_numeric_route_template_only_accepts_one_positive_integer_segment(self):
        for route in ("/thumb/0", "/thumb/-1", "/thumb/1/2", "/thumb/1/../2",
                      "/thumb/%31", "/thumb/abc", "/thumb/{id}"):
            with self.assertRaisesRegex(LocalServiceError, "route_denied"):
                read_local_service_batch(self.policy, [{"route": route}])
        self.assertEqual([], self.server.calls)
        response = read_local_service_batch(self.policy, [{"route": "/thumb/123", "query": {"w": 256}}])[0]
        self.assertEqual("/thumb/123", response.route)
        self.assertEqual(["/thumb/123?w=256"], self.server.calls)

    def test_attested_project_config_is_required_and_reloaded_for_every_batch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            host = ArtifactActions()
            project = SimpleNamespace(slug="project-123", root=root)
            host.config = SimpleNamespace(state_dir=root)
            host._project = lambda payload: project
            host._project_execution_local = SimpleNamespace(project=None)
            payload = {"project": project.slug, "service_id": "curation",
                       "requests": [{"route": "/thumb/42", "query": {"w": 256}}]}
            self.assertFalse(host.artifact_local_service_preview(payload).ok)
            host._project_execution_local.project = project
            self.assertFalse(host.artifact_local_service_preview(payload).ok)
            settings = root / "agent-settings.json"
            entry = {"root": str(root), "services": {"curation": {
                "host": "127.0.0.1", "port": self.server.server_port,
                "routes": [{"path": "/thumb/{id}", "query_fields": ["w"],
                            "media_types": ["image/png"]}],
            }}}
            data = {"project_local_services": {project.slug: entry}}
            settings.write_text(json.dumps(data), encoding="utf-8")
            self.assertFalse(host.artifact_local_service_preview({**payload, "host": "127.0.0.1"}).ok)
            self.assertFalse(host.artifact_local_service_preview({**payload, "project": "project-999"}).ok)
            self.assertFalse(host.artifact_local_service_preview({**payload, "max_bytes": 1}).ok)
            self.assertEqual([], self.server.calls)
            result = host.artifact_local_service_preview(payload)
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(1, result.data["image_count"])
            self.assertEqual(["/thumb/42?w=256"], self.server.calls)
            entry["root"] = str(root / "another")
            settings.write_text(json.dumps(data), encoding="utf-8")
            self.assertFalse(host.artifact_local_service_preview(payload).ok)
            settings.write_text("{}", encoding="utf-8")
            self.assertFalse(host.artifact_local_service_preview(payload).ok)
            self.assertEqual(["/thumb/42?w=256"], self.server.calls)

    def test_rejects_non_loopback_and_model_supplied_origin_or_method_before_io(self):
        for policy in (replace(self.policy, host="localhost"),
                       replace(self.policy, host="192.168.1.1")):
            with self.assertRaisesRegex(LocalServiceError, "origin_denied"):
                read_local_service_batch(policy, [{"route": "/json"}])
        for request in ({"route": "/json", "method": "POST"},
                        {"route": "/json", "url": "http://127.0.0.1:9"},
                        {"route": "http://127.0.0.1:9/json"},
                        {"route": "/json", "query": {"token": "secret"}}):
            with self.assertRaises(LocalServiceError):
                read_local_service_batch(self.policy, [{"route": "/json"}, request])
        self.assertEqual(self.server.calls, [])

    def test_denies_redirects_and_host_authorization_errors_without_retry(self):
        with self.assertRaisesRegex(LocalServiceError, "http_status_302"):
            read_local_service_batch(self.policy, [{"route": "/redirect"}])
        self.assertEqual(self.server.calls, ["/redirect"])
        with self.assertRaisesRegex(LocalServiceError, "http_status_403"):
            read_local_service_batch(self.policy, [{"route": "/unauthorized"}])
        self.assertEqual(self.server.calls, ["/redirect", "/unauthorized"])

    def test_enforces_total_byte_budget_and_mime_policy(self):
        with self.assertRaisesRegex(LocalServiceError, "byte_budget_exceeded"):
            read_local_service_batch(self.policy, [{"route": "/large"}], max_bytes=128)
        with self.assertRaisesRegex(LocalServiceError, "byte_budget_exceeded"):
            read_local_service_batch(self.policy, [{"route": "/json"}] * 2, max_bytes=8)
        with self.assertRaisesRegex(LocalServiceError, "media_type_denied"):
            read_local_service_batch(self.policy, [{"route": "/bad-media"}])

    def test_batch_size_and_route_policy_fail_closed(self):
        with self.assertRaisesRegex(LocalServiceError, "batch_size_invalid"):
            read_local_service_batch(self.policy, [{"route": "/json"}] * 9)
        invalid = replace(self.policy, routes=(
            LocalServiceRoute("/../json", frozenset(), frozenset({"application/json"})),
        ))
        with self.assertRaisesRegex(LocalServiceError, "route_policy_invalid"):
            read_local_service_batch(invalid, [{"route": "/json"}])
        self.assertEqual(self.server.calls, [])


if __name__ == "__main__":
    unittest.main()
