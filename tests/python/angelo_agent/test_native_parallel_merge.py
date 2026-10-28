# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The lead's merge and its wait for workers, without Blender (``worker_merge``, ``worker_sessions``, ``worker_spec``).

Pinned here: merge arguments come back in their own JSON shapes (validating twice changes nothing) and refuse what is
not a placement or a swap; the approval binds the exact saved part files and the placements; a busy worker, an
unknown worker and a missing file are refused before anything runs; the merge is the lead's alone (never a connected
app's edit level); a merge admits a worker only by the status wait's own predicate (its brief reached it and its turn
ended, break-it E2E-2 M2); a status wait is deferred, answers once any worker becomes done or after its timeout (more
in ``test_native_worker_queue``), and a Stop ends the wait only; Auto starts at most ``MAX_AUTO_STARTS`` parts per request without a card (E2E-2 M3); a live
part is never read while its worker answers; a placement leaves a Child Of follower to its target only when that
alone carries it exactly (break-it E2E-10); a placement's material swap is made only where a node sets the
material, and checked element by element (break-it P2-3). The in-Blender proofs are
``test_blender_parallel_merge.py`` and ``test_blender_parallel_merge_materials.py``.
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
from angelo_agent import (document_scene, worker_material_check, worker_materials, worker_merge,  # noqa: E402,F401
                          worker_placement, worker_sessions, worker_spec)
from angelo_agent.refusal_codes import RefusalError  # noqa: E402

MERGE, STATUS = worker_spec.PREFIX + "merge", worker_spec.PREFIX + "status"
AWAITING = worker_sessions.awaiting_person   # The real one (WaitTests patch the module's name).


def chat(busy=False, pending=None, messages=None):
    return SimpleNamespace(busy=busy, pending=pending or {}, state={"id": "w", "messages": messages or []})


class MergeArgumentTests(unittest.TestCase):
    def test_arguments_keep_their_json_shapes_and_validate_again_unchanged(self):
        clean = worker_spec.validate_worker(MERGE, {"parts": [
            {"worker": 1, "placements": [{"location": [1, 2, 3]},
                                         {"materials": {"Oak": "Walnut", "Paint": {"name": "Blue", "base_color": [0, 0, 1]}}}]},
            {"worker": 2}]})
        self.assertEqual(clean["parts"][0]["placements"][0], {"location": [1.0, 2.0, 3.0], "rotation": [0.0, 0.0, 0.0],
                                                             "scale": [1.0, 1.0, 1.0], "materials": {}})
        self.assertEqual(clean["parts"][0]["placements"][1]["materials"]["Paint"],
                         {"name": "Blue", "base_color": [0.0, 0.0, 1.0, 1.0]})
        self.assertEqual(clean["parts"][1]["placements"], [{"location": [0.0, 0.0, 0.0], "rotation": [0.0, 0.0, 0.0],
                                                            "scale": [1.0, 1.0, 1.0], "materials": {}}])
        self.assertEqual(worker_spec.validate_worker(MERGE, clean), clean)

    def test_what_is_not_a_part_a_placement_or_a_swap_is_refused(self):
        for parts in ([], [{"worker": "1"}], [{"worker": 1}, {"worker": 1}], [{"worker": 1, "extra": 1}],
                      [{"worker": 1, "placements": [{"location": [0, 0]}]}],
                      [{"worker": 1, "placements": [{"scale": [0, 1, 1]}]}],
                      [{"worker": 1, "placements": [{"location": [float("nan"), 0, 0]}]}],
                      [{"worker": 1, "placements": [{"materials": {"Oak": {"base_color": [1, 0, 0]}}}]}],
                      [{"worker": 1, "placements": [{"materials": {"Oak": {"name": "X", "base_color": [2, 0, 0]}}}]}],
                      [{"worker": 1, "placements": [{"materials": {"Oak": 3}}]}]):
            with self.subTest(parts=parts), self.assertRaises(ValueError):
                worker_spec.validate_worker(MERGE, {"parts": parts})

    def test_the_merge_is_the_leads_never_a_connected_apps(self):
        from angelo_agent import external_clients
        self.assertNotIn(MERGE, external_clients.EDIT_TOOLS)
        self.assertIn(MERGE, worker_spec.WORKER_NAMES)


class MergeBindingTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.path = self.folder / "part.blend"
        self.path.write_bytes(b"BLENDER")

    def bind(self, parts, arguments=None):
        return worker_merge.precondition(None, arguments or {"parts": [{"worker": 1}]}, parts)

    def test_the_binding_is_the_saved_file_and_the_placements(self):
        first = self.bind({1: (chat(), self.path)})
        self.assertEqual(first, self.bind({1: (None, self.path)}))   # An ended worker's file merges too.
        moved = self.bind({1: (chat(), self.path)}, {"parts": [{"worker": 1, "placements": [{"location": [1, 0, 0]}]}]})
        self.assertNotEqual(first, moved)
        stat = os.stat(self.path)
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10 ** 9))
        self.assertNotEqual(first, self.bind({1: (chat(), self.path)}))

    def test_a_busy_worker_an_unknown_one_and_a_missing_file_are_refused(self):
        for parts, code in (({1: (chat(busy=True), self.path)}, "WRITER_BUSY"),
                            ({1: (chat(pending={"c": 1}), self.path)}, "WRITER_BUSY"),
                            ({2: (chat(), self.path)}, "VALIDATION"),
                            ({1: (chat(), self.folder / "missing.blend")}, "VALIDATION")):
            with self.subTest(code=code), self.assertRaises(RefusalError) as caught:
                self.bind(parts)
            self.assertEqual(caught.exception.code, code)


class MergeAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "part.blend"
        self.path.write_bytes(b"BLENDER")

    def admit(self, worker, done, asking=False):
        with patch.object(worker_sessions, "finished", lambda session: done), \
                patch.object(worker_sessions, "awaiting_person", lambda session: asking):
            return worker_merge.precondition(None, {"parts": [{"worker": 1}]}, {1: (worker, self.path)},
                                             worker_sessions.merge_blocker)

    def test_only_a_worker_whose_brief_reached_it_and_whose_turn_ended_merges(self):
        for messages, done, why in (([], False, "brief"),
                                    ([{"role": "user", "text": "brief"}], False, "still working")):
            with self.subTest(why=why), self.assertRaises(RefusalError) as caught:
                self.admit(chat(messages=messages), done)
            self.assertEqual(caught.exception.code, "WRITER_BUSY")
            self.assertIn(why, str(caught.exception))
        self.assertEqual(self.admit(chat(messages=[{"role": "user", "text": "brief"}]), True)[0], "merge")
        with self.assertRaises(RefusalError) as caught:
            self.admit(chat(messages=[{"role": "user", "text": "brief"}]), False, asking=True)
        self.assertIn("approval", str(caught.exception))

    def test_a_worker_that_never_started_does_not_merge_as_done(self):
        worker = chat(messages=[{"role": "user", "text": "brief"}])
        with patch.dict(worker_sessions._parts, {"w": (1, "Rug", self.path)}), \
                patch.dict(worker_sessions._started, {"lead-x": ["w"]}), \
                patch.dict(worker_sessions.failures, {"lead-x/1": "Stopped before the message was sent"}), \
                self.assertRaises(RefusalError) as caught:
            self.admit(worker, True)
        self.assertIn("never started", str(caught.exception))

    def test_the_merge_tool_uses_that_predicate(self):
        from angelo_agent import worker_routing
        lead = SimpleNamespace(state={"id": "lead"})
        with patch.object(worker_routing, "worker_snapshots", lambda controller: False), \
                patch.object(worker_sessions, "parts_of", lambda controller: {1: (chat(), self.path)}), \
                patch.object(worker_sessions, "finished", lambda session: False), \
                self.assertRaises(RefusalError) as caught:
            worker_sessions.precondition(lead, MERGE, {"parts": [{"worker": 1}]})
        self.assertIn("brief", str(caught.exception))


class MergedOnceTests(unittest.TestCase):
    """A worker's part merges once while the scene records its merge (phase 1's live Sol trial on combined, 2026-10-25:
    a second merge of one worker added a second copy), unless the merge says ``again``; an Undo of the merge allows
    it."""

    def test_a_part_in_the_file_is_refused_again_until_its_merge_is_undone(self):
        from angelo_agent import parallel_sessions
        path = Path(tempfile.mkdtemp()) / "part.blend"
        path.write_bytes(b"BLENDER")
        scene, present = {}, [True]   # The lead's document scene, and whether its merge record is in the file.
        with patch.object(parallel_sessions, "_in_file", lambda lead: frozenset(
                scene.get(parallel_sessions.PART_KEY, {}) if present[0] else ())):
            admit = lambda: worker_merge.precondition(None, {"parts": [{"worker": 1}]}, {1: (None, path)})  # noqa: E731
            self.assertEqual(admit()[0], "merge")
            parallel_sessions.parts_merged([path], scene)   # The scene records the part, inside the merge's Undo step.
            self.assertEqual(scene, {parallel_sessions.PART_KEY: {parallel_sessions._part_key(path): 1}})
            self.assertNotIn(str(path.parent), str(scene))   # No path of the person's in their file.
            parallel_sessions.dismiss("worker-chat")   # A dismissed tile forgets nothing here.
            with self.assertRaises(RefusalError) as caught:
                admit()
            self.assertIn("merged into this scene before", str(caught.exception))
            self.assertIn('sets this part\'s "again"', str(caught.exception))
            again = worker_merge.precondition(None, {"parts": [{"worker": 1, "again": True}]}, {1: (None, path)})
            self.assertEqual(again[0], "merge")   # The merge's own word places it anew (break-it E2E-20 R4-M-2),
            self.assertNotEqual(again[2], worker_merge._canonical(worker_merge.validate({"parts": [{"worker": 1}]})))
            # and its approval binds that word.
            with self.assertRaises(ValueError):
                worker_merge.validate({"parts": [{"worker": 1, "again": "yes"}]})
            present[0] = False   # Undo took the collection out.
            self.assertEqual(admit()[0], "merge")


