from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ordax_dev_agent.action_concurrency import ActionConcurrencyCoordinator


class ActionConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name)

    @staticmethod
    def project(payload):
        return SimpleNamespace(slug=str(payload.get("project") or "default"))

    def coordinator(self) -> ActionConcurrencyCoordinator:
        return ActionConcurrencyCoordinator(self.state)

    def test_independent_file_resources_can_overlap(self) -> None:
        first = self.coordinator()
        second = self.coordinator()
        a = first.try_acquire(
            "computer.text_write", {"path": str(self.state / "a.txt")},
            project_resolver=self.project,
        )
        self.assertIsNotNone(a)
        try:
            b = second.try_acquire(
                "computer.text_write", {"path": str(self.state / "b.txt")},
                project_resolver=self.project,
            )
            self.assertIsNotNone(b)
            b.release()
        finally:
            a.release()

    def test_same_file_resource_conflicts_across_clients(self) -> None:
        first = self.coordinator()
        second = self.coordinator()
        payload = {"path": str(self.state / "same.txt")}
        a = first.try_acquire(
            "computer.text_write", payload, project_resolver=self.project,
        )
        self.assertIsNotNone(a)
        try:
            self.assertIsNone(second.try_acquire(
                "computer.text_patch", payload, project_resolver=self.project,
            ))
        finally:
            a.release()
        b = second.try_acquire(
            "computer.text_patch", payload, project_resolver=self.project,
        )
        self.assertIsNotNone(b)
        b.release()

    def test_graphical_input_remains_exclusive(self) -> None:
        first = self.coordinator()
        second = self.coordinator()
        click = first.try_acquire(
            "computer.click", {"x": 10, "y": 10}, project_resolver=self.project,
        )
        self.assertIsNotNone(click)
        try:
            self.assertIsNone(second.try_acquire(
                "computer.type", {"text": "x"}, project_resolver=self.project,
            ))
        finally:
            click.release()

    def test_runtime_update_remains_globally_exclusive(self) -> None:
        first = self.coordinator()
        second = self.coordinator()
        active = first.try_acquire(
            "computer.text_write", {"path": str(self.state / "active.txt")},
            project_resolver=self.project,
        )
        self.assertIsNotNone(active)
        try:
            self.assertIsNone(second.try_acquire(
                "agent.update", {}, project_resolver=self.project,
            ))
        finally:
            active.release()
        update = second.try_acquire(
            "agent.update", {}, project_resolver=self.project,
        )
        self.assertIsNotNone(update)
        update.release()

    def test_different_projects_can_overlap(self) -> None:
        first = self.coordinator()
        second = self.coordinator()
        a = first.try_acquire(
            "project.observe", {"project": "alpha"}, project_resolver=self.project,
        )
        self.assertIsNotNone(a)
        try:
            b = second.try_acquire(
                "project.observe", {"project": "beta"}, project_resolver=self.project,
            )
            self.assertIsNotNone(b)
            b.release()
        finally:
            a.release()


if __name__ == "__main__":
    unittest.main()
