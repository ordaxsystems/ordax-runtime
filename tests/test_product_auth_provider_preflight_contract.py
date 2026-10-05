from __future__ import annotations

import unittest
from pathlib import Path


class ProductAuthProviderPreflightContractTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.script = (
            root / "scripts" / "cloudflare" / "verify_product_auth_provider.py"
        ).read_text(encoding="utf-8")
        self.workflow = (
            root / ".github" / "workflows" / "cloudflare-v3-deploy.yml"
        ).read_text(encoding="utf-8")
        self.deploy = (
            root / "scripts" / "cloudflare" / "deploy-v3.sh"
        ).read_text(encoding="utf-8")

    def test_preflight_requires_https_and_asymmetric_jwks(self):
        self.assertIn('parsed.scheme != "https"', self.script)
        self.assertIn('{"RS256", "ES256"}', self.script)
        self.assertIn('key.get("kid")', self.script)

    def test_deploy_requires_product_auth_preflight(self):
        self.assertIn("verify_product_auth_provider.py", self.deploy)
        self.assertIn("PRODUCT_AUTH_ISSUER", self.deploy)
        self.assertIn("PRODUCT_AUTH_AUDIENCE", self.deploy)
        self.assertIn("PRODUCT_AUTH_JWKS_URL", self.deploy)

    def test_workflow_points_to_ordax_supabase_auth(self):
        self.assertIn("eobcxuyvhkvdmkbaihwh.supabase.co/auth/v1", self.workflow)
        self.assertIn("PRODUCT_AUTH_AUDIENCE: authenticated", self.workflow)

    def test_deploy_health_requires_product_auth_ready(self):
        self.assertIn('data.get("product_auth_configured") is True', self.workflow)
        self.assertIn("Remote health + Product auth OK", self.workflow)

    def test_deploy_waits_for_green_main_bridge_ci(self):
        self.assertIn('workflows: ["Bridge CI"]', self.workflow)
        self.assertIn("github.event.workflow_run.conclusion == 'success'", self.workflow)
        self.assertIn("github.event.workflow_run.head_branch == 'main'", self.workflow)
        self.assertIn("github.event.workflow_run.head_sha", self.workflow)



if __name__ == "__main__":
    unittest.main()
