import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp_blender_unity.config import (
    find_unity,
    read_unity_api_compatibility_level,
    read_unity_project_version,
    resolve_unity,
    unity_installation_diagnostics,
)
from mcp_blender_unity.server import _classify_failure


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        # These fixtures model Windows Hub installations, even on Linux CI.
        platform_patch = patch("mcp_blender_unity.config.platform.system", return_value="Windows")
        platform_patch.start()
        self.addCleanup(platform_patch.stop)

    def make_project(self, root: Path) -> Path:
        project = root / "Project"
        (project / "Assets").mkdir(parents=True)
        settings = project / "ProjectSettings"
        settings.mkdir()
        (settings / "ProjectVersion.txt").write_text(
            "m_EditorVersion: 6000.6.1f1\n", encoding="utf-8"
        )
        (settings / "ProjectSettings.asset").write_text(
            "  apiCompatibilityLevel: 6\n", encoding="utf-8"
        )
        return project

    def make_installation(
        self,
        root: Path,
        directory_name: str,
        *,
        references: bool,
        upm: bool = True,
    ) -> Path:
        editor = root / directory_name / "Editor"
        unity = editor / "Unity.exe"
        unity.parent.mkdir(parents=True)
        unity.write_bytes(b"test")
        data = editor / "Data"
        data.mkdir()
        if references:
            (data / "UnityReferenceAssemblies" / "unity-4.8-api" / "Facades").mkdir(parents=True)
        if upm:
            upm_path = data / "Resources" / "PackageManager" / "Server" / "UnityPackageManager.exe"
            upm_path.parent.mkdir(parents=True)
            upm_path.write_bytes(b"test")
        return unity

    def test_reads_project_version_and_api_level(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            project = self.make_project(Path(raw_root))
            self.assertEqual(read_unity_project_version(project), "6000.6.1f1")
            self.assertEqual(read_unity_api_compatibility_level(project), 6)

    def test_finds_required_editor_in_configured_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            project = self.make_project(root)
            unity = root / "Editors" / "6000.6.1f1" / "Editor" / "Unity.exe"
            unity.parent.mkdir(parents=True)
            unity.write_bytes(b"test")
            with patch.dict(os.environ, {"UNITY_EXE": ""}, clear=False), patch(
                "mcp_blender_unity.config._unity_editor_roots", return_value=[root / "Editors"]
            ):
                self.assertEqual(find_unity(project), unity)

    def test_reports_missing_reference_assemblies(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            project = self.make_project(root)
            unity = root / "Editor" / "Unity.exe"
            unity.parent.mkdir(parents=True)
            unity.write_bytes(b"test")
            diagnostics = unity_installation_diagnostics(unity, project)
            self.assertTrue(diagnostics["reference_assemblies_required"])
            self.assertFalse(diagnostics["reference_assemblies_available"])
            self.assertFalse(diagnostics["installation_healthy"])

    def test_broken_exact_and_healthy_variant_selects_variant(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            project = self.make_project(root)
            self.make_installation(root / "Editors", "6000.6.1f1", references=False)
            variant = self.make_installation(root / "Editors", "6000.6.1f1-x86_64", references=True)

            with patch.dict(os.environ, {"UNITY_EXE": ""}, clear=False), patch(
                "mcp_blender_unity.config._unity_editor_roots", return_value=[root / "Editors"]
            ):
                resolved = resolve_unity(project)

            self.assertEqual(Path(resolved["selected_path"]), variant)
            self.assertTrue(resolved["installation_healthy"])
            self.assertEqual(len(resolved["candidates"]), 2)

    def test_healthy_exact_and_variant_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            project = self.make_project(root)
            exact = self.make_installation(root / "Editors", "6000.6.1f1", references=True)
            self.make_installation(root / "Editors", "6000.6.1f1-x86_64", references=True)

            with patch.dict(os.environ, {"UNITY_EXE": ""}, clear=False), patch(
                "mcp_blender_unity.config._unity_editor_roots", return_value=[root / "Editors"]
            ):
                first = resolve_unity(project)
                second = resolve_unity(project)

            self.assertEqual(Path(first["selected_path"]), exact)
            self.assertEqual(first["selected_path"], second["selected_path"])

    def test_only_broken_installation_classifies_as_unity_installation(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            project = self.make_project(root)
            broken = self.make_installation(root / "Editors", "6000.6.1f1", references=False)

            with patch.dict(os.environ, {"UNITY_EXE": ""}, clear=False), patch(
                "mcp_blender_unity.config._unity_editor_roots", return_value=[root / "Editors"]
            ):
                resolved = resolve_unity(project)

            self.assertEqual(Path(resolved["selected_path"]), broken)
            self.assertFalse(resolved["installation_healthy"])
            self.assertEqual(
                _classify_failure("", "", resolved["selected_diagnostics"], 0),
                "unity_installation",
            )

    def test_healthy_explicit_override_wins(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            project = self.make_project(root)
            override = self.make_installation(root / "Override", "6000.6.1f1-x86_64", references=True)

            with patch.dict(os.environ, {"UNITY_EXE": str(override)}, clear=False), patch(
                "mcp_blender_unity.config._unity_editor_roots", return_value=[root / "Editors"]
            ):
                resolved = resolve_unity(project)

            self.assertEqual(Path(resolved["selected_path"]), override)
            self.assertEqual(resolved["source"], "explicit")
            self.assertFalse(resolved["explicit_invalid"])

    def test_broken_explicit_override_is_reported_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            project = self.make_project(root)
            broken = self.make_installation(root / "Override", "6000.6.1f1", references=False)
            healthy_variant = self.make_installation(root / "Editors", "6000.6.1f1-x86_64", references=True)

            with patch.dict(os.environ, {"UNITY_EXE": str(broken)}, clear=False), patch(
                "mcp_blender_unity.config._unity_editor_roots", return_value=[root / "Editors"]
            ):
                resolved = resolve_unity(project)

            self.assertEqual(Path(resolved["selected_path"]), broken)
            self.assertNotEqual(Path(resolved["selected_path"]), healthy_variant)
            self.assertTrue(resolved["explicit_invalid"])
            self.assertIn("invalid Unity installation", resolved["explicit_error"])


if __name__ == "__main__":
    unittest.main()
