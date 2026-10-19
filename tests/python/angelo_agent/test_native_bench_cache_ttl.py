# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Benchmark list prices charge each cache write at the TTL the provider reported for it.

Claude's usage splits every response's cache writes into 5-minute and 1-hour
ones (``usage.cache_creation``); Claude Code on the MCP side writes 1-hour
entries, as Angelo's chats do. The fixtures have the shapes the drivers record:
Claude Code stream lines, a trial's ``trial.json`` and the bench records. The
recorded-run check embeds three requests per side of a recorded Opus run;
set ``ANGELO_BENCH_RECORDED_RUN`` to a recorded Claude head-to-head folder to
check every request of it. No process, account or model is involved.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_mcp_report  # noqa: E402
import bench_mcp_vs_angelo as bench  # noqa: E402
import oneshot_metrics as metrics  # noqa: E402
import oneshot_session  # noqa: E402

OPUS = "claude-opus-5-5"   # 4 / 20 / 0.20 USD per million fresh, output and cached; writes 5 (5m) or 8 (1h).
# 0.1M fresh, 1M cached, 0.1M written and 30k output: 0.4 + 0.2 + 0.6 USD before the writes.
USAGE = {"input_tokens": 1_200_000, "cached_input_tokens": 1_000_000, "cache_write_input_tokens": 100_000,
         "output_tokens": 30_000}
UNWRITTEN = 0.4 + 0.2 + 0.6
ROUNDING = 0.00005 + 1e-9   # list_cost rounds to 4 decimals.


def split(five, hour):
    return dict(USAGE, cache_write_5m_input_tokens=five, cache_write_1h_input_tokens=hour)


def response(identity, five, hour, read=9000, output=10, reported=True):
    """One Claude Code ``assistant`` stream message; ``reported`` False omits ``usage.cache_creation``."""
    usage = {"input_tokens": 2, "cache_read_input_tokens": read, "cache_creation_input_tokens": five + hour,
             "output_tokens": output}
    if reported:
        usage["cache_creation"] = {"ephemeral_5m_input_tokens": five, "ephemeral_1h_input_tokens": hour}
    return {"type": "assistant", "message": {"id": identity, "content": [], "usage": usage}}


class ListCostTests(unittest.TestCase):
    def test_each_write_is_priced_at_its_reported_ttl(self):
        cases = {"1h only": (split(0, 100_000), "5m", UNWRITTEN + 0.8),
                 "5m only": (split(100_000, 0), "1h", UNWRITTEN + 0.5),
                 "mixed": (split(40_000, 60_000), "5m", UNWRITTEN + 0.2 + 0.48)}
        for name, (usage, assumed, expected) in cases.items():
            with self.subTest(name):
                # The assumed TTL is never used: every write has its own.
                self.assertAlmostEqual(metrics.list_cost(usage, OPUS, assumed), expected, places=4)
                self.assertEqual(metrics.cache_writes(usage, assumed)[1], 0)
                self.assertEqual(metrics.list_price_basis([usage], OPUS, assumed),
                                 "model_profiles list prices, cache writes at the TTLs the provider reported")

    def test_without_a_split_the_assumed_ttl_prices_every_write(self):
        self.assertAlmostEqual(metrics.list_cost(USAGE, OPUS, "5m"), UNWRITTEN + 0.5, places=4)
        self.assertAlmostEqual(metrics.list_cost(USAGE, OPUS, "1h"), UNWRITTEN + 0.8, places=4)
        self.assertEqual(metrics.cache_writes(USAGE, "1h"), ({"5m": 0, "1h": 100_000}, 100_000))
        self.assertEqual(metrics.list_price_basis([USAGE], OPUS, "1h"),
                         "model_profiles list prices, 1h cache writes assumed (no TTL split recorded)")

    def test_writes_a_split_does_not_cover_are_assumed_and_counted(self):
        partial = split(0, 60_000)                 # 40k written by responses that reported no split.
        self.assertAlmostEqual(metrics.list_cost(partial, OPUS, "5m"), UNWRITTEN + 0.48 + 0.2, places=4)
        self.assertIn("40000 write tokens without a reported TTL at the 5m rate",
                      metrics.list_price_basis([partial], OPUS, "5m"))
        contradicting = split(0, 150_000)          # More than was written: another source, ignored.
        self.assertAlmostEqual(metrics.list_cost(contradicting, OPUS, "5m"), UNWRITTEN + 0.5, places=4)
        self.assertEqual(metrics.cache_writes(dict(USAGE, cache_write_5m_input_tokens=None,
                                                   cache_write_1h_input_tokens=7), "5m")[1], 100_000)

    def test_a_model_with_one_write_rate_and_unknown_usage(self):
        # GPT-6 Astra has one write rate (Codex usage reports no split); a split cannot change its price.
        self.assertEqual(metrics.list_cost(split(0, 100_000), "gpt-6-astra"), metrics.list_cost(USAGE, "gpt-6-astra"))
        self.assertEqual(metrics.list_price_basis([USAGE], "gpt-6-astra", "5m", metrics.CODEX_UNSPLIT),
                         "model_profiles list prices, the model's one cache-write rate")
        for usage, model in ((None, OPUS), (split(0, 1), ""), (split(0, 1), "unknown-model")):
            self.assertIsNone(metrics.list_cost(usage, model))   # Unknown, never zero.


