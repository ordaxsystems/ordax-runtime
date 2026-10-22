"""P0 regression: avoid exhausting Hyperdrive daily quota with empty claims."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.main import product_idle_claim_delay_seconds


class ProductIdlePollBudgetTests(unittest.TestCase):
    def test_legacy_one_second_setting_is_safely_upgraded(self):
        with tempfile.TemporaryDirectory() as location:
            state = Path(location)
            (state / "agent-settings.json").write_text(json.dumps({"poll_seconds": 1.0}))
            with mock.patch.dict(os.environ, {
                "ORDAX_AGENT_STATE_DIR": location,
                "ORDAX_AGENT_POLL_SECONDS": "",
            }):
                config = AgentConfig.from_env()
            self.assertEqual(config.poll_seconds, 8.0)

    def test_new_default_and_operator_ceiling(self):
        with tempfile.TemporaryDirectory() as location:
            with mock.patch.dict(os.environ, {
                "ORDAX_AGENT_STATE_DIR": location,
                "ORDAX_AGENT_POLL_SECONDS": "",
            }):
                self.assertEqual(AgentConfig.from_env().poll_seconds, 12.0)
            with mock.patch.dict(os.environ, {
                "ORDAX_AGENT_STATE_DIR": location,
                "ORDAX_AGENT_POLL_SECONDS": "120",
            }):
                self.assertEqual(AgentConfig.from_env().poll_seconds, 60.0)

    def test_short_initial_burst_then_safe_idle_ceiling(self):
        self.assertLessEqual(product_idle_claim_delay_seconds(1, 12), 0.5)
        self.assertEqual(product_idle_claim_delay_seconds(60, 12), 30.0)
        self.assertEqual(product_idle_claim_delay_seconds(60, 1), 30.0)
        self.assertEqual(product_idle_claim_delay_seconds(60, 60), 60.0)
        with self.assertRaises(ValueError):
            product_idle_claim_delay_seconds(0, 12)

    def test_one_idle_device_stays_far_below_100k_query_daily_quota(self):
        remaining_seconds = 86400.0
        misses = 1
        requests = 0
        while remaining_seconds > 0:
            remaining_seconds -= product_idle_claim_delay_seconds(misses, 12)
            misses += 1
            requests += 1
        # Account for two SQL roundtrips per claim and one heartbeat every
        # 20 seconds, each conservatively estimated at two DB statements.
        upper_bound_queries = requests * 2 + 4320 * 2
        self.assertLess(upper_bound_queries, 16000)
        # The account Free limit is shared by devices, not per device.
        # Even 4 idle agents must leave plenty of room for real commands.
        self.assertLess(upper_bound_queries * 4, 100000)

    def test_invalid_ceiling_fails_closed(self):
        for v in (float("nan"), float("inf"), -float("inf")):
            with self.assertRaises(ValueError):
                product_idle_claim_delay_seconds(10, v)


if __name__ == "__main__":
    unittest.main()
