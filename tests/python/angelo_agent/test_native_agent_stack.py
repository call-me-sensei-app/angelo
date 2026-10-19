# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Toolset 21: the fast-tier reliability fixes on toolset 20's semantics.

Each test fails on toolset 20 and passes on toolset 21
(doc/angelo/agent_stack_integration.rst). They pin two Haiku 5.5 wrong-result
triggers of the long task set (the observation delta listed no objects; a
lookup ended the turn before the edit it was for) and the
reply-less edit default: a later round with nothing to add never fails a turn
and never shows an empty chat row. Pure ledgers, then the real Controller and
ClaudeProvider over the unbilled stdio fixture: no Blender, Claude process or
inference.
"""

import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import context_ledger  # noqa: E402
from angelo_agent.identifiers import IdentifierError, IdentifierTable, expand_call  # noqa: E402
from angelo_agent.providers import EMPTY_PLAN, superseded_plans  # noqa: E402
from angelo_agent.routing import TOOLSET_VERSION  # noqa: E402
from angelo_agent.turn_context import TurnContext  # noqa: E402
from test_native_cache_diet import ControllerFixture  # noqa: E402
from test_native_round_savings import committed, passed  # noqa: E402
from test_native_turn_diet import HANDLE, NEWER, OTHER, REVISION, entry, observation, transform  # noqa: E402

DIMENSIONS = {"dimensions": [2.0, 2.0, 2.0]}
LOOKUP = {"tool": "angelo_scene_object", "arguments": {"object": "o1", "sections": ["TRANSFORM"], "limit": 10}}
MOVE = {"tool": "angelo_scene_program", "arguments": {
    "mode": "APPLY", "operations": [transform(location=[-1, -1, 0])]}}


def named(revision=REVISION, **fields):
    """The Eval Cube entry of a turn context whose message names it (turn_context.build)."""
    return entry(HANDLE, revision, mentioned=True, active=True, selected=True, **dict(DIMENSIONS, **fields))


def exact(handle, revision, **fields):
    """An angelo_scene_object/search row as scene_fingerprints writes it: an exact program revision.

    A Claude plan's lookups bind its next round (query_observations.observe_lookups); a row without
    these fields would read as inexact and revoke the object's binding.
    """
    return dict({"object": handle, "object_revision": revision, "revision_exact": True,
                 "program_revision_supported": True, "revision_limitations": []}, **fields)


def delta_of(text):
    header = context_ledger.OBSERVATION_DELTA_HEADER
    return json.loads(text[len(header):]) if text.startswith(header) else None


class NamedObjectDeltaTests(unittest.TestCase):
    """Trigger 1: "the delta listed no objects"."""

    def first_and_next(self, first, later, *, events_as_state=False):
        table = IdentifierTable()
        blocks = context_ledger.TurnBlocks(identifiers=table)
        blocks.observation(first)
        follow = context_ledger.TurnBlocks(dict(blocks.after), identifiers=table)
        if events_as_state:
            return follow.observation_value(later, events_as_state=True)
        return follow.observation(later)

    def test_an_unchanged_named_object_is_listed_whole(self):
        scene = observation(named(), entry(OTHER, NEWER, name="Lamp"))
        text = self.first_and_next(scene, copy.deepcopy(scene))
        self.assertNotEqual(text, context_ledger.OBSERVATION_DELTA_UNCHANGED, "Never 'unchanged' when it names one")
        sent = delta_of(text)
        self.assertEqual([item["name"] for item in sent["objects"]], ["Eval Cube"])
        self.assertEqual(sent["objects"][0]["dimensions"], [2.0, 2.0, 2.0])
        self.assertEqual(sent["objects"][0]["object_revision"], REVISION[:8])
        self.assertEqual(sent["unchanged_objects"], 1)

    def test_a_changed_named_object_is_whole_not_a_field_level_delta(self):
        scene = observation(named(), entry(OTHER, NEWER, name="Lamp"))
        moved = observation(named(NEWER, location=[-1.0, -1.0, 0.0]), entry(OTHER, NEWER, name="Lamp"))
        sent = delta_of(self.first_and_next(scene, moved))
        self.assertEqual(sent["objects"][0]["name"], "Eval Cube", "The name the person used is beside its handle")
        self.assertEqual(sent["objects"][0]["dimensions"], [2.0, 2.0, 2.0], "Unchanged values a question needs")
        self.assertEqual(sent["objects"][0]["location"], [-1.0, -1.0, 0.0])

    def test_other_changed_objects_keep_field_level_deltas(self):
        scene = observation(named(), entry(OTHER, NEWER, name="Lamp"))
        later = observation(named(), entry(OTHER, REVISION, name="Lamp", location=[0.0, 0.0, 3.0]))
        sent = delta_of(self.first_and_next(scene, later))
        lamp = next(item for item in sent["objects"] if item.get("name") != "Eval Cube")
        self.assertEqual(set(lamp), {"handle", "location", "object_revision"})

    def test_without_a_named_object_an_unchanged_observation_stays_one_line(self):
        scene = observation(entry(HANDLE, REVISION, active=True), entry(OTHER, NEWER, name="Lamp"))
        self.assertEqual(self.first_and_next(scene, copy.deepcopy(scene)), context_ledger.OBSERVATION_DELTA_UNCHANGED)

    def test_a_later_round_of_the_same_turn_does_not_repeat_it(self):
        scene = observation(named())
        self.assertEqual(self.first_and_next(scene, copy.deepcopy(scene), events_as_state=True), ("unchanged", None))


class NamedObjectControllerTests(ControllerFixture):
    def test_the_next_turn_lists_the_named_cube_although_nothing_changed(self):
        scene = observation(named(), entry(OTHER, NEWER, name="Lamp"))
        self.turn("What are the current dimensions of 'Eval Cube'?", observation=scene)
        prompt = self.turn("Move 'Eval Cube' to location (-1, -1, 0).", observation=copy.deepcopy(scene))
        self.assertNotIn("Observation: unchanged", prompt)
        self.assertIn('"name":"Eval Cube"', prompt.split("Observation delta", 1)[1])


class Superseded(ControllerFixture):
    """Claude Code returns only a turn's last StructuredOutput call; record each one as the stream does."""

    def reply(self, process, event):
        content = event["message"]["content"]
        self.prompts.append(content if isinstance(content, str) else content[0]["text"])
        plan = self.plans.pop(0) if self.plans else {"reply": "Done.", "operations": [], "verify": False}
        calls = plan if isinstance(plan, list) else [plan]
        for index, call in enumerate(calls):
            # ("rejected", call): the runtime's schema check refused it and the model retried.
            rejected, call = (True, call[1]) if isinstance(call, tuple) else (False, call)
            identifier = "toolu_%d_%d" % (len(self.prompts), index)
            process.stdout.emit({"type": "assistant", "session_id": "native-1", "message": {"content": [
                {"type": "tool_use", "id": identifier, "name": "StructuredOutput", "input": call}]}})
            process.stdout.emit({"type": "user", "session_id": "native-1", "message": {"content": [
                {"type": "tool_result", "tool_use_id": identifier, "is_error": rejected,
                 "content": "schema mismatch" if rejected else "Structured output provided successfully"}]}})
        last = calls[-1][1] if isinstance(calls[-1], tuple) else calls[-1]
        process.stdout.emit({"type": "result", "subtype": "success", "is_error": False, "session_id": "native-1",
                             "structured_output": last})