class StreamSplitTests(unittest.TestCase):
    def test_the_split_of_distinct_responses_in_every_stream_shape(self):
        messages = [response("m1", 0, 700), response("m1", 0, 700), response("m2", 100, 200)]  # m1 repeats.
        expected = {"cache_write_5m_input_tokens": 100, "cache_write_1h_input_tokens": 900}
        shapes = {"messages": messages, "(stamp, message)": [(0.1 * i, m) for i, m in enumerate(messages)],
                  "raw lines": [{"t_s": 0.1 * i, "task": "t", "message": m} for i, m in enumerate(messages)]}
        for name, lines in shapes.items():
            with self.subTest(name):
                self.assertEqual(metrics.claude_write_split(lines), expected)
        self.assertIsNone(metrics.claude_write_split([response("m1", 0, 700, reported=False)]))
        self.assertIsNone(metrics.claude_write_split([{"type": "system"}]))

    def test_a_turns_usage_gains_the_split_once_and_only_when_it_fits(self):
        recorded = {"input_tokens": 30_000, "cached_input_tokens": 27_000, "cache_write_input_tokens": 1000,
                    "output_tokens": 30, "cost_usd": 0.01}
        lines = [response("m1", 0, 700), response("m2", 0, 300, reported=False)]
        usage = metrics.with_write_split(recorded, lines)
        self.assertEqual((usage["cache_write_5m_input_tokens"], usage["cache_write_1h_input_tokens"]), (0, 700))
        self.assertNotIn("cache_write_1h_input_tokens", recorded, "The recorded usage is never changed")
        self.assertIn("300 write tokens without a reported TTL", metrics.list_price_basis([usage], OPUS, "5m"))
        self.assertIs(metrics.with_write_split(usage, [response("m3", 900, 0)]), usage)   # Already split.
        self.assertIs(metrics.with_write_split(recorded, [response("m1", 0, 5000)]), recorded)   # Does not fit.
        self.assertIs(metrics.with_write_split(recorded, []), recorded)
        self.assertIsNone(metrics.with_write_split(None, lines))

    def test_the_running_usage_and_the_one_shot_path_price_claude_codes_1h_writes(self):
        lines = [response("m1", 0, 8000, output=100), response("m2", 0, 400, output=100)]
        running = metrics.usage_from_claude(lines)
        self.assertEqual((running["cache_write_input_tokens"], running["cache_write_1h_input_tokens"]), (8400, 8400))
        expected = (4 * 4 + 18000 * 0.2 + 8400 * 8 + 200 * 20) / 1e6
        self.assertAlmostEqual(metrics.list_cost(running, OPUS, "5m"), expected, places=4)   # The cost cap's price.
        # A one-shot record from before the split was kept: its raw stream supplies it.
        record = {"status": "completed", "tool_calls": [],
                  "provider_usage": {key: running[key] for key in ("input_tokens", "cached_input_tokens",
                                                                    "cache_write_input_tokens", "output_tokens")}}
        path = metrics.mcp_path(record, [{"t_s": 1.0, "task": "brief", "message": m} for m in lines], {}, OPUS)
        self.assertAlmostEqual(path["cost_list_usd"], expected, places=4)
        self.assertEqual(path["cost_list_basis"],
                         "model_profiles list prices, cache writes at the TTLs the provider reported")
        codex = metrics.mcp_path(dict(record, runtime="codex"), [], {}, "gpt-6-astra")
        self.assertEqual(codex["cost_list_basis"], "model_profiles list prices, the model's one cache-write rate")


