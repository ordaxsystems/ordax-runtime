"""OrdaX MCP USB actions delegate to OS source and never invent a writer."""
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock
import json
import subprocess
import sys
import tempfile

from ordax_dev_agent.usb_media_actions import USBMediaActions, USB_SCHEMA
from ordax_dev_agent.product_gateway import PRODUCT_ACTIONS
from ordax_dev_agent.product_action_scope import DEVICE_SCOPED_ACTIONS
from ordax_dev_agent.action_contracts import dispatch_device_capability


class MCPUSBMediaTests(TestCase):
    def setUp(self):
        self.agent = USBMediaActions()
        self.agent.config = SimpleNamespace(os_source_root=None)

    def test_canonical_product_scope_is_device_read_only(self):
        for action in ("usb.list", "usb.inspect"):
            self.assertIn(action, DEVICE_SCOPED_ACTIONS)
            spec = PRODUCT_ACTIONS[action]
            self.assertEqual(spec.effect, "read")
            self.assertFalse(spec.project_required)
            self.assertEqual(dispatch_device_capability(action, {}).action, action)
        self.assertEqual(PRODUCT_ACTIONS["usb.list"].allowed_fields, frozenset())
        self.assertEqual(PRODUCT_ACTIONS["usb.inspect"].allowed_fields,
                         frozenset({"disk_number", "serial"}))
        self.assertNotIn("usb.update", PRODUCT_ACTIONS)
        self.assertNotIn("usb.repair", PRODUCT_ACTIONS)

    def test_invalid_device_payload_fails_before_process(self):
        with mock.patch.object(self.agent, "_usb_os_read") as invoke:
            self.assertFalse(self.agent.usb_list({"disk_number": 1}).ok)
            self.assertFalse(self.agent.usb_inspect({"disk_number": 1}).ok)
            for serial in ("../../C", "", "A" * 65):
                self.assertFalse(self.agent.usb_inspect({"disk_number": 1, "serial": serial}).ok)
            self.assertFalse(self.agent.usb_inspect({"disk_number": True, "serial": "6699E36B"}).ok)
            self.assertFalse(self.agent.usb_inspect({
                "disk_number": 1, "serial": "6699E36B", "shell": "powershell"
            }).ok)
            invoke.assert_not_called()

    def test_minimum_product_mcp_handlers_delegate_only_typed_usb_reads(self):
        from ordax_dev_agent import product_mcp_handlers as handlers
        result = {"ok": True, "status": "succeeded"}
        with mock.patch.object(handlers, "_invoke", return_value=result) as invoke:
            self.assertIs(handlers.usb_list("device-a"), result)
            self.assertEqual(invoke.call_args.kwargs, {
                "device_id": "device-a", "action": "usb.list",
                "space_id": None, "arguments": {},
            })
            self.assertIs(handlers.usb_inspect("device-a", 1, "6699E36B"), result)
            self.assertEqual(invoke.call_args.kwargs, {
                "device_id": "device-a", "action": "usb.inspect",
                "space_id": None,
                "arguments": {"disk_number": 1, "serial": "6699E36B"},
            })

    def test_missing_trusted_os_source_denies_execution(self):
        with mock.patch("ordax_dev_agent.usb_media_actions.subprocess.run") as run:
            receipt = self.agent.usb_list({})
            self.assertFalse(receipt.ok)
            self.assertFalse(receipt.data["physical_write"])
            run.assert_not_called()

    def test_untrusted_git_origin_and_dirty_source_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for sub in ("creator/tools/owner_dev_usb_inventory.py",
                        "creator/tools/owner_dev_qemu_partition_readonly.py"):
                target = root / sub
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("# source", encoding="utf-8")
            self.agent.config.os_source_root = root
            subprocess_ok = lambda body: SimpleNamespace(returncode=0, stdout=body, stderr=b"")
            def forged(args, **_):
                if "get-url" in args:
                    return subprocess_ok(b"https://github.com/attacker/usb.git\n")
                return subprocess_ok(b"main\n")
            with mock.patch("ordax_dev_agent.usb_media_actions.subprocess.run", side_effect=forged):
                self.assertFalse(self.agent.usb_list({}).ok)

    def test_inspect_denies_os_target_swap_and_list_refuses_inconsistent_count(self):
        from ordax_dev_agent.models import ActionResult
        swapped = {"$schema": USB_SCHEMA, "status": "recognized",
                   "physical_write": False, "physical_write_authorized": False,
                   "disk_number": 2, "usb_serial": "6699E36B",
                   "ext_filesystem_superblock_verified": True}
        self.agent._usb_os_read = lambda arguments: ActionResult(
            True, "test", swapped,
        )
        self.assertFalse(self.agent.usb_inspect({"disk_number": 1, "serial": "6699E36B"}).ok)
        inconsistent = {"$schema": USB_SCHEMA, "status": "ok",
                        "physical_write": False, "devices": [],
                        "device_count": 1}
        self.agent._usb_os_read = lambda arguments: ActionResult(
            True, "test", inconsistent,
        )
        self.assertFalse(self.agent.usb_list({}).ok)

    def test_os_receipt_schema_and_write_flag_are_checked(self):
        self.agent._verified_os_usb_command = lambda: Path("owner_dev_usb_inventory.py")
        for body in (
            b"not-json",
            json.dumps({"$schema": USB_SCHEMA, "physical_write": True}).encode(),
            json.dumps({"$schema": "fake", "physical_write": False}).encode(),
        ):
            with mock.patch("ordax_dev_agent.usb_media_actions.subprocess.run",
                            return_value=SimpleNamespace(returncode=0, stdout=body,
                                                          stderr=b"")):
                self.assertFalse(self.agent.usb_list({}).ok)
        valid = {"$schema": USB_SCHEMA, "status": "ok",
                 "devices": [], "device_count": 0, "physical_write": False}
        with mock.patch("ordax_dev_agent.usb_media_actions.subprocess.run",
                        return_value=SimpleNamespace(returncode=0,
                                                      stdout=json.dumps(valid).encode(),
                                                      stderr=b"")) as invoke:
            result = self.agent.usb_list({})
            self.assertTrue(result.ok)
            self.assertEqual(result.data["devices"], [])
            argv = invoke.call_args.args[0]
            self.assertEqual(argv[:2], [sys.executable, "-B"])
            self.assertEqual(argv[-1], "--list")


if __name__ == "__main__":
    import unittest
    unittest.main()
