# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A scene with more objects than the turn observation lists: edits of looked-up objects, copies whose
numbering is already in use, and a long request that keeps making progress.

Recorded (Opus 5.5 on Angelo 1.0.2, a 90-object scene, 2026-10-21): angelo_scene_object read the lights' exact
revisions, the next round's observation listed those lights among its eight unrevisioned rows, and the plan
that set a light's energy was refused as having "no revision in your latest observation"; a lookup showed an
eye's revision could not be read (its parent has modifiers), and the refusal sent the model back to that
lookup; a batch of copies of 'Base Cup 1' beside 'Base Cup 2' to 'Base Cup 7' was refused as NAME_TAKEN; and
the request ended Failed at its third correction although the rounds between had committed edits.

The scene here is a held-out one (a bookshop): none of the recorded names, sizes or counts. Host only; no
Blender, model or provider.
"""

import hashlib
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import batch_spec, context_ledger, query_observations  # noqa: E402
from angelo_agent.identifiers import IdentifierError, IdentifierTable, expand_call  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402
from angelo_agent.plan_recovery import (CLAUDE_PLAN_REJECTIONS, CLAUDE_REQUEST_CORRECTIONS, PLAN_ROUNDS,  # noqa: E402
                                         changed_scene)

EPOCH = "c0ffee00" * 4
NAMES = (["Desk", "Floor", "Ceiling", "Counter", "Front Window", "Lamp", "Lamp Light", "Window Light"]
         + ["Shelf %d" % number for number in range(1, 13)] + ["Book %d" % number for number in range(1, 21)])
HANDLES = {name: "scn:%s:%d" % (EPOCH, 500 + index) for index, name in enumerate(NAMES)}
SCENE = "scn:%s:2" % EPOCH


def revision(name, version=0):
    return hashlib.sha256(("%s/%d" % (name, version)).encode()).hexdigest()


def turn_observation():
    """The turn context of a 40-object scene with nothing active, selected, named or changed: its eight
    scene-order rows, listed without revisions (turn_context.build)."""
    return {"context_version": 2, "scene": "Bookshop", "object_count": len(NAMES), "truncated": True,
            "scene_settings": {"handle": SCENE, "revision": "5" * 64},
            "objects": [{"name": name, "type": "LIGHT" if "Light" in name else "MESH", "handle": HANDLES[name],
                         "location": [0, 0, 0], "selected": False, "active": False} for name in NAMES[:8]]}


def detail(name, version=0, **row):
    """An angelo_scene_object result for one object."""
    return dict({"object": HANDLES[name], "name": name, "revision_exact": True, "program_revision_supported": True,
                 "revision_limitations": [], "object_revision": revision(name, version)}, **row)


def nested(name):
    return detail(name, object_revision=None, revision_exact=False, program_revision_supported=False,
                  revision_limitations=["nested_dependency_configuration"],
                  uncertain_reason="untracked_mutable_configuration")


def search(*rows):
    return {"objects": list(rows), "snapshot_exact": False}


def budget_row(name):
    """A search row the search's shared read budget ran out before."""
    return detail(name, object_revision=None, revision_exact=False, program_revision_supported=False,
                  uncertain_reason="original_state_work_budget")


class Context:
    """What query_observations reads of a Claude request context."""

    def __init__(self):
        self.table = IdentifierTable("native")
        self.host = SimpleNamespace(_identifiers=self.table)
        self.short_identifiers = True

    def compact_identifiers(self, value):
        return self.table.compact(value)

    def turn(self):
        """A new message's whole turn observation (context_ledger.compact_observation)."""
        context_ledger.compact_observation(turn_observation(), self.table)

    def round(self, operations, results):
        """A Claude continuation round, in the provider's order: the fresh observation compacted, the plan's
        results compacted and its lookups recorded, then that observation again (providers._run_plans)."""
        observation = turn_observation()
        context_ledger.compact_observation(observation, self.table)
        query_observations.compacted(self, {"results": results}, operations, results, observation)

    def short(self, name):
        return self.table.short_handle(HANDLES[name])


def lookups(*names):
    return [{"tool": "angelo_scene_object", "arguments": {"object": HANDLES[name]}} for name in names]


def configure(short, energy=40):
    return {"mode": "APPLY", "operations": [{"kind": "light_configure", "target": short,
                                             "parameters": {"settings": {"energy": energy}}}]}


def transform(short):
    return {"mode": "APPLY", "operations": [{"kind": "transform", "target": short,
                                             "parameters": {"location": [0, 0, 1]}}]}


