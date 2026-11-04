"""Fail-closed background input contracts, independent from a Windows desktop."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from ordax_dev_agent.computer_background_input import (
    UnsupportedBackgroundTarget,
    type_to_background_edit,
    validate_background_request,
)
from ordax_dev_agent.computer_control_actions import ComputerControlActions


class ComputerBackgroundInputTests(unittest.TestCase):
    def test_exact_target_pid_and_printable_unicode_required(self):
        p = {"text":"ABCDE", "handle":"0x5F01DC","expected_pid":15172}
        self.assertEqual(validate_background_request(p),(0x5F01DC,15172,"ABCDE"))
        self.assertEqual(validate_background_request({**p, "mode":"background"})[2],"ABCDE")
        for bad in (
            {"text":"ABC"},
            {"text":"ABC", "handle":"0x5F01DC"},
            {**p,"handle":"notepad"},
            {**p,"expected_pid":"15172"},
            {**p,"expected_pid":True},
            {**p,"mode":"foreground"},
            {**p,"extra":1},
            {**p,"text":"A\nB"},
            {**p,"text":"A"*257},
            {**p,"text":"\x00"},
        ):
            with self.subTest(bad=bad), self.assertRaises(UnsupportedBackgroundTarget):
                validate_background_request(bad)

    def test_unavailable_background_is_not_silent_global_input(self):
        with patch("ordax_dev_agent.computer_background_input.os.name","posix"):
            with self.assertRaisesRegex(UnsupportedBackgroundTarget,"windows_only"):
                type_to_background_edit({"text":"A","handle":"0x101","expected_pid":20})

    def test_native_handler_uses_only_targeted_backend(self):
        payload={"text":"ABCDEFGHIJKLMNOPQRSTUVWXYZ","handle":"0x5F01DC","expected_pid":15172}
        with patch("ordax_dev_agent.computer_background_input.type_to_background_edit",
                   return_value={"characters":26,"verified":True,"mode":"background"}) as backend:
            result=ComputerControlActions().computer_type(payload)
        self.assertTrue(result.ok)
        self.assertEqual(result.data["characters"],26)
        backend.assert_called_once_with(payload)

    def test_unsupported_target_returns_recoverable_error_not_sent_success(self):
        payload={"text":"A","handle":"0xDEAD","expected_pid":20}
        with patch("ordax_dev_agent.computer_background_input.type_to_background_edit",
                   side_effect=UnsupportedBackgroundTarget("background_target_closed")):
            result=ComputerControlActions().computer_type(payload)
        self.assertFalse(result.ok)
        self.assertEqual(result.data["error_code"],"background_target_closed")
        self.assertFalse(result.data["action_dispatched"])


if __name__ == "__main__":
    unittest.main()
