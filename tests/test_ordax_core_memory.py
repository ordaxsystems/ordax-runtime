import json
import tempfile
import unittest
from pathlib import Path

from ordax_core.memory import MemoryStore


class OrdaxCoreMemoryTests(unittest.TestCase):
    def test_project_memory_survives_store_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            db = root / "state.db"

            first = MemoryStore(db)
            memory_id = first.remember("demo", project, "Use metric scale", "decision")
            task_id = first.add_task("demo", project, "Create Blender scene")
            checkpoint_id = first.checkpoint("demo", project, "Foundation created")

            self.assertGreater(memory_id, 0)
            self.assertGreater(task_id, 0)
            self.assertGreater(checkpoint_id, 0)

            reopened = MemoryStore(db)
            context = reopened.context("demo", project)
            self.assertEqual(context["project"]["slug"], "demo")
            self.assertEqual(context["memories"][0]["content"], "Use metric scale")
            self.assertEqual(context["tasks"][0]["title"], "Create Blender scene")
            self.assertFalse(context["tasks"][0]["done"])
            self.assertEqual(context["checkpoints"][0]["summary"], "Foundation created")

            done = reopened.toggle_task("demo", project, task_id)
            self.assertTrue(done)
            final = reopened.context("demo", project)
            self.assertTrue(final["tasks"][0]["done"])

            context_path = reopened.write_context("demo", project)
            text = context_path.read_text(encoding="utf-8")
            self.assertIn("ORDAX Studio", text)
            self.assertIn("Use metric scale", text)
            self.assertIn("Create Blender scene", text)

    def test_status_reports_database_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            store = MemoryStore(root / "state.db")
            store.remember("demo", project, "Persistent fact")
            status = store.status()
            self.assertTrue(status["ok"])
            self.assertEqual(status["counts"]["projects"], 1)
            self.assertEqual(status["counts"]["memories"], 1)
            self.assertEqual(status["counts"]["project_state"], 0)
            json.dumps(status)


    def test_session_resume_persists_active_project_and_boot_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            store = MemoryStore(root / "state.db")
            store.remember("demo", project, "Keep session continuity", "decision")
            checkpoint_id = store.checkpoint("demo", project, "Ready to resume")
            session = store.start_session("demo", project)
            self.assertGreater(session["session_id"], 0)
            self.assertEqual(session["resumed_from_checkpoint_id"], checkpoint_id)
            self.assertEqual(store.active_project()["name"], "demo")
            boot = Path(session["boot_context_path"])
            self.assertTrue(boot.is_file())
            self.assertIn("Keep session continuity", boot.read_text(encoding="utf-8"))

            reopened = MemoryStore(root / "state.db")
            self.assertEqual(reopened.active_project()["name"], "demo")
            second = reopened.start_session("demo", project)
            self.assertNotEqual(second["session_id"], session["session_id"])
            self.assertFalse(reopened.finish_session(session["session_id"]))
            self.assertTrue(reopened.finish_session(second["session_id"]))
            self.assertEqual(reopened.status()["counts"]["sessions"], 2)

    def test_durable_project_state_survives_reopen_and_checkpoint_preserves_details(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            db = root / "state.db"
            store = MemoryStore(db)

            state = store.update_project_state(
                "demo",
                project,
                "Foundation is ready",
                next_action="Implement navigation",
                completed=["project bootstrap"],
                blockers=["waiting for asset"],
                changed_paths=["src/app.py"],
            )
            self.assertEqual(state["next_action"], "Implement navigation")
            self.assertEqual(state["source"], "manual")

            checkpoint_id = store.checkpoint("demo", project, "Navigation skeleton complete")
            reopened = MemoryStore(db)
            persisted = reopened.project_state("demo", project)
            self.assertIsNotNone(persisted)
            self.assertEqual(persisted["summary"], "Navigation skeleton complete")
            self.assertEqual(persisted["next_action"], "Implement navigation")
            self.assertEqual(persisted["blockers"], ["waiting for asset"])
            self.assertEqual(persisted["changed_paths"], ["src/app.py"])
            self.assertEqual(persisted["source"], "checkpoint")
            self.assertEqual(persisted["source_ref"], str(checkpoint_id))
            self.assertEqual(reopened.status()["counts"]["project_state"], 1)

            context = reopened.context("demo", project)
            self.assertEqual(context["project_state"]["summary"], "Navigation skeleton complete")
            text = reopened.write_context("demo", project).read_text(encoding="utf-8")
            self.assertIn("Estado durável", text)
            self.assertIn("Implement navigation", text)

    def test_search_returns_bounded_project_scoped_recall(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            store = MemoryStore(root / "state.db")
            store.remember("first", first, "Water gameplay must support diving", "decision")
            store.remember("first", first, "Traffic system remains pending", "note")
            store.add_task("first", first, "Polish diving movement")
            store.checkpoint("first", first, "Diving controller foundation complete")
            store.update_project_state(
                "first", first, "Aquatic gameplay foundation",
                next_action="Tune diving physics",
                changed_paths=["src/water.py"],
            )
            store.remember("second", second, "Diving belongs to another project")

            hits = store.search("first", first, "diving", limit=10)
            self.assertGreaterEqual(len(hits), 3)
            self.assertEqual(hits[0]["type"], "project_state")
            self.assertTrue(all("another project" not in item["snippet"] for item in hits))
            self.assertTrue(any(item["type"] == "memory" for item in hits))
            self.assertTrue(any(item["type"] == "task" for item in hits))
            self.assertTrue(any(item["type"] == "checkpoint" for item in hits))
            self.assertEqual(store.search("first", first, "", limit=10), [])
            with self.assertRaisesRegex(ValueError, "between 1 and 50"):
                store.search("first", first, "diving", limit=51)

    def test_handoff_refreshes_durable_state_without_sharing_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            store = MemoryStore(root / "state.db")
            handoff = store.create_handoff(
                "demo",
                project,
                "Renderer stabilized",
                next_action="Profile frame time",
                completed=["renderer"],
                blockers=[],
                changed_paths=["render/core.py"],
                ttl_hours=1,
            )
            state = store.project_state("demo", project)
            self.assertIsNotNone(state)
            self.assertEqual(state["summary"], "Renderer stabilized")
            self.assertEqual(state["next_action"], "Profile frame time")
            self.assertEqual(state["source"], "handoff")
            self.assertEqual(state["source_ref"], handoff["handoff_id"])
            self.assertNotIn("expires_at", state)

    def test_expiring_handoff_round_trip_is_project_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_project = root / "first"
            second_project = root / "second"
            first_project.mkdir()
            second_project.mkdir()
            store = MemoryStore(root / "state.db")

            handoff = store.create_handoff(
                "first",
                first_project,
                "Backend refactor is complete",
                next_action="Run regression tests",
                completed=["refactor complete"],
                blockers=["none"],
                changed_paths=["src/backend.py"],
                ttl_hours=24,
            )
            self.assertTrue(handoff["handoff_id"].startswith("hof_"))
            loaded = store.get_handoff("first", first_project, handoff["handoff_id"])
            self.assertEqual(loaded["summary"], "Backend refactor is complete")
            self.assertEqual(loaded["next_action"], "Run regression tests")
            self.assertEqual(loaded["completed"], ["refactor complete"])
            self.assertEqual(loaded["changed_paths"], ["src/backend.py"])
            self.assertIn("git", loaded)

            with self.assertRaisesRegex(ValueError, "not found for project"):
                store.get_handoff("second", second_project, handoff["handoff_id"])

            self.assertEqual(store.status()["counts"]["handoffs"], 1)

    def test_handoff_rejects_invalid_id_and_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            store = MemoryStore(root / "state.db")
            with self.assertRaisesRegex(ValueError, "summary"):
                store.create_handoff("demo", project, "")
            with self.assertRaisesRegex(ValueError, "ttl_hours"):
                store.create_handoff("demo", project, "summary", ttl_hours=0)
            with self.assertRaisesRegex(ValueError, "invalid handoff id"):
                store.get_handoff("demo", project, "bad")

    def test_start_session_closes_previous_project_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_project = root / "first"
            second_project = root / "second"
            first_project.mkdir()
            second_project.mkdir()
            store = MemoryStore(root / "state.db")
            first = store.start_session("first", first_project)
            second = store.start_session("second", second_project)
            self.assertFalse(store.finish_session(first["session_id"]))
            self.assertTrue(store.finish_session(second["session_id"]))
            self.assertEqual(store.active_project()["name"], "second")


if __name__ == "__main__":
    unittest.main()
