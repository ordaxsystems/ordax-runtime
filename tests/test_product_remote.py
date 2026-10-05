from __future__ import annotations

import unittest

from ordax_dev_agent.models import ActionResult
from ordax_dev_agent.product_remote import (
    PRODUCT_REMOTE_CAPABILITY,
    PRODUCT_REMOTE_LEGACY_CAPABILITY,
    execute_product_invocation,
    parse_product_invocation,
)


class Executor:
    @property
    def names(self):
        return ["git.status"]

    def execute(self, action, payload):
        return ActionResult(True, "ok", {"command": ["git", "-C", "C:/secret"], "branch": "main"})


class Transport:
    def __init__(self):
        self.events = []

    def record_product_audit(self, event):
        self.events.append(event)


def payload():
    return {
        "action": "git.status",
        "arguments": {"project": "demo"},
        "context": {
            "request_id": "11111111-1111-4111-8111-111111111111",
            "subject_id": "user:1",
            "device_id": "device-1",
            "space_id": "space-1",
        },
        "grant": {
            "grant_id": "22222222-2222-4222-8222-222222222222",
            "subject_id": "user:1",
            "actions": ["git.status"],
            "projects": ["demo"],
            "space_id": "space-1",
            "device_id": "device-1",
            "expires_at_unix": 4_000_000_000,
        },
    }


class ProductRemoteTests(unittest.TestCase):
    def test_product_capability_is_dedicated(self):
        self.assertEqual(PRODUCT_REMOTE_CAPABILITY, "ordax.product.invoke")
        self.assertEqual(PRODUCT_REMOTE_LEGACY_CAPABILITY, "ordax.product.read.invoke")

    def test_parse_requires_device_bound_grant(self):
        body = payload()
        body["grant"]["device_id"] = None
        with self.assertRaisesRegex(ValueError, "grant device is required"):
            parse_product_invocation(body)

    def test_parse_rejects_extra_envelope_fields(self):
        body = payload()
        body["shell"] = "whoami"
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            parse_product_invocation(body)

    def test_execution_goes_through_gateway_and_audits(self):
        transport = Transport()
        result = execute_product_invocation(Executor(), transport, payload())
        self.assertTrue(result.ok)
        self.assertNotIn("command", result.data)
        self.assertEqual(len(transport.events), 2)
        self.assertEqual(transport.events[0].phase, "decision")
        self.assertEqual(transport.events[1].phase, "result")


if __name__ == "__main__":
    unittest.main()
