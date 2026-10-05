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

    def test_shared_surface_owns_project_navigation_and_preview(self) -> None:
        html = (ROOT / "ordax_studio" / "studio_product.html").read_text(encoding="utf-8")
        css = (ROOT / "ordax_studio" / "assets" / "studio.css").read_text(encoding="utf-8")
        self.assertIn("<title>ORDAX Studio</title>", html)
        self.assertIn('class="projectSidebar"', html)
        self.assertIn('class="mainPane"', html)
        self.assertIn('class="previewPane"', html)
        self.assertIn('class="statusbar"', html)
        self.assertIn('--sidebar:286px', css)
        self.assertIn('--preview:minmax(460px,42%)', css)
        self.assertIn('background:#60738c', css)
        self.assertIn('.statusPill.ok .statusDot', css)
        self.assertIn('.statusPill.attention .statusDot', css)
        self.assertIn('grid-template-columns:var(--sidebar) minmax(460px,1fr) var(--preview)', css)
        script = (ROOT / 'ordax_studio' / 'assets' / 'studio.js').read_text(encoding='utf-8')
        self.assertIn('function renderRuntimePill()', script)
        self.assertIn("transport==='connected'", script)
        self.assertIn("transport==='connecting'||transport==='reconnecting'", script)
        self.assertIn('Link do dispositivo', script)
        # Global operating-system navigation belongs to prototipo-ordax-os, not Studio.
        self.assertNotIn('>Network<', html)
        self.assertNotIn('>Ajustes<', html)
        self.assertNotIn('>Sistema<', html)

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

    def test_shared_surface_boots_through_host_bridge(self) -> None:
        html = (ROOT / "ordax_studio" / "studio_product.html").read_text(encoding="utf-8")
        script = (ROOT / "ordax_studio" / "assets" / "studio.js").read_text(encoding="utf-8")
        host = (ROOT / "ordax_studio" / "host_bridge.js").read_text(encoding="utf-8")
        blender = (ROOT / "ordax_studio" / "assets" / "blender-connection.js").read_text(encoding="utf-8")
        account = (ROOT / "ordax_studio" / "assets" / "product_account.js").read_text(encoding="utf-8")

        self.assertLess(html.index('src="host_bridge.js"'), html.index('src="assets/studio.js"'))
        self.assertIn("window.addEventListener('pywebviewready',markReady,{once:true})", host)
        self.assertIn("window.chrome?.webview", host)
        self.assertIn("Object.defineProperty(window,'ordaxStudioHost'", host)
        self.assertIn("window.ordaxStudioHost.whenReady(startInitOnce)", script)
        self.assertIn("let initStarted=false", script)
        self.assertIn("if(initStarted)return", script)
        for portable in (script, blender, account):
            self.assertNotIn("window.pywebview", portable)
            self.assertNotIn("window.chrome", portable)
            self.assertNotIn("pywebviewready", portable)

    def test_native_bridge_exposes_ai_sessions_used_by_surface(self) -> None:
        bridge = (ROOT / "ordax_studio" / "workbench_bridge.py").read_text(encoding="utf-8")
        script = (ROOT / "ordax_studio" / "assets" / "studio.js").read_text(encoding="utf-8")
        self.assertIn('"ai_sessions_status"', bridge)
        self.assertIn("ai_sessions_status:'aiSessionsStatus'", script)

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
