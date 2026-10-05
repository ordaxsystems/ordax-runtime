import unittest
from pathlib import Path


class CloudflareProductionWorkflowTests(unittest.TestCase):
    def test_remote_verifier_runtime_is_installed_before_use(self) -> None:
        root = Path(__file__).resolve().parents[1]
        workflow = (
            root / ".github" / "workflows" / "cloudflare-v3-deploy.yml"
        ).read_text(encoding="utf-8")

        validate_index = workflow.index("- name: Validate required secrets")
        checkout_index = workflow.index("- name: Checkout verified main revision")
        setup_index = workflow.index("uses: actions/setup-python@v5")
        install_index = workflow.index(
            "python -m pip install --disable-pip-version-check -e ."
        )
        deploy_index = workflow.index("run: bash scripts/cloudflare/deploy-v3.sh")
        verify_index = workflow.index("python scripts/verify_cloudflare_v3.py")

        self.assertLess(validate_index, checkout_index)
        self.assertLess(checkout_index, setup_index)
        self.assertLess(setup_index, install_index)
        self.assertLess(install_index, deploy_index)
        self.assertLess(deploy_index, verify_index)
        self.assertIn('python-version: "3.11"', workflow)
        self.assertIn("cache-dependency-path: \"pyproject.toml\"", workflow)


if __name__ == "__main__":
    unittest.main()