class LookupRoundTests(Superseded):
    """Trigger 2: a lookup with verify left out, and the move never ran."""

    def moved(self):
        check = passed("transform", target=HANDLE, observed={"location": [-1.0, -1.0, 0.0], "scope": "local_RNA"})
        return committed(checks=[check])

    def roles(self):
        return [message["role"] for message in self.c.state["messages"]]

    def move(self):
        """One request; a continuation round observes the scene through the turn-context builder."""
        built = observation(named())
        with patch("angelo_agent.turn_context.prepare",
                   return_value=TurnContext(copy.deepcopy(built), stats={})):
            return self.turn("Move 'Eval Cube' to location (-1, -1, 0).", observation=built)

    def test_a_lookup_only_plan_gets_the_round_that_does_the_edit(self):
        self.executor.results = [exact(HANDLE, REVISION, name="Eval Cube"), self.moved()]
        self.plans = [{"reply": "Checking the cube before moving it.", "operations": [LOOKUP]},
                      {"operations": [MOVE]}]
        self.move()
        self.assertEqual(self.executor.calls, ["angelo_scene_object", "angelo_scene_program"])
        self.assertEqual(len(self.prompts), 2)
        self.assertTrue(any(message.get("host_summary") for message in self.c.state["messages"]))

    def test_a_lookup_superseded_by_done_is_never_taken_as_the_answer(self):
        """Regression: two StructuredOutput calls, the last "Done." with no operations."""
        self.executor.results = [self.moved()]
        self.plans = [[{"reply": "Checking the cube before moving it.", "operations": [LOOKUP]},
                       {"reply": "Done.", "operations": []}],
                      {"operations": [MOVE]}]
        self.move()
        self.assertEqual(self.executor.calls, ["angelo_scene_program"], "Nothing of the superseded plan ran")
        self.assertTrue(self.prompts[1].startswith("You called StructuredOutput 2 times in one turn"))
        self.assertFalse(any(message["role"] == "assistant" and message["text"] == "Done."
                             for message in self.c.state["messages"]), "Its untrue 'Done.' is never shown")
        self.assertNotIn("error", self.roles())

    def test_a_retry_after_a_schema_rejection_is_the_plan(self):
        """Regression: Haiku program steps without {tool, arguments}, refused by the runtime, then fixed."""
        self.executor.results = [self.moved()]
        wrong = {"reply": "Moving it.", "operations": [transform(location=[-1, -1, 0])]}
        self.plans = [[("rejected", wrong), {"operations": [MOVE]}]]
        self.move()
        self.assertEqual(self.executor.calls, ["angelo_scene_program"])
        self.assertEqual(len(self.prompts), 1, "No extra round for a retry the runtime already asked for")

    def test_a_later_round_with_nothing_to_add_ends_with_the_host_summary(self):
        """A reply-less edit with verify=true: the continuation may say nothing too."""
        self.executor.results = [self.moved()]
        self.plans = [{"operations": [MOVE], "verify": True}, {"operations": []}]
        self.move()
        self.assertEqual(len(self.prompts), 2)
        self.assertNotIn("error", self.roles())
        self.assertNotIn("assistant", self.roles(), "No empty chat row")
        self.assertTrue(any(message.get("host_summary") for message in self.c.state["messages"]))

    def test_an_object_a_search_showed_binds_the_next_round(self):
        """Regression: the turn context lists at most eight objects; a plan that then
        edited an object a search had just shown was refused ("no revision in your latest observation")."""
        row = exact(OTHER, NEWER, name="Lamp", type="MESH")
        self.executor.results = [{"objects": [row], "total": 1}, self.moved()]
        search = {"tool": "angelo_scene_search", "arguments": {"query": "Lamp"}}
        move = {"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [
            transform(target="o2", location=[0, 0, 1])]}}
        self.plans = [{"operations": [search], "verify": True}, {"operations": [move]}]
        seen, execute = [], self.executor.execute
        self.executor.execute = lambda name, arguments: seen.append(arguments) or execute(name, arguments)
        self.move()
        self.assertIn('"object":"o2"', self.prompts[1], "The search's row is the second numbered object")
        self.assertEqual(self.executor.calls, ["angelo_scene_search", "angelo_scene_program"], self.prompts[-1])
        self.assertEqual(seen[-1]["write_set"], [{"handle": OTHER, "revision": NEWER}])

    def test_an_inexact_search_row_binds_nothing(self):
        row = {"object": OTHER, "name": "Lamp", "type": "MESH", "object_revision": NEWER, "revision_exact": False}
        self.executor.results = [{"objects": [row], "total": 1}]
        search = {"tool": "angelo_scene_search", "arguments": {"query": "Lamp"}}
        move = {"tool": "angelo_scene_program", "arguments": {"mode": "APPLY", "operations": [
            transform(target="o2", location=[0, 0, 1])]}}
        self.plans = [{"operations": [search], "verify": True}, {"operations": [move]}, {"operations": []}]
        self.move()
        self.assertEqual(self.executor.calls, ["angelo_scene_search"])
        self.assertIn("no revision in your latest observation", self.prompts[2])

    def test_an_empty_plan_after_a_lookup_is_asked_again(self):
        self.executor.results = [exact(HANDLE, REVISION), self.moved()]
        self.plans = [{"operations": [LOOKUP], "verify": True}, {"operations": []}, {"operations": [MOVE]}]
        self.move()
        self.assertEqual(self.prompts[2], EMPTY_PLAN)
        self.assertEqual(self.executor.calls, ["angelo_scene_object", "angelo_scene_program"])

    def test_an_empty_first_plan_is_asked_again_within_the_rejection_budget(self):
        self.plans = [{"operations": []}, {"reply": "Hello! What shall we make?", "operations": []}]
        self.turn("Hello.", observation=observation(named()))
        self.assertEqual(self.prompts[1], EMPTY_PLAN)
        self.assertEqual([m["text"] for m in self.c.state["messages"] if m["role"] == "assistant"],
                         ["Hello! What shall we make?"])
        self.plans = [{"operations": []}] * 3
        self.turn("Hello again.", observation=observation(named()))
        self.assertTrue(any(m["role"] == "error" and "neither a reply nor operations" in m["text"]
                            for m in self.c.state["messages"]))


