from __future__ import annotations

import unittest
from pathlib import Path


class ProductLinkAwareGrantContractTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.worker = (
            root / "control-plane" / "cloudflare" / "src" / "index.ts"
        ).read_text(encoding="utf-8")

    def test_from_link_route_is_operator_only_and_uses_active_link(self):
        self.assertIn('"/v3/product-grants/from-link"', self.worker)
        start = self.worker.index("async function createProductGrantFromLink")
        end = self.worker.index("async function listProductGrants", start)
        body = self.worker[start:end]
        self.assertIn("operatorAuthorized(request, env)", body)
        self.assertIn("ordax_product_device_links", body)
        self.assertIn("l.revoked_at IS NULL", body)
        self.assertIn("d.revoked_at IS NULL", body)
        self.assertIn("persistProductGrant", body)

    def test_from_link_rejects_identity_overrides(self):
        start = self.worker.index("async function createProductGrantFromLink")
        end = self.worker.index("async function listProductGrants", start)
        body = self.worker[start:end]
        self.assertIn("body.subject_id != null", body)
        self.assertIn("body.device_id != null", body)
        self.assertIn("body.space_id != null", body)

    def test_grant_validation_and_persistence_are_shared(self):
        self.assertIn("function parseProductGrantInput", self.worker)
        self.assertIn("async function persistProductGrant", self.worker)
        raw_start = self.worker.index("async function createProductGrant(request")
        raw_end = self.worker.index("async function createProductGrantFromLink", raw_start)
        raw = self.worker[raw_start:raw_end]
        linked_start = raw_end
        linked_end = self.worker.index("async function listProductGrants", linked_start)
        linked = self.worker[linked_start:linked_end]
        self.assertIn("parseProductGrantInput(body)", raw)
        self.assertIn("persistProductGrant", raw)
        self.assertIn("parseProductGrantInput(body)", linked)
        self.assertIn("persistProductGrant", linked)


if __name__ == "__main__":
    unittest.main()
