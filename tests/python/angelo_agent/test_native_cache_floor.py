# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The cache-floor check: each session is judged over all its calls; a recorded passing gate and a single provider cold
miss stay clear, a prefix or key break that hits every call is a cache SEV, two first-call misses warn. Nothing is sent."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

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
ASTRA = ("codex_subscription", "gpt-6-astra")
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
# cut-15's Astra long trial 03 (fafa88b4502, 2026-11-04), every turn: the provider served its first call cold under
# the same key and prefix as trials 01 and 02, then every later call from the cache (0.956 over 40 calls).
ASTRA_LONG_COLD_FIRST = [
    (25220, 0), (25613, 25088), (26068, 25472), (26440, 25984), (26761, 24448), (27127, 26752), (27656, 26368),
    (27874, 27008), (28259, 27776), (28607, 28160), (28977, 28544), (29516, 27520), (29746, 28928), (30130, 29568),
    (30467, 29440), (30831, 30080), (31369, 30720), (31586, 31232), (31966, 31488), (32302, 31872), (32665, 32256),
    (33197, 32640), (33421, 33152), (33803, 33280), (34146, 33664), (34513, 34048), (35049, 30464), (35272, 34432),
    (35658, 34944), (36001, 35200), (36368, 35584), (36905, 35968), (37130, 36864), (37512, 36992), (37845, 37376),
    (38207, 37760), (38736, 38144), (38954, 38656), (39336, 36352), (39683, 39296)]
ASTRA_LONG_WARM = [(25414, 24448)] + ASTRA_LONG_COLD_FIRST[1:]   # Trial 01's first call (24,448 read) before them.
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


