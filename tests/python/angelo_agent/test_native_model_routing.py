# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Dependency-free tests of the unwired model tier policy (model_routing.py).

Prices in the fixture are Anthropic's published rates
(doc/angelo/model_routing.rst); measured blocks are invented test data, not
benchmark results.
"""

import ast
import copy
import sys
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core"
sys.path.insert(0, str(ADDON))
from angelo_agent import model_profiles  # noqa: E402
from angelo_agent import model_routing as policy  # noqa: E402

CONNECTION = "6f1c2b9e-0000-4000-8000-000000000001"
HAIKU = {"id": "claude-haiku-5-5", "tier": "fast", "images": True, "context_window": 1000000,
         "price_step_tokens": 100000,
         "prices": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.20}}
SONNET = {"id": "claude-sonnet-5-5", "tier": "balanced", "images": True, "context_window": 1000000,
          "prices": {"input": 2.0, "output": 10.0, "cache_read": 0.20, "cache_write": 4.0}}
OPUS = {"id": "claude-opus-5-5", "tier": "strong", "images": True, "context_window": 1000000,
        "prices": {"input": 4.0, "output": 20.0, "cache_read": 0.20, "cache_write": 8.0}}
EDIT = {"kind": "scene_edit"}


def profile(*models, billing="SUBSCRIPTION", runtime="claude"):
    return {"connection_id": CONNECTION, "runtime": runtime, "billing": billing,
            "models": [copy.deepcopy(m) for m in (models or (HAIKU, SONNET, OPUS))]}


def measured(model, kind="scene_edit", *, tasks=25, pass_rate=1.0, cost_usd=None, p75_ms=None):
    model = copy.deepcopy(model)
    record = {"tasks": tasks, "pass_rate": pass_rate}
    if cost_usd is not None:
        record["cost_usd"] = cost_usd
    if p75_ms is not None:
        record["p75_ms"] = p75_ms
    model.setdefault("measured", {})[kind] = record
    return model


class RouteTests(unittest.TestCase):
    def test_without_measured_parity_the_default_runs_as_today(self):
        default_opus = dict(OPUS, default=True)
        for kind in ("scene_edit", "planning", "scene_read"):
            chosen = policy.route(profile(HAIKU, SONNET, default_opus), {"kind": kind})
            self.assertEqual((chosen.model, chosen.measured, chosen.pinned), ("claude-opus-5-5", False, False))
        # A measured cheaper model is not admitted while the default itself is unmeasured.
        chosen = policy.route(profile(measured(HAIKU), SONNET, default_opus), EDIT)
        self.assertEqual(chosen.model, "claude-opus-5-5")
        self.assertIn("the default runs", chosen.reason)
        # No default row: the balanced tier, else the nearest stronger, never a weaker one.
        edit = policy.route(profile(), EDIT)
        self.assertEqual((edit.model, [rung.model for rung in edit.ladder]), ("claude-sonnet-5-5", ["claude-opus-5-5"]))
        self.assertEqual(policy.route(profile(HAIKU, OPUS), EDIT).model, "claude-opus-5-5")
        self.assertEqual(policy.route(profile(HAIKU), EDIT).model, "claude-haiku-5-5")

    def test_measured_fast_tier_within_the_quality_bar_is_chosen(self):
        default_opus = dict(measured(OPUS, pass_rate=1.0, cost_usd=0.028), default=True)
        chosen = policy.route(profile(measured(HAIKU, pass_rate=0.98, cost_usd=0.0009), SONNET, default_opus), EDIT)
        self.assertEqual((chosen.model, chosen.measured), ("claude-haiku-5-5", True))
        self.assertEqual([rung.tier for rung in chosen.ladder], ["balanced", "strong"])

    def test_a_cheaper_tier_below_the_bar_is_not_chosen(self):
        chosen = policy.route(profile(measured(HAIKU, pass_rate=0.90, cost_usd=0.0009),
                                      measured(SONNET, pass_rate=0.99, cost_usd=0.017),
                                      measured(OPUS, pass_rate=1.0, cost_usd=0.028)), EDIT)
        self.assertEqual(chosen.model, "claude-sonnet-5-5")

    def test_too_few_trials_do_not_count_as_measurement(self):
        chosen = policy.route(profile(measured(HAIKU, tasks=5), SONNET, OPUS), EDIT)
        self.assertEqual((chosen.model, chosen.measured), ("claude-sonnet-5-5", False))

    def test_unknown_cost_ranks_by_tier(self):
        chosen = policy.route(profile(measured(HAIKU), SONNET, measured(OPUS)), EDIT)
        self.assertEqual(chosen.model, "claude-haiku-5-5")

    def test_local_models_cost_nothing_so_latency_decides(self):
        slow = dict(measured(HAIKU, p75_ms=9000), id="local-small", prices=None)
        fast = dict(measured(SONNET, p75_ms=2000), id="local-large", prices=None)
        chosen = policy.route(profile(slow, fast, billing="LOCAL", runtime="codex"), EDIT)
        self.assertEqual((chosen.model, chosen.cache_ttl), ("local-large", None))

    def test_requests_exclude_models_that_cannot_take_them(self):
        blind = dict(SONNET, images=False)
        self.assertEqual(policy.route(profile(HAIKU, blind, OPUS), {"kind": "vision_check", "images": 2}).model,
                         "claude-opus-5-5")
        small = dict(OPUS, context_window=200000)
        with self.assertRaisesRegex(ValueError, "fits"):
            policy.route(profile(small), {"kind": "scene_read", "context_tokens": 300000})

    def test_repeated_escalations_hold_one_tier_up(self):
        base = profile(measured(HAIKU), SONNET, measured(OPUS))
        self.assertEqual(policy.route(base, dict(EDIT, recent_escalations=1)).model, "claude-haiku-5-5")
        held = policy.route(base, dict(EDIT, recent_escalations=2))
        self.assertEqual(held.model, "claude-sonnet-5-5")
        self.assertIn("held one tier up", held.reason)

    def test_the_persons_pin_wins_and_never_leaves_the_connection(self):
        pinned = policy.route(profile(measured(HAIKU), SONNET, OPUS), EDIT, pinned_model="claude-opus-5-5")
        self.assertEqual((pinned.model, pinned.pinned, pinned.ladder), ("claude-opus-5-5", True, ()))
        with self.assertRaisesRegex(ValueError, "never changes accounts"):
            policy.route(profile(), EDIT, pinned_model="gpt-6.1-sol")


class EscalationTests(unittest.TestCase):
    def setUp(self):
        self.fast = policy.route(profile(measured(HAIKU), SONNET, measured(OPUS)), EDIT)

    def test_only_quality_triggers_escalate(self):
        self.assertIsNone(policy.next_step(self.fast, "runtime_error", committed=False))
        self.assertIsNone(policy.next_step(self.fast, "cancelled", committed=False))
        step = policy.next_step(self.fast, "refusal", committed=False)
        self.assertEqual((step.action, step.rung.model), ("escalate", "claude-sonnet-5-5"))

    def test_one_cheap_rejection_round_before_escalating(self):
        self.assertIsNone(policy.next_step(self.fast, "plan_rejected", committed=False, rejections=0))
        self.assertEqual(policy.next_step(self.fast, "plan_rejected", committed=False, rejections=1).action,
                         "escalate")

    def test_possible_live_changes_are_never_retried_automatically(self):
        step = policy.next_step(self.fast, "host_check_failed", committed=True)
        self.assertEqual(step.action, "suggest")

    def test_escalation_is_bounded_per_request(self):
        step = policy.next_step(self.fast, "low_confidence", committed=False, escalations=1)
        self.assertEqual(step.action, "suggest")

    def test_pinned_models_only_suggest_and_the_top_rung_stops(self):
        pinned = policy.route(profile(), EDIT, pinned_model="claude-haiku-5-5")
        self.assertEqual(policy.next_step(pinned, "refusal", committed=False).action, "suggest")
        top = policy.route(profile(), EDIT, pinned_model="claude-opus-5-5")
        self.assertIsNone(policy.next_step(top, "refusal", committed=False))


class CacheAndDelegationTests(unittest.TestCase):
    def test_ttl_is_a_per_session_decision_matching_claude_codes_default_per_billing(self):
        # Claude Code's own main-conversation default: 1h on a subscription, 5m on any key-billed route.
        expected = {("interactive", "SUBSCRIPTION"): "1h"}
        for kind in sorted(policy.SESSION_KINDS):
            for billing in sorted(policy.BILLING):
                with self.subTest(kind=kind, billing=billing):
                    self.assertEqual(policy.cache_ttl("claude", kind, billing), expected.get((kind, billing), "5m"))
                    self.assertIsNone(policy.cache_ttl("codex", kind, billing))
        with self.assertRaises(ValueError):
            policy.cache_ttl("claude", "weekly", "SUBSCRIPTION")
        with self.assertRaises(ValueError):
            policy.cache_ttl("claude", "interactive", "TEAM")  # A team route is billed API_KEY (billing_kind).
        self.assertEqual(policy.route(profile(), EDIT, session_kind="subagent").cache_ttl, "5m")
        self.assertEqual(policy.route(profile(), EDIT).cache_ttl, "1h")
        self.assertEqual(policy.route(profile(billing="API_KEY"), EDIT).cache_ttl, "5m")

    def test_ttl_break_even_matches_the_opus_arithmetic(self):
        # 0.75 * 4 * w / (P * (1.25 * 4 - 0.2)) = 0.625 * w / P.
        self.assertAlmostEqual(policy.ttl_break_even_share(1700, 30000, 4.0, 0.2), 0.625 * 1700 / 30000)
        self.assertLess(policy.ttl_break_even_share(1000, 30000, 4.0, 0.2), 0.025)

    def test_rebase_ceiling_stays_below_a_price_step(self):
        self.assertEqual(policy.rebase_ceiling(profile(), "claude-haiku-5-5"), 90000)
        self.assertIsNone(policy.rebase_ceiling(profile(), "claude-opus-5-5"))

    def test_a_consultation_does_not_pay_for_a_single_typed_edit(self):
        value = policy.delegation_value(OPUS["prices"], HAIKU["prices"], {
            "helper_prompt": 30000, "helper_output": 500, "reply": 1500, "remaining_calls": 20,
            "lead_round_usd": 0.0276, "lead_rounds_saved": 0, "bulk": 0})
        self.assertFalse(value["pays_off"])
        self.assertLess(value["helper_usd"], 0.01)

    def test_a_read_heavy_investigation_pays_off(self):
        value = policy.delegation_value(OPUS["prices"], HAIKU["prices"], {
            "helper_prompt": 30000, "helper_output": 800, "reply": 1500, "remaining_calls": 20,
            "lead_round_usd": 0.0276, "lead_rounds_saved": 3, "bulk": 15000})
        self.assertTrue(value["pays_off"])
        self.assertIsNone(policy.delegation_value(None, HAIKU["prices"], {}))


class PublishedPriceTests(unittest.TestCase):
    def test_opus_rates_reproduce_a_recorded_claude_code_estimate(self):
        # A recorded cube_creation turn: 2 uncached, 28,661 read, 1,508 written (1h),
        # 300 output tokens; Claude Code estimated $0.023804.
        rates = model_profiles.rates("claude-opus-5-5", "1h")
        cost = (2 * rates["input"] + 28661 * rates["cache_read"] + 1508 * rates["cache_write"]
                + 300 * rates["output"]) / 1e6
        self.assertAlmostEqual(cost, 0.023804, places=6)

    def test_aliases_ttls_and_price_steps(self):
        self.assertEqual(model_profiles.resolve("haiku"), "claude-haiku-5-5")
        self.assertIsNone(model_profiles.resolve("unknown-model"))
        self.assertIsNone(model_profiles.rates("unknown-model"))
        self.assertEqual(model_profiles.rates("claude-opus-5-5", "5m")["cache_write"], 5.0)
        self.assertEqual(model_profiles.rates("claude-haiku-5-5", "1h", prompt_tokens=100001)["cache_write"], 1.0)
        self.assertEqual(model_profiles.rates("claude-haiku-5-5", "1h", prompt_tokens=100000)["cache_write"], 0.20)
        # Providers without a 1-hour TTL charge their one write rate.
        self.assertEqual(model_profiles.rates("gpt-6.1-sol", "1h")["cache_write"], 2.50)

    def test_every_row_is_sourced_and_tiered(self):
        for identity, facts in model_profiles.MODELS.items():
            with self.subTest(model=identity):
                self.assertIn(facts["source"], model_profiles.SOURCES)
                self.assertIn(facts["tier"], model_profiles.TIERS)
                self.assertIn(facts["runtime"], {"claude", "codex"})


class ProfileValidationTests(unittest.TestCase):
    def test_invalid_profiles_name_the_field(self):
        cases = [({"billing": "API_KEY", "models": [HAIKU]}, "connection_id"),
                 (dict(profile(), billing="FREE"), "billing"),
                 (profile(dict(HAIKU, tier="turbo")), "tier"),
                 (profile(HAIKU, HAIKU), "unique id"),
                 (profile(dict(HAIKU, default=True), dict(OPUS, default=True)), "Only one model"),
                 (profile(dict(HAIKU, prices={"input": -1})), "price"),
                 (profile(measured(HAIKU, kind="scene_edit", pass_rate=1.5)), "pass_rate"),
                 (dict(profile(), models=[]), "at least one model")]
        for value, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                policy.clean_profile(value)
        with self.assertRaisesRegex(ValueError, "task kind"):
            policy.route(profile(), {"kind": "sculpt_everything"})

    def test_the_policy_stays_pure_and_unwired(self):
        source = (ADDON / "angelo_agent/model_routing.py").read_text(encoding="utf-8")
        imported = {alias.name for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Import)
                    for alias in node.names}
        imported |= {node.module for node in ast.walk(ast.parse(source))
                     if isinstance(node, ast.ImportFrom) and node.module}
        self.assertEqual(imported, {"dataclasses"})
        # Product code calls only these policy entry points (Auto behind its
        # measured gate, escalation steps and the per-session TTL).
        used = set()
        for path in (ADDON / "angelo_agent").glob("*.py"):
            if path.name == "model_routing.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module == "model_routing":
                    used |= {(path.name, alias.name) for alias in node.names}
                elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "model_routing":
                    used.add((path.name, node.attr))
        self.assertEqual(used, {("controller.py", "cache_ttl"), ("controller.py", "auto_route"),
                                ("controller.py", "next_step"), ("controller.py", "STICKY_WINDOW"),
                                ("controller.py", "AUTOMATIC_ESCALATIONS"), ("delegation.py", "cache_ttl")})


if __name__ == "__main__":
    unittest.main()
