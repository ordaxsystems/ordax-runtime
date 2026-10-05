from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent import windows_dpi


ROOT = Path(__file__).resolve().parents[1]


class ComputerDpiCoordinateSpaceTests(unittest.TestCase):
    def test_non_windows_reports_unsupported_without_side_effects(self) -> None:
        with patch.object(windows_dpi.os, "name", "posix"):
            status = windows_dpi.ensure_physical_desktop_coordinates()
        self.assertFalse(status["supported"])
        self.assertFalse(status["physical_pixels"])
        self.assertEqual("unsupported", status["mode"])

    def test_registry_establishes_dpi_before_loading_projects(self) -> None:
        source = (ROOT / "ordax_dev_agent" / "actions.py").read_text(encoding="utf-8")
        init = source.index("def __init__(self, config: AgentConfig):")
        dpi = source.index("ensure_physical_desktop_coordinates()", init)
        projects = source.index("load_projects(config)", init)
        self.assertLess(dpi, projects)

    def test_screen_and_screenshot_share_physical_pixel_contract(self) -> None:
        parity = (ROOT / "ordax_dev_agent" / "computer_parity_actions.py").read_text(encoding="utf-8")
        control = (ROOT / "ordax_dev_agent" / "computer_control_actions.py").read_text(encoding="utf-8")
        self.assertIn('"coordinate_space": "physical_pixels"', parity)
        self.assertIn('"coordinate_space": "physical_pixels"', control)
        helper = (ROOT / "ordax_dev_agent" / "windows_dpi.py").read_text(encoding="utf-8")
        self.assertIn("SetProcessDpiAwarenessContext", helper)
        self.assertIn("_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4", helper)


if __name__ == "__main__":
    unittest.main()
