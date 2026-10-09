"""Real HTTP checks for the device Runtime status boundary (DNS rebinding)."""
from __future__ import annotations

import http.client
import json
import unittest

from ordax_dev_agent.status_server import start_status_server


class LocalRuntimeStatusSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls = 0

        def provider() -> dict:
            self.calls += 1
            return {
                "agent_version": "0.4.6",
                "runtime": {"state": "local-ready"},
                "actions": ["projects.list", "computer.windows"],
                "projects": [{"slug": "private", "path": "C:/secret/workspace"}],
            }

        self.server = start_status_server(provider, port=0)
        self.port = self.server.server_port
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path: str, hosts: list[str] | None = None, *, method: str = "GET"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.putrequest(method, path, skip_host=True)
            for host in hosts or []:
                connection.putheader("Host", host)
            connection.endheaders()
            response = connection.getresponse()
            body = response.read()
            return response.status, dict(response.getheaders()), body
        finally:
            connection.close()

    def test_exact_loopback_authority_serves_existing_dashboard_contract(self) -> None:
        for host in [f"127.0.0.1:{self.port}", f"localhost:{self.port}"]:
            for path in ("/", "/health", "/status", "/capabilities"):
                with self.subTest(host=host, path=path):
                    status, headers, body = self.request(path, [host])
                    self.assertEqual(status, 200)
                    self.assertEqual(headers.get("Cache-Control"), "no-store")
                    self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
                    self.assertEqual(headers.get("X-Frame-Options"), "DENY")
                    self.assertNotIn("Access-Control-Allow-Origin", headers)
                    if path == "/health":
                        self.assertEqual(json.loads(body), {
                            "ok": True, "state": "local-ready", "version": "0.4.6",
                        })
                    if path == "/status":
                        self.assertIn("private", body.decode())
                    if path == "/capabilities":
                        self.assertTrue(body.startswith(b"{"))

    def test_untrusted_hosts_never_reach_provider_on_any_endpoint(self) -> None:
        cases = [
            [], ["evil.example"],
            [f"evil.example:{self.port}"],
            ["localhost"], ["127.0.0.1"], ["127.0.0.1:0"],
            ["127.0.0.1:8766"], [f"127.0.0.1:{self.port}/"],
            [f"127.0.0.1:{self.port}@evil.example"],
            [f"evil.example@127.0.0.1:{self.port}"],
            [f"localhost.{self.port}"], [f"127.0.0.1.:{self.port}"],
            [f"[::1]:{self.port}"], [f"0.0.0.0:{self.port}"],
            [f"2130706433:{self.port}"],
            [f"127.0.0.1:{self.port}", f"evil.example:{self.port}"],
            [f"127.0.0.1:{self.port}", f"127.0.0.1:{self.port}"],
        ]
        for hosts in cases:
            for path in ("/", "/health", "/status", "/capabilities"):
                with self.subTest(hosts=hosts, path=path):
                    old_calls = self.calls
                    status, headers, body = self.request(path, hosts)
                    self.assertEqual(status, 403)
                    self.assertEqual(self.calls, old_calls)
                    self.assertEqual(body, b"")
                    self.assertEqual(headers.get("Cache-Control"), "no-store")
                    self.assertEqual(headers.get("Content-Length"), "0")
                    self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_untrusted_methods_do_not_reach_provider(self) -> None:
        for method in ("POST", "PUT", "DELETE", "OPTIONS", "HEAD"):
            with self.subTest(method=method):
                old_calls = self.calls
                status, headers, body = self.request(
                    "/status", [f"127.0.0.1:{self.port}"], method=method,
                )
                self.assertNotEqual(status, 200)
                self.assertEqual(self.calls, old_calls)

    def test_bind_validation_rejects_external_interfaces_before_listening(self) -> None:
        for host in ("0.0.0.0", "::", "::1", "", "localhost", "192.168.0.1"):
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, "bind"):
                start_status_server(lambda: {}, host=host, port=0)
        for port in (-1, 65536, True, "8765", 1.5, None):
            with self.subTest(port=port), self.assertRaisesRegex(ValueError, "port"):
                start_status_server(lambda: {}, port=port)
        self.assertEqual(self.server.server_address[0], "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
