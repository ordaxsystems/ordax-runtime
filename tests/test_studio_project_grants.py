from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ordax_dev_agent.projects import Project
from ordax_studio.product_auth import ProductAuthSession
from ordax_studio.product_web_desktop import StudioProductApi


DEVICE_ID = "24630b00-e9c0-4d93-b38a-2367048cd9d1"
CLIENT_ID = "11111111-1111-4111-8111-111111111111"
GROUP_ID = "22222222-2222-4222-8222-222222222222"
OTHER_CLIENT_ID = "77777777-7777-4777-8777-777777777777"
SPACE_ID = "33333333-3333-4333-8333-333333333333"
PROJECT_ID = "44444444-4444-4444-8444-444444444444"
OTHER_PROJECT_ID = "55555555-5555-4555-8555-555555555555"
REPOSITORY = "ordaxsystems/veste"


class _Remote:
    def __init__(self, groups=()):
        self.groups = list(groups)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def owner_remote_authority(self, token, *, device_id):
        self.calls.append(("authority", token, device_id))
        return {
            "device_id": DEVICE_ID,
            "clients": [{"client_id": CLIENT_ID, "client_name": "ChatGPT"}],
            "grant_groups": self.groups,
        }

    def owner_project_scope(self, token, *, device_id, repository_full_name):
        self.calls.append(("scope", token, device_id, repository_full_name))
        return {
            "space_id": SPACE_ID,
            "project_id": PROJECT_ID,
            "repository_id": 31,
            "repository_full_name": REPOSITORY,
        }

    def replace_owner_remote_grant_group(self, token, **kwargs):
        self.calls.append(("replace", token, kwargs))
        return {"ok": True, "grant_group_id": GROUP_ID, **kwargs}

    def revoke_owner_remote_grant_group(self, token, **kwargs):
        self.calls.append(("revoke", token, kwargs))
        return {"ok": True, "revoked": True, **kwargs}


class StudioProjectGrantTests(unittest.TestCase):
    def api(self, root: Path) -> StudioProductApi:
        api = object.__new__(StudioProductApi)
        api.agent = SimpleNamespace(
            config=SimpleNamespace(
                product_mcp_url="https://mcp.example.test",
                projects={"veste": {"path": str(root)}},
            ),
            projects={"veste": Project("veste", root)},
        )
        api.project = "veste"
        api._product_session = ProductAuthSession("owner-token", "owner@example.test")
        api._product_device_id = DEVICE_ID
        return api

    def invoke(self, api, remote, method, *args, local_repositories=(REPOSITORY, REPOSITORY), **kwargs):
        with (
            patch("ordax_studio.product_web_desktop.ProductRemoteClient", return_value=remote),
            patch(
                "ordax_studio.product_web_desktop.project_repository_full_name",
                side_effect=local_repositories,
            ),
            patch(
                "ordax_dev_agent.projects.project_repository_full_name",
                return_value=REPOSITORY,
            ),
        ):
            return getattr(api, method)(*args, **kwargs)

    def test_browser_grants_are_returned_for_platform_resolved_registered_repository(self):
        with tempfile.TemporaryDirectory() as raw:
            api = self.api(Path(raw))
            remote = _Remote([{
                "grant_group_id": GROUP_ID,
                "client_id": CLIENT_ID,
                "device_id": DEVICE_ID,
                "profile_key": "project-browser-automation",
                "space_id": SPACE_ID,
                "project_id": PROJECT_ID,
            }])
            result = self.invoke(api, remote, "remote_project_browser_grants")

        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["canonical_scope"]["project_id"], PROJECT_ID)
        self.assertEqual([g["grant_group_id"] for g in result["data"]["grant_groups"]], [GROUP_ID])
        self.assertEqual(remote.calls[1], ("scope", "owner-token", DEVICE_ID, REPOSITORY))

    def test_renderer_cannot_supply_project_ids_for_browser_grant(self):
        with tempfile.TemporaryDirectory() as raw:
            api = self.api(Path(raw))
            remote = _Remote()
            result = self.invoke(api, remote, "authorize_remote_project_browser_grant", CLIENT_ID)

        self.assertTrue(result["ok"])
        call = next(item for item in remote.calls if item[0] == "replace")[2]
        self.assertEqual(call["space_id"], SPACE_ID)
        self.assertEqual(call["project_id"], PROJECT_ID)
        self.assertEqual(call["profile_key"], "project-browser-automation")

    def test_grant_is_denied_if_local_origin_changes_during_platform_resolution(self):
        with tempfile.TemporaryDirectory() as raw:
            api = self.api(Path(raw))
            remote = _Remote()
            result = self.invoke(
                api, remote, "authorize_remote_project_browser_grant", CLIENT_ID,
                local_repositories=(REPOSITORY, "ordaxsystems/other"),
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "registered_project_repository_changed")
        self.assertFalse(any(call[0] == "replace" for call in remote.calls))

    def test_duplicate_local_origins_are_ambiguous_for_project_authorization(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            duplicate = root / "duplicate"
            duplicate.mkdir()
            api = self.api(root)
            api.agent.projects["veste-copy"] = Project("veste-copy", duplicate)
            api.agent.config.projects["veste-copy"] = {"path": str(duplicate)}
            remote = _Remote()
            result = self.invoke(api, remote, "remote_project_browser_grants")

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "registered_project_repository_mapping_unavailable")
        self.assertFalse(any(call[0] == "scope" for call in remote.calls))

    def test_revoke_rejects_grant_from_another_project(self):
        with tempfile.TemporaryDirectory() as raw:
            api = self.api(Path(raw))
            remote = _Remote([{
                "grant_group_id": GROUP_ID,
                "client_id": CLIENT_ID,
                "device_id": DEVICE_ID,
                "profile_key": "project-browser-automation",
                "space_id": SPACE_ID,
                "project_id": OTHER_PROJECT_ID,
            }])
            result = self.invoke(api, remote, "revoke_remote_project_browser_grant", GROUP_ID, CLIENT_ID)

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "owner_grant_group_not_found")
        self.assertFalse(any(call[0] == "revoke" for call in remote.calls))

    def test_revoke_requires_the_same_oauth_client_as_the_exact_project_grant(self):
        with tempfile.TemporaryDirectory() as raw:
            api = self.api(Path(raw))
            remote = _Remote([{
                "grant_group_id": GROUP_ID,
                "client_id": CLIENT_ID,
                "device_id": DEVICE_ID,
                "profile_key": "project-browser-automation",
                "space_id": SPACE_ID,
                "project_id": PROJECT_ID,
            }])
            result = self.invoke(
                api, remote, "revoke_remote_project_browser_grant", GROUP_ID, OTHER_CLIENT_ID,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "owner_grant_group_not_found")
        self.assertFalse(any(call[0] == "revoke" for call in remote.calls))

    def test_revoke_sends_only_the_resolved_grant_for_the_selected_client_and_project(self):
        with tempfile.TemporaryDirectory() as raw:
            api = self.api(Path(raw))
            remote = _Remote([{
                "grant_group_id": GROUP_ID,
                "client_id": CLIENT_ID,
                "device_id": DEVICE_ID,
                "profile_key": "project-browser-automation",
                "space_id": SPACE_ID,
                "project_id": PROJECT_ID,
            }])
            result = self.invoke(
                api, remote, "revoke_remote_project_browser_grant", GROUP_ID, CLIENT_ID,
            )

        self.assertTrue(result["ok"])
        self.assertIn(("revoke", "owner-token", {
            "client_id": CLIENT_ID, "grant_group_id": GROUP_ID,
        }), remote.calls)

if __name__ == "__main__":
    unittest.main()
