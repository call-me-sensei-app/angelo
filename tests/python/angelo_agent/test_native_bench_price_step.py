# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A per-request price step is applied to each model request, never to usage summed over several.

Haiku 5.5 charges its higher rates for one request whose prompt exceeds 100k
tokens (``model_profiles``: ``price_step_tokens``, ``prices_above_step``);
Grok 4.6's step is 200k. The harness records usage summed over a turn's
requests, so pricing that sum against the step put a turn of small requests
in the long-context tier. Fixtures have the drivers' shapes (normalized
usage, Claude Code stream lines, Codex notifications); no process, account
or model is involved.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_mcp_scenario as mcp_scenario  # noqa: E402
import long_session_cost_model as projection  # noqa: E402
import oneshot_metrics as metrics  # noqa: E402
import oneshot_session  # noqa: E402
import scenario_session  # noqa: E402

HAIKU, GROK = "claude-haiku-5-5", "grok-4.6"
# Haiku 5.5 USD per million: fresh input, output, cache read; at or below the step, then above it.
HAIKU_BASE, HAIKU_ABOVE = (0.10, 0.50, 0.01), (0.50, 2.50, 0.05)


def usage(prompt, output=1000, read=0, requests=None):
    """Normalized usage: ``prompt`` tokens in all (``read`` of them cache reads), ``output`` out."""
    value = {"input_tokens": prompt, "cached_input_tokens": read, "cache_write_input_tokens": 0,
             "output_tokens": output}
    return dict(value, requests=requests) if requests is not None else value


def price(prompt, output, rates, read=0):
    fresh_rate, output_rate, read_rate = rates
    return ((prompt - read) * fresh_rate + read * read_rate + output * output_rate) / 1e6


def claude_line(identity, prompt, output=1000):
    """One Claude Code stream line of a response whose whole prompt was fresh."""
    return {"t_s": 1.0, "task": "brief", "message": {"type": "assistant", "message": {
        "id": identity, "content": [], "usage": {"input_tokens": prompt, "cache_read_input_tokens": 0,
                                                 "cache_creation_input_tokens": 0, "output_tokens": output}}}}


def codex_update(total, last):
    def tokens(prompt, output):
        return {"inputTokens": prompt, "cachedInputTokens": 0, "outputTokens": output}
    return (0.0, {"method": "thread/tokenUsage/updated",
                  "params": {"tokenUsage": {"total": tokens(*total), "last": tokens(*last)}}})


class AggregatedUsageTests(unittest.TestCase):
    def test_three_requests_of_50k_are_never_priced_above_the_step(self):
        each = usage(50_000)
        summed = usage(150_000, output=3000, requests=3)
        # Without each request's usage the tier of the sum is unknown: never the long-context tier by default.
        self.assertIsNone(metrics.list_cost(summed, HAIKU))
        basis = metrics.list_price_basis([summed], HAIKU, "5m")
        self.assertIn("unknown", basis)
        self.assertIn("100000-token per-request price step", basis)
        # With each request's own usage, each is priced against the step: all three at the base rates.
        self.assertAlmostEqual(metrics.list_cost(summed, HAIKU, per_request=[each] * 3),
                               round(3 * price(50_000, 1000, HAIKU_BASE), 4), places=6)
        self.assertIn("per request", metrics.list_price_basis([summed], HAIKU, "5m", per_request=[[each] * 3]))
        # Per-request usage that does not add up to the sum is another source: it is not used.
        self.assertIsNone(metrics.list_cost(summed, HAIKU, per_request=[each] * 2))

    def test_one_request_above_the_step_is_priced_at_its_higher_rates(self):
        one = usage(150_000, read=100_000, requests=1)
        self.assertAlmostEqual(metrics.list_cost(one, HAIKU),
                               round(price(150_000, 1000, HAIKU_ABOVE, read=100_000), 4), places=6)
        self.assertIn("one request above", metrics.list_price_basis([one], HAIKU, "5m"))
        # A usage is one request only when it says so: no request count, or a count over turns, is not one.
        self.assertIsNone(metrics.list_cost(usage(150_000), HAIKU))
        self.assertIsNone(metrics.list_cost(dict(one, turns=2), HAIKU))
        self.assertIsNone(metrics.list_cost(dict(one, interrupted_turns=1), HAIKU))

    def test_a_sum_within_the_step_is_priced_at_the_base_rates(self):
        # Every request of a sum of at most 100k tokens is itself within the step: exact without a split.
        summed = usage(90_000, output=3000, requests=3)
        self.assertAlmostEqual(metrics.list_cost(summed, HAIKU), round(price(90_000, 3000, HAIKU_BASE), 4), places=6)
        self.assertIn("within the 100000-token price step", metrics.list_price_basis([summed], HAIKU, "5m"))

    def test_models_without_a_step_are_priced_as_before(self):
        summed = usage(3_000_000, output=30_000, read=2_000_000, requests=30)
        for model, (fresh, output, read) in (("claude-opus-5-5", (4.0, 20.0, 0.20)), ("gpt-6-astra", (10.0, 50.0, 1.0)),
                                             ("gpt-6.1-sol", (2.0, 10.0, 0.10))):
            with self.subTest(model=model):
                self.assertAlmostEqual(metrics.list_cost(summed, model),
                                       round(price(3_000_000, 30_000, (fresh, output, read), read=2_000_000), 4))
                self.assertNotIn("step", metrics.list_price_basis([summed], model, "5m"))


