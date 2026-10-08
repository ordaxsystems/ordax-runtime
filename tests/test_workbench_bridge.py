from __future__ import annotations

import unittest
import re
from pathlib import Path

from ordax_studio.workbench_bridge import _ALLOWED_METHODS, _invoke
from ordax_studio.product_web_desktop import StudioProductApi


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
        self.assertIn("execution_status", _ALLOWED_METHODS)
        self.assertIn("assistant_catalog", _ALLOWED_METHODS)
        self.assertIn("assistant_state", _ALLOWED_METHODS)
        self.assertIn("assistant_create_chat", _ALLOWED_METHODS)
        self.assertIn("assistant_select_chat", _ALLOWED_METHODS)
        self.assertIn("assistant_update_chat", _ALLOWED_METHODS)
        self.assertIn("assistant_close_chat", _ALLOWED_METHODS)
        self.assertNotIn("_activate", _ALLOWED_METHODS)
        self.assertNotIn("__dict__", _ALLOWED_METHODS)

    def test_all_native_ui_methods_have_one_canonical_allowlist_and_product_implementation(self):
        source = (Path(__file__).resolve().parents[1] / "ordax_studio" / "host_bridge.js").read_text(encoding="utf-8")
        native_ui_methods = re.findall(r"invoke\('([^']+)',args\)", source)
        self.assertEqual(len(native_ui_methods), len(set(native_ui_methods)), "duplicated native method wiring")
        self.assertEqual(set(native_ui_methods), _ALLOWED_METHODS,
                         "typed Studio host and Workbench allowlist must have identical authority")
        for method in native_ui_methods:
            self.assertTrue(callable(getattr(StudioProductApi, method, None)),
                            f"StudioProductApi must implement native method {method}")

    def test_browser_authorization_remains_typed_and_fail_closed(self):
        for method in (
            "remote_project_browser_grants",
            "authorize_remote_project_browser_grant",
            "revoke_remote_project_browser_grant",
            "browser_list", "browser_start", "browser_status", "browser_navigate",
            "browser_snapshot", "browser_screenshot", "browser_stop",
        ):
            self.assertIn(method, _ALLOWED_METHODS)
        for forbidden in ("call", "execute", "terminal_exec", "device_agent", "generic_dispatch"):
            self.assertNotIn(forbidden, _ALLOWED_METHODS)

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
