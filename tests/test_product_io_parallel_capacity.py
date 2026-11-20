"""Capacity is one local policy, not three independent device-wide queues."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from ordax_dev_agent.config import product_execution_capacity
from ordax_dev_agent.action_concurrency import ActionConcurrencyCoordinator


class ProductIoParallelCapacityTests(unittest.TestCase):
    @staticmethod
    def project(payload):
        return SimpleNamespace(slug="default")

    def test_io_bound_workers_scale_beyond_cpu_count(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ORDAX_PRODUCT_CONCURRENT_JOBS", None)
            for cpu_count, expected in ((1, 4), (2, 4), (4, 8), (8, 16), (32, 16)):
                with self.subTest(cpus=cpu_count), patch(
                    "ordax_dev_agent.config.os.cpu_count", return_value=cpu_count
                ):
                    self.assertEqual(product_execution_capacity(), expected)

    def test_dev_override_remains_explicit_and_bounded(self):
        for workers in (1, 8, 12, 32):
            with self.subTest(workers=workers), patch.dict(
                os.environ, {"ORDAX_PRODUCT_CONCURRENT_JOBS": str(workers)}
            ):
                self.assertEqual(product_execution_capacity(), workers)
        for invalid in ("", "0", "33", "-1", "abc", " 8", "8.0"):
            with self.subTest(invalid=invalid), patch.dict(
                os.environ, {"ORDAX_PRODUCT_CONCURRENT_JOBS": invalid}
            ):
                with self.assertRaises(ValueError):
                    product_execution_capacity()

    def test_resource_lanes_scale_with_workers_and_keep_observations_independent(self):
        with tempfile.TemporaryDirectory() as raw:
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("ORDAX_PRODUCT_CONCURRENT_JOBS", None)
                with patch("ordax_dev_agent.config.os.cpu_count", return_value=4):
                    coordinator = ActionConcurrencyCoordinator(Path(raw))
                    self.assertEqual(coordinator.max_slots, 8)
                    held = []
                    try:
                        for _ in range(8):
                            lease = coordinator.try_acquire(
                                "computer.terminal_exec", {"cwd": raw},
                                project_resolver=self.project,
                            )
                            self.assertIsNotNone(lease)
                            held.append(lease)
                        self.assertIsNone(coordinator.try_acquire(
                            "computer.terminal_exec", {"cwd": raw},
                            project_resolver=self.project,
                        ))
                        observation = coordinator.try_acquire(
                            "computer.directory_list", {"path": raw},
                            project_resolver=self.project,
                        )
                        self.assertIsNotNone(observation)
                        if observation:
                            observation.release()
                        self.assertIsNone(coordinator.try_acquire(
                            "agent.update", {}, project_resolver=self.project,
                        ))
                    finally:
                        for lease in reversed(held):
                            lease.release()

    def test_one_worker_override_does_not_break_local_resource_coordinator(self):
        with tempfile.TemporaryDirectory() as raw, patch.dict(
            os.environ, {"ORDAX_PRODUCT_CONCURRENT_JOBS": "1"}
        ):
            coordinator = ActionConcurrencyCoordinator(Path(raw))
            self.assertEqual(coordinator.max_slots, 1)
            first = coordinator.try_acquire(
                "computer.terminal_exec", {"cwd": raw}, project_resolver=self.project,
            )
            self.assertIsNotNone(first)
            if first:
                first.release()

    def test_http_pool_and_remote_executor_use_same_policy(self):
        root = Path(__file__).resolve().parents[1] / "ordax_dev_agent"
        source = (root / "product_device_control_plane.py").read_text(encoding="utf-8")
        main = (root / "main.py").read_text(encoding="utf-8")
        self.assertIn("worker_capacity = product_execution_capacity()", source)
        self.assertIn("max_connections=worker_capacity + 2", source)
        self.assertIn("_MAX_CONCURRENT_PRODUCT_JOBS = _product_execution_capacity()", main)
        self.assertIn("_product_execution_capacity = product_execution_capacity", main)


if __name__ == "__main__":
    unittest.main()
