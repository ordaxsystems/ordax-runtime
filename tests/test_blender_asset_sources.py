import unittest
from unittest.mock import patch

from ordax_dev_agent.blender_asset_sources import (
    polyhaven_file_manifest,
    search_polyhaven,
)


class BlenderAssetSourceTests(unittest.TestCase):
    @patch("ordax_dev_agent.blender_asset_sources._request_json")
    def test_polyhaven_search_filters_query_and_labels_cc0(self, request_json):
        request_json.return_value = {
            "studio_small": {
                "name": "Small Studio",
                "type": "hdris",
                "categories": ["studio"],
                "tags": ["soft", "neutral"],
            },
            "forest_path": {
                "name": "Forest Path",
                "type": "hdris",
                "categories": ["nature"],
                "tags": ["forest"],
            },
        }

        report = search_polyhaven(query="studio", asset_type="hdris")

        self.assertEqual("polyhaven", report["provider"])
        self.assertEqual("CC0", report["license"])
        self.assertEqual(1, report["matching_count"])
        self.assertEqual("studio_small", report["results"][0]["asset_id"])
        self.assertEqual("CC0", report["results"][0]["license"])

    @patch("ordax_dev_agent.blender_asset_sources._request_json")
    def test_polyhaven_manifest_is_typed(self, request_json):
        request_json.return_value = {"hdri": {"1k": {"hdr": {"url": "https://example.invalid/a.hdr"}}}}
        report = polyhaven_file_manifest("studio_small")
        self.assertEqual("polyhaven", report["provider"])
        self.assertEqual("studio_small", report["asset_id"])
        self.assertIn("hdri", report["files"])

    def test_polyhaven_rejects_unsafe_asset_id(self):
        with self.assertRaises(ValueError):
            polyhaven_file_manifest("../escape")


if __name__ == "__main__":
    unittest.main()
