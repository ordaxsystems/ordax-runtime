from __future__ import annotations

import unittest
from pathlib import Path


class OrdaxIntelligenceModeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[1]
        cls.contract = (cls.root / "docs" / "ORDAX_INTELLIGENCE_MODES.md").read_text(
            encoding="utf-8"
        )
        cls.account_ui = (
            cls.root / "ordax_studio" / "assets" / "product_account.js"
        ).read_text(encoding="utf-8")

    def test_external_chatgpt_plan_is_not_normal_chat_quota(self) -> None:
        self.assertIn("ChatGPT Work e Codex", self.contract)
        self.assertIn("não usam a cota do chat normal", self.contract)
        self.assertIn("chat normal != Work/Codex != API", self.contract)

    def test_local_ai_does_not_silently_escalate(self) -> None:
        self.assertIn("IA local 24h", self.contract)
        self.assertIn("Nunca fazer fallback", self.contract)
        self.assertIn("sem autorização", self.contract)

    def test_ordax_account_ui_separates_account_from_provider_usage(self) -> None:
        self.assertIn("conectar sua Conta ORDAX não conecta automaticamente nenhum provedor de IA", self.account_ui)
        self.assertIn("não consome cota de nenhum provedor", self.account_ui)
        self.assertIn("Cada conector é configurado separadamente", self.account_ui)
        self.assertNotIn("ChatGPT Work e Codex", self.account_ui)
        self.assertNotIn("cota do chat normal", self.account_ui)

    def test_browser_automation_is_not_the_product_contract(self) -> None:
        self.assertIn("não deve automatizar `chatgpt.com`", self.contract)
        self.assertIn("handoff é um checkpoint estruturado", self.contract)


if __name__ == "__main__":
    unittest.main()
