"""Bounded foreground cleanup: a timed-out child cannot hold Product dispatch indefinitely."""
from __future__ import annotations

import io
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ordax_dev_agent.process_runner import run_command


class _StubbornProcess:
    pid = 12345
    returncode = 1

    def __init__(self):
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.timeouts = []
        self.killed = False
        self.wait_timeouts = []

    def communicate(self, *, timeout):
        if timeout is None:
            raise AssertionError("Unbounded communicate is forbidden after timeout")
        self.timeouts.append(timeout)
        raise subprocess.TimeoutExpired(["test-only"], timeout)

    def kill(self):
        self.killed = True

    def wait(self, *, timeout):
        if timeout is None:
            raise AssertionError("Unbounded process wait is forbidden")
        self.wait_timeouts.append(timeout)
        return self.returncode


class ProductForegroundTimeoutTests(unittest.TestCase):
    def test_windows_inherited_pipe_timeout_never_calls_unbounded_communicate(self):
        child = _StubbornProcess()
        with (
            patch("ordax_dev_agent.process_runner.sys", SimpleNamespace(platform="win32")),
            patch("ordax_dev_agent.process_runner.subprocess.Popen", return_value=child),
            patch("ordax_dev_agent.process_runner.subprocess.run",
                  return_value=subprocess.CompletedProcess(["taskkill"], 1)) as taskkill,
        ):
            result = run_command(["test-only"], timeout=50)
        self.assertFalse(result.ok)
        self.assertTrue(result.data["timed_out"])
        self.assertTrue(result.data["cleanup_unverified"])
        self.assertEqual(result.summary, "command timed out after 50s")
        self.assertEqual(child.timeouts, [50, 3])
        self.assertEqual(child.wait_timeouts, [2])
        self.assertTrue(child.killed)
        self.assertTrue(child.stdout.closed)
        self.assertTrue(child.stderr.closed)
        self.assertEqual(taskkill.call_args.kwargs["timeout"], 10)

    def test_stalled_taskkill_fails_closed_and_still_reaps_with_bounded_wait(self):
        child = _StubbornProcess()
        with (
            patch("ordax_dev_agent.process_runner.sys", SimpleNamespace(platform="win32")),
            patch("ordax_dev_agent.process_runner.subprocess.Popen", return_value=child),
            patch("ordax_dev_agent.process_runner.subprocess.run",
                  side_effect=subprocess.TimeoutExpired(["taskkill"], 10)),
        ):
            result = run_command(["test-only"], timeout=12)
        self.assertFalse(result.ok)
        self.assertTrue(result.data["cleanup_unverified"])
        self.assertEqual(child.timeouts, [12, 3])
        self.assertEqual(child.wait_timeouts, [2])
        self.assertEqual(result.data["stdout"], "")
        self.assertEqual(result.data["stderr"], "")


if __name__ == "__main__":
    unittest.main()