class AgainSeenTests(unittest.TestCase):
    """A merge whose ``again`` places a part merged into this scene before is seen by the person: its card says so and
    Auto asks for it instead of running it (break-it E2E-20 R5-M-2). ``again`` on a part never merged is a plain
    merge."""

    def test_the_card_says_a_part_merged_before_gets_another_copy(self):
        from angelo_agent import worker_approval
        facts = {1: {"name": "Board", "objects": 65, "merged_before": True},
                 2: {"name": "Pawn", "objects": 1, "merged_before": False}}
        lines = worker_approval.merge_message({"parts": [{"worker": 1, "again": True}, {"worker": 2, "again": True}]},
                                              facts)["body"].splitlines()
        self.assertIn("merged into this scene before", lines[0])
        self.assertNotIn("before", lines[1])   # Never merged: a plain merge.
        plain = worker_approval.merge_message({"parts": [{"worker": 1}]}, facts)["body"]
        self.assertNotIn("before", plain)   # Without again the merge is refused anyway; the card claims nothing.

    def test_auto_asks_for_again_on_a_merged_part_only(self):
        from angelo_agent import parallel_sessions, worker_sessions
        paths = {1: Path("/w/1/part.blend"), 2: Path("/w/2/part.blend")}
        lead = SimpleNamespace(state={"id": "lead"})
        with patch.object(worker_sessions, "parts_of", lambda chat: {n: (None, p) for n, p in paths.items()}), \
                patch.object(parallel_sessions, "part_in_file", lambda path, chat: path == paths[1] and chat is lead):
            allowed = lambda parts: worker_merge.auto_merge_allowed(lead, {"parts": parts})  # noqa: E731
            self.assertFalse(allowed([{"worker": 1, "again": True}]))
            self.assertFalse(allowed([{"worker": 2}, {"worker": 1, "again": True}]))
            self.assertTrue(allowed([{"worker": 2, "again": True}]))   # Never merged: Auto runs it as any merge.
            self.assertTrue(allowed([{"worker": 1}]))   # No again: the merge's own admission refuses it.
            self.assertTrue(allowed([{"worker": 7, "again": True}]))   # Not this lead's: its admission refuses it.

    def test_the_card_reads_its_own_leads_workers(self):
        # Each lead numbers its workers from 1: the card of one lead's merge never reads another's worker 1, or loses
        # its own part's name and "merged before" to a number they share (break-it E2E-20 final F-M-1).
        from angelo_agent import live_view, parallel_sessions, worker_approval, worker_sessions
        parts = {"a1": (1, "Board", Path("/w/a/1/part.blend")), "b1": (1, "Rook", Path("/w/b/1/part.blend"))}
        with patch.object(worker_sessions, "_parts", parts), \
                patch.object(worker_sessions, "_started", {"lead-a": ["a1"], "lead-b": ["b1"]}), \
                patch.object(live_view, "summary", lambda session: {}), \
                patch.object(parallel_sessions, "part_in_file", lambda path, lead: path == parts["a1"][2]):
            facts = {lead: worker_approval.merge_facts(lead) for lead in ("lead-a", "lead-b")}
            self.assertEqual((facts["lead-a"][1]["name"], facts["lead-a"][1]["merged_before"]), ("Board", True))
            self.assertEqual((facts["lead-b"][1]["name"], facts["lead-b"][1]["merged_before"]), ("Rook", False))
            self.assertNotIn(1, worker_approval.merge_facts())   # Without its chat a shared number names no part.
        source = (Path(worker_merge.__file__).parent / "approvals.py").read_text(encoding="utf-8")
        self.assertIn("worker_approval.merge_message(args, worker_approval.merge_facts(chat_id))", source)

    def test_the_auto_decision_reads_it(self):
        source = (Path(worker_merge.__file__).parent / "controller.py").read_text(encoding="utf-8")
        self.assertIn('if call.name.endswith("start") else worker_merge.auto_merge_allowed\n'
                      '            return allowed(self, call.arguments)', source)


