from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import httpx

from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.product_device_control_plane import ProductDeviceControlPlane


DEVICE = "22222222-2222-4222-8222-222222222222"
PROJECT = "33333333-3333-4333-8333-333333333333"
SPACE = "44444444-4444-4444-8444-444444444444"
INTENT = "55555555-5555-4555-8555-555555555555"
BINDING = "66666666-6666-4666-8666-666666666666"
REPOSITORY = "ordaxsystems/ordax-runtime"


def _config(root: Path) -> AgentConfig:
    return AgentConfig(
        agent_name="test-agent",
        poll_seconds=1.0,
        state_dir=root / "state",
        agent_repo_path=root / "agent",
        hordax_path=root / "hordax",
        bridge_path=root / "bridge",
        control_plane_protocol="cloudflare-v3",
        device_id=DEVICE,
        product_mcp_url="https://product.example",
    )


def _write_token(root: Path) -> None:
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "device-token.cloudflare-v3.txt").write_text("a" * 64, encoding="ascii")


def _snapshot(*, project: str = PROJECT, space: str = SPACE) -> dict:
    return {
        "schema": "ordax.desktop-project-links/1",
        "host": "ordax-desktop",
        "persistence": "device",
        "links": [{
            "localProjectId": "project-7",
            "cloudProjectId": project,
            "spaceId": space,
        }],
    }


def _intents(*, project: str = PROJECT, space: str = SPACE) -> dict:
    return {
        "ok": True,
        "schema": "ordax.device-project-binding-intents/1",
        "device_id": DEVICE,
        "intents": [{
            "intent_id": INTENT,
            "project_id": project,
            "space_id": space,
            "repository_full_name": REPOSITORY,
            "expires_at": "2026-11-14T02:00:00+00:00",
        }],
    }


def _confirmed(*, project: str = PROJECT, space: str = SPACE) -> dict:
    return {
        "ok": True,
        "schema": "ordax.device-project-binding-confirmed/1",
        "intent_id": INTENT,
        "binding_id": BINDING,
        "project_id": project,
        "space_id": space,
        "device_id": DEVICE,
        "state": "active",
        "next_state": "ready_without_grants",
    }


