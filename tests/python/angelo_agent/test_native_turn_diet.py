# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Toolset 20: inferred bindings, observed dimensions, optional edit replies and a smaller turn suffix.

Pure tables and compositions, the real turn-context builder and scene-query
staleness rules over host fakes, and the real Controller and ClaudeProvider
over the unbilled stdio fixture: no Blender, Claude process or inference.
"""

import copy
import json
import sys
import time
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import context_ledger, identifiers, memory_context, task_memory, turn_context  # noqa: E402
from angelo_agent.identifiers import IdentifierError, IdentifierTable, expand_call  # noqa: E402
from angelo_agent.plan_schema import plan_arguments  # noqa: E402
from angelo_agent.routing import handoff  # noqa: E402
from angelo_agent.scene_validation import execute as verify_execute  # noqa: E402
from angelo_agent.tool_specs import TOOL_MAP, native_plan_schema, validate_call  # noqa: E402
from test_native_cache_diet import DOC, ControllerFixture, activity, memory_state  # noqa: E402
from test_native_round_savings import committed, passed  # noqa: E402
from test_native_scene_validation import cube  # noqa: E402
from test_native_turn_context import TurnContextFixture  # noqa: E402

EPOCH = "a" * 32
HANDLE, PARENT, OTHER = ("scn:%s:%d" % (EPOCH, uid) for uid in (159, 160, 161))
REVISION, PARENT_REVISION, NEWER = "1" * 8 + "b" * 56, "2" * 8 + "c" * 56, "3" * 8 + "d" * 56


def observation(*entries, **fields):
    return dict({"scene": "Scene", "object_count": len(entries), "objects": list(entries)}, **fields)


def entry(handle, revision=None, name="Eval Cube", **fields):
    value = {"name": name, "type": "MESH", "handle": handle, "location": [0.0, 0.0, 0.0]}
    if revision:
        value["object_revision"] = revision
    value.update(fields)
    return value


def observed_table(*entries):
    table = IdentifierTable()
    context_ledger.compact_observation(observation(*entries), table)
    return table


def transform(target="o1", **parameters):
    return {"kind": "transform", "target": target, "parameters": dict({"mode": "SET"}, **parameters)}


class InferredBindingTests(unittest.TestCase):
    def setUp(self):
        self.table = observed_table(entry(HANDLE, REVISION), entry(PARENT, PARENT_REVISION, name="Rig"),
                                    entry(OTHER, None, name="Unrevised"))

    def test_a_program_binds_what_it_touches_to_the_latest_observed_revisions(self):
        program = {"mode": "APPLY", "operations": [
            transform(scale=[2, 1, 1]), {"kind": "parent_set", "target": "o1", "parameters": {"parent": "o2"}}],
            "expected_checks": [{"kind": "dimensions", "target": "o1", "expected": [4, 2, 2], "tolerance": 0.001}]}
        resolved = expand_call("angelo_scene_program", program, self.table)
        self.assertEqual(resolved["write_set"], [{"handle": HANDLE, "revision": REVISION}], "Mutated target: written")
        self.assertEqual(resolved["read_set"], [{"handle": PARENT, "revision": PARENT_REVISION}], "Dependency: read")
        canonical = validate_call("angelo_scene_program", resolved)
        self.assertEqual(canonical["write_set"][0]["revision"], REVISION)
        self.assertNotIn("write_set", program, "The model's arguments are not mutated")

    def test_explicit_bindings_win_and_only_missing_ones_are_inferred(self):
        program = {"mode": "APPLY", "write_set": [{"handle": "o1", "revision": NEWER[:8]}],
                   "operations": [transform(location=[1, 0, 0])]}
        self.table.compact({"handle": HANDLE, "object_revision": NEWER})  # Shown in a lookup result.
        resolved = expand_call("angelo_scene_program", program, self.table)
        self.assertEqual(resolved["write_set"], [{"handle": HANDLE, "revision": NEWER}])
        self.assertNotIn("read_set", resolved)

    def test_an_object_without_an_observed_revision_must_be_declared(self):
        for program in ({"mode": "APPLY", "operations": [transform(target="o3", location=[1, 0, 0])]},
                        {"mode": "APPLY", "operations": [transform(location=[1, 0, 0])],
                         "expected_checks": [{"kind": "object_type", "target": "o3", "expected": "MESH"}]}):
            with self.assertRaisesRegex(IdentifierError, "o3 has no revision in your latest observation"):
                expand_call("angelo_scene_program", program, self.table)
        context_ledger.compact_observation(observation(entry(HANDLE, None, revision_omitted={"reason": "BUDGET"})),
                                           self.table)
        with self.assertRaisesRegex(IdentifierError, r"o1 has no revision .*read time.*angelo_scene_object"):
            expand_call("angelo_scene_program", {"mode": "APPLY", "operations": [transform(location=[1, 0, 0])]},
                        self.table)

    def test_recipes_and_verification_infer_their_bindings(self):
        recipe = {"recipe": "bevel-and-shade", "target": "o1", "parameters": {"width": 0.1, "segments": 2},
                  "mode": "APPLY"}
        self.assertEqual(validate_call("angelo_apply_recipe", expand_call("angelo_apply_recipe", recipe, self.table))
                         ["revision"], REVISION)
        verify = {"checks": [{"kind": "parent", "target": "o1", "expected": "o2"}]}
        self.assertEqual(expand_call("angelo_scene_verify", verify, self.table)["read_set"],
                         [{"handle": HANDLE, "revision": REVISION}, {"handle": PARENT, "revision": PARENT_REVISION}])

    def test_without_a_table_nothing_is_inferred(self):
        recipe = {"recipe": "uv-prep", "target": HANDLE, "parameters": {"method": "CUBE"}, "mode": "APPLY"}
        self.assertEqual(expand_call("angelo_apply_recipe", recipe, None), recipe)
        with self.assertRaisesRegex(ValueError, "Missing tool argument"):
            validate_call("angelo_apply_recipe", recipe)

    def test_a_change_since_the_models_last_view_is_rejected_by_the_owner(self):
        """The inferred revision is compared exactly, as an explicit one is."""
        bound = cube()
        window = SimpleNamespace(scene=SimpleNamespace(objects=[bound]))
        fake = ModuleType("angelo_agent.scene_queries")
        fake.SceneQueryError = ValueError
        fake.resolve_object = lambda window, value: bound
        current = {"revision": NEWER}  # Edited after the model's latest observation.
        fake.object_revision = lambda window, obj: current["revision"]
        arguments = expand_call("angelo_scene_verify", {"checks": [{"kind": "object_type", "target": "o1",
                                                                     "expected": "MESH"}]}, self.table)
        with patch.dict(sys.modules, {"angelo_agent.scene_queries": fake}):
            with self.assertRaisesRegex(ValueError, "changed since observation"):
                verify_execute(window, "angelo_scene_verify", arguments)
            current["revision"] = REVISION
            self.assertTrue(verify_execute(window, "angelo_scene_verify", arguments)["passed"])

    def test_observed_revisions_persist_with_the_table(self):
        loaded = IdentifierTable.load(self.table.to_dict("native"), "native")
        self.assertEqual((loaded.observed(HANDLE), loaded.observed(OTHER)), (REVISION, None))
        nineteen = dict(self.table.to_dict("native"))
        del nineteen["observed"]
        self.assertIsNone(IdentifierTable.load(nineteen, "native").observed(HANDLE),
                          "A toolset-19 table loads with nothing observed yet")
        self.assertIsNone(identifiers.clean(dict(self.table.to_dict("native"), observed=[[HANDLE, "x"]])))

    def test_the_plan_schema_leaves_inferable_bindings_and_the_edit_reply_optional(self):
        self.assertEqual(native_plan_schema()["required"], ["operations"])
        recipe = plan_arguments(TOOL_MAP["angelo_apply_recipe"])
        self.assertNotIn("revision", recipe["required"])
        self.assertTrue(all("revision" not in branch.get("required", []) for branch in recipe["oneOf"]))
        self.assertEqual(plan_arguments(TOOL_MAP["angelo_scene_verify"])["required"], ["checks"])
        self.assertIn("revision", TOOL_MAP["angelo_apply_recipe"]["inputSchema"]["required"], "Runtime stays strict")
        self.assertIn("read_set", TOOL_MAP["angelo_scene_verify"]["inputSchema"]["required"])


class ObservedShapeTests(TurnContextFixture):
    def test_entries_with_revisions_state_dimensions_and_non_identity_scale_and_rotation(self):
        for obj in self.objects:
            obj.dimensions = [2.0, 1.0, 0.5]
        self.active.scale, self.active.rotation_euler = [2.0, 1.0, 0.5], [0.0, 0.0, 1.5707963]
        entries = self.prepare().observation["objects"]
        first, selected, plain = entries[0], entries[1], entries[9]
        self.assertEqual((first["dimensions"], first["scale"], first["rotation"]),
                         ([2.0, 1.0, 0.5], [2.0, 1.0, 0.5], [0.0, 0.0, 1.570796]))
        self.assertEqual(selected["dimensions"], [2.0, 1.0, 0.5])
        self.assertNotIn("scale", selected, "Identity scale is not listed")
        self.assertNotIn("rotation", selected)
        self.assertNotIn("dimensions", plain, "Only entries that get a revision")

    def test_an_object_without_readable_bounds_simply_lacks_them(self):
        entries = self.prepare().observation["objects"]
        self.assertNotIn("dimensions", entries[0])
        self.assertIn("object_revision", entries[0])


class SuffixTests(unittest.TestCase):
    def test_change_hints_of_this_chats_own_commits_are_left_out(self):
        changes = {"status": "TRACKED", "objects": [{"handle": HANDLE, "name": "Eval Cube", "changes": ["transform"]}],
                   "shading_only_objects": 3, "datablocks": [{"type": "MESH", "name": "Eval Cube Mesh"}]}
        scene = observation(entry(HANDLE, REVISION), changes_since_last_turn=changes)
        own = context_ledger.compact_observation(scene, IdentifierTable(), {HANDLE: REVISION})
        self.assertNotIn("changes_since_last_turn", own, "Fully explained, so nothing to report")
        later = context_ledger.compact_observation(scene, IdentifierTable(), {HANDLE: NEWER})
        self.assertEqual(later["changes_since_last_turn"]["objects"], [{"handle": "o1", "changes": ["transform"]}],
                         "Changed again after the commit: the hint stays")
        self.assertEqual(later["changes_since_last_turn"]["datablocks"], {"MESH": ["Eval Cube Mesh"]})
        foreign = dict(changes, objects=changes["objects"] + [{"handle": OTHER, "changes": ["geometry"]}])
        mixed = context_ledger.compact_observation(
            observation(entry(HANDLE, REVISION), entry(OTHER, NEWER), changes_since_last_turn=foreign),
            IdentifierTable(), {HANDLE: REVISION})
        self.assertEqual([row["handle"] for row in mixed["changes_since_last_turn"]["objects"]], ["o2"])
        self.assertIn("datablocks", mixed["changes_since_last_turn"], "Other changes keep their datablock hints")

    def test_a_changed_entry_is_sent_as_its_handle_and_changed_fields(self):
        table = IdentifierTable()
        first = context_ledger.TurnBlocks(identifiers=table)
        first.observation(observation(entry(HANDLE, REVISION, dimensions=[2, 2, 2], selected=True)))
        later = observation(entry(HANDLE, NEWER, dimensions=[2, 2, 2]))
        text = context_ledger.TurnBlocks(context_ledger.usable(first.ledger("native"), "native"),
                                         identifiers=table).observation(later)
        sent = json.loads(text.split("\n", 3)[3])
        self.assertEqual(sent["objects"], [{"handle": "o1", "object_revision": NEWER[:8], "selected": None}])

    def test_a_later_rounds_observation_is_only_what_changed_since_the_turns_message(self):
        table = IdentifierTable()
        controller = SimpleNamespace(_identifiers=table)
        controller.compact_observation = lambda value: identifiers_compact(controller, value)
        changes = {"status": "TRACKED", "objects": [{"handle": HANDLE, "changes": ["transform"]}]}
        blocks = context_ledger.TurnBlocks(identifiers=table)
        scene = observation(entry(HANDLE, REVISION), changes_since_last_turn=changes)
        blocks.observation(scene)
        controller._pending_ledger = {"blocks": blocks}
        self.assertEqual(controller.compact_observation(copy.deepcopy(scene)), {"unchanged": True})
        moved = copy.deepcopy(scene)
        moved["objects"][0]["object_revision"] = NEWER
        delta = controller.compact_observation(moved)
        self.assertEqual(delta["objects"], [{"handle": "o1", "object_revision": NEWER[:8]}])
        self.assertNotIn("changes_since_last_turn", delta, "The same event is not repeated within a turn")
        self.assertEqual(table.observed(HANDLE), NEWER, "The later round is the latest view")

    def test_a_field_back_to_its_turn_value_after_a_later_round_is_sent_next_turn(self):
        """A (the turn's message) -> B (a later round of that turn) -> A (the next turn) reaches the model.

        A later round moves the whole observation baseline, per-field digests included: a stale
        field baseline left the entry counted as changed but sent as its handle alone.
        """
        table = IdentifierTable()
        controller = SimpleNamespace(_identifiers=table)
        controller.compact_observation = lambda value: identifiers_compact(controller, value)
        bystanders = [entry("scn:%s:%d" % (EPOCH, 170 + index), name="Prop %d" % index) for index in range(6)]
        turn_value = entry(HANDLE, REVISION, location=[0.0, 0.0, 0.0])
        moved = entry(HANDLE, NEWER, location=[1.5, 0.0, 0.0])
        blocks = context_ledger.TurnBlocks(identifiers=table)
        blocks.observation(observation(turn_value, *bystanders))
        controller._pending_ledger = {"blocks": blocks}
        round_delta = controller.compact_observation(observation(moved, *bystanders))
        self.assertEqual(round_delta["objects"], [{"handle": "o1", "object_revision": NEWER[:8],
                                                   "location": [1.5, 0, 0]}])
        later = context_ledger.TurnBlocks(context_ledger.usable(blocks.ledger("native"), "native"), identifiers=table)
        kind, value = later.observation_value(observation(turn_value, *bystanders))
        self.assertEqual(kind, "delta")
        self.assertEqual(value["objects"], [{"handle": "o1", "object_revision": REVISION[:8], "location": [0, 0, 0]}],
                         "The model last saw B; it must learn the object went back to A")
        self.assertEqual(table.observed(HANDLE), REVISION)

    def test_a_later_round_replaces_every_observation_digest_of_the_turns_ledger(self):
        """Every ledger key an observation records is one of OBSERVATION_KEYS, so a later round moves them all."""
        table = IdentifierTable()
        blocks = context_ledger.TurnBlocks(identifiers=table)
        blocks.observation(observation(entry(HANDLE, REVISION), *[entry("scn:%s:%d" % (EPOCH, 170 + index))
                                                                   for index in range(4)]))
        self.assertEqual(set(blocks.after) - {"version"}, set(context_ledger.OBSERVATION_KEYS))
        later = observation(entry(HANDLE, NEWER, location=[2.0, 0.0, 0.0]))
        blocks.continue_observation(copy.deepcopy(later))
        fresh = context_ledger.TurnBlocks(identifiers=table)
        fresh.observation_value(later)
        self.assertEqual({key: blocks.after.get(key) for key in context_ledger.OBSERVATION_KEYS},
                         {key: fresh.after.get(key) for key in context_ledger.OBSERVATION_KEYS})

    def test_a_certified_result_keeps_only_identities_the_observation_does_not_show(self):
        rows = [{"role": "user", "text": "Create it"}, activity("c1", committed()),
                {"role": "system", "text": "Host-verified result:\nCommitted.", "host_summary": True}]
        result_handle = committed()["live_revisions"]["objects"][0]
        shown = observed_table(entry(result_handle["handle"], result_handle["object_revision"]))
        merged = json.loads(handoff(rows, identifiers=shown, delta=True)[0].splitlines()[-1])
        self.assertEqual(set(merged), {"role", "tool", "verified"})
        unshown = json.loads(handoff(rows, identifiers=IdentifierTable(), delta=True)[0].splitlines()[-1])
        self.assertEqual(unshown["live"][0]["name"], "Eval Cube")

    def test_memory_bookkeeping_alone_is_not_sent(self):
        digest = context_ledger.digest
        state = memory_state([("c%d" % index, committed()) for index in range(24)])
        received = memory_context.received_lines(state, DOC, digest)
        more = memory_state([("c%d" % index, committed()) for index in range(26)])
        text, after = memory_context.render_delta(more, DOC, received, digest, known_results={"c24", "c25"})
        self.assertEqual(text, "", "Only the revision and the omitted-receipt count moved")
        for key in ("revision", "omitted"):
            self.assertEqual(after.get(digest(key)), received.get(digest(key)))


def identifiers_compact(controller, value):
    from angelo_agent.controller import Controller
    return Controller.compact_observation(controller, value)


class OfflineTurns(ControllerFixture):
    def scene(self, revision=REVISION):
        return observation(entry(HANDLE, revision, selected=True, active=True))

    def test_an_edit_without_bindings_or_reply_runs_and_adds_no_empty_chat_row(self):
        moved = passed("transform", target=HANDLE, observed={"location": [1.0, 0.0, 0.0], "scope": "local_RNA"})
        self.executor.results = [committed(checks=[moved])]
        self.plans = [{"operations": [{"tool": "angelo_scene_program", "arguments": {
            "mode": "APPLY", "operations": [transform(location=[1, 0, 0])]}}]}]
        self.turn("Move it.", observation=self.scene())
        call = next(m for m in self.c.state["messages"] if m.get("tool") == "angelo_scene_program")
        self.assertEqual(call["arguments"]["write_set"], [{"handle": HANDLE, "revision": REVISION}])
        self.assertEqual(self.executor.calls, ["angelo_scene_program"])
        rows = [m["role"] for m in self.c.state["messages"]]
        self.assertNotIn("assistant", rows, "No reply, no empty bubble")
        self.assertTrue(any(m.get("host_summary") for m in self.c.state["messages"]), "The host's summary shows")
        handed = self.turn("Thanks.", observation=self.scene(NEWER))
        self.assertIn('"verified":"Committed', handed, "The next turn learns the result")

    def test_a_plan_with_neither_a_reply_nor_operations_fails_the_turn(self):
        # Toolset 21 asks again within the rejection budget (test_native_agent_stack.py).
        self.plans = [{"operations": []}] * 3
        self.turn("Hello.", observation=self.scene())
        self.assertTrue(any(m["role"] == "error" and "neither a reply nor operations" in m["text"]
                            for m in self.c.state["messages"]))

    def test_a_question_round_after_a_lookup_sees_an_unchanged_observation(self):
        verify = {"tool": "angelo_scene_verify", "arguments": {"checks": [
            {"kind": "object_type", "target": "o1", "expected": "MESH"}]}}
        self.executor.results = [{"success": True, "passed": True, "status": "pass", "checks": [
            {"index": 0, "kind": "object_type", "target": HANDLE, "status": "pass", "passed": True,
             "observed": {"type": "MESH"}}]}]
        self.plans = [{"reply": "Checking.", "operations": [verify], "verify": True},
                      {"reply": "It is a mesh.", "operations": []}]
        built = self.scene()
        with patch("angelo_agent.turn_context.prepare",
                   return_value=turn_context.TurnContext(copy.deepcopy(built), stats={})):
            self.turn("Is it a mesh?", observation=built)
        self.assertEqual(len(self.prompts), 2)
        self.assertIn('"observation":{"unchanged":true}', self.prompts[1])
        self.assertEqual(self.executor.calls, ["angelo_scene_verify"])


class RebaseTests(ControllerFixture):
    """A context rebase starts a fresh native context: compact, and with fresh short identifiers."""

    def scene(self, *entries):
        return observation(*(entries or (entry(HANDLE, REVISION, selected=True, active=True),)))

    def test_a_rebased_context_gets_a_fresh_table_and_old_numbers_are_unknown(self):
        self.c.context_rebase_tokens = 1000
        other = entry(OTHER, NEWER, name="Lamp")
        self.turn("Look.", observation=self.scene(other, entry(HANDLE, REVISION)))
        self.assertEqual(self.c.resolve_call("angelo_scene_object", {"object": "o2"})["object"], HANDLE)
        self.entry()["context_tokens"] = 5000
        rebased = self.turn("Move the cube.", observation=self.scene())
        self.assertIn("Context refresh", rebased)
        self.assertEqual(self.c.state["native_id"], "native-2")
        self.assertIn('"handle":"o1"', rebased, "The fresh context numbers from o1 again")
        self.assertEqual(self.c.resolve_call("angelo_scene_object", {"object": "o1"})["object"], HANDLE,
                         "o1 now means what the fresh context was shown")
        with self.assertRaisesRegex(IdentifierError, "Unknown object handle o2"):
            self.c.resolve_call("angelo_scene_object", {"object": "o2"})
        self.assertEqual(self.entry()["identifiers"]["native_id"], "native-2")

    def test_inferred_bindings_after_a_rebase_use_only_the_fresh_observation(self):
        self.c.context_rebase_tokens = 1000
        self.turn("Look.", observation=self.scene())
        self.entry()["context_tokens"] = 5000
        moved = passed("transform", target=HANDLE, observed={"location": [1.0, 0.0, 0.0], "scope": "local_RNA"})
        self.executor.results = [committed(checks=[moved])]
        self.plans = [{"operations": [{"tool": "angelo_scene_program", "arguments": {
            "mode": "APPLY", "operations": [transform(target="o1", location=[1, 0, 0])]}}]}]
        self.turn("Move the cube.", observation=self.scene(entry(HANDLE, NEWER, selected=True, active=True)))
        call = next(m for m in self.c.state["messages"] if m.get("tool") == "angelo_scene_program")
        self.assertEqual(call["arguments"]["write_set"], [{"handle": HANDLE, "revision": NEWER}],
                         "Bound to the rebased message's revision, never the old context's")

    def test_the_rebased_message_carries_only_the_last_turn_and_counts_receipts(self):
        self.c.context_rebase_tokens = 1000
        for number in range(6):
            moved = passed("transform", target=HANDLE, observed={"location": [float(number), 0.0, 0.0],
                                                                  "scope": "local_RNA"})
            self.executor.results = [committed(checks=[moved])]
            self.plans = [{"reply": "Moving %d." % number, "operations": [{"tool": "angelo_scene_program",
                           "arguments": {"mode": "APPLY", "operations": [
                               transform(target="o1", location=[number, 0, 0])]}}]}]
            self.turn("Move it to %d." % number, observation=self.scene(entry(HANDLE, "%d" % number * 8 + "b" * 56)))
        self.entry()["context_tokens"] = 5000
        rebased = self.turn("Thanks.", observation=self.scene())
        handoff_part = rebased.split("Shared conversation handoff", 1)[1]
        self.assertIn("Move it to 5.", handoff_part, "The last turn is handed off")
        self.assertNotIn("Move it to 4.", handoff_part, "Earlier turns are summarized by the seed's requests")
        self.assertIn("historical_native_results_omitted=", rebased)
        self.assertNotIn("historical_native_result=", rebased)
        self.assertLess(len(rebased.encode()), 4500)


if __name__ == "__main__":
    unittest.main()
