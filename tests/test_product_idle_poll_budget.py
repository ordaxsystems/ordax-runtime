"""P0 regression: avoid exhausting Hyperdrive daily quota with empty claims."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ordax_dev_agent.config import AgentConfig
from ordax_dev_agent.main import (
    product_idle_claim_delay_seconds, product_idle_wait_seconds,
)


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
                default = AgentConfig.from_env()
                self.assertEqual(default.poll_seconds, 12.0)
                self.assertTrue(default.product_wake_enabled)
            with mock.patch.dict(os.environ, {
                "ORDAX_AGENT_STATE_DIR": location,
                "ORDAX_AGENT_POLL_SECONDS": "120",
            }):
                self.assertEqual(AgentConfig.from_env().poll_seconds, 60.0)

    def test_short_initial_burst_then_safe_idle_ceiling(self):
        self.assertLessEqual(product_idle_claim_delay_seconds(1, 12), 0.5)
        self.assertEqual(product_idle_claim_delay_seconds(60, 12), 18.0)
        self.assertEqual(product_idle_claim_delay_seconds(60, 1), 18.0)
        self.assertEqual(product_idle_claim_delay_seconds(60, 60), 18.0)
        with self.assertRaises(ValueError):
            product_idle_claim_delay_seconds(0, 12)

    def test_repeated_interactive_work_limits_empty_claims(self):
        # Count fallback attempts during the first ten seconds after an empty
        # response. A real wake can interrupt the wait at any time.
        for verified, expected in ((False, 3), (True, 0)):
            elapsed = 0
            retries = 0
            while True:
                elapsed += product_idle_claim_delay_seconds(
                    retries + 1, 12, verified_wake=verified, active_jobs=1,
                )
                if elapsed > 10:
                    break
                retries += 1
            self.assertEqual(retries, expected)

    def test_verified_wake_reconciliation_stays_inside_action_ttl(self):
        # Connection without an actual authenticated notification is not
        # permission to replace the safe bounded claim polling budget.
        self.assertEqual(product_idle_claim_delay_seconds(60, 12), 18.0)
        self.assertEqual(product_idle_claim_delay_seconds(60, 12, verified_wake=False), 18.0)
        self.assertEqual(product_idle_claim_delay_seconds(60, 12, verified_wake=True), 60.0)
        self.assertEqual(product_idle_claim_delay_seconds(1, 12, verified_wake=True), 60.0)
        self.assertEqual(
            product_idle_claim_delay_seconds(
                60, 12, verified_wake=True, active_jobs=1,
            ),
            18.0,
            "active work must not depend exclusively on a later wake signal",
        )
        with self.assertRaises(ValueError):
            product_idle_claim_delay_seconds(60, 12, active_jobs=-1)
        with self.assertRaises(ValueError):
            product_idle_claim_delay_seconds(60, 12, verified_wake=1)

    def test_presence_heartbeat_is_independent_of_idle_claim_budget(self):
        # Product wake is the default low-latency path; even its long
        # reconciliation wait must not suppress the independent heartbeat.
        self.assertEqual(product_idle_wait_seconds(
            now=100.0, next_claim_due=160.0, next_heartbeat=130.0,
        ), 30.0)
        # After the heartbeat at 130, the next SQL claim is still due at 160.
        self.assertEqual(product_idle_wait_seconds(
            now=130.0, next_claim_due=160.0, next_heartbeat=160.0,
        ), 30.0)
        # No additional claims are needed merely to maintain presence.

    def test_verified_wake_uses_same_independent_heartbeat_gate(self):
        self.assertEqual(product_idle_wait_seconds(
            now=100.0, next_claim_due=160.0, next_heartbeat=130.0,
        ), 30.0)
        self.assertEqual(product_idle_wait_seconds(
            now=130.0, next_claim_due=160.0, next_heartbeat=160.0,
        ), 30.0)
        self.assertLess(
            product_idle_claim_delay_seconds(60, 12, verified_wake=True),
            120.0,
            "lost-wake reconciliation must precede the Product action queue TTL",
        )

    def test_idle_wait_prefers_next_claim_when_earlier_and_bounds_overdue(self):
        self.assertEqual(product_idle_wait_seconds(
            now=100.0, next_claim_due=105.0, next_heartbeat=120.0,
        ), 5.0)
        self.assertEqual(product_idle_wait_seconds(
            now=121.0, next_claim_due=160.0, next_heartbeat=120.0,
        ), 0.10)
        for invalid in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(deadline=invalid), self.assertRaises(ValueError):
                product_idle_wait_seconds(
                    now=100.0, next_claim_due=160.0, next_heartbeat=invalid,
                )

    def test_one_idle_device_stays_far_below_100k_query_daily_quota(self):
        remaining_seconds = 86400.0
        misses = 1
        requests = 0
        while remaining_seconds > 0:
            remaining_seconds -= product_idle_claim_delay_seconds(misses, 12)
            misses += 1
            requests += 1
        # The current Platform uses one SQL statement for each valid claim
        # and heartbeat. This is an IDLE baseline, not a full daily budget:
        # action-status reads, enqueues, receipts and other clients add usage.
        claim_queries = requests
        heartbeat_queries = 2880
        idle_queries = claim_queries + heartbeat_queries
        self.assertLess(idle_queries, 11000)
        self.assertLess(idle_queries * 4, 50000)

    def test_daily_budget_accounts_for_status_reads_and_active_commands(self):
        # Scenario inputs are explicit so an idle-only test cannot be
        # misinterpreted as a guarantee of 24-hour production headroom.
        daily_quota = 100000
        base_claims = 4800
        heartbeats = 2880
        status_reads = 70000
        enqueues = 1000
        terminal_receipts = 1200
        total = base_claims + heartbeats + status_reads + enqueues + terminal_receipts
        self.assertEqual(total, 79880)
        self.assertEqual(daily_quota - total, 20120)
        # The observed half-hour active rate, sustained for 24h, exceeds
        # the free quota even before idle heartbeat / claim traffic.
        observed_hourly_rate = 4512
        self.assertGreater(observed_hourly_rate * 24, daily_quota)

    def test_invalid_ceiling_fails_closed(self):
        for v in (float("nan"), float("inf"), -float("inf")):
            with self.assertRaises(ValueError):
                product_idle_claim_delay_seconds(10, v)


if __name__ == "__main__":
    unittest.main()
