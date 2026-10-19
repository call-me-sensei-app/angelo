# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whole turns with the everyday kinds on both runtimes, played by scripted stand-ins for the model.

A Codex chat turn ends with one strict final message: here a scripted
app-server writes it (``test_native_codex_plan.ScriptedAppServer``) for a real
``Controller`` and ``CodexProvider``, so the strict form of ``batch`` (a
deferred kind, written as JSON text), ``also`` on a material kind, a sky and
Cycles settings is decoded, validated exactly and dispatched as one program,
and the turn ends with the host's verified summary. A Claude plan is the
ordinary form of the same document: its validation (every kind exact, batch
included, so no lookup) and what a rejected plan is sent back with are checked
on the same plans. Results are stand-ins shaped like the backend's; the real stage,
commit, Undo and the stand-in ``claude`` CLI playing these plans in the
compiled app are in test_blender_everyday_turns.py. No provider or paid call.
"""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent.program_checks import required_checks
from angelo_agent.claude_outcomes import certified_completion, compact_receipt, terse_receipt  # noqa: E402
from angelo_agent.codex_plan import PLAN_TOOLS  # noqa: E402
from angelo_agent.plan_schema import CLAUDE_CORE_ACTIONS, PLAN_CORE_ACTIONS, deferred_references  # noqa: E402
from angelo_agent.providers import _plan_problems  # noqa: E402
from angelo_agent.tool_specs import TOOL_MAP, validate_call  # noqa: E402
from test_native_codex_plan import PlanTurnFixture, resolver  # noqa: E402
from test_native_round_savings import committed, passed  # noqa: E402

SCENE = "scn:" + "a" * 32 + ":1"
REVISION = "b" * 64


def op(kind, target=None, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def cube(name, alias, location, scale):
    return op("create_primitive", primitive="CUBE", name=name, alias=alias, location=location, scale=scale)


def program(*operations, **sets):
    return {"tool": "angelo_scene_program", "arguments": dict({"mode": "APPLY", "operations": list(operations)}, **sets)}


# "Add a stone footbridge at the origin: a deck 4 m long and 1.2 m wide, four square posts and two rails, in a
# grey stone material": six operations for seven objects.
FOOTBRIDGE = program(
    cube("Footbridge Deck", "deck", [0, 0, 0.075], [2, 0.6, 0.075]),
    cube("Footbridge Post 1", "post", [-1.85, -0.5, 0.65], [0.06, 0.06, 0.5]),
    cube("Footbridge Rail 1", "rail", [0, -0.5, 1.19], [2, 0.04, 0.04]),
    op("material_principled", "$deck", name="Grey Stone", inputs={"Base Color": [0.4, 0.4, 0.39, 1], "Roughness": 0.85},
       also=["$post", "$rail"]),
    op("batch", sources=["$post"], array=[{"step": [3.7, 0, 0], "count": 2}, {"step": [0, 1, 0], "count": 2}]),
    op("batch", sources=["$rail"], mirror="Y"))
# "Light the cube with a key, a fill and a rim area light, set the world to a physical sky with the sun 5 degrees
# up, and switch to Cycles at 128 samples with denoising": one program.
LIGHTING = program(
    *[op("light_create", name=name, location=location, look_at=[0, 0, 0],
         settings={"light_type": "AREA", "energy": energy, "size": size})
      for name, location, energy, size in (("Key Light", [4, -4, 5], 1000, 2), ("Fill Light", [-2, -6, 2.5], 250, 3),
                                           ("Rim Light", [-5, 5, 3.5], 600, 1.5))],
    op("world_configure", SCENE, sky={"sun_elevation": 0.0873}),
    op("property_set", SCENE, values={"render.engine": "CYCLES", "cycles.samples": 128, "cycles.use_denoising": True}),
    write_set=[{"handle": SCENE, "revision": REVISION}])


def certified(call):
    """A committed result whose checks are exactly the ones the host derives for ``call``, all passed."""
    canonical = validate_call(call["tool"], copy.deepcopy(call["arguments"]))
    return committed(checks=[passed(check["kind"], check["target"], observed={"read": check["kind"]})
                             for check in required_checks(canonical)])


def read_only(name):
    return TOOL_MAP[name]["annotations"]["readOnlyHint"]


class CodexTurnTests(PlanTurnFixture):
    def strict(self, call):
        return json.loads(self.plan_text(call))["operations"][0]["arguments"]["operations"]

    def test_mirror_set_and_clear_reach_the_host_as_exact_obligations(self):
        call = program(
            op("create_primitive", primitive="CUBE", alias="owner"),
            op("create_object", type="EMPTY", alias="origin"),
            op("modifier_add", "$owner", type="MIRROR", name="Origin", settings={"mirror_object": "$origin"}),
            op("modifier_add", "$owner", type="MIRROR", name="Cleared", settings={"mirror_object": "$origin"}),
            op("modifier_configure", "$owner", type="MIRROR", modifier="Cleared", settings={"clear": True}))
        written = self.strict(call)
        self.assertEqual(written[-1]["parameters"]["settings"]["clear"], True)
        self.results["angelo_scene_program"] = [certified(call)]
        self.assertEqual(self.run_turns([("message", self.plan_text(call))]), "completed")
        ((_, tool, arguments),) = self.dispatched
        checks = required_checks(validate_call(tool, copy.deepcopy(arguments)))
        pointers = {row["name"]: row["values"]["mirror_object"] for row in checks if row["kind"] == "modifier_values"}
        self.assertEqual(pointers, {"Origin": {"object": "$origin"}, "Cleared": None})
        (summary,) = [row for row in self.c.state["messages"] if row.get("host_summary")]
        self.assertEqual(summary["text"].count("- modifier_values: "), 2)
        self.assertEqual(len(self.starts()), 1)

    def test_a_strict_plan_with_batch_and_also_is_one_verified_program(self):
        written = self.strict(FOOTBRIDGE)
        self.assertEqual(written[3]["parameters"]["also"], ["$post", "$rail"], "also is a field of an exact kind")
        self.assertIsInstance(written[4]["parameters"], str, "batch is a deferred kind: its parameters are JSON text")
        self.assertEqual(json.loads(written[4]["parameters"])["array"][1], {"step": [0, 1, 0], "count": 2})
        self.assertEqual(json.loads(written[5]["parameters"]), {"sources": ["$rail"], "mirror": "Y"},
                         "JSON text carries only what the model states")
        self.results["angelo_scene_program"] = [certified(FOOTBRIDGE)]
        self.assertEqual(self.run_turns([("message", self.plan_text(FOOTBRIDGE))]), "completed")
        ((_, tool, arguments),) = self.dispatched
        self.assertEqual(tool, "angelo_scene_program")
        self.assertEqual([operation["kind"] for operation in arguments["operations"]],
                         ["create_primitive"] * 3 + ["material_principled", "batch", "batch"])
        canonical = validate_call(tool, copy.deepcopy(arguments))
        kinds = [check["kind"] for check in required_checks(canonical)]
        self.assertEqual((kinds.count("copies"), kinds.count("material_assignment")), (2, 3))
        (summary,) = [row for row in self.c.state["messages"] if row.get("host_summary")]
        self.assertTrue(summary["text"].startswith("Host-verified result (no further model round was needed)"))
        self.assertEqual(summary["text"].count("- copies: "), 2, "One line per source the batches copied")
        self.assertEqual(len(self.starts()), 1, "One model round for seven objects")

    def test_one_program_carries_lights_a_sky_and_cycles(self):
        written = self.strict(LIGHTING)
        self.assertEqual(json.loads(written[3]["parameters"]), {"sky": {"sun_elevation": 0.0873}})
        self.assertEqual(json.loads(written[4]["parameters"])["values"]["cycles.use_denoising"], True)
        self.results["angelo_scene_program"] = [certified(LIGHTING)]
        self.assertEqual(self.run_turns([("message", self.plan_text(LIGHTING))]), "completed")
        ((_, tool, arguments),) = self.dispatched
        canonical = validate_call(tool, copy.deepcopy(arguments))
        kinds = [check["kind"] for check in required_checks(canonical)]
        self.assertIn("world_settings", kinds)
        self.assertIn("property", kinds)
        self.assertTrue([row for row in self.c.state["messages"] if row.get("host_summary")])

    def test_a_wrong_batch_comes_back_with_its_exact_schema(self):
        wrong = copy.deepcopy(FOOTBRIDGE)
        wrong["arguments"]["operations"][4]["parameters"]["array"][0]["count"] = 1  # A row of one makes no copy.
        self.results["angelo_scene_program"] = [certified(FOOTBRIDGE)]
        status = self.run_turns([("message", self.plan_text(wrong))], [("message", self.plan_text(FOOTBRIDGE))])
        self.assertEqual(status, "completed")
        fix = self.starts()[1]["input"][0]["text"]
        self.assertIn("The host rejected your last plan", fix)
        self.assertIn("operation.parameters.array does not match one closed schema", fix)
        self.assertIn('"batch"', fix, "the reference omits bounds; correction feedback states the exact schema")
        self.assertIn('"mirror"', fix)
        self.assertIn('"minimum":2', fix)
        self.assertIs(self.starts()[0]["outputSchema"], self.starts()[1]["outputSchema"])
        self.assertEqual(len(self.starts()), 2, "One correction round")
        self.assertEqual(len(self.dispatched), 1, "Nothing ran for the rejected plan")


class ClaudePlanTests(unittest.TestCase):
    """The ordinary form a Claude plan writes: validated by the same host rules before anything runs."""

    def test_the_plans_validate_and_need_no_lookup(self):
        for call in (FOOTBRIDGE, LIGHTING):
            problems, _ = _plan_problems([copy.deepcopy(call)], PLAN_TOOLS, resolver())
            self.assertEqual(problems, [])
        self.assertEqual(deferred_references([FOOTBRIDGE], CLAUDE_CORE_ACTIONS), {},
                         "batch is exact in Claude's plan (plan_kinds.CLAUDE_REPEAT_ACTIONS)")
        self.assertEqual(deferred_references([FOOTBRIDGE], PLAN_CORE_ACTIONS), {"ACTIONS": ["batch"]})
        self.assertEqual(deferred_references([LIGHTING], CLAUDE_CORE_ACTIONS), {},
                         "Lights, the world and settings are exact in Claude's plan")
        # Codex states only the core kinds exactly: the rest of the same plan is named and read on demand.
        self.assertEqual(deferred_references([LIGHTING], PLAN_CORE_ACTIONS),
                         {"ACTIONS": ["light_create", "property_set", "world_configure"]})

    def test_a_wrong_field_is_refused_before_anything_runs(self):
        wrong = copy.deepcopy(FOOTBRIDGE)
        wrong["arguments"]["operations"][3]["parameters"]["also"] = ["$deck"]  # The target itself.
        problems, _ = _plan_problems([wrong], PLAN_TOOLS, resolver())
        self.assertEqual(len(problems), 1)
        self.assertIn("also lists objects other than its target", problems[0])


class EvidenceTests(unittest.TestCase):
    """What a model reads after a certified program: the summary now, the receipt in the next turn's context."""

    def test_both_runtimes_get_the_verified_checks(self):
        result = certified(FOOTBRIDGE)
        summary = certified_completion([FOOTBRIDGE], [result], read_only=read_only)
        self.assertIn("Host checks passed on the live result (18 of 18; 18 implied by the operations)", summary)
        self.assertEqual(summary.count("- copies: "), 2)
        terse = terse_receipt("angelo_scene_program", result)  # A Claude context's handoff entry.
        self.assertIn("copies", terse["verified"])
        self.assertIn("material_assignment", terse["verified"])
        receipt = compact_receipt("angelo_scene_program", result)  # What a Codex thread's next turn reads.
        self.assertEqual(receipt["status"], "committed")
        self.assertTrue(receipt["live_revisions"]["objects"])


if __name__ == "__main__":
    unittest.main()
