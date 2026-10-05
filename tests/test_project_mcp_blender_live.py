import unittest
from pathlib import Path
from types import SimpleNamespace

from ordax_dev_agent.mcp_server import _ensure_blender_live
from ordax_dev_agent.models import ActionResult


class FakeRegistry:
    def __init__(self, status: ActionResult, start: ActionResult | None = None):
        self.status = status
        self.start = start
        self.calls: list[tuple[str, dict]] = []
        self.project = SimpleNamespace(path=lambda value: Path(value))

    def _project(self, payload: dict):
        return self.project

    def execute(self, action: str, payload: dict) -> ActionResult:
        self.calls.append((action, payload))
        if action == "blender.live_status":
            return self.status
        if action == "blender.live_start" and self.start is not None:
            return self.start
        if action == "blender.live_stop":
            return ActionResult(True, "stopped", {})
        return ActionResult(False, f"unexpected action: {action}")


class BlenderLiveMCPRecoveryTests(unittest.TestCase):
    def test_current_session_is_reused(self) -> None:
        registry = FakeRegistry(ActionResult(True, "ready", {
            "presence_fresh": True,
            "protocol_compatible": True,
            "companion_current": True,
        }))
        result = _ensure_blender_live(registry, "demo", blend_file=None, wait_seconds=30)
        self.assertFalse(result["auto_started"])
        self.assertEqual(["blender.live_status"], [name for name, _ in registry.calls])

    def test_missing_session_requires_explicit_scene(self) -> None:
        registry = FakeRegistry(ActionResult(False, "not running", {"presence_fresh": False}))
        with self.assertRaisesRegex(RuntimeError, "pass blend_file"):
            _ensure_blender_live(registry, "demo", blend_file=None, wait_seconds=30)
        self.assertEqual(["blender.live_status"], [name for name, _ in registry.calls])

    def test_missing_session_auto_starts_requested_scene(self) -> None:
        registry = FakeRegistry(
            ActionResult(False, "not running", {"presence_fresh": False}),
            ActionResult(True, "started", {"companion_current": True}),
        )
        result = _ensure_blender_live(
            registry, "demo", blend_file="scene.blend", wait_seconds=999
        )
        self.assertTrue(result["auto_started"])
        self.assertEqual("blender.live_start", registry.calls[1][0])
        self.assertEqual("scene.blend", registry.calls[1][1]["blend_file"])
        self.assertEqual(600.0, registry.calls[1][1]["wait_seconds"])

    def test_requested_current_scene_is_reused(self) -> None:
        scene = str(Path("scene.blend").resolve())
        registry = FakeRegistry(ActionResult(True, "ready", {
            "presence_fresh": True,
            "protocol_compatible": True,
            "companion_current": True,
            "presence": {"file": scene, "is_dirty": False},
        }))
        result = _ensure_blender_live(registry, "demo", blend_file="scene.blend", wait_seconds=30)
        self.assertFalse(result["auto_started"])
        self.assertEqual(["blender.live_status"], [name for name, _ in registry.calls])

    def test_different_dirty_scene_refuses_switch(self) -> None:
        registry = FakeRegistry(ActionResult(True, "ready", {
            "presence_fresh": True,
            "protocol_compatible": True,
            "companion_current": True,
            "presence": {"file": str(Path("other.blend").resolve()), "is_dirty": True},
        }))
        with self.assertRaisesRegex(RuntimeError, "unsaved changes"):
            _ensure_blender_live(registry, "demo", blend_file="scene.blend", wait_seconds=30)



if __name__ == "__main__":
    unittest.main()
