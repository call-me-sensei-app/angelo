# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a lead reads of its workers' parts, without Blender (``worker_facts``; ROUNDS-AND-ASSEMBLY, 2026-10-25).

Evidence: a lead (Opus, 3 of 3 live trials) ended its turn with finished parts never merged; status said "done" and
listed the part's objects (the worker's file), and nothing said the part was not in its scene. Pinned here, through
``worker_sessions.status``, the status wait, ``worker_sessions._send`` and ``turn_context.prepare`` as they run: each
status row's ``merged_into_this_scene`` (true only while the merged collection is in the file: an Undo makes it false again)
and ``mergeable`` (the merge's own admission of that part, so every place that says a part can merge agrees with the
merge and with each other: a worker that died after a save reads failed and mergeable, and is listed);
``mergeable_not_in_this_scene`` only while a mergeable part exists (never one still working, one without a saved
file, or one that never started); the wait's answer carries it; the lead's scene observation has it as a state
field, so a delta sends it when it appears or changes, ``null`` once when it goes, and nothing while it is absent; a
running worker's ``elapsed_s`` (from its brief) and ``rounds`` (its chat's model requests in this turn, an Auto
re-run counted on), and a done worker's ``built_in_s`` (brief to its turn's end, its chat's own clock). The
in-Blender proofs are ``test_blender_parallel_lead.py`` and ``test_blender_worker_pool.py`` (a real worker death
after a save).
"""

import json
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_worker_queue import Fixture, Process  # noqa: E402  (its module tables, emptied per test)
from test_native_turn_context import make_window  # noqa: E402
import angelo_agent  # noqa: E402
from angelo_agent import (change_feed, context_ledger, identifiers, ledger_events, parallel_sessions,  # noqa: E402
                          turn_context, worker_merge, worker_queue, worker_routing, worker_sessions, worker_status)

OBSERVED = "worker_parts_mergeable_not_in_this_scene"
LISTED = "mergeable_not_in_this_scene"


class FactsFixture(Fixture):
    """Workers of the lead "lead" with real part files; the lead's file holds the collections in ``present``."""

    def setUp(self):
        super().setUp()
        self.present, self.collections = set(), {}
        self.folder = Path(tempfile.mkdtemp())
        rail = SimpleNamespace(states=lambda: [SimpleNamespace(session=s, kind="done") for s in self.sessions],
                               turn_end=lambda chat: ("completed", "Built it."))
        patches = [mock.patch.dict(sys.modules, {"angelo_agent.session_rail_draw": rail}),
                   mock.patch.object(angelo_agent, "session_rail_draw", rail, create=True),
                   mock.patch.object(parallel_sessions, "_in_file", lambda lead: frozenset(   # Its scene's record.
                       key for uid, key in self.collections.items() if uid in self.present and lead is self.lead)),
                   mock.patch.object(parallel_sessions, "lead", "lead")]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.sessions = []
        change_feed.clear()
        self.addCleanup(change_feed.clear)

    def worker(self, number, name, saved=True):
        """A bound worker chat whose turn ended (done), its part file saved unless ``saved`` is False."""
        session = "w-%s" % uuid.uuid4().hex[:8]   # Never one an earlier test's records name.
        path = self.folder / ("%d" % number) / "part.blend"
        path.parent.mkdir(parents=True)
        if saved:
            path.write_bytes(b"BLENDER")
        chat, _worker = self.running(session, Process(path), number=number)
        worker_sessions._parts[session] = (number, name, path)
        self.sessions.append(session)
        return chat

    def merge(self, chat, uid):
        """The lead merged this part as collection ``uid`` (``worker_merge`` records it so)."""
        path = worker_sessions._parts[chat.state["id"]][2]
        scene = {}
        parallel_sessions.parts_merged([path], scene)   # The document scene records the part, as a digest.
        self.collections[uid] = next(iter(scene[parallel_sessions.PART_KEY]))
        self.present.add(uid)

    def status(self):
        return worker_sessions.status(self.lead)

    def brief(self, chat):
        """The brief reaches the chat through ``worker_sessions._send`` (its send recorded, never made)."""
        self.ui.prepare_controller, self.ui.main_window = (lambda controller: None), (lambda: None)
        chat.timings = {}   # As ``Controller.send`` does, its request's clock starts inside the send.
        chat.send = lambda text, observation=None, recovery_evidence=None: chat.timings.update(started=time.monotonic())
        with mock.patch.object(worker_routing, "deliver", lambda controller, window, text, send, failed: send(
                SimpleNamespace(observation={}, recovery_evidence={}))):
            worker_sessions._send(chat, "Part: Sofa.\n\nA sofa.", "lead/%d" % worker_sessions._parts[
                chat.state["id"]][0])


