from __future__ import annotations

import unittest
from pathlib import Path


class ProductReadonlyE2EContractTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.worker = (root / "control-plane/cloudflare/src/index.ts").read_text(encoding="utf-8")
        self.main = (root / "ordax_dev_agent/main.py").read_text(encoding="utf-8")

    def test_product_action_routes_are_separate_from_operator_jobs(self):
        self.assertIn('"/v3/product/actions"', self.worker)
        self.assertIn("product_typed_actions_v2", self.worker)
        self.assertIn("'ordax.product.invoke'", self.worker)
        self.assertIn("authenticateProductRequest(request, env)", self.worker)
        self.assertIn("resolveProductGrantForContext", self.worker)

    def test_artifacts_list_is_in_product_read_only_worker_catalog(self):
        self.assertIn('"artifacts.list"', self.worker)
        self.assertNotIn('"artifact.read_chunk",\n]);\nconst PRODUCT_PROJECT_ACTIONS', self.worker)

    def test_product_grants_and_targets_are_device_bound(self):
        self.assertIn('!UUID_RE.test(deviceId)', self.worker)
        self.assertIn('AND device_id = ?3', self.worker)
        self.assertIn('"/v3/product/targets"', self.worker)
        self.assertIn('g.device_id IS NOT NULL', self.worker)
        self.assertIn('g.subject_id = ?1', self.worker)
        self.assertIn('FROM ordax_product_device_links l', self.worker)
        self.assertIn("COALESCE(l.space_id, '') = COALESCE(g.space_id, '')", self.worker)
        self.assertIn(') AS link_id', self.worker)
        self.assertIn('link_id: row.link_id', self.worker)

    def test_product_poll_is_subject_scoped(self):
        self.assertIn("r.subject_id = ?2", self.worker)
        self.assertIn("product_action_not_found", self.worker)

    def test_audit_is_device_authenticated_and_request_bound(self):
        self.assertIn('"/v3/product/audit"', self.worker)
        self.assertIn("authenticateDevice(env, deviceId, token)", self.worker)
        self.assertIn("product_audit_context_mismatch", self.worker)

    def test_agent_product_jobs_do_not_execute_directly_in_registry(self):
        self.assertIn('PRODUCT_REMOTE_CAPABILITY', self.main)
        self.assertIn('PRODUCT_REMOTE_LEGACY_CAPABILITY', self.main)
        self.assertIn('execute_product_invocation', self.main)
        self.assertIn("execute_product_invocation", self.main)


if __name__ == "__main__":
    unittest.main()