class Scene:
    """A Blender scene: its name, session id and custom properties (the merge's record lives in them)."""

    def __init__(self, name, uid):
        self.name, self.session_uid, self.record = name, uid, {}

    def get(self, key, default=None):
        return self.record.get(key, default)

    def __setitem__(self, key, value):
        self.record[key] = value


class LeadSceneTests(unittest.TestCase):
    """A merge lands in its lead's own scene, and only while the window shows it; "this scene" is the lead's own scene
    whichever scene the window shows (break-it E2E-20 R5-M-1; the maintainer, 2026-10-25: selecting a session only
    changes what is displayed)."""

    def setUp(self):
        from angelo_agent import parallel_sessions
        self.scenes = {101: Scene("Scene", 101), 202: Scene("Kitchen", 202)}
        self.lead = SimpleNamespace(state={"id": "lead"}, document=(7, 101), document_invalid=False)
        self.path = Path(tempfile.mkdtemp()) / "part.blend"
        self.path.write_bytes(b"BLENDER")
        self.bpy = SimpleNamespace(app=SimpleNamespace(background=False), data=SimpleNamespace(
            scenes=list(self.scenes.values()), libraries=SimpleNamespace(load=self.load)))
        self.loads = []
        for patcher in (patch.dict(sys.modules, {"bpy": self.bpy}),
                        patch.object(parallel_sessions, "_present", [None, frozenset()])):
            patcher.start()
            self.addCleanup(patcher.stop)

    def load(self, *args, **kwargs):
        self.loads.append(args)
        raise AssertionError("a merge that is refused reads no part")

    def window(self, uid):
        return SimpleNamespace(scene=self.scenes[uid], view_layer="layer")

    def test_merged_reads_the_leads_own_scene_whichever_is_shown(self):
        from angelo_agent import parallel_sessions
        parallel_sessions.parts_merged([self.path], self.scenes[101])
        kitchen = SimpleNamespace(state={"id": "kitchen-lead"}, document=(7, 202), document_invalid=False)
        for _ in range(2):   # Read alternately in one file generation: each lead its own scene, never a cached other.
            self.assertTrue(parallel_sessions.part_in_file(self.path, self.lead))
            self.assertFalse(parallel_sessions.part_in_file(self.path, kitchen))
        with self.assertRaises(RefusalError):
            parallel_sessions.refuse_merged_again(1, self.path, self.lead)
        parallel_sessions.refuse_merged_again(1, self.path, kitchen)   # Never merged into the kitchen.
        gone = SimpleNamespace(state={"id": "gone"}, document=(7, 999), document_invalid=False)
        self.assertFalse(parallel_sessions.part_in_file(self.path, gone))   # Its scene is not in the file now.

    def test_a_merge_lands_only_in_its_leads_scene_while_the_window_shows_it(self):
        worker_merge.refuse_away(self.lead, self.window(101))
        for lead, uid in ((self.lead, 202), (SimpleNamespace(state={"id": "gone"}, document=(7, 999)), 101)):
            with self.subTest(shown=uid), self.assertRaises(RefusalError) as caught:
                worker_merge.refuse_away(lead, self.window(uid))
            self.assertEqual(caught.exception.code, "STALE_CONTEXT")
        self.assertIn("scene 'Scene'; the main window shows scene 'Kitchen'", str(self._refusal(self.lead, 202)))
        with self.assertRaises(RefusalError):   # The merge itself, by any route: nothing read, nothing appended.
            worker_merge.merge(self.lead, self.window(202), {"parts": [{"worker": 1}]}, {1: (None, self.path)},
                               lambda number: "Part")
        self.assertEqual(self.loads, [])

    def _refusal(self, lead, uid):
        try:
            worker_merge.refuse_away(lead, self.window(uid))
        except RefusalError as error:
            return error
        return None

    def test_the_admission_refuses_while_the_main_window_shows_another_scene(self):
        import angelo_agent
        shown = [(7, 202)]
        ui = SimpleNamespace(document_key=lambda: shown[0], main_window=lambda: self.window(shown[0][1]))
        with patch.dict(sys.modules, {"angelo_agent.ui": ui}), patch.object(angelo_agent, "ui", ui, create=True):
            with self.assertRaises(RefusalError) as caught:
                worker_merge.precondition(self.lead, {"parts": [{"worker": 1}]}, {1: (None, self.path)})
            self.assertEqual(caught.exception.code, "STALE_CONTEXT")
            self.assertIn("'Kitchen'", str(caught.exception))
            shown[0] = (7, 101)   # The lead's tile brought its scene back.
            self.assertEqual(worker_merge.precondition(self.lead, {"parts": [{"worker": 1}]},
                                                       {1: (None, self.path)})[0], "merge")
            shown[0] = (8, 303)   # Another file: the chat's own invalidation decides, not this check.
            self.lead.document_invalid = True
            self.assertEqual(worker_merge.precondition(self.lead, {"parts": [{"worker": 1}]},
                                                       {1: (None, self.path)})[0], "merge")

    def test_the_merge_tool_passes_its_lead(self):
        source = (Path(worker_merge.__file__).parent / "worker_sessions.py").read_text(encoding="utf-8")
        self.assertIn("worker_merge.merge(lead, window, arguments, parts,", source)


