from __future__ import annotations

import unittest

from ordax_dev_agent.studio_local_authorizer import (
    LocalStudioInvocationContext,
    NormalizedStudioActionRequest,
    TRUSTED_STUDIO_SOURCE,
    authorize_local_studio_action,
)


class StudioLocalAuthorizerTests(unittest.TestCase):
    def context(self, **overrides):
        values = {
            "source": TRUSTED_STUDIO_SOURCE,
            "accepted_device_ids": frozenset({"device-local", "device-paired"}),
            "available_local_actions": frozenset({
                "computer.file_stat",
                "project.inventory",
                "workspace.text_write",
            }),
            "known_projects": frozenset({"alpha"}),
        }
        values.update(overrides)
        return LocalStudioInvocationContext(**values)

    def request(self, **overrides):
        values = {
            "capability": "computer.file_stat",
            "device_id": "device-local",
            "actor_kind": "device-owner",
            "actor_subject_id": None,
            "project_id": None,
            "space_id": None,
            "parameters": {"path": r"C:\\Users\\owner\\file.txt"},
        }
        values.update(overrides)
        return NormalizedStudioActionRequest(**values)

    def test_allows_typed_device_owner_action_from_embedded_studio(self):
        decision = authorize_local_studio_action(self.request(), self.context())
        self.assertTrue(decision.allowed)
        self.assertEqual("allowed", decision.code)
        self.assertEqual("computer.file_stat", decision.spec.name)

    def test_rejects_untrusted_source_and_non_owner_actor(self):
        decision = authorize_local_studio_action(
            self.request(),
            self.context(source="remote-connector"),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual("untrusted_source", decision.code)

        decision = authorize_local_studio_action(
            self.request(actor_kind="account", actor_subject_id="subject-1"),
            self.context(),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual("actor_not_local_device_owner", decision.code)

    def test_rejects_device_mismatch_and_space_scope(self):
        decision = authorize_local_studio_action(
            self.request(device_id="other-device"),
            self.context(),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual("device_mismatch", decision.code)

        decision = authorize_local_studio_action(
            self.request(space_id="space-1"),
            self.context(),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual("device_owner_space_scope_forbidden", decision.code)

    def test_rejects_untyped_and_unavailable_capabilities(self):
        decision = authorize_local_studio_action(
            self.request(capability="shell.generic", parameters={}),
            self.context(),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual("capability_not_typed", decision.code)

        decision = authorize_local_studio_action(
            self.request(capability="computer.windows", parameters={}),
            self.context(),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual("capability_unavailable", decision.code)

    def test_project_requirement_is_taken_from_canonical_action_spec(self):
        decision = authorize_local_studio_action(
            self.request(
                capability="project.inventory",
                project_id=None,
                parameters={"max_depth": 2, "max_entries": 100},
            ),
            self.context(),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual("project_required", decision.code)

        decision = authorize_local_studio_action(
            self.request(
                capability="project.inventory",
                project_id="alpha",
                parameters={"max_depth": 2, "max_entries": 100},
            ),
            self.context(),
        )
        self.assertTrue(decision.allowed)

    def test_rejects_payload_fields_outside_canonical_spec(self):
        decision = authorize_local_studio_action(
            self.request(parameters={"path": "safe.txt", "token": "forbidden"}),
            self.context(),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual("unsupported_parameters", decision.code)

    def test_authorizer_never_executes_actions(self):
        decision = authorize_local_studio_action(
            self.request(
                capability="workspace.text_write",
                project_id="alpha",
                parameters={"path": "note.txt", "content": "hello", "create": True},
            ),
            self.context(),
        )
        self.assertTrue(decision.allowed)
        self.assertEqual("workspace.text_write", decision.spec.local_action)
        # No executor is accepted by the authorization API; an allowed decision
        # is metadata only and cannot execute anything by itself.


if __name__ == "__main__":
    unittest.main()
