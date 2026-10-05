import unittest

from ordax_dev_agent.component_updates import component_catalog, plan_component_update


class ComponentUpdateTests(unittest.TestCase):
    def test_catalog_keeps_components_independently_versioned(self):
        catalog = component_catalog()
        self.assertEqual(catalog["schema"], "ordax.device-agent-components/1")
        versions = {
            item["id"]: item["version"]
            for item in catalog["components"]
        }
        self.assertIn("device-agent-core", versions)
        self.assertIn("adapter-blender", versions)
        self.assertIn("adapter-game-assets", versions)
        self.assertIn("adapter-alephgeo", versions)
        self.assertNotEqual(versions["device-agent-core"], versions["adapter-blender"])
        self.assertFalse(
            catalog["delivery_policy"]["whole_os_reinstall_required_for_component_update"]
        )

    def test_blender_change_does_not_require_whole_os_reinstall_or_reboot(self):
        plan = plan_component_update([
            "ordax_dev_agent/blender_actions.py",
            "ordax_dev_agent/assets/blender_quality_rules.py",
        ])
        self.assertEqual(plan["affected_components"], ["adapter-blender"])
        self.assertTrue(plan["device_agent_restart_required"])
        self.assertFalse(plan["whole_os_reinstall_required"])
        self.assertFalse(plan["whole_os_reboot_required"])
        self.assertFalse(plan["install_refresh_required"])

    def test_game_asset_change_stays_in_generation_failure_domain(self):
        plan = plan_component_update([
            "ordax_dev_agent/game_asset_artifact_actions.py",
            "ordax_dev_agent/rodin_actions.py",
            "ordax_dev_agent/comfyui_actions.py",
            "ordax_dev_agent/assets/blender_game_asset_pipeline.py",
        ])
        self.assertEqual(plan["affected_components"], ["adapter-game-assets"])
        self.assertTrue(plan["device_agent_restart_required"])
        self.assertEqual(plan["unknown_paths"], [])

    def test_aleph_scene_change_is_owned_by_geospatial_adapter(self):
        plan = plan_component_update([
            "ordax_dev_agent/aleph_scene_actions.py",
            "ordax_dev_agent/assets/blender_aleph_scene.py",
        ])
        self.assertEqual(plan["affected_components"], ["adapter-alephgeo"])
        self.assertEqual(plan["unknown_paths"], [])

    def test_git_change_stays_in_git_adapter_failure_domain(self):
        plan = plan_component_update(["ordax_dev_agent/git_actions.py"])
        self.assertEqual(plan["affected_components"], ["adapter-git"])


    def test_adapter_contract_is_owned_by_device_core(self):
        plan = plan_component_update(["ordax_dev_agent/adapter_contracts.py"])
        self.assertEqual(plan["affected_components"], ["device-agent-core"])
        self.assertTrue(plan["device_agent_restart_required"])
        self.assertEqual(plan["unknown_paths"], [])

    def test_shared_action_contract_is_owned_by_device_core(self):
        plan = plan_component_update(["ordax_dev_agent/action_contracts.py"])
        self.assertEqual(plan["affected_components"], ["device-agent-core"])
        self.assertTrue(plan["device_agent_restart_required"])
        self.assertEqual(plan["unknown_paths"], [])

    def test_product_gateway_is_owned_by_device_mcp(self):
        plan = plan_component_update(["ordax_dev_agent/product_gateway.py"])
        self.assertEqual(plan["affected_components"], ["device-mcp"])
        self.assertFalse(plan["device_agent_restart_required"])
        self.assertEqual(plan["restart_policies"], ["mcp-client-session"])
        self.assertEqual(plan["unknown_paths"], [])

    def test_packaging_change_refreshes_install_contract_without_os_reinstall(self):
        plan = plan_component_update(["pyproject.toml"])
        self.assertTrue(plan["install_refresh_required"])
        self.assertIn("device-agent-core", plan["affected_components"])
        self.assertIn("adapter-unity", plan["affected_components"])
        self.assertIn("adapter-game-assets", plan["affected_components"])
        self.assertFalse(plan["whole_os_reinstall_required"])

    def test_docs_only_change_needs_no_runtime_restart(self):
        plan = plan_component_update(["docs/ORDAX_DEVICE_AGENT_EVOLUTION.md"])
        self.assertEqual(plan["affected_components"], [])
        self.assertFalse(plan["device_agent_restart_required"])
        self.assertEqual(
            plan["unknown_paths"],
            ["docs/ORDAX_DEVICE_AGENT_EVOLUTION.md"],
        )

    def test_rejects_repository_escape(self):
        with self.assertRaises(ValueError):
            plan_component_update(["../secret"])


if __name__ == "__main__":
    unittest.main()
