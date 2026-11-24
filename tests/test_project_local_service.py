from __future__ import annotations

import hashlib
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

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