class RecordedRequestTests(unittest.TestCase):
    def test_a_claude_code_stream_prices_each_response(self):
        lines = [claude_line("m%d" % n, 50_000) for n in (1, 2, 3)]
        running = metrics.usage_from_claude(lines)
        self.assertEqual((running["input_tokens"], running["requests"]), (150_000, 3))
        self.assertEqual(len(metrics.claude_requests(lines)), 3)
        record = {"status": "completed", "tool_calls": [], "provider_usage": {
            key: running[key] for key in ("input_tokens", "cached_input_tokens", "cache_write_input_tokens",
                                          "output_tokens")}}
        path = metrics.mcp_path(record, lines, {}, HAIKU)
        self.assertAlmostEqual(path["cost_list_usd"], round(3 * price(50_000, 1000, HAIKU_BASE), 4), places=6)
        self.assertIn("per request", path["cost_list_basis"])

    def test_codex_notifications_price_each_request_from_its_last_usage(self):
        # Grok 4.6 through Codex: three requests of 100k tokens (300k in all) against its 200k step.
        updates = [codex_update((100_000 * n, 1000 * n), (100_000, 1000)) for n in (1, 2, 3)]
        updates.insert(2, updates[1])   # An update whose total did not grow repeats a request: counted once.
        requests = metrics.codex_requests(updates)
        self.assertEqual([request["input_tokens"] for request in requests], [100_000] * 3)
        running = metrics.usage_from_codex(updates)
        base = (300_000 * 2.0 + 3000 * 6.0) / 1e6
        self.assertAlmostEqual(metrics.list_cost(running, GROK, per_request=requests), base, places=6)
        self.assertIsNone(metrics.list_cost(running, GROK))   # Its sum alone: unknown.
        record = {"status": "completed", "runtime": "codex", "tool_calls": [], "provider_usage": running}
        self.assertAlmostEqual(metrics.mcp_path(record, [{"t_s": 1.0, "task": "brief", "message": message}
                                                         for _, message in updates], {}, GROK)["cost_list_usd"],
                               base, places=6)


class CostCapTests(unittest.TestCase):
    def test_the_mcp_side_caps_the_priced_responses_not_their_sum(self):
        slices = [([(0.0, claude_line("m%d" % n, 50_000)["message"])], None, "deadline") for n in (1, 2, 3)]
        slices.append(([], {"type": "result", "subtype": "success"}, None))
        calls = {"interrupts": 0}

        def collect(deadline, task):
            return slices.pop(0)
        stream = NS(send=lambda text, images=(): None, collect=collect,
                    interrupt=lambda: calls.__setitem__("interrupts", calls["interrupts"] + 1))
        with tempfile.TemporaryDirectory() as folder:
            output = {}
            config = {"task_set": "oneshot-footbridge", "runtime": "claude", "model": HAIKU, "cost_cap": 0.02,
                      "claude_cwd": str(Path(folder) / "cwd")}
            mcp_scenario.guard(stream, config, Path(folder), output)
            stream.collect(time.monotonic() + 600, "brief")
        # Three base-rate responses: 0.0165 USD, under the cap; their sum at the long-context tier was 0.0825.
        self.assertEqual((calls["interrupts"], output["oneshot"]["stop"]), (0, None))
        self.assertAlmostEqual(output["oneshot"]["watch"]["cost_usd"], round(3 * price(50_000, 1000, HAIKU_BASE), 4))

    def test_the_angelo_side_caps_an_unsplit_sum_at_an_upper_bound(self):
        environment = {"ANGELO_EVAL_MODEL": HAIKU, oneshot_session.COST_CAP_ENV: "0.05"}
        with tempfile.TemporaryDirectory() as folder, mock.patch.dict(os.environ, environment):
            one = scenario_session.for_trial("oneshot-footbridge", Path(folder))
        stops = []
        one.controller = NS(state={"id": "r1", "messages": []}, busy=True, stop=lambda: stops.append(1),
                            _request_metrics=NS(usage=usage(150_000, output=3000, requests=3)), provider=None)
        one.watch()
        upper = round(price(150_000, 3000, HAIKU_ABOVE), 4)
        self.assertIsNone(one.state["watch"]["cost_usd"])           # Its list price is unknown: never guessed.
        self.assertEqual((one.state["watch"]["cap_cost_usd"], one.state["watch"]["cap_bound"]), (upper, "upper"))
        self.assertEqual(stops, [1])                                 # The cap still holds, on the bound.
        self.assertEqual(one.state["stop"], {"reason": "cost_cap", "cost_usd": upper, "cap_usd": 0.05,
                                             "bound": "upper"})


class ProjectionTests(unittest.TestCase):
    def test_a_projected_call_above_the_step_is_priced_at_the_higher_rates(self):
        self.addCleanup(projection.use_model, projection.MODEL)
        projection.use_model(HAIKU)
        below, above = projection.Session(0), projection.Session(0)
        below._price(90_000, 0, 0)
        above._price(150_000, 0, 0)
        self.assertAlmostEqual(below.cost, (90_000 * 0.01 + 2 * 0.10) / 1e6)
        self.assertAlmostEqual(above.cost, (150_000 * 0.05 + 2 * 0.50) / 1e6)
        projection.use_model(projection.MODEL)   # Opus 5.5 has no step: its calibration is unchanged.
        self.assertEqual((projection.READ, projection.WRITE, projection.OUTPUT, projection.INPUT, projection.STEP),
                         (0.20, 8.0, 20.0, 4.0, 0))


if __name__ == "__main__":
    unittest.main()
