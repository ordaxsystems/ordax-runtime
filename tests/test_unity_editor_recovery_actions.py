import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.models import ActionResult


class UnityEditorRecoveryActionTests(unittest.TestCase):
    def make_registry(self, root: Path) -> tuple[ActionRegistry, Path]:
        project = root / "project"
        (project / "Assets").mkdir(parents=True)
        (project / "ProjectSettings").mkdir()
        (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
            "m_EditorVersion: 6000.6.1f1\n",
            encoding="utf-8",
        )
        config = AgentConfig(
            "test",
            
            
            5,
            root / "state",
            root / "agent",
            root / "hordax",
            root / "bridge",
            projects={"salvador": {"path": str(project), "apps": ["unity"]}},
            default_project="salvador",
        )
        return ActionRegistry(config), project

    def test_stuck_editor_termination_requires_single_exact_editor_process(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry, project = self.make_registry(root)
            (project / "Temp").mkdir()
            (project / "Temp" / "UnityLockfile").write_text("lock", encoding="utf-8")
            editor = root / "Unity" / "6000.6.1f1" / "Editor" / "Unity.exe"
            editor.parent.mkdir(parents=True)
            editor.write_bytes(b"test")

            active = {"state": "active", "path": str(project / "Temp" / "UnityLockfile")}
            stale = {"state": "stale", "path": str(project / "Temp" / "UnityLockfile")}
            missing = {"state": "missing", "path": str(project / "Temp" / "UnityLockfile")}
            stopped = subprocess.CompletedProcess(
                args=["powershell.exe"],
                returncode=0,
                stdout="",
                stderr="",
            )

            with patch("ordax_dev_agent.unity_actions.sys.platform", "win32"), patch(
                "ordax_dev_agent.unity_actions.find_unity",
                return_value=editor,
            ), patch(
                "ordax_dev_agent.unity_actions._windows_unity_lock_probe",
                side_effect=[active, stale, missing],
            ), patch(
                "ordax_dev_agent.unity_actions._windows_unity_processes",
                return_value=[
                    {"pid": 16860, "path": str(editor), "title": "project - Unity"}
                ],
            ), patch(
                "ordax_dev_agent.unity_actions.subprocess.run",
                return_value=stopped,
            ):
                result = registry.execute(
                    "unity.editor_terminate_stuck",
                    {"wait_seconds": 5},
                )

            self.assertTrue(result.ok, f"{result.summary}: {result.data}")
            self.assertEqual(16860, result.data["pid"])
            self.assertTrue(result.data["stale_lock_cleared"])
            self.assertEqual("missing", result.data["project_lock_probe"]["state"])

    def test_stuck_editor_termination_fails_closed_for_ambiguous_processes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry, project = self.make_registry(root)
            (project / "Temp").mkdir()
            (project / "Temp" / "UnityLockfile").write_text("lock", encoding="utf-8")
            editor = root / "Unity" / "6000.6.1f1" / "Editor" / "Unity.exe"
            editor.parent.mkdir(parents=True)
            editor.write_bytes(b"test")
            active = {"state": "active", "path": str(project / "Temp" / "UnityLockfile")}

            with patch("ordax_dev_agent.unity_actions.sys.platform", "win32"), patch(
                "ordax_dev_agent.unity_actions.find_unity",
                return_value=editor,
            ), patch(
                "ordax_dev_agent.unity_actions._windows_unity_lock_probe",
                return_value=active,
            ), patch(
                "ordax_dev_agent.unity_actions._windows_unity_processes",
                return_value=[
                    {"pid": 1, "path": str(editor), "title": "A"},
                    {"pid": 2, "path": str(editor), "title": "B"},
                ],
            ), patch(
                "ordax_dev_agent.unity_actions.subprocess.run"
            ) as stop:
                result = registry.execute("unity.editor_terminate_stuck", {})

            self.assertFalse(result.ok)
            self.assertIn("exactly one", result.summary)
            stop.assert_not_called()

    def test_installations_action_reports_discovered_editors(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry, _project = self.make_registry(root)
            editor = root / "Unity" / "6000.6.2f1" / "Editor" / "Unity.exe"
            editor.parent.mkdir(parents=True)
            editor.write_bytes(b"unity")
            with patch(
                "ordax_dev_agent.unity_actions.sys.platform", "win32"
            ), patch(
                "ordax_dev_agent.unity_actions._windows_unity_installation_scan",
                return_value=[
                    {
                        "editor": str(editor),
                        "sources": [{"source": "registry", "detail": "Unity 6000.6.2f1"}],
                    }
                ],
            ):
                result = registry.execute(
                    "unity.installations",
                    {"version": "6000.6.2f1"},
                )
            self.assertTrue(result.ok)
            self.assertEqual(1, result.data["count"])
            self.assertEqual(1, len(result.data["requested_matches"]))

    def test_hub_install_editor_is_typed_and_verifies_installation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry, _project = self.make_registry(root)
            hub = root / "Unity Hub.exe"
            hub.write_bytes(b"test")
            completed = subprocess.CompletedProcess(
                args=[str(hub)],
                returncode=0,
                stdout="installed",
                stderr="",
            )

            with patch("ordax_dev_agent.unity_actions.sys.platform", "win32"), patch(
                "ordax_dev_agent.unity_actions._hub_editor_install_exists",
                side_effect=[False, True],
            ), patch(
                "ordax_dev_agent.unity_actions._find_unity_hub",
                return_value=hub,
            ), patch(
                "ordax_dev_agent.unity_actions.subprocess.run",
                return_value=completed,
            ) as run:
                result = registry.execute(
                    "unity.hub_install_editor",
                    {
                        "version": "6000.6.2f1",
                        "changeset": "770e33f6875c",
                        "timeout_seconds": 120,
                    },
                )

            self.assertTrue(result.ok)
            command = run.call_args.args[0]
            self.assertIn("--headless", command)
            self.assertIn("6000.6.2f1", command)
            self.assertIn("770e33f6875c", command)
            self.assertFalse(run.call_args.kwargs["shell"])


    def test_release_metadata_requires_exact_version_changeset_and_download(self) -> None:
        from ordax_dev_agent.unity_actions import _unity_release_installer_metadata

        payload = {
            "results": [
                {
                    "version": "6000.6.2f1",
                    "shortRevision": "770e33f6875c",
                    "downloads": [
                        {
                            "url": (
                                "https://download.unity3d.com/download_unity/"
                                "770e33f6875c/Windows64EditorInstaller/"
                                "UnitySetup64-6000.6.2f1.exe"
                            ),
                            "integrity": "sha1-" + __import__("base64").b64encode(
                                b"0123456789abcdef0123456789abcdef01234567\n"
                            ).decode("ascii"),
                            "type": "EXE",
                            "platform": "WINDOWS",
                            "architecture": "X86_64",
                        }
                    ],
                }
            ]
        }
        response = Mock()
        response.read.return_value = __import__("json").dumps(payload).encode("utf-8")
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)

        with patch(
            "ordax_dev_agent.unity_actions.urllib.request.urlopen",
            return_value=response,
        ):
            metadata = _unity_release_installer_metadata(
                "6000.6.2f1",
                "770e33f6875c",
            )

        self.assertEqual("sha1", metadata["algorithm"])
        self.assertEqual(
            "0123456789abcdef0123456789abcdef01234567",
            metadata["expected_hash"],
        )
        self.assertIn("version=6000.6.2f1", metadata["api_url"])

    def test_release_metadata_accepts_unity_published_md5(self) -> None:
        from ordax_dev_agent.unity_actions import _unity_release_installer_metadata

        import base64
        import json

        digest = "0123456789abcdef0123456789abcdef"
        payload = {
            "results": [
                {
                    "version": "6000.6.2f1",
                    "shortRevision": "770e33f6875c",
                    "downloads": [
                        {
                            "url": (
                                "https://download.unity3d.com/download_unity/"
                                "770e33f6875c/Windows64EditorInstaller/"
                                "UnitySetup64-6000.6.2f1.exe"
                            ),
                            "integrity": "md5-" + base64.b64encode(
                                bytes.fromhex(digest)
                            ).decode("ascii"),
                            "type": "EXE",
                            "platform": "WINDOWS",
                            "architecture": "X86_64",
                        }
                    ],
                }
            ]
        }
        response = Mock()
        response.read.return_value = json.dumps(payload).encode("utf-8")
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)

        with patch(
            "ordax_dev_agent.unity_actions.urllib.request.urlopen",
            return_value=response,
        ):
            metadata = _unity_release_installer_metadata(
                "6000.6.2f1",
                "770e33f6875c",
            )

        self.assertEqual("md5", metadata["algorithm"])
        self.assertEqual(digest, metadata["expected_hash"])

    def test_release_metadata_accepts_ascii_hex_integrity_compatibility(self) -> None:
        from ordax_dev_agent.unity_actions import _unity_release_installer_metadata

        import base64
        import json

        digest = "0123456789abcdef0123456789abcdef01234567"
        payload = {
            "results": [
                {
                    "version": "6000.6.2f1",
                    "shortRevision": "770e33f6875c",
                    "downloads": [
                        {
                            "url": (
                                "https://download.unity3d.com/download_unity/"
                                "770e33f6875c/Windows64EditorInstaller/"
                                "UnitySetup64-6000.6.2f1.exe"
                            ),
                            "integrity": "sha1-" + base64.b64encode(
                                (digest + "\n").encode("ascii")
                            ).decode("ascii"),
                            "type": "EXE",
                            "platform": "WINDOWS",
                            "architecture": "X86_64",
                        }
                    ],
                }
            ]
        }
        response = Mock()
        response.read.return_value = json.dumps(payload).encode("utf-8")
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)

        with patch(
            "ordax_dev_agent.unity_actions.urllib.request.urlopen",
            return_value=response,
        ):
            metadata = _unity_release_installer_metadata(
                "6000.6.2f1",
                "770e33f6875c",
            )

        self.assertEqual("sha1", metadata["algorithm"])
        self.assertEqual(digest, metadata["expected_hash"])

    def test_file_integrity_matches_official_digest(self) -> None:
        from ordax_dev_agent.unity_actions import _verify_file_integrity
        import hashlib

        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "installer.exe"
            path.write_bytes(b"unity-installer")
            expected = hashlib.sha1(b"unity-installer").hexdigest()
            result = _verify_file_integrity(path, "sha1", expected)
        self.assertTrue(result["valid"])
        self.assertEqual(expected, result["actual_hash"])

    def test_direct_install_editor_uses_official_signed_installer(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry, _project = self.make_registry(root)
            local_app_data = root / "local"
            expected_editor = (
                local_app_data
                / "Unity"
                / "Hub"
                / "Editor"
                / "6000.6.2f1"
                / "Editor"
                / "Unity.exe"
            )
            curl = root / "curl.exe"
            curl.write_bytes(b"curl")

            def fake_run(command, **_kwargs):
                if str(command[0]) == str(curl):
                    output = Path(command[command.index("--output") + 1])
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(b"official-installer")
                    return subprocess.CompletedProcess(command, 0, "", "")
                if str(command[0]).endswith("UnitySetup64-6000.6.2f1.exe"):
                    expected_editor.parent.mkdir(parents=True, exist_ok=True)
                    expected_editor.write_bytes(b"unity")
                    return subprocess.CompletedProcess(command, 0, "", "")
                raise AssertionError(f"unexpected command: {command}")

            with patch.dict(
                "ordax_dev_agent.unity_actions.os.environ",
                {"LOCALAPPDATA": str(local_app_data)},
                clear=False,
            ), patch(
                "ordax_dev_agent.unity_actions.sys.platform", "win32"
            ), patch(
                "ordax_dev_agent.unity_actions._hub_editor_install_exists",
                return_value=False,
            ), patch(
                "ordax_dev_agent.unity_actions.shutil.which",
                return_value=str(curl),
            ), patch(
                "ordax_dev_agent.unity_actions._unity_release_installer_metadata",
                return_value={
                    "api_url": "https://services.api.unity.com/test",
                    "url": (
                        "https://download.unity3d.com/download_unity/"
                        "770e33f6875c/Windows64EditorInstaller/"
                        "UnitySetup64-6000.6.2f1.exe"
                    ),
                    "algorithm": "sha1",
                    "expected_hash": __import__("hashlib").sha1(
                        b"official-installer"
                    ).hexdigest(),
                    "integrity": "sha1-test",
                },
            ), patch(
                "ordax_dev_agent.unity_actions.subprocess.run",
                side_effect=fake_run,
            ):
                result = registry.execute(
                    "unity.direct_install_editor",
                    {
                        "version": "6000.6.2f1",
                        "changeset": "770e33f6875c",
                        "download_timeout_seconds": 120,
                        "install_timeout_seconds": 120,
                    },
                )

            self.assertTrue(result.ok, f"{result.summary}: {result.data}")
            self.assertTrue(expected_editor.is_file())
            self.assertEqual("unity-download-archive", result.data["source"])
            self.assertEqual(
                "https://download.unity3d.com/download_unity/"
                "770e33f6875c/Windows64EditorInstaller/"
                "UnitySetup64-6000.6.2f1.exe",
                result.data["url"],
            )
            self.assertTrue(result.data["integrity"]["valid"])
            self.assertEqual("sha1", result.data["integrity"]["algorithm"])

    def test_direct_install_editor_refuses_missing_changeset(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry, _project = self.make_registry(root)
            with patch("ordax_dev_agent.unity_actions.sys.platform", "win32"):
                result = registry.execute(
                    "unity.direct_install_editor",
                    {"version": "6000.6.2f1"},
                )
            self.assertFalse(result.ok)
            self.assertIn("changeset is required", result.summary)

    def test_editor_start_can_target_exact_hub_version(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry, _project = self.make_registry(root)
            editor_path = root / "Unity" / "6000.6.2f1" / "Editor" / "Unity.exe"
            editor_path.parent.mkdir(parents=True)
            editor_path.write_bytes(b"test")

            bridge = Mock()
            bridge.presence_is_fresh.side_effect = [False, True]
            bridge.project_appears_open.return_value = False
            bridge.status.return_value = {
                "presence_fresh": False,
                "presence": {"protocol": "ordax-generic-v5"},
            }

            with patch.object(registry, "_editor", return_value=bridge), patch(
                "ordax_dev_agent.unity_actions._find_hub_editor_executable",
                return_value=editor_path,
            ), patch(
                "ordax_dev_agent.unity_actions.find_unity"
            ) as legacy_find, patch(
                "ordax_dev_agent.unity_actions.subprocess.Popen",
                return_value=SimpleNamespace(pid=4242),
            ) as popen:
                result = registry.execute(
                    "unity.editor_start",
                    {"version": "6000.6.2f1", "wait_seconds": 1},
                )

            self.assertTrue(result.ok, f"{result.summary}: {result.data}")
            legacy_find.assert_not_called()
            self.assertEqual(str(editor_path), popen.call_args.args[0][0])
            self.assertEqual("6000.6.2f1", result.data["requested_version"])

    def test_recover_resume_runs_closed_loop_on_same_release_stream(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry, _project = self.make_registry(root)
            ok = ActionResult(True, "ok", {})
            started = ActionResult(
                True,
                "started",
                {"presence": {"unityVersion": "6000.6.2f1", "protocol": "ordax-generic-v5"}},
            )

            with patch(
                "ordax_dev_agent.unity_actions.unity_project_profile",
                side_effect=[
                    {"unity_version": "6000.6.1f1"},
                    {"unity_version": "6000.6.2f1"},
                ],
            ), patch.object(
                registry, "unity_editor_terminate_stuck", return_value=ok
            ) as terminate, patch.object(
                registry, "unity_hub_install_editor", return_value=ok
            ) as install, patch.object(
                registry, "unity_install_companion", return_value=ok
            ) as companion, patch.object(
                registry, "unity_editor_start", return_value=started
            ) as start, patch.object(
                registry, "unity_compile", return_value=ok
            ) as compile_, patch.object(
                registry, "unity_scene_summary", return_value=ok
            ) as summary, patch.object(
                registry, "unity_physics_audit", return_value=ok
            ) as physics, patch.object(
                registry, "unity_spatial_audit", return_value=ok
            ) as spatial, patch.object(
                registry, "unity_play_start", return_value=ok
            ) as play, patch.object(
                registry,
                "unity_capture",
                return_value=ActionResult(
                    True,
                    "captured",
                    {"artifact": "capture.png", "snapshot_path": "capture.json"},
                ),
            ) as capture:
                result = registry.execute(
                    "unity.recover_resume",
                    {
                        "version": "6000.6.2f1",
                        "changeset": "770e33f6875c",
                    },
                )

            self.assertTrue(result.ok, f"{result.summary}: {result.data}")
            terminate.assert_called_once()
            install.assert_called_once()
            companion.assert_called_once()
            start.assert_called_once()
            self.assertEqual(
                "6000.6.2f1",
                start.call_args.args[0]["version"],
            )
            compile_.assert_called_once()
            summary.assert_called_once()
            physics.assert_called_once()
            spatial.assert_called_once()
            play.assert_called_once()
            capture.assert_called_once()
            self.assertEqual("capture.png", result.data["artifact"])
            self.assertEqual(
                [
                    "terminate_stuck_editor",
                    "install_target_editor",
                    "install_companion",
                    "start_target_editor",
                    "compile",
                    "scene_summary",
                    "play_start",
                    "physics_audit",
                    "spatial_audit",
                    "capture",
                ],
                [item["step"] for item in result.data["steps"]],
            )

    def test_recover_resume_refuses_cross_stream_editor(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            registry, _project = self.make_registry(root)

            result = registry.execute(
                "unity.recover_resume",
                {"version": "6000.5.10f1"},
            )

            self.assertFalse(result.ok)
            self.assertIn("current Unity release stream", result.summary)
            self.assertEqual("6000.6.1f1", result.data["current_version"])
            self.assertEqual("6000.5.10f1", result.data["target_version"])


if __name__ == "__main__":
    unittest.main()
