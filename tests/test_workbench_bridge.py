from __future__ import annotations

import unittest

from ordax_studio.workbench_bridge import _ALLOWED_METHODS, _invoke


class _FakeApi:
    def projects_catalog(self):
        return {"ok": True, "projects": []}

    def search(self, query: str, max_results: int = 50):
        return {"ok": True, "query": query, "max_results": max_results}

    def _activate(self, slug: str):
        return {"slug": slug}


class WorkbenchBridgeTests(unittest.TestCase):
    def test_bridge_exposes_only_explicit_studio_methods(self):
        self.assertIn("projects_catalog", _ALLOWED_METHODS)
        self.assertIn("preview_status", _ALLOWED_METHODS)
        self.assertIn("product_status", _ALLOWED_METHODS)
        self.assertIn("remote_computer_grants", _ALLOWED_METHODS)
        self.assertIn("authorize_remote_computer_grant", _ALLOWED_METHODS)
        self.assertIn("revoke_remote_computer_grant", _ALLOWED_METHODS)
        self.assertIn("remote_browser_grants", _ALLOWED_METHODS)
        self.assertIn("authorize_remote_browser_grant", _ALLOWED_METHODS)
        self.assertIn("revoke_remote_browser_grant", _ALLOWED_METHODS)
        self.assertIn("execution_status", _ALLOWED_METHODS)
        self.assertNotIn("_activate", _ALLOWED_METHODS)
        self.assertNotIn("__dict__", _ALLOWED_METHODS)

    def test_bridge_routes_positional_and_keyword_arguments(self):
        api = _FakeApi()
        self.assertEqual({"ok": True, "projects": []}, _invoke(api, "projects_catalog", []))
        self.assertEqual(
            {"ok": True, "query": "renderer", "max_results": 12},
            _invoke(api, "search", ["renderer", 12]),
        )
        self.assertEqual(
            {"ok": True, "query": "water", "max_results": 7},
            _invoke(api, "search", {"query": "water", "max_results": 7}),
        )

    def test_bridge_refuses_private_or_unknown_calls(self):
        api = _FakeApi()
        with self.assertRaises(ValueError):
            _invoke(api, "_activate", ["secret"])
        with self.assertRaises(ValueError):
            _invoke(api, "missing", [])


if __name__ == "__main__":
    unittest.main()
