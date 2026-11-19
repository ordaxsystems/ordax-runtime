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

    def test_observational_device_actions_overlap_long_terminal_work(self) -> None:
        first = self.coordinator()
        second = self.coordinator()
        terminal = first.try_acquire(
            "computer.terminal_exec", {"cwd": str(self.state)},
            project_resolver=self.project,
        )
        self.assertIsNotNone(terminal)
        try:
            for action, payload in (
                ("computer.process_list", {}),
                ("computer.directory_list", {"path": str(self.state)}),
                ("computer.screen_info", {}),
            ):
                observation = second.try_acquire(
                    action, payload, project_resolver=self.project,
                )
                self.assertIsNotNone(observation, action)
                observation.release()
        finally:
            terminal.release()

    def test_command_starts_in_same_cwd_can_overlap(self) -> None:
        for action, payload in (
            ("computer.terminal_exec", {"cwd": str(self.state)}),
            ("computer.process_start", {"cwd": str(self.state)}),
            ("terminal.exec", {"project": "alpha", "cwd": str(self.state)}),
            ("process.start", {"project": "alpha", "cwd": str(self.state)}),
        ):
            with self.subTest(action=action):
                first = self.coordinator()
                second = self.coordinator()
                a = first.try_acquire(
                    action, payload, project_resolver=self.project,
                )
                self.assertIsNotNone(a)
                try:
                    b = second.try_acquire(
                        action, payload, project_resolver=self.project,
                    )
                    self.assertIsNotNone(
                        b,
                        f"{action} must not serialize independent starts by cwd",
                    )
                    b.release()
                finally:
                    a.release()

    def test_observation_lane_remains_available_when_execution_is_full(self) -> None:
        coordinator = ActionConcurrencyCoordinator(self.state, max_slots=2)
        first = coordinator.try_acquire(
            "computer.terminal_exec", {"cwd": str(self.state)},
            project_resolver=self.project,
        )
        second = coordinator.try_acquire(
            "computer.terminal_exec", {"cwd": str(self.state)},
            project_resolver=self.project,
        )
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        try:
            self.assertIsNone(coordinator.try_acquire(
                "computer.terminal_exec", {"cwd": str(self.state)},
                project_resolver=self.project,
            ))
            read = coordinator.try_acquire(
                "computer.directory_list", {"path": str(self.state)},
                project_resolver=self.project,
            )
            self.assertIsNotNone(read)
            if read is not None:
                read.release()
        finally:
            if second is not None:
                second.release()
            if first is not None:
                first.release()

    def test_global_maintenance_excludes_observations_too(self) -> None:
        coordinator = ActionConcurrencyCoordinator(self.state, max_slots=2)
        update = coordinator.try_acquire(
            "agent.update", {}, project_resolver=self.project,
        )
        self.assertIsNotNone(update)
        try:
            self.assertIsNone(coordinator.try_acquire(
                "computer.directory_list", {"path": str(self.state)},
                project_resolver=self.project,
            ))
        finally:
            if update is not None:
                update.release()

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
