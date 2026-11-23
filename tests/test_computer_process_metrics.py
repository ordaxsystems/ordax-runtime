"""Process metrics are optional and do not start polling or WMI queries."""
from __future__ import annotations

import ctypes
import os
import subprocess
import unittest
from ctypes import wintypes
from unittest.mock import Mock, patch

from ordax_dev_agent.computer_control_actions import (
    ComputerControlActions,
    _PROCESS_MEMORY_COUNTERS,
)


class ComputerProcessMetricsTests(unittest.TestCase):
    def setUp(self):
        self.actions = ComputerControlActions()

    def test_existing_process_list_omits_extra_metrics_and_extra_args(self):
        with patch.object(ComputerControlActions, "_system_process_snapshot", return_value=[
            {"pid": 8, "parent_pid": 1, "name": "parent", "executable": "C:/parent.exe"},
        ]) as snapshot:
            result = self.actions.computer_processes({"query": "parent", "max_items": 10})
        self.assertTrue(result.ok, result.summary)
        snapshot.assert_called_once_with()
        self.assertNotIn("working_set_bytes", result.data["processes"][0])
        self.assertNotIn("cpu_time_seconds", result.data["processes"][0])

    def test_process_metrics_opt_in_is_explicit_and_does_not_fabricate_missing_values(self):
        samples = [
            {"pid": 12, "parent_pid": 1, "name": "Ordax.exe", "executable": "C:/Ordax.exe",
             "working_set_bytes": 21_000_000, "cpu_time_seconds": 12.5},
            {"pid": 14, "parent_pid": 1, "name": "unreadable", "executable": ""},
        ]
        with patch.object(ComputerControlActions, "_system_process_snapshot", return_value=samples) as snapshot:
            result = self.actions.computer_processes({
                "query": "ordax", "max_items": 10, "include_metrics": True,
            })
        self.assertTrue(result.ok, result.summary)
        snapshot.assert_called_once_with(include_metrics=True)
        self.assertEqual(1, result.data["total_matches"])
        self.assertEqual(21_000_000, result.data["processes"][0]["working_set_bytes"])
        self.assertEqual(12.5, result.data["processes"][0]["cpu_time_seconds"])

        for invalid in ("true", 1, None):
            with self.subTest(invalid=invalid):
                with patch.object(ComputerControlActions, "_system_process_snapshot") as snapshot:
                    response = self.actions.computer_processes({"include_metrics": invalid})
                self.assertFalse(response.ok)
                snapshot.assert_not_called()

    def test_win32_metric_api_uses_same_process_handle(self):
        psapi = Mock()
        kernel32 = Mock()

        def memory(handle, pointer, size):
            self.assertEqual(900, handle)
            self.assertEqual(ctypes.sizeof(_PROCESS_MEMORY_COUNTERS), size)
            counters = ctypes.cast(pointer, ctypes.POINTER(_PROCESS_MEMORY_COUNTERS)).contents
            counters.WorkingSetSize = 12_345_678
            return 1

        def times(handle, created, exited, kernel, user):
            self.assertEqual(900, handle)
            ctypes.cast(kernel, ctypes.POINTER(wintypes.FILETIME)).contents.dwLowDateTime = 15_000_000
            ctypes.cast(user, ctypes.POINTER(wintypes.FILETIME)).contents.dwLowDateTime = 20_000_000
            return 1

        psapi.GetProcessMemoryInfo.side_effect = memory
        kernel32.GetProcessTimes.side_effect = times
        result = ComputerControlActions._windows_process_metrics(900, kernel32=kernel32, psapi=psapi)
        self.assertEqual({"working_set_bytes": 12_345_678, "cpu_time_seconds": 3.5}, result)
        psapi.GetProcessMemoryInfo.assert_called_once()
        kernel32.GetProcessTimes.assert_called_once()

    def test_win32_permission_denied_omits_unknown_metrics(self):
        psapi = Mock()
        kernel32 = Mock()
        psapi.GetProcessMemoryInfo.return_value = 0
        kernel32.GetProcessTimes.return_value = 0
        self.assertEqual(
            {}, ComputerControlActions._windows_process_metrics(33, kernel32=kernel32, psapi=psapi),
        )

    def test_unix_ps_cpu_time_parser(self):
        parse = ComputerControlActions._ps_cpu_seconds
        self.assertEqual(75, parse("01:15"))
        self.assertEqual(3675, parse("01:01:15"))
        self.assertEqual(90015, parse("1-01:00:15"))
        self.assertIsNone(parse("not-a-time"))
        self.assertIsNone(parse("00:99:99"))

    @unittest.skipIf(os.name == "nt", "Unix ps snapshot is not available on Windows")
    def test_unix_single_ps_command_enriches_only_on_demand(self):
        completed = subprocess.CompletedProcess(
            args=["ps"], returncode=0,
            stdout=" 101 1 2048 00:01:15 python\n 102 1 4096 00:00:04 node\n",
            stderr="",
        )
        with patch("ordax_dev_agent.computer_control_actions.subprocess.run", return_value=completed) as run:
            enriched = ComputerControlActions._system_process_snapshot(include_metrics=True)
        self.assertEqual(["ps", "-eo", "pid=,ppid=,rss=,time=,comm="], run.call_args.args[0])
        self.assertEqual(2048 * 1024, enriched[0]["working_set_bytes"])
        self.assertEqual(75, enriched[0]["cpu_time_seconds"])
        self.assertEqual(4, enriched[1]["cpu_time_seconds"])

        plain = subprocess.CompletedProcess(
            args=["ps"], returncode=0, stdout=" 101 1 python\n", stderr="",
        )
        with patch("ordax_dev_agent.computer_control_actions.subprocess.run", return_value=plain) as run:
            original = ComputerControlActions._system_process_snapshot()
        self.assertEqual(["ps", "-eo", "pid=,ppid=,comm="], run.call_args.args[0])
        self.assertNotIn("working_set_bytes", original[0])


if __name__ == "__main__":
    unittest.main()
