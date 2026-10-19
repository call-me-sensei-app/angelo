# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How many bytes a model writes for the bridge scenario's typed turns: an upper bound per turn, both runtimes.

``scenario_bridge_plans.json`` holds the nine turns of the recorded bridge
session (GPT-6 Astra) as the same edits written in the current
plan contract: no restated checks, no defaults, no target where there is
nothing to choose. ``scenario_plan_replay.py`` runs it through the real host
on a real binary (46 of 46 closed checks); this suite keeps what it costs to
write from growing. As recorded on toolset 35 the seven typed turns were
31,924 bytes of plan text, 5,128 of them plans that failed and were written
again; the bounds below are what the contract needs now, with a few bytes of
headroom. A change that makes a plan longer to write fails here before it
costs a model its output tokens.

Pure Python: plans are validated by the host's own resolver and validator
over a scripted observation; nothing is staged.
"""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import codex_plan, context_ledger, derived_checks  # noqa: E402
from angelo_agent.identifiers import IdentifierTable, expand_call  # noqa: E402
from angelo_agent.program_spec import MAX_OPERATIONS  # noqa: E402
from angelo_agent.strict_schema import decode, encode  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402

PLANS = json.loads((Path(__file__).resolve().parent / "scenario_bridge_plans.json").read_text(encoding="utf-8"))["turns"]
EPOCH = "c" * 32
# Bytes of the plan a model writes per typed turn: (a Codex thread's strict final message, a Claude plan call).
BOUNDS = {"railings": (2750, 2730), "lamps": (3320, 3075), "water_rocks": (6500, 5900), "golden_hour": (540, 485),
          "deck_camera": (540, 480), "car_animation": (4380, 4100), "render_setup": (565, 525)}
TOTAL = (18600, 17300)
RECORDED_TOOLSET_35 = 31924


def size(value):
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())


class Scene:
    """The objects a turn's plans name, as the turn context would show them: short handles and revisions."""

    def __init__(self):
        self.table, self.names = IdentifierTable(), {}
        self.observation = {"scene": "Scene", "scene_settings": {"handle": "scn:%s:1" % EPOCH, "revision": "5" * 64},
                            "object_count": 0, "objects": []}

    def handle(self, name):
        if name not in self.names:
            self.names[name] = "scn:%s:%d" % (EPOCH, 100 + len(self.names))
            self.observation["objects"].append({"name": name, "type": "MESH", "handle": self.names[name],
                                                "location": [0., 0., 0.], "object_revision": "%064x" % len(self.names)})
        return self.names[name]

    def written(self, value):
        """A fixture value as the model writes it: the short handle of each object it names."""
        if isinstance(value, dict):
            return {key: self.written(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.written(item) for item in value]
        if isinstance(value, str) and value.startswith("@name:"):
            self.handle(value[6:])
            shown = context_ledger.compact_observation(copy.deepcopy(self.observation), self.table)
            return next(row["handle"] for row in shown["objects"] if row["name"] == value[6:])
        return value


def plans_of(turn):
    scene = Scene()
    plans = [scene.written(step["plan"]) for step in PLANS[turn] if "plan" in step and step["plan"]["operations"]]
    context_ledger.compact_observation(copy.deepcopy(scene.observation), scene.table)
    return scene, plans


def strict_plan(plan, ordinary):
    """The plan in a Codex thread's strict format: a kind it does not spell out has its target written (null)."""
    spelled = copy.deepcopy(plan)
    for item in spelled["operations"]:
        for index, operation in enumerate(item["arguments"].get("operations", ())):
            try:
                encode({"reply": "", "operations": [{"tool": item["tool"], "arguments": dict(
                    item["arguments"], operations=[operation])}]}, ordinary)
            except ValueError:
                item["arguments"]["operations"][index] = dict(operation, target=operation.get("target"))
    return encode(dict(spelled, reply=spelled.get("reply", "")), ordinary)


class BridgePlanBytesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ordinary = codex_plan.ordinary_schema()

    def measured(self, turn):
        scene, plans = plans_of(turn)
        codex = sum(size(strict_plan(plan, self.ordinary)) for plan in plans)
        claude = sum(size(dict(plan, verify=False, reply=plan.get("reply", ""))) for plan in plans)
        return scene, plans, codex, claude

    def test_every_typed_turn_is_a_plan_the_host_accepts(self):
        self.assertEqual(set(BOUNDS), {turn for turn in PLANS if plans_of(turn)[1]})
        for turn in BOUNDS:
            scene, plans = plans_of(turn)
            for plan in plans:
                for item in plan["operations"]:
                    with self.subTest(turn=turn, tool=item["tool"]):
                        program = validate_call(item["tool"], expand_call(item["tool"], item["arguments"], scene.table))
                        self.assertLessEqual(len(program["operations"]), MAX_OPERATIONS)
                        self.assertEqual(decode(strict_plan(plan, self.ordinary), self.ordinary)["operations"][0]["tool"],
                                         plan["operations"][0]["tool"])

    def test_no_plan_restates_a_check_the_host_derives(self):
        for turn in BOUNDS:
            scene, plans = plans_of(turn)
            for plan in plans:
                for item in plan["operations"]:
                    arguments = expand_call(item["tool"], item["arguments"], scene.table)
                    program = validate_call(item["tool"], arguments)
                    derived = {derived_checks.identity(check) for check in derived_checks.derive_checks(program["operations"])}
                    declared = validate_call(item["tool"], dict(arguments, operations=arguments["operations"]))["expected_checks"][
                        :len(item["arguments"].get("expected_checks", ()))]
                    with self.subTest(turn=turn):
                        self.assertFalse([check for check in declared if derived_checks.identity(check) in derived])

    def test_bytes_written_per_turn_stay_within_their_bounds(self):
        totals = [0, 0]
        for turn, (codex_bound, claude_bound) in BOUNDS.items():
            _, _, codex, claude = self.measured(turn)
            with self.subTest(turn=turn):
                self.assertLessEqual(codex, codex_bound, "A Codex thread's strict final message")
                self.assertLessEqual(claude, claude_bound, "A Claude plan call")
                # A bound far above the measurement would no longer guard anything.
                self.assertGreater(codex, codex_bound * .94)
                self.assertGreater(claude, claude_bound * .94)
            totals[0] += codex
            totals[1] += claude
        self.assertLessEqual(totals[0], TOTAL[0])
        self.assertLessEqual(totals[1], TOTAL[1])
        self.assertLess(totals[0], RECORDED_TOOLSET_35 * .6, "Under three fifths of what the recorded session wrote")


if __name__ == "__main__":
    unittest.main()
