from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkbenchComputerFirstUiTests(unittest.TestCase):
    def test_native_workbench_is_a_thin_host_for_the_studio_surface(self) -> None:
        xaml = (ROOT / "native" / "ordax-workbench" / "MainWindow.xaml").read_text(encoding="utf-8")
        self.assertIn('Title="ORDAX Studio"', xaml)
        self.assertIn('Header="Projeto"', xaml)
        self.assertIn('x:Name="StudioView"', xaml)
        self.assertIn('Header="Web IA"', xaml)
        self.assertIn('Header="Preview"', xaml)
        self.assertIn('Header="Browser"', xaml)
        self.assertIn('Header="Execuções"', xaml)
        self.assertIn('Header="Diagnóstico"', xaml)
        self.assertIn('x:Name="ProviderView"', xaml)
        self.assertIn('x:Name="ComputerControlState"', xaml)
        self.assertIn('x:Name="DeviceCardState"', xaml)
        self.assertIn('x:Name="McpCardState"', xaml)
        self.assertNotIn('x:Name="TopConnectionDot"', xaml)
        self.assertNotIn('x:Name="ProjectRailState"', xaml)
        self.assertNotIn('Text="Projetos · IA · Computer Control"', xaml)
        self.assertNotIn('Header="Visão geral"', xaml)
        self.assertNotIn('Text="COMPUTADOR"', xaml)
        self.assertNotIn('Text="CAPACIDADES ESPECIALIZADAS"', xaml)
        self.assertIn('Title = string.IsNullOrWhiteSpace(project) ? "ORDAX Studio"', (ROOT / "native" / "ordax-workbench" / "MainWindow.xaml.cs").read_text(encoding="utf-8"))

    def test_primary_surface_initializes_before_auxiliary_webviews(self) -> None:
        code = (ROOT / "native" / "ordax-workbench" / "MainWindow.xaml.cs").read_text(encoding="utf-8")
        startup = code.split("private async void MainWindow_Loaded", 1)[1].split("private async Task InitializeViewAsync", 1)[0]
        self.assertIn("await EnsureStudioViewAsync();", startup)
        self.assertNotIn("ProviderView", startup)
        self.assertNotIn("PreviewView", startup)
        self.assertNotIn("WorkbenchBrowserView", startup)
        self.assertIn('Equals(tab.Header, "Web IA")', code)
        self.assertIn("await EnsureProviderViewAsync();", code)
        self.assertIn("await EnsurePreviewViewAsync();", code)
        self.assertIn("await EnsureBrowserViewAsync();", code)
        self.assertIn("_providerViewReady", code)
        self.assertIn("_previewViewReady", code)
        self.assertIn("_browserViewReady", code)

    def test_workbench_host_bridge_exposes_ai_session_api(self) -> None:
        bridge = (ROOT / "ordax_studio" / "workbench_bridge.py").read_text(encoding="utf-8")
        host = (ROOT / "ordax_studio" / "host_bridge.js").read_text(encoding="utf-8")
        self.assertIn('"ai_sessions_status"', bridge)
        self.assertIn("aiSessionsStatus:", host)
        self.assertIn("window.chrome?.webview", host)
        self.assertIn("Object.defineProperty(window,'ordaxStudioHost'", host)

    def test_status_surface_uses_real_computer_control_contract(self) -> None:
        api = (ROOT / "ordax_studio" / "web_desktop.py").read_text(encoding="utf-8")
        code = (ROOT / "native" / "ordax-workbench" / "MainWindow.xaml.cs").read_text(encoding="utf-8")
        self.assertIn('"computer_control": computer_control', api)
        self.assertIn('"computer.screenshot"', api)
        self.assertIn('"computer.type"', api)
        self.assertIn('"computer.clipboard_read"', api)
        self.assertIn('"computer.launch_app"', api)
        self.assertIn('TryGetProperty("computer_control"', code)
        self.assertIn('ActionCountState.Text', code)
        self.assertNotIn('ExecutionSummary.Text', code)


if __name__ == "__main__":
    unittest.main()