class LastTurnTests(unittest.TestCase):
    def test_only_a_turn_that_ended_in_an_error_or_a_stop_before_any_reply_is_failed(self):
        def worker(rows, cancelled=False):
            return SimpleNamespace(state={"messages": rows}, cancelled=cancelled)
        brief = {"role": "user", "text": "brief"}
        cases = (([brief, {"role": "error", "text": "boom"}], False, True),
                 ([brief, {"role": "error", "text": "boom"}, {"role": "assistant", "text": "done"}], False, False),
                 ([brief, {"role": "assistant", "text": "done"}], True, False),   # Stop pressed once it was idle.
                 ([brief], True, True))
        for rows, cancelled, failed in cases:
            with self.subTest(rows=[r["role"] for r in rows], cancelled=cancelled):
                self.assertEqual(worker_sessions._last_turn_failed(worker(rows, cancelled)), failed)

    def test_the_last_receipt_decides_once_there_is_one(self):
        streamed = [{"role": "user", "text": "brief"}, {"role": "assistant", "text": "I'll start with the base."}]
        for status, failed in (("completed", False), ("interrupted", True), ("failed", True)):
            store = SimpleNamespace(receipts=[{"session_id": "w", "status": "completed"},
                                              {"session_id": "other", "status": "failed"},
                                              {"session_id": "w", "status": status}])
            chat = SimpleNamespace(state={"id": "w", "messages": streamed}, cancelled=False, telemetry=store)
            with self.subTest(status=status):   # A stop after a streamed line counts (break-it E2E-5 M-3).
                self.assertEqual(worker_sessions._last_turn_failed(chat), failed)


class AutoStartTests(unittest.TestCase):
    def test_auto_starts_up_to_the_cap_per_request_then_asks(self):
        lead = SimpleNamespace(state={"id": "lead-cap", "messages": [{"role": "user", "text": "Build a tea set"}]})
        parts = lambda count: {"parts": [{"name": "p", "brief": "b"}] * count}   # noqa: E731
        with patch.dict(worker_sessions._requests, clear=True):
            self.assertTrue(worker_sessions.auto_start_allowed(lead, parts(worker_sessions.MAX_AUTO_STARTS)))
            worker_sessions._count_starts(lead, worker_sessions.MAX_AUTO_STARTS - 1)
            self.assertTrue(worker_sessions.auto_start_allowed(lead, parts(1)))
            self.assertFalse(worker_sessions.auto_start_allowed(lead, parts(2)))
            lead.state["messages"].append({"role": "user", "text": "Add cups"})   # The person's next message.
            self.assertTrue(worker_sessions.auto_start_allowed(lead, parts(2)))

    def test_the_controllers_auto_decision_asks_past_the_cap(self):
        from angelo_agent.controller import Controller
        lead = SimpleNamespace(state={"id": "lead-cap2", "options": {}}, _request_message_index=1)
        start = lambda count: SimpleNamespace(name="angelo_workers_start", arguments={"parts": [{}] * count})  # noqa
        with patch.dict(worker_sessions._requests, clear=True):
            self.assertTrue(Controller._auto_bounded(lead, start(1), "AUTO_BOUNDED"))
            self.assertFalse(Controller._auto_bounded(lead, start(worker_sessions.MAX_AUTO_STARTS + 1), "AUTO_BOUNDED"))
            self.assertFalse(Controller._auto_bounded(lead, start(1), "ASK"))
            self.assertTrue(Controller._auto_bounded(lead, SimpleNamespace(name="angelo_workers_merge", arguments={}),
                                                     "AUTO_BOUNDED"))


