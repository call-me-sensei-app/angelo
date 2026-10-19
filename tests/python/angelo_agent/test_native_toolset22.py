# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Toolset 22: the two blockers of the toolset-21 zero-failure gate.

* ``sonnet-long-t21`` trials 2 and 3, move_01: Sonnet moved the cube with
  ``angelo_set_transform`` and, as the plan contract says targets are handles,
  passed ``"object": "o4"``. The tool looked up an object *named* "o4", the
  batch was refused at dispatch and the request failed.
* ``opus-long-t21-rebase40k`` bevel_23, bevel_28 (trial 1) and bevel_38
  (trial 2): the first turn after a context rebase could not see the cube's
  "Bevel" modifier and applied ``bevel-and-shade``, which stacked a second
  bevel (two wrong results); the third attempt then exceeded the modifier
  budget and failed.

Every test here fails on ``ddaa28cad55`` (toolset 21). No Blender, Claude
process or inference: identifier expansion, the turn context's entry builder
with fake objects, the recipe guard, the typed-plan loop with mocked rounds
and the real Controller with a fake executor.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_runtime_fixtures import AgentRuntimeFixture  # noqa: E402
from claude_protocol_fixtures import host  # noqa: E402
from angelo_agent import action_recipes, turn_context  # noqa: E402
from angelo_agent.refusal_codes import RefusalError
from angelo_agent.claude_outcomes import correctable_failure  # noqa: E402
from angelo_agent.identifiers import IdentifierTable, expand_call  # noqa: E402
from angelo_agent.providers import ClaudeProvider  # noqa: E402
from angelo_agent.routing import TOOLSET_VERSION  # noqa: E402

HANDLE = "scn:" + "c" * 32 + ":159"
OTHER = "scn:" + "c" * 32 + ":136"
REFUSED = {"error_code": "VALIDATION", "error": "The bound scene has no editable object with that name or handle", "scene_changed": False}


def table_showing(*handles):
    table = IdentifierTable()
    for handle in handles:
        table.short_handle(handle)
    return table


class SetTransformHandleTests(unittest.TestCase):
    def test_a_known_short_handle_becomes_the_long_handle(self):
        table = table_showing(OTHER, HANDLE)  # o1, o2
        moved = expand_call("angelo_set_transform", {"object": "o2", "location": [-2, -1, 0]}, table)
        self.assertEqual(moved["object"], HANDLE)
        batch = expand_call("angelo_apply_batch", {"operations": [
            {"tool": "angelo_create_cube", "arguments": {"name": "o2", "size": 1, "location": [0, 0, 0]}},
            {"tool": "angelo_set_transform", "arguments": {"object": "o2", "location": [1, 0, 0]}}]}, table)
        self.assertEqual(batch["operations"][1]["arguments"]["object"], HANDLE)
        self.assertEqual(batch["operations"][0]["arguments"]["name"], "o2", "A created object's name is never a handle")

    def test_names_and_unknown_short_forms_stay_names(self):
        table = table_showing(HANDLE)
        for value in ("Eval Cube", "o7", HANDLE):
            with self.subTest(value=value):
                self.assertEqual(expand_call("angelo_set_transform", {"object": value, "location": [0, 0, 0]},
                                             table)["object"], value)
        self.assertEqual(expand_call("angelo_set_transform", {"object": "o1", "location": [0, 0, 0]}, None)["object"],
                         "o1", "Codex threads have no table and send names")


