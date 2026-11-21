from __future__ import annotations

import base64
import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from ordax_dev_agent.browser_session_actions import BrowserSessionActions


class FakeBrowser(BrowserSessionActions):
    def __init__(self, root: Path) -> None:
        self.config = SimpleNamespace(state_dir=root)
        self.project = SimpleNamespace(slug="demo")

    def _project(self, _payload):
        return self.project

    def _with_page(self, project, session_id, callback, tab_id=None):
        if project is not self.project or session_id != "test-session":
            raise ValueError("wrong owned browser session")
        return callback(None, None, None)


class BrowserScreenshotPreviewTests(unittest.TestCase):
    def png_bytes(self) -> bytes:
        image = Image.new("RGBA", (1440, 900), (70, 20, 140, 160))
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    def test_default_returns_only_original_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = FakeBrowser(Path(tmp))
            raw = self.png_bytes()

            def cdp(_ws, _id, method, _args):
                return {"data": base64.b64encode(raw).decode("ascii")} if method == "Page.captureScreenshot" else {}

            with patch("ordax_dev_agent.browser_session_actions._cdp_call", side_effect=cdp) as capture:
                result = host.browser_screenshot({"session_id": "test-session"})
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(capture.call_count, 2)
            self.assertEqual(result.data["size_bytes"], len(raw))
            self.assertNotIn("base64", result.data)
            self.assertNotIn("mime_type", result.data)
            self.assertEqual(Path(result.data["image_path"]).read_bytes(), raw)

    def test_opt_in_reuses_exactly_one_cdp_screenshot_and_bounds_jpeg(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = FakeBrowser(Path(tmp))
            raw = self.png_bytes()

            def cdp(_ws, _id, method, _args):
                return {"data": base64.b64encode(raw).decode("ascii")} if method == "Page.captureScreenshot" else {}

            with patch("ordax_dev_agent.browser_session_actions._cdp_call", side_effect=cdp) as capture:
                result = host.browser_screenshot({
                    "session_id": "test-session", "include_preview": True,
                })
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(capture.call_count, 2)
            self.assertEqual(sum(call.args[2] == "Page.captureScreenshot" for call in capture.call_args_list), 1)
            data = result.data
            self.assertEqual(data["source_size_bytes"], len(raw))
            self.assertEqual(Path(data["image_path"]).read_bytes(), raw)
            self.assertEqual(data["mime_type"], "image/jpeg")
            self.assertLessEqual(data["size_bytes"], 262144)
            image_bytes = base64.b64decode(data["base64"], validate=True)
            self.assertEqual(len(image_bytes), data["size_bytes"])
            self.assertEqual(hashlib.sha256(image_bytes).hexdigest(), data["sha256"])
            with Image.open(io.BytesIO(image_bytes)) as image:
                self.assertEqual(image.format, "JPEG")
                self.assertEqual(image.size, tuple(data["thumbnail_size"]))
                self.assertLessEqual(image.width, 960)
                self.assertLessEqual(image.height, 720)

    def test_invalid_opt_in_is_rejected_without_cdp_or_filesystem_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = FakeBrowser(Path(tmp))
            with patch("ordax_dev_agent.browser_session_actions._cdp_call") as capture:
                result = host.browser_screenshot({
                    "session_id": "test-session", "include_preview": "yes",
                })
            self.assertFalse(result.ok)
            capture.assert_not_called()
            self.assertFalse((Path(tmp) / "artifacts").exists())


if __name__ == "__main__":
    unittest.main()
