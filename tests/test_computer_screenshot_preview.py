from __future__ import annotations

import base64
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageGrab
from ordax_dev_agent.computer_control_actions import ComputerControlActions


class FakeComputer(ComputerControlActions):
    def __init__(self, root: Path) -> None:
        self.config = SimpleNamespace(state_dir=root)

    @staticmethod
    def _windows_only() -> None:
        pass


class ScreenshotPreviewTests(unittest.TestCase):
    def test_default_preserves_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            fake = FakeComputer(Path(root))
            with patch.object(ImageGrab, "grab", return_value=Image.new("RGB", (1280, 800), (32, 64, 96))):
                result = fake.computer_screenshot({"mode": "desktop"})
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(result.data["scope"], "device")
            self.assertEqual(result.data["width"], 1280)
            self.assertNotIn("base64", result.data)
            self.assertNotIn("mime_type", result.data)
            self.assertTrue(Path(result.data["image_path"]).is_file())

    def test_opt_in_preview_reuses_one_capture_and_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            fake = FakeComputer(Path(root))
            with patch.object(ImageGrab, "grab", return_value=Image.new("RGB", (1920, 1080), (70, 20, 120))) as grab:
                result = fake.computer_screenshot({"mode": "desktop", "include_preview": True})
                grab.assert_called_once_with(all_screens=True)
            self.assertTrue(result.ok, result.summary)
            data = result.data
            self.assertEqual(data["mime_type"], "image/jpeg")
            self.assertLessEqual(data["size_bytes"], 262144)
            self.assertEqual(data["source_size_bytes"], Path(data["image_path"]).stat().st_size)
            preview = base64.b64decode(data["base64"], validate=True)
            self.assertEqual(hashlib.sha256(preview).hexdigest(), data["sha256"])
            self.assertEqual(len(preview), data["size_bytes"])
            with Image.open(io.BytesIO(preview)) as image:
                self.assertEqual(image.format, "JPEG")
                self.assertLessEqual(image.width, 960)
                self.assertLessEqual(image.height, 720)

    def test_invalid_opt_in_rejected_before_capture(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            fake = FakeComputer(Path(root))
            with patch.object(ImageGrab, "grab") as grab:
                result = fake.computer_screenshot({"include_preview": "true"})
            self.assertFalse(result.ok)
            grab.assert_not_called()


if __name__ == "__main__":
    unittest.main()
