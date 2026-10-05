import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch, Mock

from ordax_dev_agent.actions import ActionRegistry
from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.execution_lock import ExecutionLock
from ordax_dev_agent.models import AgentJob, ActionResult
from ordax_dev_agent.projects import load_projects
from ordax_dev_agent.unity_editor_bridge import UnityEditorBridge
from ordax_dev_agent.workspace_actions import _remove_tree_force


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        self.config = AgentConfig('test',   5, self.root / 'state', self.root / 'agent',
                                  self.root / 'hordax', self.root / 'bridge',
                                  projects={'model': {'path': str(self.project), 'apps': ['blender', 'unity']}},
                                  default_project='model')

    def test_legacy_hordax_and_explicit_empty_registry(self):
        self.assertIn('hordax', load_projects(replace(self.config, projects=None)))
        self.assertEqual(load_projects(replace(self.config, projects={})), {})

    def test_invalid_settings_never_fall_back_to_another_project(self):
        self.config.state_dir.mkdir()
        (self.config.state_dir / 'agent-settings.json').write_text('{broken')
        with patch.dict(os.environ, {'ORDAX_AGENT_STATE_DIR': str(self.config.state_dir)}):
            with self.assertRaises(ValueError):
                AgentConfig.from_env()

    def test_single_explicit_project_replaces_obsolete_legacy_default(self):
        self.config.state_dir.mkdir()
        (self.config.state_dir / 'agent-settings.json').write_text(
            json.dumps({
                'default_project': 'hordax',
                'projects': {
                    'salvador-lacerda-2d': {
                        'path': str(self.project),
                        'apps': ['unity', 'blender'],
                    }
                },
            }),
            encoding='utf-8',
        )
        with patch.dict(
            os.environ,
            {'ORDAX_AGENT_STATE_DIR': str(self.config.state_dir)},
            clear=False,
        ):
            loaded = AgentConfig.from_env()
        self.assertEqual('salvador-lacerda-2d', loaded.default_project)

    def test_custom_companion_source_is_used(self):
        registry = ActionRegistry(replace(self.config, projects={'model': {
            'path': str(self.project), 'apps': ['unity'],
            'unity': {'companion_source': 'Assets/Editor/Custom.cs'}}}))
        self.assertEqual(
            registry._editor({}).companion_source_path,
            (self.project / 'Assets/Editor/Custom.cs').resolve(),
        )


    def test_generic_project_never_falls_back_to_hordax_companion(self):
        registry = ActionRegistry(self.config)
        self.assertEqual(
            registry._editor({}).companion_source_path,
            (self.project / 'Assets/OrdaX/Editor/OrdaXGenericAgent.cs').resolve(),
        )

    def test_hordax_profile_routes_to_dedicated_companion(self):
        registry = ActionRegistry(replace(self.config, projects={'model': {
            'path': str(self.project), 'apps': ['unity'],
            'unity': {'profile': 'hordax'}}}))
        self.assertEqual(
            registry._editor({}).companion_source_path,
            (self.project / 'Assets/HORDAX/Editor/OrdaXEditorAgent.cs').resolve(),
        )

    def test_jobs_route_to_registered_project_without_git(self):
        job = AgentJob('id', 'project.observe', project_slug='model')
        registry = ActionRegistry(self.config)
        self.assertEqual(registry._project_path(job.action_payload()), self.project.resolve())
        self.assertFalse(registry.execute('project.observe', {'project': 'unknown'}).ok)
        with self.assertRaises(ValueError):
            AgentJob('id', 'git.sync', {'project': 'other'}, project_slug='model').action_payload()

    def test_paths_cannot_escape_project(self):
        project = load_projects(self.config)['model']
        for path in ['../secret', str(self.root / 'secret')]:
            with self.assertRaises(ValueError):
                project.path(path, must_exist=False)

    def test_app_must_be_enabled(self):
        registry = ActionRegistry(replace(self.config, projects={'model': {'path': str(self.project), 'apps': []}}))
        self.assertFalse(registry.execute('unity.install_companion', {}).ok)

    def test_capture_paths_are_unique_and_project_scoped(self):
        registry = ActionRegistry(self.config)
        first = registry._capture_output({})
        second = registry._capture_output({})
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_relative_to(self.config.state_dir / 'artifacts/model'))

    def test_companion_install_is_idempotent_and_preserves_edits(self):
        (self.project / 'Assets').mkdir()
        registry = ActionRegistry(self.config)
        result = registry.execute('unity.install_companion', {})
        self.assertTrue(result.ok)
        self.assertTrue(registry.execute('unity.install_companion', {}).ok)
        target = Path(result.data['path'])
        target.write_text('// user changes')
        self.assertFalse(registry.execute('unity.install_companion', {}).ok)
        self.assertEqual(target.read_text(), '// user changes')

    def test_blender_snapshot_survives_failed_render(self):
        blend = self.project / 'scene.blend'
        blend.touch()
        def run(command, **kwargs):
            request = json.loads(Path(command[-1]).read_text())
            Path(request['snapshot']).write_text('{"camera": null}')
            return {'ok': False, 'returncode': 1, 'stderr': 'No camera'}
        registry = ActionRegistry(self.config)
        with patch('ordax_dev_agent.observations.find_blender', return_value=Path('blender')), \
             patch('ordax_dev_agent.observations.run_process', side_effect=run):
            result = registry.execute('blender.render_preview', {'blend_file': 'scene.blend'})
        self.assertFalse(result.ok)
        self.assertIn('snapshot_path', result.data)
        self.assertNotIn('artifact', result.data)

    def test_observation_stops_on_failure(self):
        registry = ActionRegistry(self.config)
        with patch.object(registry, 'blender_render_preview', return_value=ActionResult(False, 'no camera')) as render:
            result = registry.execute('observation.capture', {'app': 'blender', 'frames': 12})
        self.assertFalse(result.ok)
        self.assertEqual(render.call_count, 1)

    def test_frame_progress_is_emitted_before_sequence_finishes(self):
        registry = ActionRegistry(self.config)
        progress = []
        registry.on_observation = lambda item: progress.append(item['index'])
        def capture(payload):
            self.assertEqual(len(progress), capture.calls)
            capture.calls += 1
            return ActionResult(True, 'frame')
        capture.calls = 0
        with patch.object(registry, 'blender_render_preview', side_effect=capture), patch('ordax_dev_agent.observations.time.sleep'):
            result = registry.execute('observation.capture', {'app': 'blender', 'frames': 3})
        self.assertTrue(result.ok)
        self.assertEqual(progress, [0, 1, 2])

    def test_adapter_is_locally_enabled_and_project_scoped(self):
        entry = Mock()
        entry.name = 'example'
        handler = Mock(return_value=ActionResult(True, 'observed'))
        entry.load.return_value = lambda config: {'observe': handler}
        config = replace(self.config, adapters=('example',))
        with patch('ordax_dev_agent.actions.entry_points', return_value=[entry]):
            registry = ActionRegistry(config)
        self.assertFalse(registry.execute('example.observe', {}).ok)
        handler.assert_not_called()
        config = replace(config, projects={'model': {'path': str(self.project), 'apps': ['example']}})
        with patch('ordax_dev_agent.actions.entry_points', return_value=[entry]):
            registry = ActionRegistry(config)
        self.assertTrue(registry.execute('example.observe', {}).ok)
        self.assertEqual(handler.call_args.args[0].slug, 'model')

    def test_builtin_adapter_contracts_preserve_project_scope_and_global_blender_version(self):
        config = replace(
            self.config,
            projects={"model": {"path": str(self.project), "apps": []}},
        )
        registry = ActionRegistry(config)
        catalog = registry.adapter_contracts
        entries = {item["name"]: item for item in catalog["adapters"]}

        self.assertEqual(catalog["schema"], "ordax.device-adapter-contracts/1")
        self.assertEqual(entries["blender"]["component_id"], "adapter-blender")
        self.assertEqual(entries["unity"]["component_id"], "adapter-unity")
        self.assertEqual(entries["blender"]["global_actions"], ["blender.adoption_install", "blender.instances", "blender.version"])
        self.assertFalse(registry.execute("blender.inspect", {}).ok)
        self.assertFalse(registry.execute("unity.install_companion", {}).ok)

        with patch.object(registry, "blender_version", return_value=ActionResult(True, "version")):
            registry._actions["blender.version"] = registry.blender_version
            self.assertTrue(registry.execute("blender.version", {}).ok)

    def test_external_adapter_contract_is_entry_point_and_project_scoped(self):
        entry = Mock()
        entry.name = "example"
        handler = Mock(return_value=ActionResult(True, "observed"))
        entry.load.return_value = lambda config: {"observe": handler}
        config = replace(
            self.config,
            adapters=("example",),
            projects={"model": {"path": str(self.project), "apps": ["example"]}},
        )
        with patch("ordax_dev_agent.actions.entry_points", return_value=[entry]):
            registry = ActionRegistry(config)

        entries = {item["name"]: item for item in registry.adapter_contracts["adapters"]}
        self.assertEqual(entries["example"]["source"], "entry-point")
        self.assertEqual(entries["example"]["project_app"], "example")
        self.assertEqual(entries["example"]["global_actions"], [])
        self.assertTrue(registry.execute("example.observe", {}).ok)

    def test_quick_git_status_is_bounded_and_ignores_untracked_files(self):
        registry = ActionRegistry(self.config)
        with patch("ordax_dev_agent.git_actions._run", return_value=ActionResult(True, "ok", {"stdout": " M tracked.py\n"})) as run:
            result = registry.execute("git.quick_status", {"project": "model"})
        self.assertTrue(result.ok)
        command = run.call_args.args[0]
        self.assertIn("--untracked-files=no", command)
        self.assertEqual(3, run.call_args.kwargs["timeout"])

    def test_latest_preview_never_crosses_projects(self):
        registry = ActionRegistry(self.config)
        output = registry._capture_output({})
        output.write_bytes(b'frame')
        registry._record_capture({}, output)
        self.assertTrue(registry.execute('artifact.preview', {}).ok)
        self.assertFalse(registry.execute('artifact.preview', {'artifact_name': '../../secret'}).ok)

    def test_cloud_upload_cache_deduplicates_progress_and_final_result(self):
        from ordax_dev_agent.main import _upload_result_artifacts
        image = self.root / 'capture.png'
        image.write_bytes(b'frame')
        control = Mock()
        control.upload_artifact.return_value = {'artifact_id': 'id'}
        job = AgentJob('id', 'observation.capture', project_slug='model')
        cache = {}
        first = _upload_result_artifacts(control, job, ActionResult(True, 'frame', {'artifact': str(image)}), cache)
        final = _upload_result_artifacts(control, job, ActionResult(True, 'done', {'artifacts': [{'path': str(image)}]}), cache)
        self.assertEqual(first, final)
        self.assertEqual(control.upload_artifact.call_count, 1)

    def test_read_only_studio_surfaces_remain_available_while_agent_is_busy(self):
        registry = ActionRegistry(self.config)
        self.assertTrue(registry._execution_lock.acquire(blocking=False))
        try:
            catalog = registry.execute("workspace.repository_catalog", {})
            preview = registry.execute("project.preview_status", {"project": "model"})
        finally:
            registry._execution_lock.release()
        self.assertTrue(catalog.ok, catalog.summary)
        self.assertTrue(preview.ok, preview.summary)

    def test_process_lock_excludes_second_client(self):
        first, second = ExecutionLock(self.config.state_dir), ExecutionLock(self.config.state_dir)
        self.assertTrue(first.acquire())
        try:
            self.assertFalse(second.acquire())
            self.assertFalse(ActionRegistry(self.config).execute('project.observe', {}).ok)
        finally:
            first.release()
        self.assertTrue(second.acquire())
        second.release()

    def test_unity_timeout_does_not_return_retryable_none(self):
        bridge = UnityEditorBridge(self.project)
        with patch.object(bridge, 'presence_is_fresh', side_effect=[True, False]):
            result = bridge.request('capture', {'id': '../bad', 'action': 'bad'}, timeout_seconds=1)
        self.assertFalse(result.ok)
        self.assertTrue(result.data['outcome_unknown'])
        self.assertFalse(list(bridge.inbox.glob('*.json')))


    def test_unity_refresh_prefers_typed_companion_without_foreground_helper(self):
        registry = ActionRegistry(self.config)
        presence = self.root / "editor-presence.json"
        presence.write_text("{}", encoding="utf-8")
        initial_mtime = presence.stat().st_mtime

        editor = Mock()
        editor.presence = presence
        editor.project_appears_open.return_value = True
        editor.presence_is_fresh.return_value = True
        editor.status.return_value = {"presence": {"compiling": False}}

        def request(action, payload, timeout_seconds):
            self.assertEqual("refresh", action)
            os.utime(presence, (initial_mtime + 2.0, initial_mtime + 2.0))
            return ActionResult(True, "Asset refresh requested", {"transport": "unity-editor-companion"})

        editor.request.side_effect = request

        with (
            patch.object(registry, "_editor", return_value=editor),
            patch("ordax_dev_agent.unity_actions._run") as foreground_helper,
        ):
            result = registry.unity_refresh_editor(
                {"project": "model", "force": True, "wait_seconds": 5}
            )

        self.assertTrue(result.ok)
        self.assertEqual("unity-editor-companion", result.data["refresh_transport"])
        foreground_helper.assert_not_called()


    def test_workspace_project_create_uses_explicit_root_and_registers_immediately(self):
        workspace = self.root / "ordax-workspaces"
        state = self.root / "state-create"
        config = replace(
            self.config,
            state_dir=state,
            workspace_root=workspace,
            projects={},
            default_project="missing",
        )
        memory_db = state / "memory.db"
        with patch.dict(os.environ, {"ORDAX_MEMORY_DB": str(memory_db)}, clear=False):
            registry = ActionRegistry(config)
            result = registry.execute(
                "workspace.project_create",
                {
                    "slug": "cidade-morta",
                    "name": "Cidade Morta",
                    "apps": ["blender"],
                    "git_init": False,
                    "set_default": True,
                },
            )

        self.assertTrue(result.ok, f"{result.summary}: {result.data}")
        target = workspace / "cidade-morta"
        self.assertTrue((target / ".ordax" / "project.json").is_file())
        self.assertTrue((target / "README.md").is_file())
        self.assertTrue((target / ".gitignore").is_file())
        self.assertTrue((target / "automation" / "blender").is_dir())
        self.assertEqual("cidade-morta", result.data["active_project"])
        self.assertFalse(result.data["git_initialized"])
        self.assertIn("cidade-morta", registry.projects)
        self.assertEqual("cidade-morta", registry.select_available_project())

        settings = json.loads((state / "agent-settings.json").read_text(encoding="utf-8"))
        self.assertEqual(str(target.resolve()), settings["projects"]["cidade-morta"]["path"])
        self.assertEqual("cidade-morta", settings["default_project"])
        metadata = json.loads((target / ".ordax" / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(
            {"schema_version": 1, "slug": "cidade-morta", "name": "Cidade Morta", "apps": ["blender"]},
            metadata,
        )

    def test_workspace_project_create_rolls_back_if_git_init_fails(self):
        workspace = self.root / "ordax-workspaces-fail"
        state = self.root / "state-create-fail"
        config = replace(
            self.config,
            state_dir=state,
            workspace_root=workspace,
            projects={},
            default_project="missing",
        )
        registry = ActionRegistry(config)
        failed = ActionResult(False, "command failed", {"stderr": "git unavailable"})
        with patch("ordax_dev_agent.workspace_actions._run", return_value=failed):
            result = registry.execute(
                "workspace.project_create",
                {"slug": "broken-project", "git_init": True},
            )

        self.assertFalse(result.ok)
        self.assertIn("rolled back", result.summary)
        self.assertFalse((workspace / "broken-project").exists())
        settings_path = state / "agent-settings.json"
        if settings_path.exists():
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertNotIn("broken-project", settings.get("projects", {}))
        self.assertNotIn("broken-project", registry.projects)

    def test_workspace_bind_project_uses_legacy_hordax_parent_as_fallback_workspace(self):
        workspace = self.root / "github"
        hordax = workspace / "HORDAX-game"
        target = workspace / "dioramas-biblicos" / "diorama_jesus_samaritana_10cm"
        hordax.mkdir(parents=True)
        target.mkdir(parents=True)
        state = self.root / "state-bind"
        config = replace(
            self.config,
            state_dir=state,
            hordax_path=hordax,
        )
        memory_db = state / "memory.db"
        with patch.dict(os.environ, {"ORDAX_MEMORY_DB": str(memory_db)}, clear=False):
            registry = ActionRegistry(config)
            result = registry.execute(
                "workspace.bind_project",
                {
                    "slug": "diorama-jesus-samaritana-10cm",
                    "relative_path": "dioramas-biblicos/diorama_jesus_samaritana_10cm",
                    "apps": ["blender"],
                    "set_default": True,
                },
            )

        self.assertTrue(result.ok, f"{result.summary}: {result.data}")
        self.assertFalse(result.data["restart_required"])
        self.assertTrue(result.data["memory_active"])
        self.assertEqual("diorama-jesus-samaritana-10cm", result.data["active_project"])
        self.assertIn("diorama-jesus-samaritana-10cm", registry.projects)
        self.assertEqual(
            target.resolve(),
            registry.projects["diorama-jesus-samaritana-10cm"].root,
        )
        self.assertEqual(
            "diorama-jesus-samaritana-10cm",
            registry.select_available_project("diorama-jesus-samaritana-10cm"),
        )
        settings = json.loads(
            (config.state_dir / "agent-settings.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            str(target.resolve()),
            settings["projects"]["diorama-jesus-samaritana-10cm"]["path"],
        )
        self.assertEqual(
            "diorama-jesus-samaritana-10cm",
            settings["default_project"],
        )

    def test_workspace_bind_project_allows_repository_without_engine_adapter(self):
        workspace = self.root / "github"
        hordax = workspace / "HORDAX-game"
        target = workspace / "web-app"
        hordax.mkdir(parents=True)
        target.mkdir(parents=True)
        config = replace(self.config, state_dir=self.root / "state-bind-web", hordax_path=hordax)
        registry = ActionRegistry(config)
        result = registry.execute("workspace.bind_project", {
            "slug": "web-app", "relative_path": "web-app", "apps": [],
        })
        self.assertTrue(result.ok, result.summary)
        settings = json.loads((config.state_dir / "agent-settings.json").read_text(encoding="utf-8"))
        self.assertEqual([], settings["projects"]["web-app"]["apps"])

    def test_workspace_bind_project_rejects_escape(self):
        workspace = self.root / "github"
        hordax = workspace / "HORDAX-game"
        outside = self.root / "outside"
        hordax.mkdir(parents=True)
        outside.mkdir()
        config = replace(
            self.config,
            state_dir=self.root / "state-bind-bad",
            hordax_path=hordax,
        )
        registry = ActionRegistry(config)

        result = registry.execute(
            "workspace.bind_project",
            {
                "slug": "outside",
                "relative_path": "../outside",
                "apps": ["blender"],
            },
        )

        self.assertFalse(result.ok)
        self.assertIn("inside the ORDAX workspace", result.summary)

    def test_archive_rejects_hordax_self_archive(self):
        workspace = self.root / "github"
        hordax = workspace / "HORDAX-game"
        hordax.mkdir(parents=True)
        config = replace(
            self.config,
            hordax_path=hordax,
            projects={
                "hordax-copy": {
                    "path": str(hordax),
                    "apps": ["blender"],
                }
            },
            default_project="hordax-copy",
        )
        registry = ActionRegistry(config)

        result = registry.execute(
            "project.archive_to_hordax",
            {
                "project": "hordax-copy",
                "family": "tests",
            },
        )

        self.assertFalse(result.ok)
        self.assertIn("into itself", result.summary)



    def test_workspace_list_projects_finds_matching_nested_directory(self):
        workspace = self.root / "github"
        hordax = workspace / "HORDAX-game"
        target = workspace / "dioramas-biblicos" / "diorama-real"
        hordax.mkdir(parents=True)
        target.mkdir(parents=True)
        (target / "scene.blend").touch()
        config = replace(
            self.config,
            hordax_path=hordax,
        )
        registry = ActionRegistry(config)

        result = registry.execute(
            "workspace.list_projects",
            {
                "query": "diorama-real",
                "max_depth": 3,
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual(1, len(result.data["entries"]))
        self.assertEqual(
            "dioramas-biblicos/diorama-real",
            result.data["entries"][0]["relative_path"],
        )
        self.assertEqual(1, result.data["entries"][0]["blend_files_at_root"])



    def test_workspace_list_projects_prunes_node_modules(self):
        workspace = self.root / "github"
        hordax = workspace / "HORDAX-game"
        target = workspace / "node_modules" / "fake-samaritana"
        hordax.mkdir(parents=True)
        target.mkdir(parents=True)
        config = replace(self.config, hordax_path=hordax)
        registry = ActionRegistry(config)

        result = registry.execute(
            "workspace.list_projects",
            {"query": "samaritana", "max_depth": 5},
        )

        self.assertTrue(result.ok)
        self.assertEqual([], result.data["entries"])



    def test_force_remove_tree_retries_after_permission_error(self):
        target = self.root / "archive-cache"
        target.mkdir()
        (target / "file.bin").write_bytes(b"x")
        real_rmtree = __import__("shutil").rmtree
        calls = {"count": 0}

        def flaky_rmtree(path, *args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise PermissionError(5, "access denied")
            return real_rmtree(path, *args, **kwargs)

        with (
            patch(
                "ordax_dev_agent.workspace_actions.shutil.rmtree",
                side_effect=flaky_rmtree,
            ),
            patch("ordax_dev_agent.workspace_actions.time.sleep"),
        ):
            _remove_tree_force(target)

        self.assertEqual(2, calls["count"])
        self.assertFalse(target.exists())

    def test_archive_rejects_non_boolean_rebuild_cache(self):
        workspace = self.root / "github"
        hordax = workspace / "HORDAX-game"
        project = workspace / "project"
        hordax.mkdir(parents=True)
        project.mkdir()
        config = replace(
            self.config,
            hordax_path=hordax,
            projects={
                "model": {
                    "path": str(project),
                    "apps": ["blender"],
                }
            },
            default_project="model",
        )
        registry = ActionRegistry(config)

        result = registry.execute(
            "project.archive_to_hordax",
            {
                "project": "model",
                "family": "tests",
                "rebuild_cache": "yes",
            },
        )

        self.assertFalse(result.ok)
        self.assertIn("rebuild_cache must be boolean", result.summary)

    def test_archive_rejects_include_path_escape(self):
        workspace = self.root / "github"
        hordax = workspace / "HORDAX-game"
        project = workspace / "project"
        hordax.mkdir(parents=True)
        project.mkdir()
        config = replace(
            self.config,
            hordax_path=hordax,
            projects={
                "model": {
                    "path": str(project),
                    "apps": ["blender"],
                }
            },
            default_project="model",
        )
        registry = ActionRegistry(config)

        result = registry.execute(
            "project.archive_to_hordax",
            {
                "project": "model",
                "family": "tests",
                "include_paths": ["../secret.blend"],
            },
        )

        self.assertFalse(result.ok)
        self.assertIn("escapes project", result.summary)

    def test_archive_rejects_missing_selective_path_before_git(self):
        workspace = self.root / "github"
        hordax = workspace / "HORDAX-game"
        project = workspace / "project"
        hordax.mkdir(parents=True)
        project.mkdir()
        config = replace(
            self.config,
            hordax_path=hordax,
            projects={
                "model": {
                    "path": str(project),
                    "apps": ["blender"],
                }
            },
            default_project="model",
        )
        registry = ActionRegistry(config)

        result = registry.execute(
            "project.archive_to_hordax",
            {
                "project": "model",
                "family": "tests",
                "include_paths": ["missing.blend"],
            },
        )

        self.assertFalse(result.ok)
        self.assertIn("include path not found", result.summary)



if __name__ == '__main__':
    unittest.main()
