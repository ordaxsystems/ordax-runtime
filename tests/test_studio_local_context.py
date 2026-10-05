from __future__ import annotations

import unittest

from ordax_dev_agent.local_device_identity import WindowsRuntimeDeviceIdentity
from ordax_dev_agent.studio_local_authorizer import TRUSTED_STUDIO_SOURCE
from ordax_dev_agent.studio_local_context import build_local_studio_invocation_context


class _Registry:
    def __init__(self):
        self.names = ["computer.file_stat", "project.inventory"]
        self.projects = {"alpha": object(), "beta": object()}


class StudioLocalContextTests(unittest.TestCase):
    def test_context_is_derived_only_from_runtime_owned_state(self):
        paired = "22222222-2222-4222-8222-222222222222"
        local = "11111111-1111-4111-8111-111111111111"
        context = build_local_studio_invocation_context(
            _Registry(),
            WindowsRuntimeDeviceIdentity(
                local_device_id=local,
                paired_device_id=paired,
            ),
        )

        self.assertEqual(TRUSTED_STUDIO_SOURCE, context.source)
        self.assertEqual(frozenset({local}), context.accepted_device_ids)
        self.assertNotIn(paired, context.accepted_device_ids)
        self.assertEqual(
            frozenset({"computer.file_stat", "project.inventory"}),
            context.available_local_actions,
        )
        self.assertEqual(frozenset({"alpha", "beta"}), context.known_projects)

    def test_registry_changes_are_reflected_on_next_context_build(self):
        registry = _Registry()
        identity = WindowsRuntimeDeviceIdentity(
            local_device_id="11111111-1111-4111-8111-111111111111",
            paired_device_id=None,
        )
        first = build_local_studio_invocation_context(registry, identity)
        registry.names.append("workspace.text_write")
        registry.projects["gamma"] = object()
        second = build_local_studio_invocation_context(registry, identity)

        self.assertNotIn("workspace.text_write", first.available_local_actions)
        self.assertIn("workspace.text_write", second.available_local_actions)
        self.assertNotIn("gamma", first.known_projects)
        self.assertIn("gamma", second.known_projects)


if __name__ == "__main__":
    unittest.main()
