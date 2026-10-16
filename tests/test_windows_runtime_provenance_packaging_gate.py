from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WindowsRuntimeProvenancePackagingGateTests(unittest.TestCase):
    def test_product_device_transport_is_a_required_packaged_capability(self) -> None:
        helper = (
            ROOT / "scripts" / "windows" / "assert-runtime-package-capabilities.ps1"
        ).read_text(encoding="utf-8")
        build = (
            ROOT / "scripts" / "windows" / "build-ordax-studio-product.ps1"
        ).read_text(encoding="utf-8")
        smoke = (
            ROOT / "scripts" / "windows" / "test-ordax-studio-install-smoke.ps1"
        ).read_text(encoding="utf-8")

        self.assertIn("product_device_control_plane.py", helper)
        self.assertIn("ordax_dev_agent.product_device_control_plane", helper)
        self.assertIn("from ordax_dev_agent.control_plane import build_control_plane", helper)
        self.assertIn("ProductDeviceControlPlane", helper)
        self.assertIn('ProductDeviceControlPlane.runtime_kind != "desktop-agent"', helper)
        self.assertIn('getattr(ProductDeviceControlPlane, "_runtime_provenance", None)', helper)
        self.assertIn("transport.DEVICE_AGENT_VERSION != ordax_dev_agent.__version__", helper)
        self.assertIn("$probe | & $privatePythonPath -I - $expectedModule", helper)
        self.assertNotIn("-I -c $probe", helper)

        self.assertIn("assert-runtime-package-capabilities.ps1", build)
        self.assertIn('-Phase "staged"', build)
        self.assertIn("ORDAX_RUNTIME_PACKAGE_VERSION=", build)
        self.assertIn("runtime_version = $runtimeVersion", build)

        self.assertEqual(smoke.count("assert-runtime-package-capabilities.ps1"), 3)
        self.assertIn('-Phase "installed"', smoke)
        self.assertIn('-Phase "upgraded"', smoke)
        self.assertIn('-Phase "repaired"', smoke)

    def test_packaging_probe_is_side_effect_free(self) -> None:
        helper = (
            ROOT / "scripts" / "windows" / "assert-runtime-package-capabilities.ps1"
        ).read_text(encoding="utf-8").lower()

        self.assertNotIn("pair_if_needed", helper)
        self.assertNotIn("device_credentials", helper)
        self.assertNotIn("invoke-webrequest", helper)
        self.assertNotIn("invoke-restmethod", helper)
        self.assertNotIn("productcentralsessionclient(", helper)


if __name__ == "__main__":
    unittest.main()
