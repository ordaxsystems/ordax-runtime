import unittest
from pathlib import Path


class CloudflareDeployScriptTests(unittest.TestCase):
    def test_first_deploy_uploads_secret_with_code(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "cloudflare" / "deploy-v3.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('--secrets-file "$SECRETS_FILE"', script)
        self.assertIn('ORDAX_OPERATOR_TOKEN', script)
        self.assertIn('trap cleanup EXIT', script)
        self.assertIn('target.chmod(0o600)', script)
        self.assertNotIn('wrangler secret put', script)
        self.assertIn('/workers/subdomain', script)
        self.assertIn('ORDAX_CLOUDFLARE_SUBDOMAIN', script)
        self.assertIn('workers_dev', script)
        self.assertIn('control_plane_url=', script)

        migration_index = script.index("d1 migrations apply")
        deploy_index = script.index('wrangler deploy --config "$GENERATED_CONFIG"')
        self.assertLess(migration_index, deploy_index)

    def test_deploy_workflow_runs_remote_e2e_after_health(self) -> None:
        root = Path(__file__).resolve().parents[1]
        workflow = (
            root / ".github" / "workflows" / "cloudflare-v3-deploy.yml"
        ).read_text(encoding="utf-8")

        health_index = workflow.index("Wait for public health")
        e2e_index = workflow.index("Verify remote Cloudflare v3 end to end")
        self.assertLess(health_index, e2e_index)
        self.assertIn("steps.deploy.outputs.control_plane_url", workflow)
        self.assertIn("scripts/verify_cloudflare_v3.py", workflow)

        verify = (root / "scripts" / "verify_cloudflare_v3.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"/.well-known/oauth-protected-resource"', verify)
        self.assertIn('/.well-known/openid-configuration', verify)
        self.assertIn('{"openid", "email"}.issubset', verify)
        self.assertIn('"userinfo_endpoint"', verify)
        self.assertIn('"S256"', verify)


if __name__ == "__main__":
    unittest.main()
