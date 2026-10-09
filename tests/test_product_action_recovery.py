from __future__ import annotations

import asyncio
import gzip
import json
import os
import unittest
from unittest.mock import patch

import httpx

from ordax_dev_agent import product_mcp_server as server
from ordax_dev_agent.product_remote_client import (
    ProductActionWaitTimeout, ProductRemoteClient, ProductRemoteError,
)


class TrackedStream(httpx.SyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
        self.reads = 0

    def __iter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    def close(self):
        self.closed = True


class ProductActionRecoveryTests(unittest.TestCase):
    def client(self, handler, **http_options):
        http = httpx.Client(transport=httpx.MockTransport(handler), **http_options)
        self.addCleanup(http.close)
        return ProductRemoteClient("https://control.example.test", http=http)

    def test_wrong_task_unknown_state_and_non_string_state_stop_on_first_read(self):
        for task in (
            {"request_id": "req-other", "status": "succeeded"},
            {"request_id": "req-1", "status": "thinking"},
            {"request_id": "req-1", "status": ["running"]},
            {"request_id": "req-1"},
            {"status": "failed"},
        ):
            with self.subTest(task=task):
                calls = []
                client = self.client(lambda request: (
                    calls.append(request.method) or httpx.Response(200, json={"ok": True, "action": task})
                ))
                with self.assertRaises(ProductRemoteError) as caught:
                    client.wait_action("jwt", "req-1")
                self.assertEqual(caught.exception.request_id, "req-1")
                self.assertEqual(calls, ["GET"])

    def test_every_published_state_is_accepted_and_result_is_preserved(self):
        for status in ("queued", "leased", "running", "succeeded", "failed", "cancelled"):
            with self.subTest(status=status):
                task = {"request_id": "req-1", "status": status, "result": {"content": "user text"}}
                client = self.client(lambda request: httpx.Response(200, json={"ok": True, "action": task}))
                self.assertEqual(client.action("jwt", "req-1"), task)

    def test_http_denial_and_transport_failure_preserve_accepted_id(self):
        for response in (
            httpx.Response(403, json={"ok": False, "error": "product_owner_auth_ineligible"}),
            httpx.Response(200, json={"ok": False, "error": "not_found", "action": {
                "request_id": "req-1", "status": "succeeded",
            }}),
        ):
            client = self.client(lambda request: response)
            with self.assertRaises(ProductRemoteError) as caught:
                client.action("jwt", "req-1")
            self.assertEqual(caught.exception.request_id, "req-1")
        def disconnected(request):
            raise httpx.ReadTimeout("private transport detail", request=request)
        with self.assertRaises(ProductRemoteError) as caught:
            self.client(disconnected).action("jwt", "req-1")
        self.assertEqual(caught.exception.request_id, "req-1")
        self.assertNotIn("private", str(caught.exception))

    def test_path_identifiers_are_validated_before_network_io(self):
        calls = []
        client = self.client(lambda request: calls.append(request))
        for request_id in ("../device-links/x", "req?other=1", "req#fragment", "req%2fother", "", "x" * 129, None):
            with self.subTest(request_id=request_id), self.assertRaises(ValueError):
                client.action("jwt", request_id)
        self.assertEqual(calls, [])

    def test_malformed_ack_or_lost_submission_has_unknown_acceptance_without_retry(self):
        for body in ({"ok": True}, {"ok": True, "request_id": "../private"}, {"ok": False, "error": "unavailable"}):
            calls = []
            client = self.client(lambda request: (
                calls.append(request.method) or httpx.Response(503 if body.get("ok") is False else 202, json=body)
            ))
            with self.assertRaises(ProductRemoteError) as caught:
                client.submit_action("jwt", device_id="dev-1", action="computer.click")
            self.assertTrue(caught.exception.acceptance_unknown)
            self.assertIsNone(caught.exception.request_id)
            self.assertEqual(calls, ["POST"])
        def disconnected(request):
            raise httpx.ReadTimeout("lost ACK", request=request)
        with self.assertRaises(ProductRemoteError) as caught:
            self.client(disconnected).submit_action("jwt", device_id="dev-1", action="computer.click")
        self.assertTrue(caught.exception.acceptance_unknown)

    def test_explicit_grant_denial_does_not_claim_uncertain_acceptance(self):
        client = self.client(lambda request: httpx.Response(403, json={"ok": False, "error": "product_grant_not_resolved"}))
        with self.assertRaises(ProductRemoteError) as caught:
            client.submit_action("jwt", device_id="dev-1", action="computer.click")
        self.assertFalse(caught.exception.acceptance_unknown)

    def test_invalid_json_utf8_mime_and_raw_error_detail_are_rejected(self):
        for content, mime in (
            (b'{"ok":true,"targets":[],"value":NaN}', "application/json"),
            (b'{"ok":true,"targets":[],"value":"\xff"}', "application/json"),
            (b'{"ok":true,"targets":[]}', "text/html"),
            (b'{"ok":true,"targets":' + b"[" * 2000 + b"]" * 2000 + b"}", "application/json"),
        ):
            client = self.client(lambda request: httpx.Response(200, content=content, headers={"content-type": mime}))
            with self.assertRaises(ProductRemoteError):
                client.targets("jwt")
        client = self.client(lambda request: httpx.Response(500, json={"ok": False, "error": "SQL secret private details"}))
        with self.assertRaises(ProductRemoteError) as caught:
            client.targets("jwt")
        self.assertEqual(caught.exception.error_code, "product_remote_error")
        self.assertNotIn("secret", str(caught.exception))

    def test_published_inline_artifact_capacity_survives_transport_bound(self):
        task = {"request_id": "req-1", "status": "succeeded", "result": {
            "base64": "A" * (4 * ((2 * 1024 * 1024 + 2) // 3)),
        }}
        client = self.client(lambda request: httpx.Response(200, json={"ok": True, "action": task}))
        self.assertEqual(client.action("jwt", "req-1"), task)

    def test_oversized_stream_is_stopped_and_closed_without_reading_tail(self):
        stream = TrackedStream([b"x" * 65536] * 100)
        client = self.client(lambda request: httpx.Response(200, stream=stream, headers={"content-type": "application/json"}))
        with self.assertRaises(ProductRemoteError):
            client.targets("jwt")
        self.assertTrue(stream.closed)
        self.assertLess(stream.reads, 100)

    def test_compressed_json_is_decoded_once_and_expanded_limit_is_enforced(self):
        body = gzip.compress(b'{"ok":true,"targets":[]}')
        client = self.client(lambda request: httpx.Response(200, content=body, headers={
            "content-type": "application/json", "content-encoding": "gzip",
        }))
        self.assertEqual(client.targets("jwt"), [])
        large = gzip.compress(b'{"ok":true,"targets":[],"padding":"' + b"x" * (4 * 1024 * 1024) + b'"}')
        client = self.client(lambda request: httpx.Response(200, content=large, headers={
            "content-type": "application/json", "content-encoding": "gzip",
        }))
        with self.assertRaises(ProductRemoteError):
            client.targets("jwt")

    def test_redirect_is_not_followed_even_with_injected_client_option(self):
        calls = []
        def redirect(request):
            calls.append(str(request.url))
            return httpx.Response(307, json={"ok": True, "targets": []}, headers={"location": "https://other.test/"})
        with self.assertRaises(ProductRemoteError):
            self.client(redirect, follow_redirects=True).targets("jwt")
        self.assertEqual(calls, ["https://control.example.test/v3/product/targets"])

    def test_wait_deadline_retains_id_and_bounds_sleep_and_http_timeout(self):
        requests = []
        clock = [0.0]
        def response(request):
            requests.append(request)
            clock[0] = 0.95
            return httpx.Response(200, json={
                "ok": True, "action": {"request_id": "req-1", "status": "running"},
            })
        def advance(seconds):
            clock[0] += seconds
        client = self.client(response)
        with patch("ordax_dev_agent.product_remote_client.time.monotonic", side_effect=lambda: clock[0]), patch(
            "ordax_dev_agent.product_remote_client.time.sleep", side_effect=advance,
        ) as sleep, self.assertRaises(ProductActionWaitTimeout) as caught:
            client.wait_action("jwt", "req-1", timeout_seconds=1)
        self.assertEqual(caught.exception.request_id, "req-1")
        self.assertIsInstance(caught.exception, TimeoutError)
        self.assertAlmostEqual(sleep.call_args.args[0], 0.05)
        self.assertAlmostEqual(requests[0].extensions["timeout"]["read"], 1.0)

    def test_slow_small_chunks_cannot_keep_the_wait_alive_past_deadline(self):
        clock = [0.0]
        class SlowStream(TrackedStream):
            def __iter__(self):
                for chunk in self.chunks:
                    self.reads += 1
                    clock[0] += 0.2
                    yield chunk
        stream = SlowStream([b" "] * 50)
        client = self.client(lambda request: httpx.Response(200, stream=stream, headers={"content-type": "application/json"}))
        with patch("ordax_dev_agent.product_remote_client.time.monotonic", side_effect=lambda: clock[0]), self.assertRaises(
            ProductActionWaitTimeout
        ) as caught:
            client.wait_action("jwt", "req-1", timeout_seconds=1)
        self.assertEqual(caught.exception.request_id, "req-1")
        self.assertTrue(stream.closed)
        self.assertEqual(stream.reads, 5)

    def test_nonfinite_timeouts_are_rejected_before_any_request(self):
        client = self.client(lambda request: self.fail("Unexpected request"))
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    client.wait_action("jwt", "req-1", timeout_seconds=value)
                with self.assertRaises(ValueError):
                    client.wait_action("jwt", "req-1", poll_interval_seconds=value)
                with self.assertRaises(ValueError):
                    ProductRemoteClient("https://control.example.test", timeout_seconds=value)


class ProductMcpRecoveryFlowTests(unittest.TestCase):
    def call_tool(self, name, arguments):
        result = asyncio.run(server.mcp.call_tool(name, arguments))
        if isinstance(result, tuple):
            return result[1]
        return result if isinstance(result, dict) else json.loads(result[0].text)

    def invoke_with_transport(self, handler):
        http = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(http.close)
        self.env = patch.dict(os.environ, {
            "ORDAX_PRODUCT_ACCESS_TOKEN": "jwt-fixture",
            "ORDAX_PRODUCT_CONTROL_PLANE_URL": "https://control.example.test",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        factory = lambda url: ProductRemoteClient(url, http=http)
        client_patch = patch.object(server, "ProductRemoteClient", factory)
        client_patch.start()
        self.addCleanup(client_patch.stop)

    def test_accepted_action_is_recovered_by_get_without_second_post(self):
        calls = []
        def handler(request):
            calls.append((request.method, request.url.path, request.headers["authorization"]))
            if request.method == "POST":
                return httpx.Response(202, json={"ok": True, "request_id": "req-1"})
            if len(calls) == 2:
                return httpx.Response(503, json={"ok": False, "error": "unavailable"})
            return httpx.Response(200, json={"ok": True, "action": {
                "request_id": "req-1", "status": "succeeded", "result": {"ok": True},
            }})
        self.invoke_with_transport(handler)
        receipt = self.call_tool("computer_click", {"device_id": "dev-1", "x": 1, "y": 2})
        self.assertFalse(receipt["ok"])
        self.assertTrue(receipt["completion_unknown"])
        self.assertEqual(receipt["request_id"], "req-1")
        self.assertNotIn("status", receipt)
        self.assertIn("product_action_status", receipt["next_step"])
        recovered = self.call_tool("product_action_status", {"request_id": receipt["request_id"]})
        self.assertEqual(recovered["status"], "succeeded")
        self.assertEqual([method for method, _, _ in calls], ["POST", "GET", "GET"])
        self.assertTrue(all(token == "Bearer jwt-fixture" for _, _, token in calls))

    def test_status_tool_retains_id_on_revocation_and_never_enqueues(self):
        calls = []
        self.invoke_with_transport(lambda request: calls.append(request.method) or httpx.Response(403, json={
            "ok": False, "error": "product_owner_auth_ineligible",
        }))
        receipt = self.call_tool("product_action_status", {"request_id": "req-1"})
        self.assertEqual(receipt["request_id"], "req-1")
        self.assertEqual(receipt["error"], "product_owner_auth_ineligible")
        self.assertNotIn("result", receipt)
        self.assertEqual(calls, ["GET"])

    def test_mcp_malformed_ack_reports_uncertainty_without_status_poll_or_replay(self):
        calls = []
        self.invoke_with_transport(lambda request: calls.append(request.method) or httpx.Response(202, json={"ok": True}))
        receipt = self.call_tool("computer_click", {"device_id": "dev-1", "x": 1, "y": 2})
        self.assertFalse(receipt["ok"])
        self.assertTrue(receipt["acceptance_unknown"])
        self.assertNotIn("request_id", receipt)
        self.assertEqual(calls, ["POST"])

    def test_mcp_wait_timeout_retains_same_task_without_marking_it_failed(self):
        self.invoke_with_transport(lambda request: httpx.Response(202, json={"ok": True, "request_id": "req-1"}))
        with patch.object(ProductRemoteClient, "wait_action", side_effect=ProductActionWaitTimeout("req-1")):
            receipt = self.call_tool("computer_click", {"device_id": "dev-1", "x": 1, "y": 2})
        self.assertEqual(receipt["error"], "product_action_wait_timeout")
        self.assertEqual(receipt["request_id"], "req-1")
        self.assertTrue(receipt["completion_unknown"])
        self.assertNotIn("status", receipt)

    def test_status_tool_schema_has_only_request_id_and_no_execution_authority(self):
        tools = asyncio.run(server.mcp.list_tools())
        status = next(tool for tool in tools if tool.name == "product_action_status")
        self.assertEqual(set(status.inputSchema["properties"]), {"request_id"})
        self.assertEqual(status.inputSchema["required"], ["request_id"])
        self.assertTrue(status.annotations.readOnlyHint)
        self.assertFalse(status.annotations.destructiveHint)
        self.assertTrue(status.annotations.idempotentHint)
        self.assertFalse(status.annotations.openWorldHint)


if __name__ == "__main__":
    unittest.main()