class LookedUpObjectsBindTests(unittest.TestCase):
    def setUp(self):
        self.context = Context()
        self.context.turn()

    def bound(self, arguments):
        return expand_call("angelo_scene_program", arguments, self.context.table)

    def refused(self, arguments):
        with self.assertRaises(IdentifierError) as caught:
            self.bound(arguments)
        return str(caught.exception)

    def test_a_looked_up_object_the_next_observation_lists_without_a_revision_still_binds(self):
        self.context.round(lookups("Lamp Light", "Desk"), [detail("Lamp Light"), detail("Desk")])
        self.context.round([], [])  # Another round: the lights are among the eight unrevisioned rows again.
        resolved = self.bound(configure(self.context.short("Lamp Light")))
        self.assertEqual(resolved["write_set"], [{"handle": HANDLES["Lamp Light"], "revision": revision("Lamp Light")}],
                         "Bound to the revision the lookup showed, never a live one")
        self.bound(transform(self.context.short("Desk")))

    def test_it_still_binds_after_a_new_messages_turn_observation(self):
        self.context.round(lookups("Window Light"), [detail("Window Light")])
        self.context.turn()
        self.bound(configure(self.context.short("Window Light")))

    def test_an_object_beyond_the_listing_binds_after_its_lookup(self):
        self.context.round(lookups("Shelf 9"), [detail("Shelf 9")])
        self.context.round([], [])
        self.bound(transform(self.context.short("Shelf 9")))

    def test_a_search_row_its_budget_left_inexact_neither_revokes_nor_misstates(self):
        self.context.round(lookups("Shelf 3"), [detail("Shelf 3")])
        operations = [{"tool": "angelo_scene_search", "arguments": {"query": "Shelf"}}]
        self.context.round(operations, [search(budget_row("Shelf 3"), budget_row("Shelf 4"))])
        self.bound(transform(self.context.short("Shelf 3")))
        text = self.refused(transform(self.context.short("Shelf 4")))
        self.assertIn("the search's shared read budget ran out before it", text)
        self.assertIn('angelo_scene_object {"object":"%s"} returns its object_revision' % self.context.short("Shelf 4"),
                      text)

    def test_a_lookup_that_shows_an_unreadable_revision_names_it_in_every_later_round(self):
        short = self.context.short("Lamp")
        operations = lookups("Lamp")
        self.context.round(operations, [nested("Lamp")])
        query_observations.observe_lookups(self.context, operations, [nested("Lamp")])  # Read in this very round.
        self.assertEqual(self.refused(transform(short)), "%s's revision cannot be read (nested_dependency_configuration), "
                         "so no program can bind it; angelo_execute_python can edit it" % short)
        for later in ("round", "turn"):
            with self.subTest(later):
                self.context.round([], []) if later == "round" else self.context.turn()
                # Read before this observation: it may have changed since, so the refusal gives both routes.
                self.assertEqual(self.refused(transform(short)),
                                 "%s could not be bound when it was last read (nested_dependency_configuration); "
                                 'angelo_execute_python can edit it, and angelo_scene_object {"object":"%s"} returns '
                                 "its object_revision if that has changed" % (short, short))

    def test_an_object_never_looked_up_is_still_refused_with_its_lookup(self):
        text = self.refused(transform(self.context.short("Counter")))
        self.assertIn("it is listed without one", text)
        self.assertIn('angelo_scene_object {"object":"%s"}' % self.context.short("Counter"), text)


class EvidenceAboutTheObjectStillRevokesTests(unittest.TestCase):
    """Only a listing's own bounds keep an earlier revision; anything about the object replaces it."""

    def setUp(self):
        self.context = Context()
        self.context.turn()
        self.context.round(lookups("Book 7"), [detail("Book 7")])
        self.short = self.context.short("Book 7")

    def assert_revoked(self):
        with self.assertRaises(IdentifierError):
            expand_call("angelo_scene_program", transform(self.short), self.context.table)

    def test_a_later_lookup_that_cannot_read_it(self):
        self.context.round(lookups("Book 7"), [nested("Book 7")])
        self.assert_revoked()

    def test_a_detail_lookup_over_its_own_budget(self):
        self.context.round(lookups("Book 7"), [budget_row("Book 7")])
        self.assert_revoked()

    def test_a_commit_that_changed_it_and_could_not_read_it_back(self):
        result = {"live_revisions": {"status": "COMMITTED", "objects": [{"handle": HANDLES["Book 7"], "revision_omitted": {
            "reason": "BUDGET", "detail": "cooperative_post_commit_budget"}}]}}
        context_ledger.observe_committed(self.context.table, result)
        self.assert_revoked()

    def test_a_script_read_back_that_left_it_out(self):
        after = {"objects": [{"handle": HANDLES["Book 7"], "name": "Book 7", "revision_omitted": {
            "reason": "BUDGET", "detail": "cooperative_read_back_budget"}}]}
        context_ledger.observe_committed(self.context.table, {"after": after})
        self.assert_revoked()

    def test_an_observation_row_that_cannot_read_it(self):
        observation = turn_observation()
        observation["objects"].append({"name": "Book 7", "type": "MESH", "handle": HANDLES["Book 7"],
                                       "revision_omitted": {"reason": "UNSUPPORTED", "detail": "unfingerprinted_data:X"}})
        context_ledger.compact_observation(observation, self.context.table)
        self.assert_revoked()

    def test_a_newer_exact_revision_replaces_it(self):
        self.context.round(lookups("Book 7"), [detail("Book 7", version=1)])
        resolved = expand_call("angelo_scene_program", transform(self.short), self.context.table)
        self.assertEqual(resolved["write_set"][0]["revision"], revision("Book 7", 1))


