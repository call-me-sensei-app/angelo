# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Each model request is priced at its own tier, wherever usage is summed (request_tiers.py).

A model with a price step (GPT above 272K input tokens, Haiku 5.5 above 100K, Grok above 200K, MiniMax above 512K)
bills a request above it at higher rates for that whole request. A turn's usage is a sum of its requests, so the
runtimes' per-request counts are folded into ``above_step``: per known step, the tokens of the requests above it.
Sums of sums stay exact, so a chat's usage is priced as (total - above) at base rates plus above at the higher
rates. Dependency-free: no process, account or model.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from angelo_agent import model_profiles, provider_usage, request_tiers, telemetry, usage_meter  # noqa: E402
import list_prices  # noqa: E402

SOL = "gpt-6.2-sol"


def request(prompt, read=0, write=0, output=1000):
    return {"input_tokens": prompt, "cached_input_tokens": read, "cache_write_input_tokens": write,
            "output_tokens": output}


def usd(requests, model, ttl="5m"):
    """The reference price: each request at the tier its own prompt selects."""
    total = 0.0
    for item in requests:
        rates = model_profiles.rates(model, ttl, prompt_tokens=item["input_tokens"])
        fresh = item["input_tokens"] - item["cached_input_tokens"] - item["cache_write_input_tokens"]
        total += (fresh * rates["input"] + item["cached_input_tokens"] * rates["cache_read"]
                  + item["cache_write_input_tokens"] * rates["cache_write"] + item["output_tokens"] * rates["output"])
    return total / 1e6


class FoldTests(unittest.TestCase):
    def test_every_step_the_table_knows_is_folded(self):
        steps = {facts["price_step_tokens"] for facts in model_profiles.MODELS.values() if facts.get("price_step_tokens")}
        self.assertEqual(set(request_tiers.STEPS), steps)
        self.assertIn(model_profiles.OPENAI_STEP_TOKENS, request_tiers.STEPS)

    def test_sums_of_folds_are_the_fold_of_the_sum(self):
        first = [request(50_000), request(300_000, read=200_000)]
        second = [request(600_000, read=500_000, write=10_000)]
        folded = request_tiers.added(request_tiers.fold(first), request_tiers.fold(second))
        self.assertEqual(folded, request_tiers.fold(first + second))
        self.assertEqual(folded["272000"]["requests"], 2)
        self.assertEqual(folded["512000"], dict(request(600_000, read=500_000, write=10_000), requests=1))
        self.assertNotIn("100000", request_tiers.fold([request(100_000)]))   # At the step: base rates.
        self.assertEqual(request_tiers.fold([request(100_001)])["100000"]["requests"], 1)

    def test_a_chat_of_many_requests_is_priced_at_each_requests_own_tier(self):
        requests = [request(250_000, read=240_000), request(300_000, read=280_000, write=5_000),
                    request(400_000, read=390_000)]
        usage = dict(request_tiers.summed(requests), requests=3, turns=1, above_step=request_tiers.fold(requests))
        for model in (SOL, "gpt-6.2-astra", "claude-haiku-5-5", "grok-4.6", "claude-opus-5-5"):
            with self.subTest(model=model):
                expected = round(usd(requests, model), 4)
                self.assertAlmostEqual(usage_meter.entry({"requested_model": model}, usage)["usd"], expected, places=4)
                self.assertAlmostEqual(list_prices.list_cost(usage, model), expected, places=4)


class RecordingTests(unittest.TestCase):
    def test_codex_reports_each_model_response_so_its_turn_folds_them(self):
        meter = provider_usage.CodexThreadUsage()
        meter.thread_started("t")
        meter.begin_turn("t")
        total = dict.fromkeys(("inputTokens", "cachedInputTokens", "outputTokens", "reasoningOutputTokens"), 0)
        sent = [request(260_000, read=250_000, output=400), request(290_000, read=270_000, output=600)]
        for item in sent:
            last = {"inputTokens": item["input_tokens"], "cachedInputTokens": item["cached_input_tokens"],
                    "outputTokens": item["output_tokens"], "reasoningOutputTokens": 0}
            total = {key: total[key] + last[key] for key in total}
            meter.observe("t", {"total": dict(total), "last": last})
            meter.observe("t", {"total": dict(total), "last": last})   # A repeat that did not grow: not a request.
        usage = meter.finish_turn(1000)
        self.assertEqual(usage["requests"], 2)
        self.assertEqual(usage["above_step"], request_tiers.fold(sent))

    def test_a_turn_whose_responses_do_not_add_up_folds_nothing(self):
        meter = provider_usage.CodexThreadUsage()
        meter.thread_started("t")
        meter.begin_turn("t")
        meter.observe("t", {"total": {"inputTokens": 300_000, "cachedInputTokens": 0, "outputTokens": 10,
                                      "reasoningOutputTokens": 0},
                            "last": {"inputTokens": 100_000, "cachedInputTokens": 0, "outputTokens": 10,
                                     "reasoningOutputTokens": 0}})
        self.assertNotIn("above_step", meter.finish_turn(1000), "per-request counts that miss a request: unknown")

    def test_a_request_keeps_the_fold_only_when_every_turn_reported_one(self):
        metrics = telemetry.RequestMetrics("codex")
        metrics.count("provider_turn_requests")
        metrics.count("provider_turn_requests")
        one = [request(300_000, read=250_000)]
        metrics.provider_usage(dict(request_tiers.summed(one), requests=1, above_step=request_tiers.fold(one)))
        metrics.provider_usage(dict(request_tiers.summed(one), requests=1, above_step=request_tiers.fold(one)))
        usage = metrics.snapshot()["usage"]
        self.assertEqual(usage["above_step"], request_tiers.fold(one + one))
        cleaned = telemetry.clean_receipt(dict(metrics.snapshot(), phases={"total_ms": 1.0}))
        self.assertEqual(cleaned["usage"]["above_step"], request_tiers.fold(one + one))
        other = telemetry.RequestMetrics("codex")
        other.count("provider_turn_requests")
        other.count("provider_turn_requests")
        other.provider_usage(dict(request_tiers.summed(one), requests=1, above_step=request_tiers.fold(one)))
        other.provider_usage(dict(request_tiers.summed(one), requests=1))   # A turn that could not split its requests.
        self.assertNotIn("above_step", other.snapshot()["usage"])

    def test_a_receipt_never_keeps_a_fold_larger_than_its_usage(self):
        usage = dict(request(100_000), turns=1, requests=1,
                     above_step={"272000": dict(request(300_000), requests=1)})
        cleaned = telemetry.clean_receipt({"status": "completed", "phases": {"total_ms": 1.0}, "usage": usage})
        self.assertNotIn("above_step", cleaned["usage"])


if __name__ == "__main__":
    unittest.main()
