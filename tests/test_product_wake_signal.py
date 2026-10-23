"""Offline wake protocol tests; no SQL, auth, network, local privileges or grants."""
from __future__ import annotations

import json
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.product_ingress import TrustedProductIngress
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
            _trusted_ingress=TrustedProductIngress("https://mcp.ordax.com.br"),
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

    def test_primary_tcp_connect_failure_uses_existing_restricted_same_worker_ingress(self):
        stop = threading.Event()
        received = threading.Event()
        socket = MockSocket(received, stop)
        calls = []
        def connect(url, **_kwargs):
            calls.append(url)
            if url.startswith("wss://mcp.ordax.com.br/"):
                raise ConnectionRefusedError("primary TCP connect refused")
            return socket
        control = SimpleNamespace(
            base_http_url="https://mcp.ordax.com.br",
            device_id="900943fd-8397-471e-bf05-c66ba4de7884",
            device_token="s" * 64,
            _trusted_ingress=TrustedProductIngress("https://mcp.ordax.com.br"),
        )
        listener = ProductWakeListener(control, stop, connect_websocket=connect)
        self.assertIs(listener._trusted_ingress, control._trusted_ingress)
        listener.start()
        self.assertTrue(received.wait(3))
        listener.close()
        self.assertEqual(calls[:2], [
            "wss://mcp.ordax.com.br/v3/device/transport/wake",
            "wss://ordax-mcp.ordaxsystems.workers.dev/v3/device/transport/wake",
        ])

    def test_known_network_unreachable_uses_the_same_worker_fallback(self):
        import errno
        origins = []
        ingress = TrustedProductIngress("https://mcp.ordax.com.br")
        def connect(url):
            origins.append(url)
            if len(origins) == 1:
                raise OSError(errno.ENETUNREACH, "network unavailable")
            return "signed-upstream"
        actual = ingress.websocket("/v3/device/transport/wake", connect)
        self.assertEqual(actual, "signed-upstream")
        self.assertEqual(origins, [
            "wss://mcp.ordax.com.br/v3/device/transport/wake",
            "wss://ordax-mcp.ordaxsystems.workers.dev/v3/device/transport/wake",
        ])

    def test_no_failover_on_tls_or_server_handshake_error(self):
        import errno
        import ssl
        for failure in (ssl.SSLError("certificate_invalid"),
                        OSError(errno.EACCES, "local permission denied"),
                        ValueError("server refused websocket handshake")):
            seen = []
            policy = TrustedProductIngress("https://mcp.ordax.com.br")
            def connect(url):
                seen.append(url)
                raise failure
            with self.assertRaises(type(failure)):
                policy.websocket("/v3/device/transport/wake", connect)
            self.assertEqual(seen, [
                "wss://mcp.ordax.com.br/v3/device/transport/wake",
            ])
        with self.assertRaises(ValueError):
            TrustedProductIngress("https://mcp.ordax.com.br").websocket(
                "/v3/device/transport/claim", lambda _: None,
            )

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
            _trusted_ingress=TrustedProductIngress("https://mcp.ordax.com.br"),
        )
        listener = ProductWakeListener(control, stop, connect_websocket=lambda *_a, **_k: socket)
        listener.start()
        self.assertTrue(received.wait(3))
        self.assertTrue(closed.wait(3))
        # Cancellation is local, even if a wake connection is blocked.
        stop.set()
        listener.close()
        self.assertEqual(socket.closed[:1], [(1008, "invalid_product_wake")])

    def test_wake_mode_defaults_off_and_requires_explicit_owner_setting(self):
        with TemporaryDirectory() as folder:
            local = Path(folder)
            settings_dir = local / "OrdaX" / "DevAgent"
            settings_dir.mkdir(parents=True)
            settings = settings_dir / "agent-settings.json"
            with patch.dict("os.environ", {"LOCALAPPDATA": str(local)}, clear=True):
                self.assertFalse(AgentConfig.from_env().product_wake_enabled)
                settings.write_text(json.dumps({"product_wake_enabled": True}), encoding="utf-8")
                self.assertTrue(AgentConfig.from_env().product_wake_enabled)
                with patch.dict("os.environ", {"ORDAX_PRODUCT_WAKE_MODE": "disabled"}):
                    self.assertFalse(AgentConfig.from_env().product_wake_enabled)
                with patch.dict("os.environ", {"ORDAX_PRODUCT_WAKE_MODE": "enabled"}):
                    self.assertTrue(AgentConfig.from_env().product_wake_enabled)
                with patch.dict("os.environ", {"ORDAX_PRODUCT_WAKE_MODE": "unexpected"}):
                    with self.assertRaises(ValueError):
                        AgentConfig.from_env()
                settings.write_text(json.dumps({"product_wake_enabled": "enabled"}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    AgentConfig.from_env()

    def test_local_owner_revoke_prevents_signalling_handshake(self):
        stop = threading.Event()
        called = []
        control = SimpleNamespace(
            base_http_url="https://mcp.ordax.com.br",
            device_id="900943fd-8397-471e-bf05-c66ba4de7884",
            device_token="s" * 64,
            _trusted_ingress=TrustedProductIngress("https://mcp.ordax.com.br"),
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
