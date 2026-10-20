# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The cache-floor check: a recorded passing gate stays clear, a broken prefix is a cache SEV. Nothing is sent."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import cache_floor  # noqa: E402
import final_runs as runs  # noqa: E402

SOL = ("codex_subscription", "gpt-6.1-sol")
OPUS = ("claude_subscription", "claude-opus-5-5")
# The 1.0.0 final gate's Sol default step (release-1.0.0-final-gate-3, gate-sol-default), condensed to each turn's
# (input, cached) tokens: trial 1 warmed an expired key (first call 0), trial 5's "material" turn read nothing
# under a minute after the previous turn. Every task passed; the floors must keep it clear.
RECORDED_SOL_DEFAULT = [
    [(26483, 0), (26678, 26368), (27143, 26496), (27408, 27008), (27727, 27264), (28122, 27520)],
    [(26310, 24832), (26682, 26112), (27153, 26496), (27417, 27008), (27741, 27264), (28134, 27520)],
    [(26313, 24832), (26686, 26112), (27152, 26496), (27414, 27008), (27734, 27264), (28122, 27520)],
    [(26486, 24832), (26683, 26368), (27154, 26496), (27418, 27008), (27741, 27264), (28136, 27520)],
    [(26308, 24832), (26679, 26112), (27145, 26496), (27407, 27008), (27724, 0), (28118, 27520)]]
# A prefix broken after the instructions (a timestamp, a reordered tool list): only the first 3,072 tokens match.
BROKEN = [[(input_tokens, 3072) for input_tokens, _ in chat] for chat in RECORDED_SOL_DEFAULT]


def receipt(turns, *, route=SOL, keyed=True, task_set="default", gaps=None, requests=None, approval="default"):
    """One trial's eval-live-agent.json with one chat of the given (input, cached) turns."""
    tasks = []
    for index, (input_tokens, cached) in enumerate(turns):
        gap = (gaps or {}).get(index, "new_session" if index == 0 else "under_5m")
        usage = {"input_tokens": input_tokens, "cached_input_tokens": cached, "cache_write_input_tokens": 0,
                 "output_tokens": 50, "requests": (requests or {}).get(index, 1)}
        tasks.append({"task": "t%d" % index, "chat": "chat-1", "receipt": {
            "status": "completed", "usage": usage,
            "route": {"mode": route[0], "requested_model": route[1], "cache_gap": gap, "cache_affinity": keyed}}})
    return {"plan": {"approval": approval, "grading": {"task_set": task_set}, "returning_user": {}},
            "connection": {"mode": route[0]}, "grading": {"passed": len(turns), "total": len(turns), "failed": 0},
            "trials": [{"trial": 1, "tasks": tasks}]}


def write_step(folder, chats, **options):
    """A bench step folder: one angelo-trial-NN/eval-live-agent.json per chat."""
    for index, turns in enumerate(chats, 1):
        trial = Path(folder) / ("angelo-trial-%02d" % index)
        trial.mkdir(parents=True)
        (trial / cache_floor.RECEIPT).write_text(json.dumps(receipt(turns, **options)))
    return Path(folder)


class CacheFloorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.floors = cache_floor.load_floors()

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, chats, **options):
        step = write_step(self.folder / ("step-%d" % len(list(self.folder.iterdir()))), chats, **options)
        return cache_floor.check(cache_floor.measure([step]), self.floors)

    def test_the_recorded_passing_gate_is_clear_despite_a_cold_key_and_one_provider_miss(self):
        result = self.check(RECORDED_SOL_DEFAULT)
        self.assertFalse(result["sev"], cache_floor.describe(result))
        cell = result["cells"][0]
        self.assertEqual((cell["cell"], cell["full_misses"], cell["later_turns"]),
                         ("codex_subscription|gpt-6.1-sol|default", 1, 25))

    def test_a_broken_prefix_is_a_cache_sev_on_calls_2_and_on_the_keyed_first_call(self):
        result = self.check(BROKEN)
        self.assertTrue(result["sev"])
        self.assertEqual(sorted(b["metric"] for b in result["breaches"]), ["calls 2+", "first call"])
        self.assertTrue(cache_floor.describe(result)[0].startswith("CACHE SEV:"))

    def test_a_key_that_changes_per_process_fails_on_the_first_call_alone(self):
        chats = [[(26400, 0)] + chat[1:] for chat in RECORDED_SOL_DEFAULT[1:4]] + [RECORDED_SOL_DEFAULT[1]]
        result = self.check(chats)
        self.assertEqual([b["metric"] for b in result["breaches"]], ["first call"])

    def test_one_chat_with_one_provider_miss_is_trimmed_and_two_are_not(self):
        chat = RECORDED_SOL_DEFAULT[1]
        self.assertFalse(self.check([chat[:3] + [(chat[3][0], 0)] + chat[4:]])["sev"])
        self.assertTrue(self.check([chat[:2] + [(chat[2][0], 0), (chat[3][0], 0)] + chat[4:]])["sev"])

    def test_turns_after_a_long_gap_and_unmeasured_turns_are_left_out_never_failed(self):
        chat = RECORDED_SOL_DEFAULT[1]
        result = self.check([chat[:2] + [(chat[2][0], 0), (chat[3][0], 0)] + chat[4:]],
                            gaps={2: "5m_to_1h", 3: "over_1h"})
        self.assertFalse(result["sev"])
        found = cache_floor.measure([self.folder / "step-0"])
        self.assertEqual(found[0]["left_out"], {"long_gap": 2})

    def test_a_first_turn_with_several_requests_has_no_first_call_and_is_left_out(self):
        found = cache_floor.measure([write_step(self.folder / "s", [RECORDED_SOL_DEFAULT[1]], requests={0: 3})])
        self.assertIsNone(found[0]["first"])
        self.assertEqual(found[0]["left_out"], {"first_turn_several_requests": 1})

    def test_claude_first_calls_are_shown_never_gated_and_an_unfloored_cell_is_listed_not_failed(self):
        cold = [[(38452, 0), (39187, 38450), (39806, 39185), (40371, 39804)] for _ in range(4)]
        self.assertFalse(self.check(cold, route=OPUS, keyed=False)["sev"])
        result = self.check(BROKEN, route=("codex_api_key", "gpt-6.1-sol"))
        self.assertEqual((result["sev"], result["unfloored"]), (False, ["codex_api_key|gpt-6.1-sol|default"]))

    def test_the_variant_names_the_task_set_approval_and_returning_user_runs(self):
        report = receipt([(1, 1)], task_set="long", approval="ask")
        self.assertEqual(cache_floor.variant(report), "long/approval-ask")
        report["plan"]["returning_user"] = {"new_chat_per_task": True}
        self.assertEqual(cache_floor.variant(report), "long/approval-ask/new-chat-per-task")

    def test_the_checked_in_floors_follow_the_stated_rule_and_name_no_paths(self):
        self.assertEqual((self.floors["rule"], self.floors["margin"]), (cache_floor.RULE, cache_floor.MARGIN))
        self.assertTrue(self.floors["cells"] and self.floors["sources"])
        for cell, floor in self.floors["cells"].items():
            self.assertEqual(floor["later_floor"], cache_floor._floor([floor["later_min"]]), cell)
            if "first_floor" in floor:
                self.assertTrue(cell.startswith("codex_"), "first-call floors only on cache-keyed routes")
                self.assertEqual(floor["first_floor"], cache_floor._floor([floor["first_min"]]), cell)
        text = cache_floor.FLOORS_PATH.read_text()
        self.assertNotIn("/Users/", text)
        self.assertNotIn("\\\\", text)

    def test_floors_come_only_from_passing_gate_steps(self):
        campaign = self.folder / "campaign"
        steps = [{"id": "gate-sol-default", "phase": "gate"}, {"id": "h2h-sol-default", "phase": "h2h"}]
        write_step(campaign / "gate-sol-default", RECORDED_SOL_DEFAULT)
        bench = {"summary": {"tasks": {"t0": {"angelo": {"runs": 5, "skipped": 0, "graded": {
            "passed": 5, "failed": 0, "inconclusive": 0, "ungraded": 0, "total": 5}}}}}}
        (campaign / "gate-sol-default" / "bench-mcp-vs-angelo.json").write_text(json.dumps(bench))
        (campaign / "CONFIG.json").write_text(json.dumps({"platform": "macos", "checkout": "0123456789abcdef"}))
        (campaign / "STATE.json").write_text(json.dumps({"steps": {"gate-sol-default": {"status": "done"}}}))
        path = self.folder / "floors.json"
        with mock.patch("final_runs_plan.campaign", return_value=steps):
            floors = cache_floor.regenerate([campaign], path)
            self.assertEqual(floors["sources"][0]["steps"], ["gate-sol-default"])
            self.assertEqual(floors["cells"]["codex_subscription|gpt-6.1-sol|default"]["later_floor"], 0.93)
            bench["summary"]["tasks"]["t0"]["angelo"]["graded"]["failed"] = 1
            (campaign / "gate-sol-default" / "bench-mcp-vs-angelo.json").write_text(json.dumps(bench))
            with self.assertRaisesRegex(SystemExit, "only from passing gates"):
                cache_floor.regenerate([campaign], path)

    def test_a_cache_sev_keeps_the_gate_from_clearing_and_fails_the_campaign_run(self):
        gate = {"id": "gate-sol-default", "phase": "gate", "tool": "bench", "account": "chatgpt", "trials": 5,
                "sides": ["angelo"]}
        state = {"steps": {gate["id"]: {"status": "done"}}, "blocked": {}}
        write_step(self.folder / gate["id"], BROKEN)
        with mock.patch.object(runs, "gate_passes", return_value=(30, 30)):
            self.assertFalse(runs.gates_clear(self.folder, [gate], state))
            (self.folder / runs.STATE).write_text(json.dumps(state))
            with mock.patch.object(runs.quiet, "load", return_value=[1, 1, 1]), \
                    mock.patch.object(runs, "completed", side_effect=lambda step, folder: runs.planned(step)):
                self.assertEqual(runs.run({}, self.folder, [gate]), 1)
        events = [json.loads(line)["event"] for line in (self.folder / runs.JOURNAL).read_text().splitlines()]
        self.assertIn("cache_sev", events)


if __name__ == "__main__":
    unittest.main()