class ObservedModifierTests(unittest.TestCase):
    def shaped(self, modifiers):
        obj = SimpleNamespace(dimensions=[2, 2, 2], scale=[1, 1, 1], rotation_mode="XYZ", rotation_euler=[0, 0, 0],
                              modifiers=[SimpleNamespace(name=name, type=kind) for name, kind in modifiers])
        entry = {}
        turn_context._shape(obj, entry)
        return entry

    def test_entries_list_their_modifiers_in_stack_order(self):
        entry = self.shaped([("Bevel", "BEVEL"), ("Angelo Recipe Shade", "NODES")])
        self.assertEqual(list(entry["modifiers"].items()), [("Bevel", "BEVEL"), ("Angelo Recipe Shade", "NODES")])
        self.assertNotIn("modifiers", self.shaped([]), "No modifiers, no field")
        many = self.shaped([("M%02d" % index, "BEVEL") for index in range(turn_context.MAX_ENTRY_MODIFIERS + 3)])
        self.assertEqual((len(many["modifiers"]), many["modifiers_omitted"]), (turn_context.MAX_ENTRY_MODIFIERS, 3))


class RecipeGuardTests(unittest.TestCase):
    def test_bevel_and_shade_refuses_an_object_that_has_a_bevel_and_names_it(self):
        cube = SimpleNamespace(modifiers=[SimpleNamespace(name="Bevel", type="BEVEL"),
                                          SimpleNamespace(name="Shade", type="NODES")])
        with self.assertRaisesRegex(ValueError, r"already has a Bevel modifier \('Bevel'\).*modifier_configure of 'Bevel'"):
            action_recipes._refuse_second_bevel(cube)
        action_recipes._refuse_second_bevel(SimpleNamespace(modifiers=[SimpleNamespace(name="Shade", type="NODES")]))
        self.assertIn("Refuses an object that already has a Bevel", action_recipes.recipe_capabilities()
                      and action_recipes._DESCRIPTIONS["bevel-and-shade"])


class DispatchRefusalTests(AgentRuntimeFixture, unittest.TestCase):
    def test_a_refusal_before_approval_says_nothing_changed(self):
        self.executor.on_precondition = Mock(side_effect=RefusalError(REFUSED["error"], code="VALIDATION"))
        answers, _ = self.enqueue()
        self.pump()
        self.assertEqual(answers, [dict(REFUSED, native_dispatch_wait_ms=answers[0]["native_dispatch_wait_ms"])])


class CorrectedPlanTests(unittest.TestCase):
    def provider(self, plans, results):
        context = host()
        context.request_observation = Mock(side_effect=lambda callback: callback({"context_version": 2, "objects": []}))
        provider = ClaudeProvider(context, {})
        provider._turn = Mock(side_effect=[dict(plan, verify=False) for plan in plans])
        provider._native_call = Mock(side_effect=results)
        return provider

    def test_a_refused_batch_gets_one_corrected_plan(self):
        move = lambda value: {"tool": "angelo_set_transform", "arguments": {"object": value, "location": [-2, -1, 0]}}
        provider = self.provider([{"reply": "", "operations": [move("o9")]}, {"reply": "", "operations": [move("Eval Cube")]}],
                                 [dict(REFUSED), {"success": True, "operations": [{"object": "Eval Cube"}],
                                                  "verified": {"object_count": 4, "locations_read_back": True}}])
        self.assertEqual(provider.run(None, "Move 'Eval Cube' to (-2, -1, 0)", observation={}), "completed")
        self.assertEqual(provider._turn.call_count, 2)
        self.assertIn("no editable object with that name or handle", provider._turn.call_args.args[1])

    def test_only_an_explicit_no_change_batch_refusal_comes_back(self):
        batch = [{"tool": "angelo_apply_batch", "arguments": {}}]
        self.assertEqual(correctable_failure(batch, [dict(REFUSED)]), REFUSED["error"])
        self.assertIsNone(correctable_failure(batch, [{"error": REFUSED["error"]}]))
        self.assertIsNone(correctable_failure(batch, [dict(REFUSED, scene_changed=None, partial_changes_possible=True)]))


class ToolsetTests(unittest.TestCase):
    def test_toolset(self):
        self.assertGreaterEqual(TOOLSET_VERSION, 22)  # Native coverage (23) builds on 22.


if __name__ == "__main__":
    unittest.main()
