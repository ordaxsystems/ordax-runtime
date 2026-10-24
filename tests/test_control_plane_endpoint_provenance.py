from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.control_plane_provenance import (
    ControlPlaneProvenanceError,
    resolve_control_plane_provenance,
)


class ControlPlaneEndpointProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.platform_root = self.root / "platform"
        foundation_path = (
            self.platform_root / "control-plane" / "cloudflare" / "production-foundation.json"
        )
        foundation_path.parent.mkdir(parents=True)
        self.foundation = {
            "version": 24,
            "dns": {"zone": "ordax.com.br"},
            "deployment_ready": False,
            "readiness_blockers": [],
            "connection_modes": {
                "direct_user_chatgpt_mcp": {
                    "current_validation_endpoint": "https://mcp.ordax.com.br/mcp"
                }
            },
            "mcp_release": {
                "worker_name": "ordax-mcp",
                "hostname": "mcp.ordax.com.br",
                "path": "/mcp",
                "custom_domain_required": True,
                "workers_dev": False,
                "validation_deployment_ready": True,
                "deployment_ready": False,
                "readiness_blockers": [],
            },
        }
        self._write_foundation()
        self.lock_path = self.root / "control-plane-source.lock.json"
        self.lock = {
            "schema": "ordax.control-plane-source-lock/1",
            "source": {
                "repository": "ordaxsystems/ordax-platform",
                "path": "control-plane/cloudflare/production-foundation.json",
                "commit": "3ae5d914df03f8f6a677ddd18cf3337cd0bcb89a",
                "version": 24,
            },
            "authority": "none",
        }
        self._write_lock()

    def _write_foundation(self) -> None:
        path = self.platform_root / "control-plane" / "cloudflare" / "production-foundation.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.foundation), encoding="utf-8")

    def _write_lock(self) -> None:
        self.lock_path.write_text(json.dumps(self.lock), encoding="utf-8")

    def _resolve(self, environment: str = "validation-candidate") -> dict:
        return resolve_control_plane_provenance(
            self.lock_path, self.platform_root, environment
        )

    def test_pinned_validation_candidate_resolves_to_runtime_default_origin(self) -> None:
        provenance = self._resolve()
        self.assertEqual(provenance["endpoint"], "https://mcp.ordax.com.br/mcp")
        self.assertEqual(provenance["origin"], "https://mcp.ordax.com.br")
        self.assertEqual(provenance["environment"], "validation-candidate")
        self.assertEqual(
            provenance["source"]["commit"],
            "3ae5d914df03f8f6a677ddd18cf3337cd0bcb89a",
        )

    def test_production_fails_closed_until_both_platform_readiness_gates_are_true(self) -> None:
        with self.assertRaisesRegex(ControlPlaneProvenanceError, "not deployment-ready"):
            self._resolve("production")
        self.foundation["deployment_ready"] = True
        self.foundation["mcp_release"]["deployment_ready"] = True
        self._write_foundation()
        self.assertEqual(self._resolve("production")["environment"], "production")

    def test_production_rejects_remaining_platform_readiness_blockers(self) -> None:
        self.foundation["deployment_ready"] = True
        self.foundation["mcp_release"]["deployment_ready"] = True
        self.foundation["readiness_blockers"] = ["worker_not_provisioned"]
        self._write_foundation()
        with self.assertRaisesRegex(ControlPlaneProvenanceError, "unresolved readiness blockers"):
            self._resolve("production")

    def test_validation_endpoint_must_match_pinned_worker_host_and_path(self) -> None:
        self.foundation["connection_modes"]["direct_user_chatgpt_mcp"][
            "current_validation_endpoint"
        ] = "https://other.ordax.com.br/mcp"
        self._write_foundation()
        with self.assertRaisesRegex(ControlPlaneProvenanceError, "does not match"):
            self._resolve()

    def test_workers_dev_host_is_rejected(self) -> None:
        self.foundation["mcp_release"]["hostname"] = "ordax-mcp.ordaxsystems.workers.dev"
        self._write_foundation()
        with self.assertRaises(ControlPlaneProvenanceError):
            self._resolve()

    def test_host_outside_platform_dns_zone_is_rejected(self) -> None:
        self.foundation["mcp_release"]["hostname"] = "mcp.example.net"
        self._write_foundation()
        with self.assertRaisesRegex(ControlPlaneProvenanceError, "outside its canonical DNS zone"):
            self._resolve()

    def test_platform_foundation_version_must_match_lock(self) -> None:
        self.foundation["version"] = 25
        self._write_foundation()
        with self.assertRaisesRegex(ControlPlaneProvenanceError, "version differs"):
            self._resolve()

    def test_lock_rejects_noncanonical_repository_or_mutable_revision(self) -> None:
        self.lock["source"]["repository"] = "ordaxsystems/ordax-apps"
        self._write_lock()
        with self.assertRaisesRegex(ControlPlaneProvenanceError, "repository is not canonical"):
            self._resolve()
        self.lock["source"]["repository"] = "ordaxsystems/ordax-platform"
        self.lock["source"]["commit"] = "main"
        self._write_lock()
        with self.assertRaisesRegex(ControlPlaneProvenanceError, "immutable Git SHA"):
            self._resolve()

    def test_runtime_default_must_match_pinned_platform_origin(self) -> None:
        with patch(
            "ordax_dev_agent.control_plane_provenance.DEFAULT_PRODUCT_MCP_URL",
            "https://legacy.example.workers.dev",
        ):
            with self.assertRaisesRegex(ControlPlaneProvenanceError, "Runtime default"):
                self._resolve()

    def test_unknown_environment_is_rejected(self) -> None:
        with self.assertRaisesRegex(ControlPlaneProvenanceError, "Unsupported"):
            self._resolve("staging")


if __name__ == "__main__":
    unittest.main()
