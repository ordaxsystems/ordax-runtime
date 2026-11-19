import json
import unittest
from pathlib import Path

from ordax_dev_agent.assets.blender_rigging_contracts import normalize_rig_create
from ordax_dev_agent.blender_actions import BlenderActions
from ordax_dev_agent.models import ActionResult


ROOT = Path(__file__).resolve().parents[1]


def request():
    return {"name": "CharacterRig", "location": [1, 2, 3], "bones": [
        {"name": "Root", "head": [0, 0, 0], "tail": [0, 0, 1]},
        {"name": "Spine", "head": [0, 0, 1], "tail": [0, 0, 2], "parent": "Root"},
    ]}


class RiggingContractTests(unittest.TestCase):
    def test_valid_hierarchy(self):
        normalized = normalize_rig_create(request())
        self.assertEqual("Root", normalized["bones"][1]["parent"])
        self.assertIsNone(normalized["bones"][0]["parent"])
        self.assertEqual([1.0, 2.0, 3.0], normalized["location"])

    def test_rejects_extra_field(self):
        with self.assertRaisesRegex(ValueError, "unsupported"):
            normalize_rig_create({**request(), "python": "bad"})

    def test_rejects_duplicate_bones(self):
        raw = request()
        raw["bones"][1]["name"] = "Root"
        with self.assertRaisesRegex(ValueError, "duplicate bone"):
            normalize_rig_create(raw)

    def test_rejects_parent_out_of_order(self):
        raw = request()
        raw["bones"][0]["parent"] = "Spine"
        with self.assertRaisesRegex(ValueError, "earlier bone"):
            normalize_rig_create(raw)

    def test_rejects_degenerate_bone(self):
        raw = request()
        raw["bones"][0]["tail"] = [0, 0, 0]
        with self.assertRaisesRegex(ValueError, "nonzero bone length"):
            normalize_rig_create(raw)

    def test_rejects_nan(self):
        raw = request()
        raw["bones"][0]["head"] = [0, float("nan"), 0]
        with self.assertRaisesRegex(ValueError, "out-of-range"):
            normalize_rig_create(raw)

    def test_rejects_boolean(self):
        raw = request()
        raw["location"] = [True, 0, 0]
        with self.assertRaisesRegex(ValueError, "finite"):
            normalize_rig_create(raw)

    def test_rejects_too_many_bones(self):
        raw = request()
        raw["bones"] = raw["bones"] * 33
        with self.assertRaisesRegex(ValueError, "1 to 64"):
            normalize_rig_create(raw)

    def test_transport_fields_must_be_explicit(self):
        raw = {**request(), "project": "demo"}
        with self.assertRaisesRegex(ValueError, "unsupported"):
            normalize_rig_create(raw)
        self.assertEqual("CharacterRig", normalize_rig_create(raw, transport_fields={"project"})["name"])


class FakeBridge:
    def __init__(self):
        self.calls = []

    def request(self, operation, arguments, *, timeout_seconds):
        self.calls.append((operation, arguments, timeout_seconds))
        return ActionResult(True, "done", {"operation": operation})


class FakeActions(BlenderActions):
    def __init__(self):
        self.bridge = FakeBridge()

    def _blender_live(self, payload):
        return self.bridge


class RiggingActionTests(unittest.TestCase):
    def test_valid_action_uses_existing_bridge(self):
        actions = FakeActions()
        result = actions.blender_live_rig_create({**request(), "project": "demo"})
        self.assertTrue(result.ok)
        op, arguments, timeout = actions.bridge.calls[0]
        self.assertEqual("rig_create", op)
        self.assertEqual(60.0, timeout)
        self.assertEqual("Root", arguments["bones"][1]["parent"])
        self.assertNotIn("project", arguments)

    def test_invalid_request_never_reaches_bridge(self):
        actions = FakeActions()
        result = actions.blender_live_rig_create({"name": "Empty", "bones": []})
        self.assertFalse(result.ok)
        self.assertEqual([], actions.bridge.calls)

    def test_single_registry_and_companion_dispatch(self):
        companion = (ROOT / "ordax_dev_agent/assets/blender_live_companion.py").read_text(encoding="utf-8")
        registry = (ROOT / "ordax_dev_agent/actions.py").read_text(encoding="utf-8")
        mcp = (ROOT / "ordax_dev_agent/mcp_server.py").read_text(encoding="utf-8")
        bundle = json.loads((ROOT / "ordax_dev_agent/assets/blender_companion_bundle.json").read_text(encoding="utf-8"))
        self.assertIn('elif operation == "rig_create":', companion)
        self.assertIn('_rig_create(command)', companion)
        self.assertIn('"blender.live_rig_create": self.blender_live_rig_create', registry)
        self.assertIn('def create_armature(', mcp)
        self.assertIn("blender_rigging_contracts.py", bundle["files"])


if __name__ == "__main__":
    unittest.main()
