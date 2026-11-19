"""Product MCP Blender action must reuse the attested Project binding."""
import unittest

from ordax_dev_agent.product_device_control_plane import _PROJECT_SCOPE_ACTIONS
from ordax_dev_agent.product_gateway import PRODUCT_ACTIONS, product_action_catalog
from ordax_dev_agent.product_mcp import PRODUCT_MCP_TOOLS


BLENDER_TOOLS = {
    "blender.live_status": "read",
    "blender.live_scene_snapshot": "read",
    "blender.live_object_inspect": "read",
    "blender.live_modeling_schema": "read",
    "blender.live_start": "write",
    "blender.live_object_transform": "write",
    "blender.live_create_primitive": "write",
    "blender.live_rig_create": "write",
    "blender.live_material_apply": "write",
    "blender.live_save": "write",
}


class BlenderProductMcpContractTests(unittest.TestCase):
    def test_project_binding_required_for_every_remote_blender_action(self):
        for action in BLENDER_TOOLS:
            with self.subTest(action=action):
                self.assertIn(action, _PROJECT_SCOPE_ACTIONS)
                self.assertTrue(PRODUCT_ACTIONS[action].project_required)
                self.assertIn("project", PRODUCT_ACTIONS[action].allowed_fields)

    def test_access_modes_and_rig_payload(self):
        catalog = {item["name"]: item for item in product_action_catalog()}
        for action, access_mode in BLENDER_TOOLS.items():
            with self.subTest(action=action):
                self.assertEqual(access_mode, catalog[action]["effect"])
        rig = PRODUCT_ACTIONS["blender.live_rig_create"]
        self.assertEqual(
            frozenset({"project", "name", "bones", "location", "timeout_seconds"}),
            rig.allowed_fields,
        )
        self.assertEqual("blender.live_rig_create", rig.local_action)

    def test_product_mcp_uses_same_action_as_local_registry(self):
        tool_catalog = {tool.name: tool.action for tool in PRODUCT_MCP_TOOLS}
        self.assertEqual("blender.live_rig_create", tool_catalog["blender_rig_create"])

    def test_scripts_are_not_promoted_to_remote_product_mcp(self):
        self.assertNotIn("blender.live_run_script", PRODUCT_ACTIONS)
        self.assertNotIn("blender.live_batch", PRODUCT_ACTIONS)


if __name__ == "__main__":
    unittest.main()