# A recorded Opus 5.5 long-set trial: the first three tasks' responses (Claude Code stream) and
# recorded usage per side. ``cost_usd`` is Claude Code's own estimate; every write was a 1-hour one.
RECORDED_MCP = {
    "read_only_question": ([(28526, 7946, 24), (36472, 270, 32), (36742, 436, 43)],
                           {"input_tokens": 110398, "cached_input_tokens": 101740, "cache_write_input_tokens": 8652,
                            "output_tokens": 168, "cost_usd": 0.092948}),
    "cube_creation": ([(37178, 106, 24), (37284, 357, 32), (37641, 338, 78)],
                      {"input_tokens": 112910, "cached_input_tokens": 112103, "cache_write_input_tokens": 801,
                       "output_tokens": 420, "cost_usd": 0.0372526}),
    "move_01": ([(37979, 128, 32), (38107, 201, 34)],
                {"input_tokens": 76419, "cached_input_tokens": 76086, "cache_write_input_tokens": 329,
                 "output_tokens": 169, "cost_usd": 0.0212452}),
}
RECORDED_ANGELO = [{"input_tokens": 36269, "cached_input_tokens": 34543, "cache_write_input_tokens": 1724,
               "output_tokens": 122, "cost_usd": 0.023149},
              {"input_tokens": 36684, "cached_input_tokens": 36267, "cache_write_input_tokens": 415,
               "output_tokens": 149, "cost_usd": 0.013561},
              {"input_tokens": 37281, "cached_input_tokens": 36682, "cache_write_input_tokens": 597,
               "output_tokens": 129, "cost_usd": 0.0147}]