class UnmergedPartTests(FactsFixture):
    def test_a_done_part_is_listed_until_merged_and_again_after_an_undo(self):
        sofa, lamp = self.worker(1, "Sofa"), self.worker(2, "Lamp")
        answer = self.status()
        self.assertEqual([(row["worker"], row["merged_into_this_scene"]) for row in answer["workers"]], [(1, False), (2, False)])
        self.assertEqual(answer[LISTED], ["1 Sofa", "2 Lamp"])
        self.merge(sofa, 501)
        answer = self.status()
        self.assertEqual([(row["worker"], row["merged_into_this_scene"]) for row in answer["workers"]], [(1, True), (2, False)])
        self.assertEqual(answer[LISTED], ["2 Lamp"])
        self.merge(lamp, 502)
        self.assertNotIn(LISTED, self.status())   # Nothing unmerged: not a byte.
        self.present.discard(501)   # An Undo took the sofa's collection out of the file.
        answer = self.status()
        self.assertEqual((answer["workers"][0]["merged_into_this_scene"], answer[LISTED]), (False, ["1 Sofa"]))

    def test_this_scene_is_the_leads_own_whichever_the_window_shows(self):
        # Status, the list and the observation read the lead's own scene's record (``_in_file(lead)``), never another
        # chat's or the shown scene's (break-it E2E-20 R5-M-1: the person may show another scene meanwhile).
        sofa = self.worker(1, "Sofa")
        self.merge(sofa, 701)
        answer = self.status()
        self.assertEqual((answer["workers"][0]["merged_into_this_scene"], answer.get(LISTED)), (True, None))
        other = SimpleNamespace(state={"id": "other-lead"}, document_invalid=False)
        self.assertFalse(parallel_sessions.part_in_file(worker_sessions._parts[sofa.state["id"]][2], other))
        self.assertTrue(parallel_sessions.part_in_file(worker_sessions._parts[sofa.state["id"]][2], self.lead))

    def test_in_this_scene_follows_the_state_in_each_row(self):
        self.worker(1, "Sofa")
        row = self.status()["workers"][0]
        self.assertEqual(list(row)[:4], ["worker", "name", "state", "merged_into_this_scene"])

    def test_only_finished_parts_with_a_saved_file_count(self):
        working, unsaved, never = self.worker(1, "Sofa"), self.worker(2, "Lamp", saved=False), self.worker(3, "Rug")
        working.busy = True   # Still in its turn.
        worker_sessions.failures["lead/3"] = "the brief was not sent"   # Never started: its merge is refused.
        answer = self.status()
        self.assertNotIn(LISTED, answer)
        self.assertEqual({row["worker"]: row["merged_into_this_scene"] for row in answer["workers"]},
                         {1: False, 2: False, 3: False})
        working.busy = False
        self.assertEqual(self.status()[LISTED], ["1 Sofa"])

    def test_a_worker_that_died_after_a_save_is_failed_and_mergeable_everywhere(self):
        # One rule decides every place (the merge's own admission): its row says failed, with why, and mergeable; the
        # list and the lead's observation name it; the merge takes it (its last save, with a warning). One whose saved
        # file is gone is mergeable nowhere and listed nowhere (the coordinator, 2026-10-25: two facts disagreed).
        saved, gone = self.worker(1, "Sofa"), self.worker(2, "Lamp", saved=False)
        for chat in (saved, gone):
            worker_sessions.died[chat.state["id"]] = "its Blender exited (signal 9)"
            chat.remote_executor = None   # As a confirmed death unbinds it.
        answer = self.status()
        rows = {row["worker"]: row for row in answer["workers"]}
        self.assertEqual([(rows[n]["state"], rows[n]["mergeable"]) for n in (1, 2)], [("failed", True), ("failed", False)])
        self.assertEqual(answer[LISTED], ["1 Sofa"])
        self.assertEqual(turn_context.prepare(make_window([]), "lead").observation[OBSERVED], ["1 Sofa"])
        parts = worker_sessions.parts_of(self.lead)
        self.assertIsNone(worker_merge.part_refusal(1, *parts[1], self.lead, worker_sessions.merge_blocker))
        self.assertIn("no saved part", str(worker_merge.part_refusal(2, *parts[2], self.lead,
                                                                     worker_sessions.merge_blocker)))

    def test_a_part_whose_file_holds_no_objects_is_mergeable_nowhere_and_the_merge_says_why(self):
        # Its file is made when its worker launches: one that died before its first edit is empty. The merge refuses
        # it by name, saying why, with nothing added (the coordinator, 2026-10-25), and so status and the list agree.
        built, empty = self.worker(1, "Sofa"), self.worker(2, "Lamp")
        worker_sessions.died[empty.state["id"]] = "its Blender exited (signal 9)"
        empty.remote_executor = None
        empty_path = str(worker_sessions._parts[empty.state["id"]][2])
        with mock.patch.object(worker_merge, "part_object_count", lambda path: 0 if str(path) == empty_path else 3):
            answer = self.status()
            rows = {row["worker"]: row for row in answer["workers"]}
            self.assertEqual([(rows[n]["state"], rows[n]["mergeable"]) for n in (1, 2)],
                             [("done", True), ("failed", False)])
            self.assertEqual(answer[LISTED], ["1 Sofa"])
            refused = worker_merge.part_refusal(2, *worker_sessions.parts_of(self.lead)[2], self.lead,
                                               worker_sessions.merge_blocker)
        self.assertIn("holds no objects", str(refused))
        self.assertIn("signal 9", str(refused))

    def test_every_rows_mergeable_is_the_merges_own_answer_and_the_list_is_those_rows(self):
        from test_native_worker_queue import item
        working, done, unsaved, never = (self.worker(1, "Sofa"), self.worker(2, "Lamp"), self.worker(3, "Rug", False),
                                         self.worker(4, "Vase"))
        working.busy = True
        worker_sessions.failures["lead/4"] = "the brief was not sent"
        self.merge(done, 601)
        dead = self.worker(5, "Chair")
        worker_sessions.died[dead.state["id"]] = "its Blender exited"
        dead.remote_executor = None
        worker_queue.queue.append(item(self.lead, 6, "Lamp shade"))
        answer, parts = self.status(), worker_sessions.parts_of(self.lead)
        names = {number: name for number, name, _path in worker_sessions._parts.values()}
        for row in answer["workers"]:
            with self.subTest(worker=row["worker"]):
                admitted = row["worker"] in parts and worker_merge.part_refusal(
                    row["worker"], *parts[row["worker"]], self.lead, worker_sessions.merge_blocker) is None
                self.assertEqual(row["mergeable"], admitted)
                self.assertEqual("%d %s" % (row["worker"], names.get(row["worker"])) in answer.get(LISTED, ()),
                                 admitted)
        self.assertEqual(answer[LISTED], ["5 Chair"])

    def test_queued_and_starting_rows_are_not_in_this_scene(self):
        from test_native_worker_queue import item
        self.worker(1, "Sofa")
        worker_queue.queue.append(item(self.lead, 3, "Rug"))
        starting = item(self.lead, 2, "Lamp")
        worker_sessions._launching.append(starting)
        rows = {row["worker"]: (row["state"], row["merged_into_this_scene"]) for row in self.status()["workers"]}
        self.assertEqual(rows, {1: ("done", False), 2: ("starting", False), 3: ("queued", False)})

    def test_the_status_waits_answer_carries_it(self):
        sofa = self.worker(1, "Sofa")
        sofa.busy = True
        waiting = worker_status.wait(self.lead, 60)
        self.assertIsNone(waiting.poll())
        sofa.busy = False   # Its turn ended: the wait answers, listing it as not in this scene.
        answer = waiting.poll()
        self.assertEqual((answer["done_since_wait"], answer[LISTED]), ([1], ["1 Sofa"]))


