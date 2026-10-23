"""Offline wake protocol tests; no SQL, auth, network, local privileges or grants."""
from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ordax_dev_agent.product_wake_signal import (
    ProductWakeListener, WAKE_MESSAGE, trusted_wake_url,
)


class MockSocket:
    def __init__(self, received, stop):
        self.received = received
        self.stop = stop
        self.closed = []
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def recv(self, *, timeout):
        self.calls += 1
        if self.calls == 1:
            self.received.set()
            return WAKE_MESSAGE
        self.stop.set()
        raise TimeoutError()

    def close(self, *, code, reason):
        self.closed.append((code, reason))


class ProductWakeTests(unittest.TestCase):
    def test_only_tls_origin_can_produce_wss_endpoint(self):
        self.assertEqual(trusted_wake_url("https://mcp.ordax.com.br"),
                         "wss://mcp.ordax.com.br/v3/device/transport/wake")
        self.assertEqual(trusted_wake_url("https://mcp.ordax.com.br/"),
                         "wss://mcp.ordax.com.br/v3/device/transport/wake")
        for origin in (
            "http://mcp.ordax.com.br",
            "https://evil.example.org",
            "https://mcp.ordax.com.br:444",
            "https://someuser:secret@mcp.ordax.com.br",
            "https://mcp.ordax.com.br/mcp",
            "https://mcp.ordax.com.br?grant=full",
            "https://mcp.ordax.com.br/#item",
            "ws://mcp.ordax.com.br",
        ):
            with self.subTest(origin=origin):
                with self.assertRaises(ValueError):
                    trusted_wake_url(origin)

    def test_listener_uses_canonical_credential_only_and_never_accepts_work(self):
        stop = threading.Event()
        received = threading.Event()
        socket = MockSocket(received, stop)
        observed = []
        device = "900943fd-8397-471e-bf05-c66ba4de7884"
        token = "s" * 64

        def connect(url, **kwargs):
            observed.append((url, kwargs))
            return socket

        control = SimpleNamespace(
            base_http_url="https://mcp.ordax.com.br",
            device_id=device, device_token=token,
        )
        listener = ProductWakeListener(control, stop, connect_websocket=connect)
        listener.start()
        self.assertTrue(received.wait(3))
        listener.close()
        self.assertGreaterEqual(len(observed), 1)
        url, options = observed[0]
        self.assertEqual(url, "wss://mcp.ordax.com.br/v3/device/transport/wake")
        self.assertEqual(options["additional_headers"], {
            "x-ordax-device-id": device, "x-ordax-device-token": token,
        })
        self.assertEqual(options["max_size"], 32)
        self.assertTrue(listener.wake.is_set())
        self.assertFalse(listener.connected.is_set())
        self.assertEqual(socket.closed, [])
        with self.assertRaises(RuntimeError):
            listener.start()  # never duplicate listeners

    def test_untrusted_signal_cannot_become_action(self):
        stop = threading.Event()
        received = threading.Event()
        closed = threading.Event()
        socket = MockSocket(received, stop)
        def invalid_message(**_kwargs):
            received.set()
            return {"command": "terminal.exec", "grant": "spoof"}
        socket.recv = invalid_message
        original_close = socket.close
        def closed_invalid(**kwargs):
            original_close(**kwargs)
            closed.set()
        socket.close = closed_invalid
        control = SimpleNamespace(
            base_http_url="https://mcp.ordax.com.br",
            device_id="900943fd-8397-471e-bf05-c66ba4de7884",
            device_token="s" * 64,
        )
        listener = ProductWakeListener(control, stop, connect_websocket=lambda *_a, **_k: socket)
        listener.start()
        self.assertTrue(received.wait(3))
        self.assertTrue(closed.wait(3))
        # Cancellation is local, even if a wake connection is blocked.
        stop.set()
        listener.close()
        self.assertEqual(socket.closed[:1], [(1008, "invalid_product_wake")])

    def test_local_owner_revoke_prevents_signalling_handshake(self):
        stop = threading.Event()
        called = []
        control = SimpleNamespace(
            base_http_url="https://mcp.ordax.com.br",
            device_id="900943fd-8397-471e-bf05-c66ba4de7884",
            device_token="s" * 64,
        )
        listener = ProductWakeListener(
            control, stop, lambda: False,
            connect_websocket=lambda *_a, **_k: called.append(True),
        )
        listener.start()
        self.assertTrue(listener.wake.wait(3))
        listener.close()
        self.assertEqual(called, [])
        self.assertFalse(listener.connected.is_set())


if __name__ == "__main__":
    unittest.main()
