from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx

from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.product_device_control_plane import ProductDeviceControlPlane
from ordax_dev_agent.windows_project_binding import (
    resolve_windows_project_workspace,
    validate_windows_project_links,
    windows_project_links,
)

DEVICE = "22222222-2222-4222-8222-222222222222"
PROJECT = "33333333-3333-4333-8333-333333333333"
SPACE = "44444444-4444-4444-8444-444444444444"
INTENT = "55555555-5555-4555-8555-555555555555"
BINDING = "66666666-6666-4666-8666-666666666666"
REPOSITORY = "ordaxsystems/ordax-runtime"


def _config(root: Path, projects: dict | None = None) -> AgentConfig:
    state = root / "state"
    state.mkdir(exist_ok=True)
    (state / "device-token.cloudflare-v3.txt").write_text("a" * 64, encoding="ascii")
    return AgentConfig(
        agent_name="windows-agent",
        poll_seconds=12.0,
        state_dir=state,
        agent_repo_path=root / "agent",
        hordax_path=root / "hordax",
        bridge_path=root / "bridge",
        control_plane_protocol="cloudflare-v3",
        device_id=DEVICE,
        product_mcp_url="https://product.example",
        projects=projects,
    )


def _checkout(root: Path) -> Path:
    checkout = root / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "-C", str(checkout), "init", "-q"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(checkout), "remote", "add", "origin",
                    "https://github.com/" + REPOSITORY + ".git"],
                   check=True, capture_output=True)
    return checkout


def _mapped_config(root: Path) -> AgentConfig:
    checkout = _checkout(root)
    return _config(root, {"project-7": {
        "path": str(checkout),
        "cloud_project_id": PROJECT,
        "space_id": SPACE,
    }})


def _request_intents() -> dict:
    return {
        "ok": True,
        "schema": "ordax.device-project-binding-intents/1",
        "device_id": DEVICE,
        "intents": [{
            "intent_id": INTENT, "project_id": PROJECT, "space_id": SPACE,
            "repository_full_name": REPOSITORY,
            "expires_at": "2026-11-22T12:00:00+00:00",
        }],
    }


