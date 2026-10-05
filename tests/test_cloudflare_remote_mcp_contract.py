from __future__ import annotations

import unittest
from pathlib import Path


class CloudflareRemoteMcpContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path(__file__).resolve().parents[1]
        cls.worker = (root / "control-plane" / "cloudflare" / "src" / "index.ts").read_text(encoding="utf-8")
        cls.mcp = (root / "control-plane" / "cloudflare" / "src" / "mcp_http.ts").read_text(encoding="utf-8")

    def test_worker_exposes_authenticated_streamable_http_mcp(self) -> None:
        self.assertIn('import { handleOrdaxMcp } from "./mcp_http";', self.worker)
        self.assertIn('url.pathname === "/mcp"', self.worker)
        self.assertIn('"/.well-known/oauth-protected-resource"', self.worker)
        self.assertIn('authorization_servers', self.worker)
        self.assertIn('session: (inner) => productSession(inner, env)', self.worker)
        self.assertIn('targets: (inner) => listProductTargets(inner, env)', self.worker)

    def test_mcp_uses_product_session_as_auth_gate(self) -> None:
        auth = self.mcp.index("const sessionProbe = await handlers.session")
        parse = self.mcp.index("const raw = await request.json()")
        self.assertLess(auth, parse)
        self.assertIn('www-authenticate', self.mcp.lower())
        self.assertIn('oauth-protected-resource', self.mcp)

    def test_mcp_exposes_typed_tools_not_generic_shell(self) -> None:
        for tool in (
            '"ordax_profile"',
            '"ordax_targets"',
            '"repository_catalog"',
            '"project_create"',
            '"project_import"',
            '"project_briefing"',
            '"continuity_state"',
            '"continuity_update"',
            '"project_text_read"',
            '"project_text_write"',
            '"handoff_get"',
            '"handoff_create"',
            '"workspace_text_read"',
            '"workspace_text_write"',
            '"workspace_path_remove"',
            '"git_command"',
            '"terminal_exec"',
            '"browser_status"',
            '"browser_list"',
            '"browser_snapshot"',
            '"browser_screenshot"',
            '"browser_start"',
            '"browser_navigate"',
            '"browser_click"',
            '"browser_type"',
            '"browser_stop"',
            '"computer_windows"',
            '"computer_active_window"',
            '"computer_screenshot"',
            '"computer_focus_window"',
            '"computer_click"',
            '"computer_scroll"',
            '"computer_type"',
            '"computer_hotkey"',
            '"computer_access_status"',
            '"computer_file_stat"',
            '"computer_directory_list"',
            '"computer_text_read"',
            '"computer_search"',
            '"computer_processes"',
            '"computer_terminate_process"',
            '"computer_text_write"',
            '"computer_text_patch"',
            '"computer_directory_create"',
            '"computer_path_move"',
            '"computer_path_remove"',
            '"blender_status"',
            '"blender_scene_snapshot"',
            '"blender_start"',
            '"blender_transform"',
            '"blender_create_primitive"',
            '"blender_apply_material"',
            '"blender_save"',
        ):
            self.assertIn(tool, self.mcp)
        self.assertNotIn('action_execute', self.mcp)
        self.assertNotIn('shell_execute', self.mcp)
        self.assertNotIn('run_command', self.mcp)
        self.assertIn('action: "terminal.exec"', self.mcp)
        self.assertIn('Requires an explicit terminal.exec grant', self.mcp)
        self.assertIn('recall_limit: { type: "integer", minimum: 1, maximum: 50 }', self.mcp)
        self.assertIn("continue/resume project work", self.mcp)
        self.assertIn("pass the user intent as query", self.mcp)

    def test_mcp_actions_still_flow_through_product_grants(self) -> None:
        self.assertIn('handlers.createAction(createRequest)', self.mcp)
        self.assertIn('handlers.getAction(statusRequest, requestId)', self.mcp)
        self.assertIn('wait_for_completion_ms', self.mcp)
        self.assertIn('Math.min(20000', self.mcp)
        self.assertIn('call ordax_action_status', self.mcp)

    def test_mcp_supports_standard_initialize_and_tools_methods(self) -> None:
        self.assertIn('method === "initialize"', self.mcp)
        self.assertIn('protocolVersion: "2025-06-18"', self.mcp)
        self.assertIn('method === "tools/list"', self.mcp)
        self.assertIn('method === "tools/call"', self.mcp)
        self.assertIn('method === "notifications/initialized"', self.mcp)

    def test_session_reports_tool_surface_fingerprint(self) -> None:
        self.assertIn('const MCP_TOOL_SURFACE_REVISION = "2026-10-05.1"', self.mcp)
        self.assertIn("mcp_tool_surface_revision: MCP_TOOL_SURFACE_REVISION", self.mcp)
        self.assertIn("mcp_tool_count: TOOLS.length", self.mcp)

    def test_mcp_advertises_public_review_metadata(self) -> None:
        self.assertIn("annotations: toolAnnotations(tool.name)", self.mcp)
        self.assertIn("readOnlyHint: readOnly", self.mcp)
        self.assertIn("destructiveHint: destructive", self.mcp)
        self.assertIn("NON_DESTRUCTIVE_WRITE_TOOLS", self.mcp)
        self.assertIn('project_import: "Import existing ORDAX project"', self.mcp)
        self.assertIn('browser_screenshot: "Capture browser page"', self.mcp)
        self.assertIn('computer_click: "Click desktop"', self.mcp)
        self.assertIn('computer_access_status: "Inspect computer access policy"', self.mcp)
        self.assertIn('computer_text_read: "Read computer text"', self.mcp)
        self.assertIn('computer_text_write: "Write computer text"', self.mcp)
        self.assertIn('computer_processes: "List system processes"', self.mcp)
        self.assertIn('computer_terminate_process: "Terminate system process"', self.mcp)
        self.assertIn('"browser_screenshot"', self.mcp)
        self.assertIn('"computer_click"', self.mcp)
        self.assertIn("effectClassCount !== 1", self.mcp)
        self.assertIn("openWorldHint: OPEN_WORLD_TOOLS.has(name)", self.mcp)
        self.assertIn("outputSchema:", self.mcp)
        self.assertIn("title: TOOL_TITLES[tool.name]", self.mcp)
        self.assertIn('"openai/toolInvocation/invoking"', self.mcp)
        self.assertIn('"openai/toolInvocation/invoked"', self.mcp)
        self.assertIn('"openai/profile": true', self.mcp)
        self.assertIn('required: ["id"]', self.mcp)
        self.assertIn('serverInfo: { name: "ORDAX Control Plane", version: "0.4.2" }', self.mcp)

    def test_device_enrollment_uses_ordax_product_identity_not_github_admin(self) -> None:
        self.assertIn('X-Ordax-Product-Subject', self.worker)
        self.assertIn('owner_product_subject_id', self.worker)
        self.assertIn('authenticateProductRequest(request, env)', self.worker)
        self.assertNotIn('verifyGithubRepositoryAdmin', self.worker)
        self.assertNotIn('repository_admin_required', self.worker)
        self.assertNotIn('X-Ordax-GitHub-User-Id', self.worker)

    def test_worker_exposes_public_plugin_review_routes(self) -> None:
        self.assertIn('openAiAppsChallenge', self.worker)
        self.assertIn('publicProductPage', self.worker)
        self.assertIn('"/.well-known/openai-apps-challenge"', self.worker)


if __name__ == "__main__":
    unittest.main()
