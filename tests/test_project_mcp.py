import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class ProjectMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_action_and_image_over_real_stdio(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'agent-settings.json').write_text(json.dumps({
                'default_project': 'test', 'projects': {'test': {'path': str(root), 'apps': ['unity']}}
            }))
            (root / 'README.md').write_text('ORDAX agent bridge\n', encoding='utf-8')
            image = root / 'artifacts/test/frame.png'
            image.parent.mkdir(parents=True)
            png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1cAAAAASUVORK5CYII=')
            image.write_bytes(png)
            server = StdioServerParameters(command=sys.executable,
                args=['-m', 'ordax_studio.mcp_server'],
                env={**os.environ, 'ORDAX_AGENT_STATE_DIR': directory,
                     'ORDAX_MEMORY_DB': str(root / 'memory.db'),
                     'ORDAX_WORKSPACE_ROOT': str(root / 'workspace')},
                cwd=str(Path(__file__).resolve().parents[1]))
            async with stdio_client(server) as (read, write):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    self.assertEqual('ordax-runtime', initialized.serverInfo.name)
                    tools = (await session.list_tools()).tools
                    names = [tool.name for tool in tools]
                    schemas = {tool.name: tool.inputSchema for tool in tools}
                    self.assertIn('studio_status', names)
                    self.assertIn('workspace_discover', names)
                    self.assertIn('project_create', names)
                    self.assertIn('project_inventory', names)
                    self.assertIn('project_read', names)
                    self.assertIn('project_write', names)
                    self.assertIn('project_patch', names)
                    self.assertIn('git_status', names)
                    self.assertIn('git_diff', names)
                    for blender_tool in (
                        'get_blender_status', 'start_blender', 'get_scene_info',
                        'get_object_info', 'get_viewport_screenshot', 'add_primitive',
                        'modify_object', 'scatter_on_surface', 'preview_boolean_cut',
                        'commit_boolean_cut', 'cancel_boolean_cut', 'cleanup_mesh',
                        'preview_degenerate_repair', 'commit_degenerate_repair', 'cancel_degenerate_repair',
                        'preview_merge_by_distance', 'commit_merge_by_distance', 'cancel_merge_by_distance',
                        'preview_boundary_hole_fill', 'commit_boundary_hole_fill', 'cancel_boundary_hole_fill',
                        'delete_object', 'set_material', 'batch_edit',
                        'save_blender', 'run_blender_project_script',
                    ):
                        self.assertIn(blender_tool, names)
                    self.assertIn('artifact_image', names)
                    self.assertIn('blender_live_view', names)
                    self.assertIn('blender_live_multiview', names)
                    self.assertIn('blend_file', schemas['blender_live_view']['properties'])
                    self.assertIn('blend_file', schemas['blender_live_multiview']['properties'])
                    self.assertIn('repository_catalog', names)
                    self.assertIn('session_context', names)
                    self.assertIn('project_health', names)
                    self.assertIn('agent_briefing', names)
                    self.assertIn('continuity_state', names)
                    self.assertIn('continuity_update', names)
                    self.assertIn('project_search', names)
                    self.assertIn('project_read_batch', names)
                    self.assertIn('session_resume', names)
                    self.assertIn('session_finish', names)
                    self.assertIn('memory_remember', names)
                    self.assertIn('session_checkpoint', names)
                    self.assertIn('project_preview_image', names)
                    self.assertIn('project_preview_status', names)
                    self.assertIn('project_preview_logs', names)
                    self.assertIn('project_preview_start', names)
                    self.assertIn('project_preview_stop', names)
                    created = await session.call_tool('project_create', {
                        'slug': 'new-project',
                        'name': 'New Project',
                        'apps': [],
                        'git_init': False,
                    })
                    self.assertFalse(created.isError)
                    created_payload = json.loads(created.content[0].text)
                    self.assertTrue(created_payload['ok'])
                    self.assertTrue((root / 'workspace' / 'new-project' / '.ordax' / 'project.json').is_file())
                    response = await session.call_tool('projects_list', {})
                    self.assertFalse(response.isError)
                    self.assertIn('test', response.content[0].text)
                    studio = await session.call_tool('studio_status', {'project': 'test'})
                    self.assertFalse(studio.isError)
                    self.assertIn('ORDAX Studio', studio.content[0].text)
                    inventory = await session.call_tool('project_inventory', {'project': 'test'})
                    self.assertFalse(inventory.isError)
                    self.assertIn('README.md', inventory.content[0].text)
                    opened = await session.call_tool('project_read', {'project': 'test', 'path': 'README.md'})
                    opened_payload = json.loads(opened.content[0].text)
                    self.assertTrue(opened_payload['ok'])
                    written = await session.call_tool('project_write', {
                        'project': 'test', 'path': 'README.md', 'content': 'ORDAX Studio MCP\n',
                        'expected_sha256': opened_payload['data']['sha256'],
                    })
                    written_payload = json.loads(written.content[0].text)
                    self.assertTrue(written_payload['ok'])
                    patched = await session.call_tool('project_patch', {
                        'project': 'test', 'path': 'README.md',
                        'expected_sha256': written_payload['data']['sha256'],
                        'replacements': [{'old': 'Studio', 'new': 'Studio Unified', 'expected_count': 1}],
                    })
                    self.assertTrue(json.loads(patched.content[0].text)['ok'])
                    catalog = await session.call_tool('repository_catalog', {})
                    self.assertFalse(catalog.isError)
                    health = await session.call_tool('project_health', {'project': 'test'})
                    self.assertFalse(health.isError)
                    self.assertIn('memory', health.content[0].text)
                    briefing = await session.call_tool('agent_briefing', {'project': 'test'})
                    self.assertFalse(briefing.isError)
                    self.assertIn('continuity', briefing.content[0].text)
                    search = await session.call_tool('project_search', {'project': 'test', 'query': 'Studio Unified'})
                    self.assertFalse(search.isError)
                    self.assertIn('README.md', search.content[0].text)
                    batch = await session.call_tool('project_read_batch', {'project': 'test', 'paths': ['README.md']})
                    self.assertFalse(batch.isError)
                    self.assertIn('ORDAX Studio Unified MCP', batch.content[0].text)
                    preview_status = await session.call_tool('project_preview_status', {'project': 'test'})
                    self.assertFalse(preview_status.isError)
                    preview_logs = await session.call_tool('project_preview_logs', {'project': 'test'})
                    self.assertFalse(preview_logs.isError)
                    resumed = await session.call_tool('session_resume', {'project': 'test'})
                    self.assertFalse(resumed.isError)
                    resumed_payload = json.loads(resumed.content[0].text)
                    session_id = resumed_payload['data']['session_id']
                    response = await session.call_tool('memory_remember', {'project': 'test', 'content': 'resume me'})
                    self.assertFalse(response.isError)
                    response = await session.call_tool('session_context', {'project': 'test'})
                    self.assertFalse(response.isError)
                    self.assertIn('resume me', response.content[0].text)
                    response = await session.call_tool('continuity_update', {
                        'project': 'test',
                        'summary': 'durable stdio state',
                        'next_action': 'continue tests',
                        'completed': ['memory'],
                    })
                    self.assertFalse(response.isError)
                    durable = await session.call_tool('continuity_state', {'project': 'test'})
                    self.assertFalse(durable.isError)
                    self.assertIn('durable stdio state', durable.content[0].text)
                    response = await session.call_tool('session_checkpoint', {'project': 'test', 'summary': 'stdio checkpoint'})
                    self.assertFalse(response.isError)
                    durable_after_checkpoint = await session.call_tool('continuity_state', {'project': 'test'})
                    self.assertIn('continue tests', durable_after_checkpoint.content[0].text)
                    self.assertIn('stdio checkpoint', durable_after_checkpoint.content[0].text)
                    finished = await session.call_tool('session_finish', {'session_id': session_id})
                    self.assertFalse(finished.isError)
                    preview = await session.call_tool('project_preview_image', {'project': 'test'})
                    self.assertFalse(preview.isError)
                    self.assertEqual('image', preview.content[1].type)
                    response = await session.call_tool('artifact_image', {'project': 'test', 'artifact_path': str(image)})
                    self.assertFalse(response.isError)
                    self.assertEqual(base64.b64decode(response.content[1].data), png)
                    response = await session.call_tool('artifact_image', {'project': 'test', 'artifact_path': str(root / 'secret.png')})
                    self.assertTrue(response.isError)
                    response = await session.call_tool('action_execute', {'action': 'project.observe', 'project': 'missing'})
                    self.assertIn('project not registered', response.content[0].text)
