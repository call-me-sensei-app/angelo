# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pins of the payload trims, on the change feed.

The reproductions are in test_native_feed_review.py, whose real-Controller
harness these reuse. The change feed ships without the continuation
acknowledgement (``feed_acks``), so the four pins of what it would acknowledge
pin that a continuation acknowledges nothing (each says what it pinned before),
and the Codex script pin states the rule: ``verified`` stays where no
observation restates it.
"""

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_heavy_payloads import SCRIPT, SEARCH
from test_native_scene_queries import FakeObject
from test_native_turn_context import Clock, TurnContextFixture, Update, make_window, object_id
from test_native_feed_review import DONE, LOOKUP, Harness
from angelo_agent import change_feed, turn_context
from angelo_agent.model_results import model_result, plan_results


class PinControllerAcknowledgementTests(Harness):
    """The real Controller and change feed: a verify round acknowledges nothing; every edit stays."""

    def test_an_edit_during_the_round_stays_and_the_reported_one_is_listed_again(self):
        # Was: the reported early edit is not listed again (feed_acks acknowledged it).
        early, late = self.objects[20], self.objects[21]
        self.plans = [LOOKUP]
        self.on_main[1] = lambda: self.person_moves(early)        # Before the continuation is built.
        self.on_main[2] = lambda: self.person_moves(late, 9.5)    # While the model reads it.
        self.send("Look around.")
        self.pump()
        self.assertIn("7.25", self.prompts[1][1], "The continuation reported the early edit")
        names = [row["name"] for row in self.next_report()["objects"]]
        self.assertEqual(names, [early.name, late.name])

    def test_a_round_the_provider_never_accepted_acknowledges_nothing(self):
        mover = self.objects[20]
        self.plans = [LOOKUP]
        self.on_main[1] = lambda: self.person_moves(mover)

        def refuse(process):
            process.stdout.emit({"type": "result", "subtype": "error_during_execution", "is_error": True,
                                 "session_id": "fixture-native-0", "errors": ["API Error: 529 Overloaded"]})
            return False
        self.hooks[2] = refuse
        self.send("Look around.")
        self.pump()
        self.assertIn(mover.name, str(self.next_report()))

    def test_stop_after_the_round_was_accepted_acknowledges_nothing(self):
        mover = self.objects[20]
        self.plans = [LOOKUP]
        self.on_main[1] = lambda: self.person_moves(mover)

        def accept_only(process):
            process.stdout.emit({"type": "assistant", "message": {"id": "m", "role": "assistant", "content": [],
                                                                  "usage": {"output_tokens": 3}}})
            return False
        self.hooks[2] = accept_only
        self.send("Look around.")
        self.pump(until=self.continuation_accepted)
        self.c.stop()
        self.pump()
        self.assertEqual(self.c.status, "Interrupted")
        self.assertIn(mover.name, str(self.next_report()))

    def test_stop_before_acceptance_acknowledges_nothing_even_if_the_round_answers(self):
        mover = self.objects[20]
        self.plans = [LOOKUP, DONE]
        self.on_main[1] = lambda: self.person_moves(mover)
        self.on_main[2] = self.c.stop   # Stop lands, then the provider's answer arrives.
        self.send("Look around.")
        self.pump()
        self.assertIn(mover.name, str(self.next_report()))


class PinBoundaryTests(TurnContextFixture):
    """Two corrections in one request, and the payload limit at an ordinary boundary."""

    def test_two_corrections_in_one_request_both_report_and_keep_their_changes(self):
        # Was: a second correction overwriting an unaccepted one still reports its changes, and its accepted
        # acknowledgement then empties the feed. Now no continuation observation acknowledges anything.
        turn_context.prepare(self.window, "chat-a", clock=Clock(0.)).commit()
        self.depsgraph(Update(self.objects[3], transform=True))
        turn_context.prepare(self.window, "chat-a", clock=Clock(0.))           # Correction 1.
        self.depsgraph(Update(self.objects[4], transform=True))
        second = turn_context.prepare(self.window, "chat-a", clock=Clock(0.))  # Correction 2.
        rows = [row["name"] for row in second.observation["changes_since_last_turn"]["objects"]]
        self.assertEqual(rows, ["Prop 03", "Prop 04"])
        report = change_feed.report("chat-a", turn_context.build(self.window, Clock(0.))[2])[0]
        self.assertEqual([row["name"] for row in report["objects"]], ["Prop 03", "Prop 04"])

    def test_pre_existing_an_ordinary_boundary_acknowledges_rows_its_payload_dropped_and_flags_them(self):
        # Pre-existing (console.execute): a payload-limited observation drops feed rows yet its commit
        # acknowledges them; objects_overflow is the model's only sign. Continuations now do the same.
        objects = [object_id(FakeObject(5000 + i, "House %02d %s" % (i, "x" * 240), mesh=True)) for i in range(70)]
        self.window = make_window(objects, objects[0])
        turn_context.prepare(self.window, "chat-a", clock=Clock(0.)).commit()
        for obj in objects[:60]:
            self.depsgraph(Update(obj, transform=True))
        turn = turn_context.prepare(self.window, "chat-a", clock=Clock(0.))
        report = turn.observation["changes_since_last_turn"]
        self.assertTrue(turn.observation.get("payload_limited"))
        self.assertLess(len(report["objects"]), 60)
        self.assertTrue(report["objects_overflow"])
        turn.commit()
        self.assertEqual(change_feed.report("chat-a", turn_context.build(self.window, Clock(0.))[2])[0]["objects"], [])


class PinFillRowTests(unittest.TestCase):
    """MAX_FILL_OBJECTS never cuts the active, mentioned, selected or changed objects."""

    def test_every_prioritized_row_survives_the_fill_cap(self):
        objects = [object_id(FakeObject(3000 + i, "Obj %03d" % i, mesh=True)) for i in range(100)]
        active, mentioned, selected = objects[99], objects[60:68], objects[10:50]
        changed = objects[80:88]
        window = make_window(objects, active, selected)
        message = " ".join("'%s'" % obj.name for obj in mentioned)
        chosen = turn_context._ordered_objects(window.scene, window.view_layer, active, mentioned, changed)
        names = {obj.name for obj in chosen}
        self.assertLessEqual(len(chosen), turn_context.MAX_OBJECTS)
        self.assertTrue({obj.name for obj in [active, *mentioned, *changed]} <= names)
        room = turn_context.MAX_OBJECTS - len(changed)
        self.assertEqual(len([obj for obj in chosen if obj in selected]), room - 1 - len(mentioned))
        self.assertTrue(message)

    def test_fill_is_at_most_eight_and_only_in_a_scene_over_the_cap(self):
        objects = [object_id(FakeObject(4000 + i, "Obj %03d" % i, mesh=True)) for i in range(33)]
        window = make_window(objects, objects[0])
        chosen = turn_context._ordered_objects(window.scene, window.view_layer, objects[0])
        self.assertEqual(len(chosen), 1 + turn_context.MAX_FILL_OBJECTS)
        window = make_window(objects[:32], objects[0])
        self.assertEqual(len(turn_context._ordered_objects(window.scene, window.view_layer, objects[0])), 32)


class PinModelResultTests(unittest.TestCase):
    """What the trims leave a model: every planning fact, and the raw result untouched."""

    def test_refusals_keep_codes_details_and_tokens(self):
        refusal = {"error_code": "STALE_BINDING", "error": "Changed", "details": {"handle": "h"},
                   "cache": {"x": 1}, "budgets": {"y": 2}, "native_exec_ms": 1.0, "observation_token": "obs:1"}
        shown = model_result("angelo_scene_object", copy.deepcopy(refusal))
        self.assertEqual(shown, {k: v for k, v in refusal.items() if k != "native_exec_ms"})
        refused = dict(SCRIPT, success=False, error="NameError", error_code="SCRIPT_FAILED", main_data_restored=True)
        self.assertEqual(set(model_result("angelo_execute_python", refused)),
                         set(refused) - {"native_exec_ms", "native_dispatch_wait_ms"})

    def test_paging_and_truncation_facts_stay(self):
        result = dict(SEARCH, truncated=True, next_cursor="c2", uncertain_count=3, matching_count_exact=False)
        shown = model_result("angelo_scene_search", result)
        for key in ("truncated", "next_cursor", "uncertain_count", "matching_count_exact", "page", "observation_token",
                    "document_epoch", "objects"):
            self.assertEqual(shown[key], result[key])

    def test_a_script_keeps_undo_output_result_changes_and_its_after_objects(self):
        rows = {"scene_settings": {"handle": "s"}, "objects": [{"handle": "h", "object_revision": "r"}], "omitted": 2}
        script = dict(SCRIPT, after=rows, output_omitted_characters=10)
        for follows in (False, True):
            shown = model_result("angelo_execute_python", copy.deepcopy(script), observation_follows=follows)
            for key in ("undo", "output", "result", "output_omitted_characters", "success"):
                self.assertEqual(shown[key], script[key])
            self.assertEqual(shown["after"]["objects"], rows["objects"])
            self.assertEqual(shown["after"]["omitted"], 2)
        self.assertEqual(script["after"], rows, "The raw result is never modified")

    def test_codex_reads_a_script_with_its_scene_count_when_no_observation_follows(self):
        # ``verified`` stays for Codex, where no observation restates it, and leaves only when a turn-context
        # observation follows in the same message.
        shown = model_result("angelo_execute_python", copy.deepcopy(SCRIPT))
        self.assertEqual(shown["verified"], SCRIPT["verified"])
        self.assertEqual(shown["changes"]["object_count_after"], SCRIPT["changes"]["object_count_after"])
        self.assertNotIn("verified", model_result("angelo_execute_python", copy.deepcopy(SCRIPT), observation_follows=True))

    def test_plan_results_never_reorder_or_drop_rows(self):
        operations = [{"tool": "angelo_scene_search"}, {"tool": "angelo_execute_python"}, {"tool": "angelo_view"}]
        rows = [copy.deepcopy(SEARCH), copy.deepcopy(SCRIPT), {"views": [{"id": "v"}], "native_exec_ms": 2}]
        shown = plan_results(operations, {"success": True, "operations": rows}, observation_follows=True)
        self.assertEqual(len(shown["operations"]), 3)
        self.assertEqual(shown["operations"][2], {"views": [{"id": "v"}]})
        self.assertEqual(rows[0], SEARCH)


if __name__ == "__main__":
    unittest.main()