class CopyNamesTests(unittest.TestCase):
    """Copies continue a numbered name after the numbers already held (batch_spec.unit_names)."""

    def names(self, sources, total, held):
        return batch_spec.unit_names(sources, total, lambda name: name in held)

    def test_copies_skip_the_numbers_other_objects_already_hold(self):
        held = {"Shelf %d" % number for number in range(1, 13)}
        self.assertEqual(self.names(["Shelf 1"], 4, held), [(["Shelf 13", "Shelf 14", "Shelf 15"], 1)])

    def test_a_gap_too_small_for_the_whole_block_is_passed_over(self):
        held = {"Book 1", "Book 2", "Book 4"}
        self.assertEqual(self.names(["Book 1"], 3, held), [(["Book 5", "Book 6"], 1)])
        self.assertEqual(self.names(["Book 1"], 2, held), [(["Book 3"], 1)])

    def test_sources_sharing_a_stem_keep_their_stride_after_held_numbers(self):
        held = {"Post 1", "Post 2", "Post 3", "Post 4"}
        self.assertEqual(self.names(["Post 1", "Post 2"], 3, held),
                         [(["Post 5", "Post 7"], 2), (["Post 6", "Post 8"], 2)])

    def test_widths_and_unnumbered_names_follow_the_same_rule(self):
        self.assertEqual(self.names(["Rail 01"], 3, {"Rail 01", "Rail 02"}), [(["Rail 03", "Rail 04"], 1)])
        self.assertEqual(self.names(["Pier"], 3, {"Pier", "Pier.001"}), [(["Pier.002", "Pier.003"], 1)])

    def test_a_copies_check_without_first_is_inconclusive(self):
        from angelo_agent.validation_reader import Inconclusive
        data = object()
        cups = [SimpleNamespace(name="Cup %d" % n, type="MESH", data=data) for n in range(1, 8)]
        reader = SimpleNamespace(context={"targets": {}, "scene_objects": cups}, spend=lambda _count: None,
                                 scene_objects=lambda: list(cups))
        check = {"kind": "copies", "target": "o1", "count": 2, "linked": True}
        for scene in (cups, cups[:3]):  # Copies there or gone: Cup 2 and 3 may be older objects either way.
            reader.scene_objects = lambda scene=scene: list(scene)
            with self.assertRaisesRegex(Inconclusive, "state first"):
                batch_spec.evaluate_copies(reader, cups[0], check)
        reader.scene_objects = lambda: list(cups)
        passed, observed = batch_spec.evaluate_copies(reader, cups[0], dict(check, first="Cup 6"))
        self.assertTrue(passed)
        self.assertEqual((observed["first"], observed["last"]), ("Cup 6", "Cup 7"))

    def test_free_numbers_and_no_predicate_are_unchanged(self):
        for held in (set(), {"Lamp 9"}):
            self.assertEqual(self.names(["Lamp 1"], 3, held), batch_spec.unit_names(["Lamp 1"], 3))
        self.assertEqual(batch_spec.unit_names(["Lamp 1"], 3), [(["Lamp 2", "Lamp 3"], 1)])


