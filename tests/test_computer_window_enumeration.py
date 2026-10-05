from __future__ import annotations

import unittest

from ordax_dev_agent.computer_control_actions import ComputerControlActions


class ComputerWindowEnumerationTests(unittest.TestCase):
    def test_normal_app_window_is_user_selectable(self) -> None:
        self.assertTrue(ComputerControlActions._window_style_is_user_selectable(0))

    def test_tool_window_without_appwindow_is_hidden_from_user_catalog(self) -> None:
        self.assertFalse(
            ComputerControlActions._window_style_is_user_selectable(0x00000080)
        )

    def test_explicit_appwindow_overrides_tool_window_filter(self) -> None:
        self.assertTrue(
            ComputerControlActions._window_style_is_user_selectable(
                0x00000080 | 0x00040000
            )
        )

    def test_signed_extended_style_is_normalized_to_32_bits(self) -> None:
        signed_tool_style = -2147483520  # 0x80000080
        self.assertFalse(
            ComputerControlActions._window_style_is_user_selectable(signed_tool_style)
        )


if __name__ == "__main__":
    unittest.main()
