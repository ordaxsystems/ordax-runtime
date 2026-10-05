from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
CONTRACT = ROOT / "docs" / "contracts" / "ordax-os-runtime-compatibility.json"
PYPROJECT = ROOT / "pyproject.toml"


class OrdaxOsRuntimeCompatibilityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        with PYPROJECT.open("rb") as handle:
            cls.pyproject = tomllib.load(handle)

    def test_contract_identity_matches_packaged_product(self) -> None:
        product = self.pyproject["project"]
        contract_product = self.contract["product"]

        self.assertEqual("ordax.studio-runtime-compatibility/1", self.contract["$schema"])
        self.assertEqual("ordax-studio", contract_product["id"])
        self.assertEqual(product["version"], contract_product["version"])
        self.assertEqual("washingtonmsdj/mcp-blender", contract_product["historical_repository"])
        self.assertTrue(contract_product["single_product"])

    def test_headless_profile_does_not_require_desktop_ui_dependency(self) -> None:
        project = self.pyproject["project"]
        runtime = self.contract["runtime"]
        dependencies = [item.lower() for item in project["dependencies"]]
        desktop = [item.lower() for item in project["optional-dependencies"]["desktop"]]
        scripts = project["scripts"]

        self.assertEqual("headless", runtime["profile"])
        self.assertEqual(project["requires-python"], runtime["python"])
        self.assertFalse(runtime["desktop_extra_required"])
        self.assertFalse(any("pywebview" in item for item in dependencies))
        self.assertTrue(any("pywebview" in item for item in desktop))
        self.assertEqual("ordax_dev_agent.main:main", scripts["ordax-dev-agent"])
        self.assertEqual("ordax_device_agent.main:main", scripts["ordax-device-agent"])
        self.assertEqual(
            {"ordax-dev-agent", "ordax-device-agent"},
            set(runtime["entrypoints"]),
        )

    def test_ordax_os_remains_platform_authority(self) -> None:
        authority = self.contract["platform_authority"]
        self.assertEqual(
            {
                "identity_owner",
                "spaces_owner",
                "permissions_owner",
                "intelligence_owner",
                "memory_owner",
                "model_router_owner",
            },
            set(authority),
        )
        self.assertEqual({"ordax-os"}, set(authority.values()))

        integration = self.contract["ordax_os"]
        self.assertEqual("studio", integration["surface_app_id"])
        self.assertEqual("ordax.device-agent/1", integration["device_agent_schema"])
        self.assertEqual(
            "ordax.device-agent-capability-reader/1",
            integration["capability_reader_schema"],
        )
        self.assertTrue(integration["same_action_gateway_required"])
        self.assertTrue(integration["surface_raw_execute_forbidden"])
        self.assertTrue(integration["parallel_runtime_forbidden"])

    def test_contract_does_not_enable_dispatch_or_make_runtime_boot_critical(self) -> None:
        activation = self.contract["activation"]
        self.assertEqual("supported", activation["capability_discovery"])
        self.assertFalse(activation["runtime_dispatch_enabled"])
        self.assertEqual(
            "separate-ordax-os-gate",
            activation["remote_action_gateway_activation"],
        )
        self.assertFalse(activation["missing_runtime_boot_critical"])

        capabilities = self.contract["capabilities"]
        for capability in ("blender", "unity", "git", "filesystem", "processes", "browser"):
            self.assertEqual("studio-capability", capabilities[capability])


if __name__ == "__main__":
    unittest.main()