class PureTests(unittest.TestCase):
    def test_superseded_plans(self):
        done = {"reply": "Done.", "operations": []}
        lookup = {"reply": "Checking.", "operations": [LOOKUP]}
        self.assertIsNone(superseded_plans([done], done), "One call")
        self.assertIsNone(superseded_plans([lookup, {"operations": [MOVE]}], {"operations": [MOVE]}),
                          "An earlier lookup the last plan does not need")
        self.assertIsNone(superseded_plans([{"operations": [MOVE]}, {"operations": [MOVE]}], {"operations": [MOVE]}),
                          "A repeated plan")
        self.assertIn("2 times", superseded_plans([lookup, done], done))
        self.assertIn("never ran", superseded_plans([{"operations": [MOVE]}, {"operations": [LOOKUP]}],
                                                    {"operations": [LOOKUP]}))

    def test_a_name_where_a_handle_belongs_names_the_fix(self):
        table = IdentifierTable()
        context_ledger.compact_observation(observation(entry(HANDLE, REVISION)), table)
        with self.assertRaisesRegex(IdentifierError, "'Eval Cube' is not an object handle: .* never its name"):
            expand_call("angelo_scene_program", {"mode": "APPLY", "operations": [
                transform(target="Eval Cube", location=[1, 0, 0])]}, table)

    def test_toolset(self):
        self.assertGreaterEqual(TOOLSET_VERSION, 22)  # Native coverage (23) builds on 22.


if __name__ == "__main__":
    unittest.main()