class ObservationTests(FactsFixture):
    def observe(self, chat="lead"):
        return turn_context.prepare(make_window([]), chat).observation

    def test_the_leads_observation_has_the_line_only_while_a_part_is_not_in_its_scene(self):
        self.assertNotIn(OBSERVED, self.observe())   # No workers: nothing.
        sofa = self.worker(1, "Sofa")
        sofa.busy = True
        self.assertNotIn(OBSERVED, self.observe())   # Still working: nothing.
        sofa.busy = False
        self.assertEqual(self.observe()[OBSERVED], ["1 Sofa"])
        self.assertNotIn(OBSERVED, self.observe("someone-else"))   # Only the lead's own parts, only in its chat.
        self.merge(sofa, 501)
        self.assertNotIn(OBSERVED, self.observe())
        self.present.discard(501)   # Undo.
        self.assertEqual(self.observe()[OBSERVED], ["1 Sofa"])

    def test_a_delta_sends_it_when_it_changes_null_when_it_goes_and_nothing_while_absent(self):
        """The lead's rounds as a Claude context reads them (``Controller.compact_observation``'s path)."""
        blocks = context_ledger.TurnBlocks(None, identifiers=identifiers.IdentifierTable())
        first = blocks.observation(self.observe())
        self.assertNotIn(OBSERVED, first)

        def round_():
            kind, value = blocks.continue_observation(self.observe())
            return kind, (json.dumps(value, separators=(",", ":")) if value is not None else "")
        self.assertEqual(round_(), ("unchanged", ""))   # Absent and was absent: no byte.
        sofa = self.worker(1, "Sofa")
        kind, text = round_()
        listed = {"objects", "unchanged_objects"}   # A delta's object list, whatever else changed.
        self.assertEqual((kind, {k: v for k, v in json.loads(text).items() if k not in listed}),
                         ("delta", {OBSERVED: ["1 Sofa"]}))
        self.assertEqual(round_(), ("unchanged", ""))   # Unchanged since the model saw it.
        self.merge(sofa, 501)
        kind, text = round_()
        self.assertEqual((kind, {k: v for k, v in json.loads(text).items() if k not in listed}),
                         ("delta", {OBSERVED: None}))   # Once, when it goes.
        self.assertEqual(round_(), ("unchanged", ""))


