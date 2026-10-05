from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ordax_core.memory import MemoryStore
from ordax_core.orchestrator import OrchestratorStore
from ordax_studio.web_desktop import StudioApi


ROOT = Path(__file__).resolve().parents[1]


class StudioAiSessionsSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.html = (ROOT / "ordax_studio" / "studio_product.html").read_text(encoding="utf-8")
        self.js = (ROOT / "ordax_studio" / "assets" / "studio.js").read_text(encoding="utf-8")
        self.api_source = (ROOT / "ordax_studio" / "web_desktop.py").read_text(encoding="utf-8")

    def test_surface_exposes_real_ai_sessions_view(self) -> None:
        self.assertIn('data-view="sessions"', self.html)
        self.assertIn('id="sessionsCanvas"', self.html)
        self.assertIn("call('ai_sessions_status')", self.js)
        self.assertIn("Esta tela não automatiza sites de IA", self.js)
        self.assertIn("rollover_at_tokens", self.js)
        self.assertIn("session.provider", self.js)
        self.assertIn("session.model", self.js)

    def test_sessions_api_reuses_memory_ssot(self) -> None:
        self.assertIn("OrchestratorStore(self.store.db_path)", self.api_source)
        self.assertIn("self.orchestrator.status(self.project)", self.api_source)
        self.assertIn("self.orchestrator.continuation_bundle", self.api_source)
        self.assertNotIn("OrchestratorStore()", self.api_source)

    def test_sessions_api_returns_real_continuation_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            memory = MemoryStore(Path(directory) / "memory.db")
            api = StudioApi.__new__(StudioApi)
            api.store = memory
            api.orchestrator = OrchestratorStore(memory.db_path)
            api.project = "demo"

            coordinator = api.orchestrator.create_agent("demo", "Coordinator", "coordinator")
            goal = api.orchestrator.create_goal(
                coordinator["id"], "Refinar projeto", "Continuar preservando contexto."
            )
            session = api.orchestrator.start_session(
                coordinator["id"],
                goal_id=goal["id"],
                provider="openai",
                model="gpt",
                context_window_tokens=100000,
                rollover_ratio=0.80,
            )
            api.orchestrator.record_usage(
                session["id"], input_tokens=1000, output_tokens=500
            )
            rotated = api.orchestrator.rotate_session(
                session["id"], summary="Estado salvo", next_action="Continuar ajustes"
            )

            result = api.ai_sessions_status()
            self.assertTrue(result["ok"])
            self.assertEqual("demo", result["data"]["project"])
            self.assertEqual(1, len(result["data"]["active_sessions"]))
            continuation = result["data"]["continuations"][0]
            self.assertEqual("Coordinator", continuation["agent"]["name"])
            self.assertEqual("openai", continuation["session"]["provider"])
            self.assertEqual("gpt", continuation["session"]["model"])
            self.assertEqual(
                rotated["session"]["id"], continuation["session"]["id"]
            )
            self.assertEqual(
                "Estado salvo", continuation["latest_checkpoint"]["summary"]
            )
            self.assertEqual(
                "Continuar ajustes", continuation["latest_checkpoint"]["next_action"]
            )


if __name__ == "__main__":
    unittest.main()
