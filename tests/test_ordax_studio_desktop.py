from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

from ordax_studio.cli import build_parser


ROOT = Path(__file__).resolve().parents[1]


class OrdaxStudioSingleDesktopTests(unittest.TestCase):
    def test_tkinter_desktop_and_preview_are_not_shipped(self) -> None:
        self.assertFalse((ROOT / "ordax_studio" / "desktop.py").exists())
        self.assertFalse((ROOT / "ordax_studio" / "preview.py").exists())
        self.assertFalse((ROOT / "ordax_studio" / "assets").exists())

    def test_cli_does_not_offer_a_second_desktop_implementation(self) -> None:
        parser = build_parser()
        subcommands = next(a.choices for a in parser._actions if hasattr(a, "choices") and isinstance(a.choices, dict))
        self.assertNotIn("desktop", subcommands)
        self.assertIn("status", subcommands)
        self.assertIn("mcp", subcommands)

    def test_python_package_has_no_parallel_studio_desktop_entrypoint(self) -> None:
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        scripts = pyproject["project"]["scripts"]
        self.assertNotIn("ordax-studio-desktop", scripts)
        self.assertEqual("ordax_studio.cli:main", scripts["ordax-studio"])
        self.assertEqual("ordax_studio.product_web_desktop:main", scripts["ordax-dev"])


if __name__ == "__main__":
    unittest.main()