class WaitTests(unittest.TestCase):
    def setUp(self):
        self.lead = SimpleNamespace(state={"id": "lead"})
        patcher = patch.dict(worker_sessions._started, {"lead": ["w1", "w2"]})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.finished = {"w1": False, "w2": False}
        self.asking = set()
        person = patch.object(worker_sessions, "awaiting_person", lambda session: session in self.asking)
        person.start()
        self.addCleanup(person.stop)

    def test_the_wait_keeps_waiting_while_a_worker_still_waits_for_the_person(self):
        self.asking.add("w2")   # The person sees its card on its tile; answering at once only costs a round.
        with patch.object(worker_sessions, "finished", lambda session: self.finished[session]), \
                patch.object(worker_sessions, "status", lambda lead: {"workers": ["rows"]}):
            waiting = worker_sessions._wait(self.lead, 60)   # It already waited for the person as this wait began.
            self.assertIsNone(waiting.poll())
            self.finished["w2"] = True
            self.assertFalse(waiting.poll()["timed_out"])

    def test_a_card_the_chat_runs_itself_is_not_waiting_for_the_person(self):
        import angelo_agent
        card = SimpleNamespace(approval=True, executing=False)
        chat = SimpleNamespace(pending={"c": card}, _automatic_script=lambda call: False,
                               _auto_bounded=lambda call: self.auto)
        ui = SimpleNamespace(_controllers={"w9": chat})   # Angelo's ui module, which needs Blender.
        with patch.dict(sys.modules, {"angelo_agent.ui": ui}), patch.object(angelo_agent, "ui", ui, create=True):
            self.auto = True    # Auto runs this bounded edit by itself (a card shown for a moment while it does).
            self.assertFalse(AWAITING("w9"))
            self.auto = False   # Ask: only the person answers it.
            self.assertTrue(AWAITING("w9"))

    def test_the_wait_is_deferred_and_answers_once_any_worker_is_done(self):
        with patch.object(worker_sessions, "finished", lambda session: self.finished[session]), \
                patch.object(worker_sessions, "status", lambda lead: {"workers": ["rows"]}):
            waiting = worker_sessions._wait(self.lead, 60)
            self.assertIsNone(waiting.poll())
            self.finished["w1"] = True   # Latency proposal 1c: merge and review it while the other builds.
            answer = waiting.poll()
        self.assertEqual(answer["workers"], ["rows"])
        self.assertFalse(answer["timed_out"])
        self.assertGreater(waiting.timeout, 60)   # The poll answers before the call's own expiry.

    def test_the_wait_ends_at_its_timeout_and_says_so(self):
        with patch.object(worker_sessions, "finished", lambda session: False), \
                patch.object(worker_sessions, "status", lambda lead: {"workers": []}):
            waiting = worker_sessions._wait(self.lead, 0.01)
            time.sleep(0.02)
            self.assertTrue(waiting.poll()["timed_out"])

    def test_a_stop_ends_the_wait_and_never_the_workers(self):
        with patch.object(worker_sessions, "status", lambda lead: {"workers": []}), \
                patch.object(worker_sessions, "finished", lambda session: False):
            waiting = worker_sessions._wait(self.lead, 60)
        self.assertEqual(waiting.stopped_result["error_code"], "CANCELLED")
        self.assertIn("workers carry on", waiting.stopped_result["error"])

    def test_status_takes_wait_and_a_bounded_timeout(self):
        self.assertEqual(worker_spec.validate_worker(STATUS, {}), {})
        self.assertEqual(worker_spec.validate_worker(STATUS, {"wait": True}),
                         {"wait": True, "timeout_seconds": worker_spec.WAIT_DEFAULT})
        for bad in ({"wait": "yes"}, {"wait": True, "timeout_seconds": 0}, {"wait": True, "timeout_seconds": 10 ** 6},
                    {"other": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                worker_spec.validate_worker(STATUS, bad)


class AnnouncementTests(unittest.TestCase):
    """A Codex model sees no deferred tool, so a lead learns its workers exist only from its instructions."""

    def test_only_a_lead_hears_of_its_workers_and_only_while_they_are_on(self):
        from angelo_agent import agent_guidance, parallel_access, worker_routing
        lead, worker = SimpleNamespace(state={"id": "lead"}), SimpleNamespace(state={"id": "worker"})
        with patch.object(worker_routing, "worker_snapshots", lambda chat: chat is worker):
            with patch.object(parallel_access, "enabled", lambda: False):
                self.assertEqual(agent_guidance.lead_guidance(lead), "")   # Off: byte-identical requests.
            with patch.object(parallel_access, "enabled", lambda: True):
                self.assertEqual(agent_guidance.lead_guidance(lead), worker_spec.WORKER_ANNOUNCEMENT)
                self.assertEqual(agent_guidance.lead_guidance(SimpleNamespace(host=lead)), worker_spec.WORKER_ANNOUNCEMENT)
                self.assertEqual(agent_guidance.lead_guidance(worker), worker_spec.WORKER_BRIEFING)   # Its own, fixed.
            with patch.object(parallel_access, "enabled", lambda: True):
                # A request that does not offer the family (an app's task, voice): nothing either.
                self.assertEqual(agent_guidance.lead_guidance(SimpleNamespace(host=lead, allowed_tools=frozenset())), "")
                self.assertEqual(agent_guidance.lead_guidance(SimpleNamespace(
                    host=lead, allowed_tools=frozenset({"angelo_workers_start"}))), worker_spec.WORKER_ANNOUNCEMENT)
        for name in ("angelo_workers_start", "angelo_workers_status", "angelo_workers_merge"):
            self.assertIn(name, worker_spec.WORKER_ANNOUNCEMENT)
        providers = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/providers.py").read_text()
        self.assertIn('("" if voice else lead_guidance(self.controller))', providers)   # Never a voice thread's.

    def test_both_runtimes_append_it(self):
        for module, call in (("providers.py", "lead_guidance(self.controller))"), ("claude_transport.py",
                                                                                  "lead_guidance(context)")):
            self.assertIn(call, (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
                                 / module).read_text(encoding="utf-8"))


class LiveReadTests(unittest.TestCase):
    def test_a_part_waits_while_its_worker_answers_an_edit_and_the_others_are_read(self):
        from angelo_agent import parallel_sessions
        with patch.dict(parallel_sessions.pending, {"a": 1.0, "b": 2.0}, clear=True), \
                patch.object(parallel_sessions, "_answering", lambda session: session == "a"), \
                patch("angelo_agent.live_schedule.choose", lambda ready, *rest: sorted(ready)[0] if ready else None), \
                patch.object(parallel_sessions, "reload", lambda session: session):
            self.assertEqual(parallel_sessions.pump(now=10.0, active=None, holding=False), "b")


class CarriedTests(unittest.TestCase):
    """A follower a placement reaches through its target alone, as a child through its parent, is never moved itself:
    exactly one acting Child Of of the part, every channel, full influence, world spaces, nothing reading the part
    acting before it (``worker_placement.carried``). Any other is moved as a root and measured."""

    @staticmethod
    def child_of(target, **changes):
        fields = dict(type="CHILD_OF", target=target, mute=False, influence=1.0, owner_space="WORLD",
                      target_space="WORLD", **{name: True for name in worker_placement.CHILD_OF_ALL})
        return SimpleNamespace(**{**fields, **changes})

    def test_only_a_follower_its_target_alone_carries_exactly_is_left_to_it(self):
        part, outside = object(), object()
        carried = lambda *stack: worker_placement.carried(SimpleNamespace(constraints=list(stack)), {part})  # noqa
        limit = SimpleNamespace(type="LIMIT_LOCATION", mute=False, influence=1.0)
        track = SimpleNamespace(type="TRACK_TO", target=part, mute=False, influence=1.0)
        self.assertTrue(carried(self.child_of(part)))
        self.assertTrue(carried(limit, self.child_of(part), track))   # Before it, nothing reads the part.
        self.assertTrue(carried(self.child_of(part), self.child_of(part, mute=True)))
        for stack in ((), (track, self.child_of(part)), (self.child_of(part), self.child_of(part)),
                      (self.child_of(part, influence=0.5),), (self.child_of(part, use_rotation_z=False),),
                      (self.child_of(part, owner_space="LOCAL"),), (self.child_of(part, target_space="POSE"),),
                      (self.child_of(outside),), (self.child_of(part, mute=True),)):
            self.assertFalse(carried(*stack), stack)

class MoverTests(unittest.TestCase):
    """A mover's miss at the merge frame is its drivers' only when they set its transform; keys act on nothing there,
    whatever channel they are on, and a rigid body or a copy Blender did not evaluate is never explained by them
    (``worker_placement._drivers_explain``, break-it E2E-13 H-1, M-1). Whether the rest of its world is where the
    placement puts it (E2E-14 M-1, L-1) needs Blender's matrices: ``test_blender_parallel_merge`` pins that."""

    def test_keys_a_rigid_body_or_an_unevaluated_copy_never_explain_a_miss(self):
        class Mover:   # Hashable by identity, as Blender's objects are.
            def __init__(self, paths=(), rigid_body=None):
                drivers = [SimpleNamespace(data_path=path, mute=False) for path in paths]
                self.animation_data, self.constraints = SimpleNamespace(drivers=drivers), []
                self.rigid_body, self.parent = rigid_body, None
        mover = Mover
        explain = worker_placement._drivers_explain
        copy = SimpleNamespace(as_pointer=lambda: 1, evaluated_get=lambda depsgraph: copy)   # Not evaluated.
        for obj in (mover(), mover(['["spin"]']), mover(["rotation_euler"], rigid_body=object())):
            self.assertFalse(explain(obj, copy, None, {obj: None}, object(), set(), 0.0))
        driven = mover(["rotation_euler"])
        self.assertFalse(explain(driven, copy, None, {driven: None}, object(), set(), 0.0))
        self.assertFalse(explain(driven, copy, None, {}, object(), set(), 0.0))   # Never measured as built.


class SwapTests(unittest.TestCase):
    """Where a material value goes in a node group decides whether a placement can swap it there (``worker_materials``,
    break-it P2-3): only to a node that sets it, through reroutes, switches and nested groups; a node that reads it is
    refused. And the evaluated check: the swap's material exactly where the part's was, nothing else changed. The
    in-Blender proof is ``test_blender_parallel_merge_materials.py``."""

    @staticmethod
    def node(kind, inputs=(), outputs=1, **fields):
        node = SimpleNamespace(bl_idname=kind, mute=False, internal_links=[], node_tree=None, **fields)
        node.inputs = [SimpleNamespace(identifier=name, links=[], node=node) for name in inputs]
        node.outputs = [SimpleNamespace(identifier="Output_%d" % i, links=[], node=node) for i in range(outputs)]
        return node

    @staticmethod
    def link(socket, node, name):
        target = next(s for s in node.inputs if s.identifier == name)
        socket.links.append(SimpleNamespace(is_valid=True, is_muted=False, to_node=node, to_socket=target))

    def kinds(self, consumer, name, via=()):
        material = self.node("GeometryNodeInputMaterial")
        socket = material.outputs[0]
        for step in via:   # Reroutes and switches between.
            self.link(socket, step, step.inputs[0].identifier)
            socket = step.outputs[0]
        self.link(socket, consumer, name)
        return worker_materials._from_output(None, material.outputs[0], 0)

    def test_a_material_set_through_reroutes_switches_and_groups_is_swappable(self):
        sets = self.node("GeometryNodeSetMaterial", ["Geometry", "Material"], outputs=1)
        self.assertEqual(self.kinds(sets, "Material"), {"set"})
        via = [self.node("NodeReroute", ["Input"]), self.node("GeometryNodeSwitch", ["False"])]
        self.assertEqual(self.kinds(sets, "Material", via), {"set"})
        replace = self.node("GeometryNodeReplaceMaterial", ["Geometry", "Old", "New"])
        self.assertEqual(self.kinds(replace, "New"), {"set"})
        inner = SimpleNamespace(nodes=[self.node("NodeGroupInput", outputs=0)])
        inner.nodes[0].outputs = [SimpleNamespace(identifier="Socket_1", links=[])]
        self.link(inner.nodes[0].outputs[0], self.node("GeometryNodeSetMaterial", ["Geometry", "Material"]),
                  "Material")
        group = self.node("GeometryNodeGroup", ["Socket_0", "Socket_1"])
        group.node_tree = inner
        self.assertEqual(self.kinds(group, "Socket_1"), {"set"})

    def test_a_material_a_node_reads_is_refused_and_one_a_muted_node_drops_is_unused(self):
        for kind, name in (("GeometryNodeMaterialSelection", "Material"), ("GeometryNodeReplaceMaterial", "Old"),
                           ("NodeGroupOutput", "Socket_0"), ("GeometryNodeEvaluateClosure", "Socket_0")):
            with self.subTest(kind=kind):
                self.assertEqual(self.kinds(self.node(kind, ["Geometry", "Material", "Old", "Socket_0"]), name),
                                 {"read"})
        muted = self.node("GeometryNodeSetMaterial", ["Geometry", "Material"])
        muted.mute = True
        self.assertEqual(self.kinds(muted, "Material"), set())

    def test_the_check_wants_the_swap_exactly_where_the_part_was_and_nothing_else_changed(self):
        import numpy
        class Material:   # Hashable by identity, as Blender's datablocks are.
            def __init__(self, pointer):
                self.as_pointer = lambda: pointer
        source, target = Material(11), Material(22)
        names = {11: "Ebony", 22: "Red", 33: "Oak"}
        before = [("faces", numpy.array([11, 33, 11]), True), ("instance references", numpy.array([0, 1]), False)]
        mismatch = worker_material_check.mismatch
        self.assertIsNone(mismatch(before, [("faces", numpy.array([22, 33, 22]), True), before[1]],
                                   {source: target}, names))
        self.assertEqual(mismatch(before, [("faces", numpy.array([11, 33, 22]), True), before[1]], {source: target},
                                  names), "1 of its faces show Ebony where Red belongs")
        self.assertIn("Oak", mismatch(before, [("faces", numpy.array([22, 22, 22]), True), before[1]],
                                      {source: target}, names))   # Another material changed too.
        self.assertIn("changed in number", mismatch(before, [("faces", numpy.array([22, 33]), True), before[1]],
                                                    {source: target}, names))
        self.assertIn("instance references", mismatch(before, [before[0], ("instance references", numpy.array(
            [1, 0]), False)], {}, names))

if __name__ == "__main__":
    unittest.main()
