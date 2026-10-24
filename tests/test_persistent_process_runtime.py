from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ordax_dev_agent.persistent_process_runtime import ProcessRuntime


class PersistentProcessRuntimeTests(unittest.TestCase):
    @staticmethod
    def _runtime(stdin_path: Path) -> ProcessRuntime:
        runtime = ProcessRuntime.__new__(ProcessRuntime)
        runtime.stdin_path = stdin_path
        runtime.child = SimpleNamespace(poll=lambda: None, stdin=io.StringIO())
        runtime._stopping = False
        return runtime

    def test_stdin_pump_backs_off_when_journal_is_idle(self):
        with tempfile.TemporaryDirectory() as root:
            stdin_path = Path(root) / "stdin.jsonl"
            stdin_path.touch()
            runtime = self._runtime(stdin_path)
            intervals = []

            def stop_after_five_polls(interval):
                intervals.append(interval)
                if len(intervals) == 5:
                    runtime._stopping = True

            with patch(
                "ordax_dev_agent.persistent_process_runtime.time.sleep",
                side_effect=stop_after_five_polls,
            ):
                runtime._pump_stdin()

        self.assertEqual([0.05, 0.1, 0.2, 0.2, 0.2], intervals)

    def test_stdin_pump_resets_to_fast_poll_after_new_input(self):
        with tempfile.TemporaryDirectory() as root:
            stdin_path = Path(root) / "stdin.jsonl"
            stdin_path.touch()
            runtime = self._runtime(stdin_path)
            intervals = []

            def deliver_after_first_poll(interval):
                intervals.append(interval)
                if len(intervals) == 3:
                    stdin_path.write_text(
                        json.dumps({"text": "wake", "newline": True}) + "\n",
                        encoding="utf-8",
                    )
                elif len(intervals) == 4:
                    runtime._stopping = True

            with patch(
                "ordax_dev_agent.persistent_process_runtime.time.sleep",
                side_effect=deliver_after_first_poll,
            ):
                runtime._pump_stdin()

        self.assertEqual([0.05, 0.1, 0.2, 0.05], intervals)
        self.assertEqual("wake\n", runtime.child.stdin.getvalue())

    def test_stdin_pump_retries_unterminated_journal_entry(self):
        with tempfile.TemporaryDirectory() as root:
            stdin_path = Path(root) / "stdin.jsonl"
            stdin_path.write_text('{"text":"wake",', encoding="utf-8")
            runtime = self._runtime(stdin_path)
            intervals = []

            def finish_entry(interval):
                intervals.append(interval)
                if len(intervals) == 1:
                    with stdin_path.open("a", encoding="utf-8", newline="\n") as handle:
                        handle.write('"newline":true}\n')
                else:
                    runtime._stopping = True

            with patch(
                "ordax_dev_agent.persistent_process_runtime.time.sleep",
                side_effect=finish_entry,
            ):
                runtime._pump_stdin()

        self.assertEqual([0.05, 0.05], intervals)
        self.assertEqual("wake\n", runtime.child.stdin.getvalue())


if __name__ == "__main__":
    unittest.main()