class WindowsProjectBindingTests(unittest.TestCase):
    def test_no_windows_project_config_skips_every_database_call(self):
        with tempfile.TemporaryDirectory() as raw:
            config = _config(Path(raw), {})
            self.assertEqual(windows_project_links(config)["links"], [])
            plane = ProductDeviceControlPlane(
                config, project_binding_provider=lambda: windows_project_links(config),
                project_binding_resolver=lambda link, repo: resolve_windows_project_workspace(config, link, repo),
            )
            try:
                with patch.object(plane, "_post", side_effect=AssertionError("unexpected SQL poll")):
                    self.assertEqual(plane.reconcile_project_bindings(), 0)
            finally:
                plane.product_central_session.close()
                plane.http.close()

    def test_explicit_windows_mapping_preserves_uuid_and_never_sends_paths(self):
        with tempfile.TemporaryDirectory() as raw:
            config = _mapped_config(Path(raw))
            links = windows_project_links(config)
            self.assertEqual(links, {
                "schema": "ordax.windows-project-links/1",
                "host": "ordax-windows-agent", "persistence": "device",
                "links": [{"localProjectId": "project-7", "cloudProjectId": PROJECT, "spaceId": SPACE}],
            })
            self.assertNotIn(str(config.projects["project-7"]["path"]), json.dumps(links))
            resolved = resolve_windows_project_workspace(config, links["links"][0], REPOSITORY)
            self.assertEqual(resolved.slug, "project-7")
            self.assertEqual(resolved.root, Path(config.projects["project-7"]["path"]).resolve())

    def test_missing_or_partial_owner_identity_and_noncanonical_slug_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            checkout = _checkout(root)
            for projects in [
                {"ordax-runtime": {"path": str(checkout), "cloud_project_id": PROJECT, "space_id": SPACE}},
                {"project-7": {"path": str(checkout), "cloud_project_id": PROJECT}},
                {"project-7": {"path": str(checkout), "cloud_project_id": "AAAAAAAA-3333-4333-8333-333333333333", "space_id": SPACE}},
                {"project-7": {"path": str(checkout), "cloud_project_id": PROJECT, "space_id": "1234"}},
            ]:
                with self.subTest(projects=projects):
                    with self.assertRaises(ValueError):
                        windows_project_links(_config(root, projects))

    def test_duplicate_cloud_project_and_extra_snapshot_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            config = _mapped_config(Path(raw))
            link = windows_project_links(config)["links"][0]
            duplicates = {"schema": "ordax.windows-project-links/1", "host": "ordax-windows-agent",
                          "persistence": "device", "links": [link, {**link, "localProjectId": "project-8"}]}
            with self.assertRaises(RuntimeError):
                validate_windows_project_links(duplicates)
            with self.assertRaises(RuntimeError):
                validate_windows_project_links({**windows_project_links(config), "localProjectPath": "C:/private"})

    def test_different_git_origin_and_changed_local_link_cannot_confirm(self):
        with tempfile.TemporaryDirectory() as raw:
            config = _mapped_config(Path(raw))
            link = windows_project_links(config)["links"][0]
            with self.assertRaises(ValueError):
                resolve_windows_project_workspace(config, link, "ordaxsystems/ordax-platform")
            with self.assertRaises(ValueError):
                resolve_windows_project_workspace(config, {**link, "cloudProjectId": DEVICE}, REPOSITORY)
            with self.assertRaises(ValueError):
                resolve_windows_project_workspace(replace(config, projects={}), link, REPOSITORY)

    def test_symlink_or_junction_workspace_is_not_project_authority(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = _mapped_config(root)
            alias = root / "alias"
            try:
                alias.symlink_to(config.projects["project-7"]["path"], target_is_directory=True)
            except OSError:
                self.skipTest("Host does not permit a directory symlink")
            projects = {"project-7": {
                **config.projects["project-7"], "path": str(alias),
            }}
            updated = replace(config, projects=projects)
            with self.assertRaises(ValueError):
                resolve_windows_project_workspace(updated, windows_project_links(updated)["links"][0], REPOSITORY)

    def test_signed_owner_intent_is_confirmed_only_after_verified_project_origin(self):
        with tempfile.TemporaryDirectory() as raw:
            config = _mapped_config(Path(raw))
            calls: list[str] = []

            def transport(request: httpx.Request):
                calls.append(request.url.path)
                if request.url.path.endswith("/project-binding-intents"):
                    return httpx.Response(200, json=_request_intents())
                if request.url.path.endswith("/project-binding-confirm"):
                    self.assertEqual(json.loads(request.content), {
                        "intent_id": INTENT,
                        "local_project_ref": "project-7",
                        "confirm": "bind_local_project",
                    })
                    return httpx.Response(200, json={
                        "ok": True, "schema": "ordax.device-project-binding-confirmed/1",
                        "intent_id": INTENT, "binding_id": BINDING,
                        "project_id": PROJECT, "space_id": SPACE, "device_id": DEVICE,
                        "state": "active", "next_state": "ready_without_grants",
                    })
                raise AssertionError(request.url.path)

            plane = ProductDeviceControlPlane(
                config, project_binding_provider=lambda: windows_project_links(config),
                project_binding_resolver=lambda link, repo: resolve_windows_project_workspace(config, link, repo),
            )
            plane.http.close()
            plane.http = httpx.Client(
                transport=httpx.MockTransport(transport),
                base_url="https://product.example",
                headers={"X-Ordax-Device-Id": DEVICE, "X-Ordax-Device-Token": "a" * 64},
            )
            try:
                self.assertEqual(plane.reconcile_project_bindings(), 1)
                self.assertEqual(calls, [
                    "/v3/device/transport/project-binding-intents",
                    "/v3/device/transport/project-binding-confirm",
                ])
            finally:
                plane.product_central_session.close()
                plane.http.close()

    def test_browser_and_artifact_actions_require_same_attested_project_scope(self):
        with tempfile.TemporaryDirectory() as raw:
            config = _mapped_config(Path(raw))
            plane = ProductDeviceControlPlane(
                config, project_binding_provider=lambda: windows_project_links(config),
                project_binding_resolver=lambda link, repo: resolve_windows_project_workspace(config, link, repo),
            )
            try:
                scope = {"project_id": PROJECT, "repository_full_name": REPOSITORY}
                for capability in ("browser.screenshot", "browser.list", "browser.start",
                                   "artifact.preview", "artifacts.list"):
                    with self.subTest(capability=capability):
                        row = {"capability": capability, "project_id": PROJECT}
                        payload, project = plane._bind_project_scope(
                            row, {"project": REPOSITORY, "_ordax_project_scope": scope},
                        )
                        self.assertEqual(payload["project"], "project-7")
                        self.assertNotIn("_ordax_project_scope", payload)
                        self.assertEqual(project.slug, "project-7")
                        with self.assertRaises(RuntimeError):
                            plane._bind_project_scope(row, {"project": REPOSITORY})
                        with self.assertRaises(RuntimeError):
                            plane._bind_project_scope(row, {
                                "project": REPOSITORY,
                                "_ordax_project_scope": {**scope, "project_id": DEVICE},
                            })
            finally:
                plane.product_central_session.close()
                plane.http.close()

    def test_parent_symlink_reparse_is_rejected_before_git_origin_resolution(self):
        with tempfile.TemporaryDirectory() as raw:
            config = _mapped_config(Path(raw))
            root = Path(config.projects["project-7"]["path"])
            alias = root.parent
            original = Path.is_symlink

            def linked_parent(path):
                return path == alias or original(path)

            with patch.object(Path, "is_symlink", linked_parent), self.assertRaises(ValueError):
                resolve_windows_project_workspace(
                    config, windows_project_links(config)["links"][0], REPOSITORY,
                )

    def test_windows_launcher_wires_existing_project_allowlist_into_canonical_transport(self):
        import os
        from ordax_dev_agent import main as runtime_main

        with tempfile.TemporaryDirectory() as raw:
            config = _mapped_config(Path(raw))
            with patch.dict(os.environ, {}, clear=True), patch.object(
                runtime_main.sys, "platform", "win32",
            ), patch.object(
                runtime_main.AgentConfig, "from_env", return_value=config,
            ), patch.object(
                runtime_main, "_startup_log",
            ), patch.object(
                runtime_main, "run", return_value=0,
            ) as runner:
                self.assertEqual(runtime_main.main(), 0)
            runner.assert_called_once()
            self.assertIs(runner.call_args.args[0], config)
            provider = runner.call_args.kwargs["project_binding_provider"]
            resolver = runner.call_args.kwargs["project_binding_resolver"]
            links = provider()["links"]
            self.assertEqual(len(links), 1)
            self.assertEqual(resolver(links[0], REPOSITORY).slug, "project-7")

    def test_windows_and_os_host_providers_never_mix(self):
        with tempfile.TemporaryDirectory() as raw:
            config = _mapped_config(Path(raw))
            snapshot = windows_project_links(config)
            with self.assertRaises(RuntimeError):
                validate_windows_project_links({**snapshot, "host": "ordax-desktop"})
            with self.assertRaises(RuntimeError):
                windows_project_links(replace(config, computer_access_source="native-unbound"))


if __name__ == "__main__":
    unittest.main()