class ChangedSceneTests(unittest.TestCase):
    PROGRAM = {"tool": "angelo_scene_program", "arguments": {}}
    SCRIPT = {"tool": "angelo_execute_python", "arguments": {}}
    DELTA = {"tool": "angelo_scene_delta", "arguments": {}}

    def test_what_counts_as_progress(self):
        modified = {"added": [], "removed": [], "modified": [{"name": "Desk", "fields": ["matrix"]}]}
        cases = [
            ([self.PROGRAM], [{"success": True, "scene_changed": True}], True),
            ([self.SCRIPT], [{"success": True, "changes": modified}], True),
            ([self.PROGRAM], [{"success": False, "scene_changed": False, "error": "x"}], False),
            ([self.SCRIPT], [{"error": "raised", "changes": modified}], False),
            ([self.SCRIPT], [{"success": True, "changes": {"added": [], "removed": [], "modified": []}}], False),
            ([self.DELTA], [{"success": True, "changes": modified}], False),  # A lookup's data is never progress.
            # A committed program says scene_changed even when its read-back delta is empty: no progress.
            ([self.PROGRAM], [{"success": True, "scene_changed": True,
                               "changes": {"added": [], "removed": [], "modified": []}}], False),
        ]
        for operations, results, expected in cases:
            with self.subTest(operations=operations, results=results):
                self.assertIs(changed_scene(operations, results), expected)


class ProgressRestartsTheCorrectionCountTests(unittest.TestCase):
    """The Claude plan loop (providers._run_plans) with mocked rounds and native calls."""

    def provider(self, plans, results):
        from claude_protocol_fixtures import host
        from program_completion_fixtures import register_completion
        from angelo_agent.providers import ClaudeProvider
        context = host()
        context.request_observation = Mock(side_effect=lambda callback: callback({"context_version": 2, "objects": []}))
        provider = ClaudeProvider(context, {})
        provider._turn = Mock(side_effect=[dict(plan, verify=plan.get("verify", False)) for plan in plans])
        answers = iter(results)

        def native(name, arguments, **_kwargs):
            result = next(answers)
            register_completion(context, name, arguments, result)
            return result
        provider._native_call = Mock(side_effect=native)
        return provider

    @staticmethod
    def rejected():
        return {"reply": "", "operations": [], "problem": "The plan was not valid"}

    @staticmethod
    def edit(verify):
        from test_native_round_savings import program
        return {"reply": "", "operations": [program()], "verify": verify}

    def test_corrections_after_a_committed_edit_are_counted_again(self):
        from test_native_round_savings import committed
        plans = ([self.rejected()] * CLAUDE_PLAN_REJECTIONS + [self.edit(True)]
                 + [self.rejected()] * CLAUDE_PLAN_REJECTIONS + [self.edit(False)])
        provider = self.provider(plans, [committed(), committed()])
        self.assertEqual(provider.run(None, "Light the shop", observation={}), "completed")
        self.assertEqual(provider._turn.call_count, len(plans))

    def test_without_progress_the_budget_still_ends_the_request(self):
        from test_native_round_savings import committed
        plans = [self.edit(True)] + [self.rejected()] * (CLAUDE_PLAN_REJECTIONS + 1) + [self.edit(False)]
        provider = self.provider(plans, [committed(), committed()])
        with self.assertRaisesRegex(RpcError, "The plan was not valid"):
            provider.run(None, "Light the shop", observation={})
        self.assertEqual(provider._turn.call_count, CLAUDE_PLAN_REJECTIONS + 2)

    def test_alternating_an_edit_with_a_mistake_ends_when_the_requests_corrections_run_out(self):
        from test_native_round_savings import committed
        plans = [self.edit(True), self.rejected()] * PLAN_ROUNDS
        provider = self.provider(plans, [committed() for _ in range(PLAN_ROUNDS)])
        with self.assertRaisesRegex(RpcError, "The plan was not valid"):
            provider.run(None, "Light the shop", observation={})
        rejected = [call for call in provider._turn.call_args_list][1::2]
        self.assertEqual(len(rejected), CLAUDE_REQUEST_CORRECTIONS + 1)
        self.assertLessEqual(provider._turn.call_count, PLAN_ROUNDS)

    def test_a_commit_that_changed_nothing_does_not_restart_the_count(self):
        from test_native_round_savings import committed
        unchanged = committed()
        unchanged["changes"].update(added=[], removed=[], modified=[])
        plans = [self.rejected()] * CLAUDE_PLAN_REJECTIONS + [self.edit(True), self.rejected(), self.edit(False)]
        provider = self.provider(plans, [unchanged, committed()])
        with self.assertRaisesRegex(RpcError, "The plan was not valid"):
            provider.run(None, "Light the shop", observation={})
        self.assertEqual(provider._turn.call_count, CLAUDE_PLAN_REJECTIONS + 2)


if __name__ == "__main__":
    unittest.main()