def write_mcp_trial(output, tasks):
    """A recorded Claude Code MCP trial as the driver writes it: ``trial.json`` and the raw stream."""
    folder = output / "mcp-trial-01"
    folder.mkdir(parents=True)
    records, lines, clock = [], [], 0.0
    for task, (responses, usage) in tasks.items():
        records.append({"side": "mcp", "trial": 1, "task": task, "status": "completed", "sent_at_s": clock,
                        "provider_usage": usage})
        for index, (read, write, output_tokens) in enumerate(responses):
            clock += 1.0
            message = response("%s-%d" % (task, index), 0, write, read=read, output=output_tokens)
            lines.append({"t_s": clock, "task": task, "message": message})
        lines.append({"t_s": clock + 0.5, "task": task, "message": {"type": "result", "subtype": "success",
                                                                     "is_error": False}})
    (folder / "trial.json").write_text(json.dumps({"side": "mcp", "trial": 1, "tasks": records}))
    (folder / "claude-stream.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines))


class RecordedRunTests(unittest.TestCase):
    def assert_matches_claude_code(self, records):
        """Each request's list price is Claude Code's estimate of it (and of its warm-ups) within rounding."""
        for record in records:
            parts = [record["provider_usage"].get("cost_usd")]
            if record.get("warm_ups"):
                parts.append((record.get("warm_up_usage") or {}).get("cost_usd"))
            if None in parts:
                continue
            with self.subTest(side=record["side"], trial=record.get("trial"), task=record.get("task")):
                self.assertLessEqual(abs(record["list_price_usd"] - sum(parts)), ROUNDING * len(parts))

    def test_rc6_opus_long_both_sides_match_claude_codes_estimate(self):
        with tempfile.TemporaryDirectory() as folder:
            write_mcp_trial(Path(folder), RECORDED_MCP)
            mcp = bench.load_mcp_trial(Path(folder), 1)["tasks"]   # The reanalysis of a recorded run.
        angelo = [{"side": "angelo", "trial": 1, "provider_usage": usage, "warm_ups": 0} for usage in RECORDED_ANGELO]
        bench.price_records(mcp + angelo, OPUS, "claude")
        self.assert_matches_claude_code(mcp + angelo)
        self.assertEqual({r["list_price_basis"] for r in mcp},
                         {"model_profiles list prices, cache writes at the TTLs the provider reported"})
        self.assertEqual({r["list_price_basis"] for r in angelo},
                         {"model_profiles list prices, 1h cache writes assumed (no TTL split recorded)"})
        # The former 5-minute assumption under-priced the MCP side: 0.0670 instead of 0.0929 for the first task.
        first = RECORDED_MCP["read_only_question"][1]
        self.assertAlmostEqual(metrics.list_cost(first, OPUS, "5m"), 0.067, places=4)
        self.assertAlmostEqual(mcp[0]["list_price_usd"], 0.0929, places=4)

    def test_assumed_ttls_by_side_and_runtime(self):
        records = [{"side": side, "provider_usage": dict(USAGE)} for side in ("angelo", "mcp")]
        bench.price_records(records, OPUS, "claude", angelo_cache_ttl="5m")   # --angelo-cache-ttl 5m
        self.assertEqual([r["list_price_usd"] for r in records], [round(UNWRITTEN + 0.5, 4)] * 2)
        self.assertEqual(records[0]["list_price_basis"],
                         "model_profiles list prices, 5m cache writes assumed (no TTL split recorded)")
        codex = [{"side": "mcp", "provider_usage": dict(USAGE)}, {"side": "angelo", "provider_usage": dict(USAGE)}]
        bench.price_records(codex, "kimi-k3", "codex")   # A Codex model with a 1-hour rate: Codex reports no split.
        self.assertEqual({r["list_price_basis"] for r in codex},
                         {"model_profiles list prices, 5m cache writes assumed (Codex usage reports no TTL split)"})

    def test_angelos_unsplit_writes_are_priced_at_the_ttl_its_session_recorded(self):
        """An API-key or team chat writes 5-minute entries (Claude Code's default there); a subscription chat 1-hour."""
        trial = {"tasks": [{"task": task, "kind": "edit", "status": "completed", "provider_usage": dict(USAGE),
                            "receipt": {"route": {"mode": mode, "cache_ttl": ttl}}}
                           for task, mode, ttl in (("a", "claude_api", "5m"), ("b", "claude_team", "5m"),
                                                   ("c", "claude_subscription", "1h"))]}
        records = bench_mcp_report.angelo_records(trial, 1, {})
        self.assertEqual([r["cache_ttl"] for r in records], ["5m", "5m", "1h"])
        older = {"side": "angelo", "provider_usage": dict(USAGE)}   # Recorded before records carried the TTL.
        bench.price_records(records + [older], OPUS, "claude")
        self.assertEqual([r["list_price_usd"] for r in records + [older]],
                         [round(UNWRITTEN + 0.5, 4)] * 2 + [round(UNWRITTEN + 0.8, 4)] * 2)
        self.assertEqual(records[0]["list_price_basis"],
                         "model_profiles list prices, 5m cache writes assumed (no TTL split recorded)")

    def test_the_one_shot_cost_cap_prices_writes_at_the_chats_session_ttl(self):
        session = oneshot_session.OneShotSession.__new__(oneshot_session.OneShotSession)
        session.model = OPUS
        for ttl, expected in (("5m", UNWRITTEN + 0.5), ("1h", UNWRITTEN + 0.8), (None, UNWRITTEN + 0.8)):
            with self.subTest(ttl=ttl):
                session.controller = type("Chat", (), {"turn_options": {}, "session_cache_ttl": lambda self, t=ttl: t})()
                self.assertAlmostEqual(session.price(dict(USAGE))[0], expected, places=4)
        session.controller = None                                       # No chat to ask: the 1-hour rate, as before.
        self.assertAlmostEqual(session.price(dict(USAGE))[0], UNWRITTEN + 0.8, places=4)

    @unittest.skipUnless(os.environ.get("ANGELO_BENCH_RECORDED_RUN"),
                         "set ANGELO_BENCH_RECORDED_RUN to a recorded Claude head-to-head folder")
    def test_a_recorded_run_matches_claude_codes_estimate_on_every_request(self):
        output = Path(os.environ["ANGELO_BENCH_RECORDED_RUN"])
        data = json.loads((output / bench.RESULTS).read_text())
        self.assertEqual(data["meta"]["plan"].get("runtime", "claude"), "claude")
        mcp = [dict(task, side="mcp", trial=int(folder.name.rsplit("-", 1)[1]))
               for folder in sorted(output.glob("mcp-trial-*")) if (folder / "trial.json").is_file()
               for task in bench.load_mcp_trial(output, int(folder.name.rsplit("-", 1)[1]))["tasks"]]
        angelo = [record for record in data["records"] if record.get("side") == "angelo"]
        records = [r for r in mcp + angelo if (r.get("provider_usage") or {}).get("cost_usd") is not None]
        self.assertTrue(mcp and angelo, "a head-to-head run has both sides")
        bench.price_records(records, data["meta"]["plan"]["model"], "claude")
        self.assert_matches_claude_code([r for r in records if r.get("list_price_usd") is not None])


if __name__ == "__main__":
    unittest.main()
