"""Background Button arguments survive the public MCP route exactly."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from ordax_dev_agent import product_mcp_handlers as handlers
from ordax_dev_agent.product_gateway import PRODUCT_ACTIONS


class ButtonProductRouteTests(unittest.TestCase):
    def test_gateway_declares_exact_target_fields(self):
        spec = PRODUCT_ACTIONS["computer.click"]
        self.assertTrue({"mode", "handle", "expected_pid", "control_text"}.issubset(
            spec.allowed_fields
        ))

    def test_handler_routes_background_without_coordinates(self):
        with patch.object(handlers, "_invoke", return_value={"status": "succeeded"}) as fn:
            result = handlers.computer_click(
                "device-1", "demo", mode="background",
                handle="0xA10", expected_pid=101,
                control_text="Select"
            )
        self.assertEqual(result["status"], "succeeded")
        args = fn.call_args.kwargs["arguments"]
        self.assertEqual(args["mode"], "background")
        self.assertEqual(args["handle"], "0xA10")
        self.assertEqual(args["expected_pid"], 101)
        self.assertEqual(args["control_text"], "Select")
        self.assertNotIn("x", args)
        self.assertNotIn("y", args)

    def test_legacy_foreground_keeps_existing_arguments(self):
        with patch.object(handlers, "_invoke", return_value={}) as fn:
            handlers.computer_click("device-1", "demo", 30, 40)
        args = fn.call_args.kwargs["arguments"]
        self.assertEqual(args["x"], 30)
        self.assertEqual(args["y"], 40)
        self.assertNotIn("mode", args)


if __name__ == "__main__":
    unittest.main()
