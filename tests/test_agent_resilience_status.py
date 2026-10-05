import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.models import ActionResult


class AgentResilienceStatusTests(unittest.TestCase):
    def make_registry(self, root: Path) -> ActionRegistry:
        agent_repo = root / "agent"
        (agent_repo / "scripts" / "windows").mkdir(parents=True)
        (root / "hordax").mkdir()
        (root / "bridge").mkdir()
        return ActionRegistry(
            AgentConfig(
                agent_name="resilience-test",
                poll_seconds=5.0,
                state_dir=root / "state",
                agent_repo_path=agent_repo,
                hordax_path=root / "hordax",
                bridge_path=root / "bridge",
            )
        )

    def test_resilience_status_parses_versioned_windows_report(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry = self.make_registry(root)
            script = (
                root
                / "agent"
                / "scripts"
                / "windows"
                / "ordax-resilience-status.ps1"
            )
            script.write_text("$result = @{}\n", encoding="utf-8")
            report = {
                "scheduled_task": {
                    "exists": True,
                    "state": "Ready",
                },
                "external_bootstrap": {
                    "script_exists": True,
                    "policy_exists": True,
                },
                "github_runner_service": {
                    "exists": False,
                },
                "local_health": None,
            }
            with (
                patch("ordax_dev_agent.agent_actions.sys.platform", "win32"),
                patch(
                    "ordax_dev_agent.agent_actions._run",
                    return_value=ActionResult(
                        True,
                        "command completed",
                        {
                            "stdout": json.dumps(report),
                            "stderr": "",
                            "returncode": 0,
                        },
                    ),
                ) as run,
            ):
                result = registry.execute("agent.resilience_status", {})

            self.assertTrue(result.ok)
            self.assertEqual(report, result.data["resilience"])
            command = run.call_args.args[0]
            self.assertEqual("powershell.exe", command[0])
            self.assertIn("ordax-resilience-status.ps1", command[-1])

    def test_resilience_status_rejects_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = self.make_registry(Path(raw))
            result = registry.execute(
                "agent.resilience_status",
                {"command": "anything"},
            )

        self.assertFalse(result.ok)
        self.assertIn("unsupported field", result.summary)

    def test_resilience_status_rejects_invalid_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = self.make_registry(Path(raw))
            with patch("ordax_dev_agent.agent_actions.sys.platform", "win32"):
                result = registry.execute(
                    "agent.resilience_status",
                    {"timeout_seconds": 31},
                )

        self.assertFalse(result.ok)
        self.assertIn("between 3 and 30", result.summary)

    def test_resilience_status_is_explicitly_windows_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            registry = self.make_registry(Path(raw))
            with patch("ordax_dev_agent.agent_actions.sys.platform", "linux"):
                result = registry.execute("agent.resilience_status", {})

        self.assertFalse(result.ok)
        self.assertIn("only on Windows", result.summary)

    def test_resilience_status_requires_repository_owned_script(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry = self.make_registry(root)
            script = (
                root
                / "agent"
                / "scripts"
                / "windows"
                / "ordax-resilience-status.ps1"
            )
            script.unlink(missing_ok=True)
            with patch("ordax_dev_agent.agent_actions.sys.platform", "win32"):
                result = registry.execute("agent.resilience_status", {})

        self.assertFalse(result.ok)
        self.assertIn("script not found", result.summary)


if __name__ == "__main__":
    unittest.main()
