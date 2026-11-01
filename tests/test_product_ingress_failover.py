"""Network failover contracts: same Worker, no ambiguous HTTP replay."""
import unittest
import uuid

import httpx

from ordax_dev_agent.product_ingress import TrustedProductIngress, inspect_pinned_https_ingress
from ordax_dev_agent.product_device_control_plane import ProductDeviceControlPlane
from ordax_dev_agent.product_central_session import ProductCentralSessionClient
from ordax_dev_agent.remote_protocol import TransientDeliveryError


PRIMARY = "https://mcp.ordax.com.br"
RESTRICTED = "https://ordax-mcp.ordaxsystems.workers.dev"
PATH = "/v3/device/transport/report"


class TrustedProductIngressTests(unittest.TestCase):
    def test_explicit_https_probe_is_get_only_and_never_claims_readiness(self):
        calls = []
        def send(request):
            calls.append(request)
            return httpx.Response(
                200 if request.url.host == "mcp.ordax.com.br" else 404,
                json={"ok": True, "grants_issued": True},
            )
        with httpx.Client(transport=httpx.MockTransport(send)) as client:
            observation = inspect_pinned_https_ingress(client=client)
        self.assertEqual(observation["schema"], "ordax.product-mcp-https-observation/1")
        self.assertEqual(observation["primary"], {
            "transport": "https_responded", "httpStatus": 200,
        })
        self.assertEqual(observation["restricted"], {
            "transport": "https_responded", "httpStatus": 404,
        })
        self.assertIs(observation["authenticated"], False)
        self.assertIs(observation["mcpReady"], False)
        self.assertIs(observation["grantsAuthorized"], False)
        self.assertEqual(
            [str(req.url) for req in calls],
            [PRIMARY + "/v3/device/setup", RESTRICTED + "/v3/device/setup"],
        )
        for req in calls:
            self.assertEqual(req.method, "GET")
            self.assertNotIn("authorization", req.headers)
            self.assertNotIn("x-ordax-device-token", req.headers)
            self.assertFalse(req.content)
        self.assertEqual(len(calls), 2)

    def test_https_probe_reports_failure_without_replaying_or_granting(self):
        calls = []
        def send(request):
            calls.append(str(request.url))
            if request.url.host == "mcp.ordax.com.br":
                raise httpx.ConnectTimeout("tcp unavailable", request=request)
            return httpx.Response(503)
        with httpx.Client(transport=httpx.MockTransport(send)) as client:
            observation = inspect_pinned_https_ingress(client=client)
        self.assertEqual(observation["primary"], {
            "transport": "unavailable", "httpStatus": None,
        })
        # A 503 still proves an HTTPS response; it does NOT prove MCP readiness.
        self.assertEqual(observation["restricted"], {
            "transport": "https_responded", "httpStatus": 503,
        })
        self.assertEqual(calls, [
            PRIMARY + "/v3/device/setup",
            RESTRICTED + "/v3/device/setup",
        ])
        self.assertIs(observation["mcpReady"], False)

    def test_https_probe_has_no_fallback_on_tls_failure(self):
        calls = []
        def send(request):
            calls.append(str(request.url))
            raise httpx.ConnectError("TLS certificate rejected", request=request)
        with httpx.Client(transport=httpx.MockTransport(send)) as client:
            observation = inspect_pinned_https_ingress(client=client)
        self.assertEqual(observation["primary"]["transport"], "unavailable")
        self.assertEqual(observation["restricted"]["transport"], "unavailable")
        self.assertEqual(len(calls), 2)
        self.assertIs(observation["grantsAuthorized"], False)

    def test_preconnect_failure_uses_restricted_same_worker_and_reprobes_primary(self):
        now = [100.0]
        ingress = TrustedProductIngress(PRIMARY, monotonic=lambda: now[0], reprobe_seconds=120)
        urls = []
        primary_down = [True]

        def send(url):
            urls.append(url)
            if url.startswith(PRIMARY) and primary_down[0]:
                raise httpx.ConnectTimeout("connect unavailable")
            return url

        self.assertEqual(RESTRICTED + PATH, ingress.request(PATH, send))
        self.assertEqual([PRIMARY + PATH, RESTRICTED + PATH], urls)

        urls.clear()
        self.assertEqual(RESTRICTED + PATH, ingress.request(PATH, send))
        self.assertEqual([RESTRICTED + PATH], urls)

        now[0] = 221.0
        primary_down[0] = False
        urls.clear()
        self.assertEqual(PRIMARY + PATH, ingress.request(PATH, send))
        self.assertEqual([PRIMARY + PATH], urls)
        urls.clear()
        self.assertEqual(PRIMARY + PATH, ingress.request(PATH, send))
        self.assertEqual([PRIMARY + PATH], urls)

    def test_read_timeout_is_ambiguous_and_never_repeats_report(self):
        urls = []
        ingress = TrustedProductIngress(PRIMARY)

        def send(url):
            urls.append(url)
            raise httpx.ReadTimeout("response lost after delivery")

        with self.assertRaises(httpx.ReadTimeout):
            ingress.request(PATH, send)
        self.assertEqual([PRIMARY + PATH], urls)

    def test_http_503_and_authorization_errors_never_switch_ingress(self):
        for code in (401, 403, 503, 504):
            urls = []
            ingress = TrustedProductIngress(PRIMARY)
            def send(url):
                urls.append(url)
                return code
            self.assertEqual(code, ingress.request(PATH, send))
            self.assertEqual([PRIMARY + PATH], urls)

    def test_only_pinned_canonical_origin_and_known_paths_can_failover(self):
        for origin, path in (
            ("https://custom.invalid", PATH),
            (PRIMARY, "/mcp"),
            (PRIMARY, "/v3/device/transport/unexpected"),
            (PRIMARY, "/v3/product/action"),
        ):
            urls = []
            ingress = TrustedProductIngress(origin)
            def send(url):
                urls.append(url)
                raise httpx.ConnectError("connect unavailable")
            with self.assertRaises(httpx.ConnectError):
                ingress.request(path, send)
            self.assertEqual([origin + path], urls)

    def test_alternate_connection_failure_probes_primary_without_mutating_identity(self):
        now = [5.0]
        ingress = TrustedProductIngress(PRIMARY, monotonic=lambda: now[0])
        calls = []
        def send(url):
            calls.append(url)
            if len(calls) == 1 or len(calls) == 3:
                raise httpx.ConnectError("connect unavailable")
            return url
        self.assertEqual(RESTRICTED + PATH, ingress.request(PATH, send))
        now[0] = 6.0
        self.assertEqual(PRIMARY + PATH, ingress.request(PATH, send))
        self.assertEqual([
            PRIMARY + PATH, RESTRICTED + PATH,
            RESTRICTED + PATH, PRIMARY + PATH,
        ], calls)

    def test_product_transport_uses_same_payload_only_when_connect_fails(self):
        seen = []
        def handler(request):
            seen.append((str(request.url), request.content))
            if request.url.host == "mcp.ordax.com.br":
                raise httpx.ConnectTimeout("connect unavailable", request=request)
            return httpx.Response(200, json={"ok": True, "lease_expires_at": "2030-01-01T00:00:00Z"})

        control = ProductDeviceControlPlane.__new__(ProductDeviceControlPlane)
        control.base_http_url = PRIMARY
        control._trusted_ingress = TrustedProductIngress(PRIMARY)
        # __new__ skips constructor; reproduce the Windows credential mode.
        control._native_credential_provider = None
        with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
            control.http = client
            response = control._post("lease", {"execution_epoch": 1})
        self.assertTrue(response["ok"])
        self.assertEqual(2, len(seen))
        self.assertEqual(seen[0][1], seen[1][1])

    def test_transport_read_timeout_does_not_send_duplicate_claim(self):
        seen = []
        def handler(request):
            seen.append(str(request.url))
            raise httpx.ReadTimeout("server may have accepted claim", request=request)

        control = ProductDeviceControlPlane.__new__(ProductDeviceControlPlane)
        control.base_http_url = PRIMARY
        control._trusted_ingress = TrustedProductIngress(PRIMARY)
        # __new__ skips constructor; reproduce the Windows credential mode.
        control._native_credential_provider = None
        with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
            control.http = client
            with self.assertRaises(TransientDeliveryError):
                control._post("claim", {"agent_instance_id": str(uuid.uuid4())})
        self.assertEqual([PRIMARY + "/v3/device/transport/claim"], seen)

    def test_attested_session_confirms_over_same_restricted_worker(self):
        challenge = str(uuid.uuid4())
        device_id = str(uuid.uuid4())
        seen = []
        def handler(request):
            seen.append((str(request.url), request.method, request.headers.get("x-ordax-device-token")))
            if request.url.host == "mcp.ordax.com.br":
                raise httpx.ConnectTimeout("connect unavailable", request=request)
            if request.method == "GET":
                return httpx.Response(200, json={"ok": True, "pending": True, "challenge_id": challenge})
            return httpx.Response(200, json={"ok": True, "status": "bound", "device_id": device_id})

        with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
            session = ProductCentralSessionClient(
                PRIMARY, device_id, lambda: "z" * 64, http=client,
            )
            status = session.tick(now=5)
        self.assertTrue(status.confirmed)
        self.assertEqual([PRIMARY, RESTRICTED, RESTRICTED],
                         [url.split("/v3/")[0] for url, _, _ in seen])
        self.assertEqual(["z" * 64] * 3, [token for _, _, token in seen])


if __name__ == "__main__":
    unittest.main()