class ProductProjectBindingReconcileTests(unittest.TestCase):
    def plane(self, root: Path, handler, provider):
        _write_token(root)
        plane = ProductDeviceControlPlane(
            _config(root), project_binding_provider=provider,
        )
        plane.http.close()
        plane.http = httpx.Client(
            transport=httpx.MockTransport(handler),
            base_url="https://product.example",
            headers={
                "X-Ordax-Device-Id": DEVICE,
                "X-Ordax-Device-Token": "a" * 64,
            },
        )
        return plane

    def test_no_host_provider_means_no_project_binding_network_call(self):
        with tempfile.TemporaryDirectory() as raw:
            _write_token(Path(raw))
            plane = ProductDeviceControlPlane(_config(Path(raw)))
            try:
                self.assertEqual(plane.reconcile_project_bindings(), 0)
            finally:
                plane.product_central_session.close()
                plane.http.close()

    def test_exact_os_project_and_space_link_confirms_platform_intent(self):
        seen = []
        def handler(request: httpx.Request):
            body = json.loads(request.content)
            seen.append((request.url.path, body))
            if request.url.path.endswith("/project-binding-intents"):
                return httpx.Response(200, json=_intents())
            if request.url.path.endswith("/project-binding-confirm"):
                self.assertEqual(body, {
                    "intent_id": INTENT,
                    "local_project_ref": "project-7",
                    "confirm": "bind_local_project",
                })
                return httpx.Response(200, json=_confirmed())
            raise AssertionError(request.url.path)

        with tempfile.TemporaryDirectory() as raw:
            plane = self.plane(Path(raw), handler, _snapshot)
            try:
                self.assertEqual(plane.reconcile_project_bindings(), 1)
                self.assertEqual([item[0] for item in seen], [
                    "/v3/device/transport/project-binding-intents",
                    "/v3/device/transport/project-binding-confirm",
                ])
            finally:
                plane.product_central_session.close()
                plane.http.close()

    def test_repository_name_never_derives_local_project_ref(self):
        calls = []
        other_project = "77777777-7777-4777-8777-777777777777"
        other_space = "88888888-8888-4888-8888-888888888888"
        variants = [
            _snapshot(project=other_project, space=SPACE),
            _snapshot(project=PROJECT, space=other_space),
        ]
        for snapshot in variants:
            def handler(request: httpx.Request):
                calls.append(request.url.path)
                if request.url.path.endswith("/project-binding-intents"):
                    return httpx.Response(200, json=_intents())
                raise AssertionError("repository name must not authorize confirmation")
            with tempfile.TemporaryDirectory() as raw:
                plane = self.plane(Path(raw), handler, lambda s=snapshot: s)
                try:
                    self.assertEqual(plane.reconcile_project_bindings(), 0)
                finally:
                    plane.product_central_session.close()
                    plane.http.close()
        self.assertTrue(all(path.endswith("/project-binding-intents") for path in calls))

    def test_untrusted_path_repo_or_ambiguous_local_mapping_fails_closed(self):
        bad = [
            {**_snapshot(), "repository_full_name": REPOSITORY},
            {**_snapshot(), "links": [{
                **_snapshot()["links"][0],
                "path": "C:/Users/model/supplied",
            }]},
            {**_snapshot(), "links": [
                _snapshot()["links"][0],
                {**_snapshot()["links"][0], "localProjectId": "project-8"},
            ]},
        ]
        for snapshot in bad:
            def handler(request: httpx.Request):
                if request.url.path.endswith("/project-binding-intents"):
                    return httpx.Response(200, json=_intents())
                raise AssertionError("malformed local authority reached confirmation")
            with self.subTest(snapshot=snapshot), tempfile.TemporaryDirectory() as raw:
                plane = self.plane(Path(raw), handler, lambda s=snapshot: s)
                try:
                    with self.assertRaises(RuntimeError):
                        plane.reconcile_project_bindings()
                finally:
                    plane.product_central_session.close()
                    plane.http.close()

    def test_disabled_platform_feature_is_not_an_execution_failure(self):
        def handler(request: httpx.Request):
            self.assertTrue(request.url.path.endswith("/project-binding-intents"))
            return httpx.Response(404, json={"ok": False, "error": "not_found"})
        with tempfile.TemporaryDirectory() as raw:
            plane = self.plane(Path(raw), handler, _snapshot)
            try:
                self.assertEqual(plane.reconcile_project_bindings(), 0)
            finally:
                plane.product_central_session.close()
                plane.http.close()

    def test_unknown_confirmation_result_is_retried_only_after_fresh_pending_read(self):
        calls = []
        confirmation_attempt = 0
        def handler(request: httpx.Request):
            nonlocal confirmation_attempt
            calls.append(request.url.path)
            if request.url.path.endswith("/project-binding-intents"):
                return httpx.Response(200, json=_intents())
            if request.url.path.endswith("/project-binding-confirm"):
                confirmation_attempt += 1
                if confirmation_attempt == 1:
                    raise httpx.ReadTimeout("unknown confirmation result", request=request)
                return httpx.Response(200, json=_confirmed())
            raise AssertionError(request.url.path)

        with tempfile.TemporaryDirectory() as raw:
            plane = self.plane(Path(raw), handler, _snapshot)
            try:
                self.assertEqual(plane.reconcile_project_bindings(), 0)
                self.assertIn(INTENT, plane._uncertain_project_binding_confirmations)
                self.assertEqual(plane.reconcile_project_bindings(), 1)
                self.assertEqual(calls, [
                    "/v3/device/transport/project-binding-intents",
                    "/v3/device/transport/project-binding-confirm",
                    "/v3/device/transport/project-binding-intents",
                    "/v3/device/transport/project-binding-confirm",
                ])
                self.assertNotIn(INTENT, plane._uncertain_project_binding_confirmations)
            finally:
                plane.product_central_session.close()
                plane.http.close()

    def test_changed_authority_is_terminal_for_that_reconciliation_not_auto_granted(self):
        calls = []
        def handler(request: httpx.Request):
            calls.append(request.url.path)
            if request.url.path.endswith("/project-binding-intents"):
                return httpx.Response(200, json=_intents())
            return httpx.Response(409, json={
                "ok": False, "error": "project_device_binding_authority_changed",
            })

        with tempfile.TemporaryDirectory() as raw:
            plane = self.plane(Path(raw), handler, _snapshot)
            try:
                self.assertEqual(plane.reconcile_project_bindings(), 0)
                self.assertEqual(len(calls), 2)
            finally:
                plane.product_central_session.close()
                plane.http.close()


if __name__ == "__main__":
    unittest.main()
