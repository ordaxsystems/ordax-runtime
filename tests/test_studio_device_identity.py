from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.device_credentials import resolve_token_path
from ordax_studio.device_identity import create_device_pairing, inspect_device_identity


class StudioDeviceIdentityTests(unittest.TestCase):
    def config(self, root: Path, *, device_id: str | None) -> AgentConfig:
        return AgentConfig(
            agent_name="test",
            poll_seconds=1.0,
            state_dir=root,
            agent_repo_path=root / "src",
            hordax_path=root / "project",
            bridge_path=root / "bridge",
            projects={},
            default_project="test",
            adapters=(),
            control_plane_protocol="cloudflare-v3",
            device_id=device_id,
            control_plane_url="https://control.example.test",
        )

    def test_unprovisioned_when_identity_and_token_are_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = inspect_device_identity(self.config(Path(tmp), device_id=None))
            self.assertFalse(status.configured)
            self.assertFalse(status.can_create_pairing)
            self.assertEqual(status.state, "unprovisioned")

    def test_provisioned_requires_device_id_and_local_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resolve_token_path(root).write_text("x" * 64, encoding="utf-8")
            status = inspect_device_identity(
                self.config(root, device_id="22222222-2222-4222-8222-222222222222")
            )
            self.assertTrue(status.configured)
            self.assertTrue(status.token_present)
            self.assertTrue(status.can_create_pairing)
            self.assertEqual(status.state, "provisioned")

    def test_device_id_without_token_is_not_considered_connected(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = inspect_device_identity(
                self.config(Path(tmp), device_id="22222222-2222-4222-8222-222222222222")
            )
            self.assertFalse(status.configured)
            self.assertEqual(status.state, "credential-missing")

    def test_pairing_refuses_unprovisioned_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RuntimeError, "identidade de dispositivo"):
                create_device_pairing(self.config(Path(tmp), device_id=None))

    def test_pairing_uses_device_authenticated_control_plane(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resolve_token_path(root).write_text("x" * 64, encoding="utf-8")
            config = self.config(
                root,
                device_id="22222222-2222-4222-8222-222222222222",
            )

            class FakeHttp:
                closed = False

                def close(self):
                    self.closed = True

            class FakeControl:
                instance = None

                def __init__(self, received):
                    self.received = received
                    self.http = FakeHttp()
                    FakeControl.instance = self

                def create_product_pairing(self):
                    return {
                        "pairing_id": "pair-1",
                        "pairing_secret": "s" * 64,
                        "expires_at": "2026-09-30T20:00:00Z",
                    }

            with patch("ordax_studio.device_identity.CloudflareControlPlane", FakeControl):
                result = create_device_pairing(config)

            self.assertTrue(result["ok"])
            self.assertEqual(result["pairing"]["pairing_id"], "pair-1")
            self.assertEqual(result["identity"]["state"], "provisioned")
            self.assertTrue(FakeControl.instance.http.closed)


if __name__ == "__main__":
    unittest.main()