def write_step(folder, chats, platform="macos", **options):
    """A bench step folder of a campaign on ``platform`` (its CONFIG.json): one angelo-trial-NN/eval-live-agent.json
    per chat."""
    config = Path(folder).parent / "CONFIG.json"
    if not config.exists():
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({"platform": platform}))
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
        self.assertEqual((cell["cell"], cell["chats"], cell["full_misses"]), ("macos|codex_subscription|gpt-6.1-sol|default", 5, 1))

    def test_a_single_provider_cold_miss_passes_and_does_not_warn(self):
        result = self.check([ASTRA_LONG_WARM, ASTRA_LONG_WARM, ASTRA_LONG_COLD_FIRST], route=ASTRA, task_set="long")
        self.assertFalse(result["sev"], cache_floor.describe(result))
        self.assertFalse(result["warning"])
        self.assertEqual([m["trial"] for m in result["first_misses"]], ["angelo-trial-03"])
        found = cache_floor.measure([self.folder / "step-0"])
        self.assertEqual([round(chat["session"], 3) for chat in found], [0.975, 0.975, 0.956])

    def test_a_prefix_or_key_break_that_hits_every_call_fails_on_every_route(self):
        for route, keyed in ((SOL, True), (ASTRA, True), (OPUS, False)):
            with self.subTest(route=route):
                result = self.check(BROKEN, route=route, keyed=keyed)
                self.assertTrue(result["sev"], cache_floor.describe(result))
                self.assertEqual(len(result["breaches"]), len(BROKEN))
                self.assertTrue(cache_floor.describe(result)[0].startswith("CACHE SEV:"))

    def test_two_first_call_misses_in_one_run_warn_and_never_fail(self):
        warm = RECORDED_SOL_DEFAULT[1]
        cold = [(warm[0][0], 0)] + warm[1:]
        result = self.check([warm, cold, cold])
        self.assertFalse(result["sev"], cache_floor.describe(result))
        self.assertTrue(result["warning"])
        self.assertTrue(any(line.startswith("CACHE WARNING: 2 first calls") for line in cache_floor.describe(result)))

    def test_the_first_chat_of_a_cell_in_each_step_is_never_a_first_call_miss(self):
        warm = RECORDED_SOL_DEFAULT[1]
        cold = [(warm[0][0], 0)] + warm[1:]
        self.assertFalse(self.check([cold, cold, warm])["warning"])   # One counted miss: the first chat warms.
        self.assertEqual(cache_floor.measure([self.folder / "step-0"])[0]["first_counted"], False)

    def test_a_steps_first_chat_is_judged_without_its_first_turn(self):
        """It may warm a key the provider had expired (maintainer, "Skip first turn"); every later chat counts all calls.
        A smoke step's one chat always is that first chat."""
        warm = RECORDED_SOL_DEFAULT[1]
        cold = [(warm[0][0], 0), (warm[1][0], 0)] + warm[2:]   # Its first turn and one more read nothing.
        found = cache_floor.measure([write_step(self.folder / "s", [cold, cold])])
        first, second = found
        self.assertEqual((first["first_counted"], second["first_counted"]), (False, True))
        self.assertAlmostEqual(first["judged"], first["after_first_turn"])
        self.assertAlmostEqual(second["judged"], second["session"])
        self.assertLess(second["judged"], first["judged"])
        one_turn = cache_floor.measure([write_step(self.folder / "t", [[(26400, 0)]])])
        self.assertIsNone(one_turn[0]["judged"], "no later turn: unknown, never a SEV")

    def test_floors_and_cells_are_per_platform(self):
        mac = cache_floor.measure([write_step(self.folder / "mac" / "step", [RECORDED_SOL_DEFAULT[1]], platform="macos")])
        win = cache_floor.measure([write_step(self.folder / "win" / "step", [RECORDED_SOL_DEFAULT[1]], platform="windows")])
        self.assertEqual((mac[0]["cell"], win[0]["cell"]), ("macos|codex_subscription|gpt-6.1-sol|default",
                                                            "windows|codex_subscription|gpt-6.1-sol|default"))
        self.assertEqual(cache_floor.platform(Path(self.tmp.name) / "no-campaign" / "step"), "unknown",
                         "no CONFIG.json above it: an unknown platform, whose cells have no floor")
        self.assertEqual({cell.split("|")[0] for cell in self.floors["cells"]}, {"macos", "windows"})

    def test_a_first_turn_with_several_requests_has_no_first_call_but_counts_in_the_session(self):
        found = cache_floor.measure([write_step(self.folder / "s", [RECORDED_SOL_DEFAULT[1]], requests={0: 3})])
        self.assertIsNone(found[0]["first"])
        self.assertEqual(found[0]["calls"], 8)
        self.assertAlmostEqual(found[0]["session"], sum(c for _, c in RECORDED_SOL_DEFAULT[1]) /
                               sum(i for i, _ in RECORDED_SOL_DEFAULT[1]))

    def test_unmeasured_turns_are_left_out_and_counted_never_failed(self):
        step = write_step(self.folder / "s", [RECORDED_SOL_DEFAULT[1]])
        path = step / "angelo-trial-01" / cache_floor.RECEIPT
        report = json.loads(path.read_text())
        del report["trials"][0]["tasks"][2]["receipt"]["usage"]
        path.write_text(json.dumps(report))
        found = cache_floor.measure([step])
        self.assertEqual((found[0]["left_out"], found[0]["calls"]), ({"unmeasured": 1}, 5))

    def test_an_unfloored_cell_is_listed_not_failed(self):
        result = self.check(BROKEN, route=("codex_api_key", "gpt-6.1-sol"))
        self.assertEqual((result["sev"], result["unfloored"]), (False, ["macos|codex_api_key|gpt-6.1-sol|default"]))

    def test_the_variant_names_the_task_set_approval_and_returning_user_runs(self):
        report = receipt([(1, 1)], task_set="long", approval="ask")
        self.assertEqual(cache_floor.variant(report), "long/approval-ask")
        report["plan"]["returning_user"] = {"new_chat_per_task": True}
        self.assertEqual(cache_floor.variant(report), "long/approval-ask/new-chat-per-task")

    def test_the_checked_in_floors_follow_the_stated_rule_and_name_no_paths(self):
        self.assertEqual((self.floors["rule"], self.floors["margin"]), (cache_floor.RULE, cache_floor.MARGIN))
        self.assertTrue(self.floors["cells"] and self.floors["sources"])
        for cell, floor in self.floors["cells"].items():
            self.assertEqual(set(floor), {"session_floor", "session_chats", "session_min", "session_median"}, cell)
            self.assertEqual(floor["session_floor"], cache_floor._floor([floor["session_min"]]), cell)
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
            cell = floors["cells"]["macos|codex_subscription|gpt-6.1-sol|default"]
            self.assertEqual((cell["session_min"], cell["session_floor"], cell["session_chats"]), (0.8077, 0.75, 5))
            bench["summary"]["tasks"]["t0"]["angelo"]["graded"]["failed"] = 1
            (campaign / "gate-sol-default" / "bench-mcp-vs-angelo.json").write_text(json.dumps(bench))
            with self.assertRaisesRegex(SystemExit, "only from passing gates"):
                cache_floor.regenerate([campaign], path)

    def test_a_cache_sev_keeps_the_gate_from_clearing_and_fails_the_campaign_run(self):
        gate = {"id": "gate-sol-default", "phase": "gate", "tool": "bench", "account": "chatgpt", "trials": 5,
                "sides": ["angelo"]}
        state = {"steps": {gate["id"]: {"status": "done"}}, "blocked": {}}
        write_step(self.folder / gate["id"], BROKEN)
        self.assertEqual(self.run_campaign(gate, state), 1)
        self.assertIn("cache_sev", self.events())

    def test_a_cache_warning_is_journaled_and_the_campaign_run_still_finishes(self):
        gate = {"id": "gate-sol-default", "phase": "gate", "tool": "bench", "account": "chatgpt", "trials": 5,
                "sides": ["angelo"]}
        state = {"steps": {gate["id"]: {"status": "done"}}, "blocked": {}}
        warm = RECORDED_SOL_DEFAULT[1]
        write_step(self.folder / gate["id"], [warm, [(warm[0][0], 0)] + warm[1:], [(warm[0][0], 0)] + warm[1:]])
        self.assertEqual(self.run_campaign(gate, state), 0)
        self.assertEqual([e for e in self.events() if e.startswith("cache_")], ["cache_warning"])
        self.assertIn("finished", self.events())

    def run_campaign(self, gate, state):
        with mock.patch.object(runs, "gate_passes", return_value=(30, 30)):
            clear = runs.gates_clear(self.folder, [gate], state)
            (self.folder / runs.STATE).write_text(json.dumps(state))
            with mock.patch.object(runs.quiet, "load", return_value=[1, 1, 1]), \
                    mock.patch.object(runs, "completed", side_effect=lambda step, folder: runs.planned(step)):
                code = runs.run({}, self.folder, [gate])
        self.assertEqual(clear, code == 0)
        return code

    def events(self):
        return [json.loads(line)["event"] for line in (self.folder / runs.JOURNAL).read_text().splitlines()]


if __name__ == "__main__":
    unittest.main()
