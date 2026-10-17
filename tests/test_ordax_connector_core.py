from __future__ import annotations

import hashlib
import unittest

from ordax_connector.capabilities import canonical_capabilities, capability_digest


class ConnectorCoreTests(unittest.TestCase):
    def test_capability_digest_matches_platform_json_contract(self):
        self.assertEqual(canonical_capabilities(["b", "a", "a"]), ("a", "b"))
        self.assertEqual(capability_digest(["b", "a", "a"]), hashlib.sha256(b'["a","b"]').hexdigest())

    def test_invalid_capabilities_fail_closed(self):
        with self.assertRaises(ValueError):
            capability_digest([""])
        with self.assertRaises(ValueError):
            capability_digest(["x" * 161])


if __name__ == "__main__":
    unittest.main()
