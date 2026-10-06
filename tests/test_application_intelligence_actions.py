from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ordax_dev_agent.application_intelligence_actions import (
    ApplicationIntelligenceActions,
    load_app_intelligence_registry,
)


def registry_payload(*, authority: str = "none") -> dict:
    return {
        "schema": "ordax.app-intelligence-registry/1",
        "authority": authority,
        "source": {
            "repository": "washingtonmsdj/ordax-apps",
            "commit": "6baff254e01dd9dee1d994ab31ec6a0028d92671",
        },
        "apps": [
            {
                "id": "studio",
                "title": "ORDAX Studio",
                "version": "0.4.4",
                "manifest": {
                    "schema": "ordax.app-intelligence-manifest/1",
                    "appId": "studio",
                    "appVersion": "0.4.4",
                    "authority": "none",
                    "execution": "declarative-only",
                    "instructions": [
                        "Use o ORDAX Studio somente através dos ports públicos fornecidos pelo host."
                    ],
                    "intents": [
                        {
                            "id": "studio.open-managed-browser",
                            "description": "Abrir uma sessão de navegador gerenciado.",
                            "effect": "write",
                            "confirmation": "policy",
                            "parameters": [
                                {
                                    "name": "url",
                                    "type": "string",
                                    "required": True,
                                    "description": "URL HTTP ou HTTPS.",
                                }
                            ],
                            "examples": [
                                "Abra o YouTube no navegador gerenciado deste projeto."
                            ],
                        }
                    ],
                },
            }
        ],
    }


class _Actions(ApplicationIntelligenceActions):
    pass


class ApplicationIntelligenceActionsTests(unittest.TestCase):
    def write_registry(self, root: Path, payload: dict) -> Path:
        path = root / "app_intelligence_registry.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_missing_registry_is_explicitly_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = load_app_intelligence_registry(
                Path(directory) / "missing-app-intelligence.json"
            )
        self.assertFalse(registry["available"])
        self.assertEqual(registry["apps"], [])
        self.assertEqual(registry["by_id"], {})

    def test_catalog_is_compact_and_detail_is_on_demand(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = load_app_intelligence_registry(
                self.write_registry(Path(directory), registry_payload())
            )

        actions = _Actions()
        actions._app_intelligence_registry = registry

        catalog = actions.intelligence_app_catalog({})
        self.assertTrue(catalog.ok)
        self.assertEqual(catalog.data["authority"], "none")
        self.assertFalse(catalog.data["tool_execution"])
        self.assertEqual(catalog.data["apps"], [{
            "app_id": "studio",
            "title": "ORDAX Studio",
            "app_version": "0.4.4",
            "intent_ids": ["studio.open-managed-browser"],
        }])
        self.assertNotIn("manifest", catalog.data["apps"][0])
        self.assertNotIn("instructions", catalog.data["apps"][0])

        detail = actions.intelligence_app_detail({"app_id": "studio"})
        self.assertTrue(detail.ok)
        self.assertEqual(detail.data["authority"], "none")
        self.assertFalse(detail.data["tool_execution"])
        self.assertEqual(
            detail.data["manifest"]["intents"][0]["id"],
            "studio.open-managed-browser",
        )
        self.assertEqual(
            detail.data["manifest"]["instructions"][0],
            "Use o ORDAX Studio somente através dos ports públicos fornecidos pelo host.",
        )

    def test_unknown_app_fails_without_fuzzy_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = load_app_intelligence_registry(
                self.write_registry(Path(directory), registry_payload())
            )

        actions = _Actions()
        actions._app_intelligence_registry = registry
        result = actions.intelligence_app_detail({"app_id": "studio beta"})
        self.assertFalse(result.ok)
        self.assertEqual(result.data["error_code"], "app_intelligence_app_id_invalid")

    def test_registry_cannot_carry_execution_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_registry(
                Path(directory),
                registry_payload(authority="execute"),
            )
            with self.assertRaisesRegex(ValueError, "authority/schema"):
                load_app_intelligence_registry(path)


if __name__ == "__main__":
    unittest.main()