class SplitFactTests(FactsFixture):
    def test_a_running_worker_has_elapsed_seconds_since_its_brief_and_its_rounds(self):
        sofa = self.worker(1, "Sofa")
        sofa.busy = True
        self.assertNotIn("elapsed_s", self.status()["workers"][0])   # Its brief has not reached it.
        before = time.monotonic()
        self.brief(sofa)
        sofa._turn_round_inputs = []
        for tokens in (41000, 43500, 47250):   # Three model requests so far (Claude rounds, Codex calls).
            ledger_events.record_round(sofa, tokens)
        time.sleep(0.15)
        row = self.status()["workers"][0]
        self.assertEqual(row["rounds"], 3)
        self.assertGreaterEqual(row["elapsed_s"], 0.1)
        self.assertLessEqual(row["elapsed_s"], round(time.monotonic() - before, 1) + 0.1)
        self.assertNotIn("built_in_s", row)

    def test_rounds_count_on_across_an_auto_re_run(self):
        # Auto re-runs a request on a stronger model in the same turn (``Controller._after_turn``): its first attempt's
        # rounds left the ledger's per-attempt list, and the turn's request count goes on.
        sofa = self.worker(1, "Sofa")
        sofa.busy, sofa._turn_round_inputs = True, []
        self.brief(sofa)
        for tokens in (41000, 43500):
            ledger_events.record_round(sofa, tokens)
        sofa._turn_round_inputs = []   # The attempt ended (``_commit_ledger``); Auto starts the re-run.
        ledger_events.record_round(sofa, 52000)
        self.assertEqual(self.status()["workers"][0]["rounds"], 3)
        from angelo_agent import controller
        source = Path(controller.__file__).read_text(encoding="utf-8")
        begin = source.index("    def _start_turn(")
        start = source[begin:source.index("        self._worker = threading.Thread(", begin)]
        self.assertIn("self._turn_requests = 0 if user_row else", start)   # Only a person's turn starts it at 0.

    def test_a_done_worker_has_its_brief_to_turn_end_seconds(self):
        sofa = self.worker(1, "Sofa")
        sofa.busy = True
        self.brief(sofa)
        sofa.busy = False   # Its turn ended 3 s after its request started (its chat's clock: ``Controller.timings``).
        sofa.timings["total_ms"] = 3000.0
        row = self.status()["workers"][0]
        self.assertAlmostEqual(row["built_in_s"], 3.0, delta=0.15)
        self.assertNotIn("elapsed_s", row)
        self.assertNotIn("rounds", row)
        del self.ui._controllers[sofa.state["id"]]   # Its chat left Angelo: what it measured stays.
        self.assertAlmostEqual(self.status()["workers"][0]["built_in_s"], 3.0, delta=0.15)

    def test_a_later_message_starts_the_workers_turn_clock_and_count_again(self):
        # A parked worker messaged later reports this turn, never its idle time or its previous turn's count
        # (break-it E2E-20 L-6).
        from angelo_agent import worker_facts
        sofa = self.worker(1, "Sofa")
        sofa.busy, sofa._turn_round_inputs = True, []
        self.brief(sofa)
        for tokens in (41000, 43500, 47250):
            ledger_events.record_round(sofa, tokens)
        worker_facts._briefed[sofa.state["id"]] -= 600.0   # Its brief's turn was ten minutes ago.
        worker_facts.turn_started(sofa)   # The person's message (worker_routing.deliver).
        row = self.status()["workers"][0]
        self.assertLess(row["elapsed_s"], 5.0)
        self.assertEqual(row["rounds"], 0)
        worker_facts.turn_started(SimpleNamespace(state={"id": "not-a-worker"}))   # Not a worker chat: nothing.
        self.assertNotIn("not-a-worker", worker_facts._briefed)

    def test_nothing_is_said_that_was_not_measured(self):
        sofa = self.worker(1, "Sofa")
        sofa.timings = {"started": 5.0}   # No brief noted and no turn end: no number.
        row = self.status()["workers"][0]
        self.assertFalse({"elapsed_s", "rounds", "built_in_s"} & set(row), row)


if __name__ == "__main__":
    unittest.main()
