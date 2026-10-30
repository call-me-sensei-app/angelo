# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Worker places without Blender (``worker_queue``, ``worker_status``; latency proposal 1a-1c, the 2026-10-25 audit).

Pinned here: a start past the free places is queued, never refused, and each queued part starts in order as a place
frees; the lead takes back a queued part, never one that started; status lists each queued part's place in line and
the places; a done worker saves (only if anything is unsaved) and stops, never died or failed, its place freed and its
part still mergeable; a save that fails, or a message that reaches it meanwhile, keeps it in its place; S2's ``REUSE``
seam gets the done worker and the queued part first; a message to a parked chat relaunches a worker on its own part
file and is delivered there (or says it could not start); the status wait answers as soon as any worker is done that
the lead was not shown done, never for one it was shown, nor for a worker's approval card (the person answers it on
the tile); a worker process that died between requests is
reconciled as a confirmed death and frees its place; a chat that cannot be made stops its launched worker. The
in-Blender proof is ``test_blender_parallel_workers.py`` and ``test_blender_parallel_merge.py``.
"""

import os
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
import angelo_agent  # noqa: E402
from angelo_agent import (parallel_access, parallel_sessions, remote_executor, session_rail, worker_dispatch,  # noqa: E402
                          worker_news, worker_places, worker_queue, worker_routing, worker_sessions, worker_spec,
                          worker_status)
from angelo_agent.refusal_codes import RefusalError  # noqa: E402

START, STATUS = worker_spec.PREFIX + "start", worker_spec.PREFIX + "status"
LAUNCH = worker_sessions._launch   # The real start (the fixture records starts instead).
CARDS_SHOWN = worker_status.cards_shown   # The real check (WaitTests set what windows show).


def until(condition, seconds=5.0, step=None):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        if step is not None:
            step()
        time.sleep(0.005)
    return condition()


class Process:
    """A worker Blender as ``WorkerProcess`` shows it: its requests, its stop, whether it still runs."""

    def __init__(self, part="/parts/1/part.blend", save=None, ready=True):
        self.part, self.requests, self.save = Path(part), [], save or {"value": None}
        self.stopped, self.running, self.ready_now = threading.Event(), True, ready
        self.process = SimpleNamespace(pid=4242, poll=lambda: None if self.running else -9)
        self.ready = {"ok": True, "layout": None}

    def start(self):
        if not self.ready_now:
            raise RuntimeError("no Blender here")
        return self.ready

    def alive(self):
        return self.running

    def request(self, op, timeout, **fields):
        self.requests.append(dict(fields, op=op))
        answer = self.save(fields) if op == "save" and callable(self.save) else self.save if op == "save" else {}
        if isinstance(answer, Exception):
            raise answer
        return dict({"document": ["serial", 7]}, **answer)

    def stop(self):
        self.running = False
        self.stopped.set()


class Chat:
    """What scheduling reads of a worker chat: its rows, whether it works, its binding."""

    def __init__(self, session, worker=None):
        self.state = {"id": session, "messages": [{"role": "user", "text": "Build the sofa."}]}
        self.busy, self.pending = False, {}
        self.remote_executor = None
        if worker is not None:
            self.remote_executor = remote_executor.RemoteExecutor(worker, "w-" + session)
            self.remote_executor.studio_context = self
        self.checkpoint_factory = self.document_invalid = None


def item(lead, number, name=None, folder=None):
    worker = Process(Path(folder or tempfile.mkdtemp()) / "part.blend")
    return {"lead": lead, "part": {"name": name or "Part %d" % number, "brief": "b"}, "number": number,
            "error": None, "done": threading.Event(), "worker": worker, "references": ()}


class Fixture(unittest.TestCase):
    """Every module-level table these tests touch, emptied for each test and put back after it."""

    LIMIT = 2

    def setUp(self):
        self.ui = SimpleNamespace(_controllers={})
        patches = [mock.patch.dict(sys.modules, {"angelo_agent.ui": self.ui}),
                   mock.patch.object(angelo_agent, "ui", self.ui, create=True),
                   mock.patch.object(parallel_access, "worker_limit", lambda: self.LIMIT),
                   mock.patch.object(parallel_access, "enabled", lambda: True),
                   mock.patch.object(worker_queue, "REUSE", []),
                   # The lanes' path (A′ off): its chats bind in Angelo; the hosted path is test_native_worker_chat_*.
                   mock.patch.dict(os.environ, {"ANGELO_WORKER_CHAT_HOST": "0"})]
        for module, names in ((parallel_sessions, ("_order", "_ended", "names", "stale", "_merged", "_merging")),
                              (worker_sessions, ("_started", "_parts", "_launching", "died", "failures", "_layouts")),
                              (worker_queue, ("queue", "parked", "_parking", "_held", "_relaunching", "_resuming")),
                              (worker_routing, ("_workers",)), (worker_news, ("_told", "_told_cards", "_turn", "_staged"))):
            for name in (n for n in names if hasattr(module, n)):   # Each table this build has.
                value = getattr(module, name)
                patches.append(mock.patch.object(module, name, type(value)()))
        self.launched = []
        patches.append(mock.patch.object(worker_sessions, "_launch", self.launch))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.lead = SimpleNamespace(state={"id": "lead"}, document_invalid=False)
        self.ui._controllers["lead"] = self.lead   # The lead's chat is open, as in Angelo.

    def launch(self, entry, take=True):
        self.launched.append(entry["number"])
        worker_sessions._launching.append(entry)

    def running(self, session, worker=None, number=1, lead="lead"):
        """A bound worker chat of ``lead``, in its place."""
        worker = worker or Process()
        chat = Chat(session, worker)
        self.ui._controllers[session] = chat
        parallel_sessions._order.append(session)
        worker_sessions._started.setdefault(lead, []).append(session)
        worker_sessions._parts[session] = (number, "Part %d" % number, Path(worker.part))
        worker_routing._workers[session] = worker
        return chat, worker


class QueueTests(Fixture):
    def test_a_start_past_the_free_places_is_queued_never_refused(self):
        self.running("w1")
        parts = {"parts": [{"name": "P%d" % n, "brief": "b"} for n in range(4)]}
        with mock.patch.object(worker_routing, "worker_snapshots", lambda controller: False):
            self.assertEqual(worker_sessions.precondition(self.lead, START, parts)[0], "workers")
        answer = worker_queue.admit(self.lead, [item(self.lead, n) for n in (2, 3, 4)])
        self.assertEqual([entry["worker"] for entry in answer["starting"]], [2])
        self.assertEqual([(entry["worker"], entry["position"]) for entry in answer["queued"]], [(3, 1), (4, 2)])
        self.assertIn("The queued parts start in order", answer["note"])
        self.assertEqual(worker_places.occupied(), 2)

    def test_queued_parts_start_in_order_as_places_free(self):
        self.running("w1")[0].busy = True   # Still at work: it keeps its place.
        worker_queue.admit(self.lead, [item(self.lead, n) for n in (2, 3, 4)])
        self.assertEqual(self.launched, [2])
        worker_sessions._launching.clear()   # Worker 2 failed to start: its place frees.
        worker_queue.tick()
        self.assertEqual(self.launched, [2, 3])
        worker_queue.tick()
        self.assertEqual(self.launched, [2, 3])   # No place: worker 4 keeps waiting.
        self.assertEqual([entry["number"] for entry in worker_queue.queue], [4])

    def test_the_lead_takes_back_a_queued_part_never_one_that_started(self):
        folder = Path(tempfile.mkdtemp())
        self.running("w1")
        other = SimpleNamespace(state={"id": "other"})
        worker_queue.admit(self.lead, [item(self.lead, 2), item(self.lead, 3, folder=folder)])
        worker_queue.queue.append(item(other, 3))
        taken, refused = worker_queue.take_back(self.lead, [3, 2, 9])
        self.assertEqual(taken, [3])
        self.assertEqual([(entry["worker"], entry["why"]) for entry in refused],
                         [(2, "it already started"), (9, "no queued part of this conversation has that number")])
        self.assertEqual([entry["lead"] for entry in worker_queue.queue], [other])   # Another chat's part stays.
        self.assertFalse(folder.exists())   # Its empty folder goes with it.

    def test_status_lists_each_queued_part_with_its_place_and_the_places(self):
        self.running("w1")
        worker_queue.admit(self.lead, [item(self.lead, n) for n in (2, 3, 4)])
        rows, places = worker_status.queued(self.lead)
        self.assertEqual([(r["worker"], r["state"], r["position"], r["waits_for"]) for r in rows],
                         [(3, "queued", 1, "a running worker to be done"), (4, "queued", 2,
                                                                          "2 running workers to be done")])
        self.assertIn("At most 2 workers run at once", places)
        self.assertIn("queued parts start in order", places)
        self.assertEqual(worker_status.queued(SimpleNamespace(state={"id": "x"})), ([], None))

    def test_the_status_call_takes_back_first(self):
        self.running("w1")
        worker_queue.admit(self.lead, [item(self.lead, n) for n in (2, 3)])
        with mock.patch.object(worker_sessions, "status", lambda lead: {"workers": []}):
            answer = worker_status.call(self.lead, {"take_back": [3, 2]})
        self.assertEqual((answer["taken_back"], answer["not_taken_back"][0]["worker"]), ([3], 2))
        self.assertEqual(worker_queue.queue, [])


class DoneWorkerTests(Fixture):
    def park(self, session):
        worker_queue.tick()   # It became done: its save starts.
        self.assertTrue(until(lambda: worker_queue._parking.get(session, {"done": threading.Event()})["done"].is_set()
                              or session not in worker_queue._parking))
        worker_queue.tick()   # Saved: it stops (or stays, as the case says).

    def test_a_done_worker_saves_stops_and_frees_its_place_never_died(self):
        chat, worker = self.running("w1")
        chat.remote_executor.part_layout = {"revision": 3, "size": [1, 1, 1]}
        self.park("w1")
        self.assertIn("w1", worker_queue.parked)
        self.assertTrue(worker.stopped.wait(5))
        self.assertEqual(worker.requests, [{"op": "save", "if_dirty": True}])   # Saved only what is unsaved.
        self.assertEqual((worker_sessions.died, parallel_sessions.ended("w1")), ({}, False))   # Never died, never ended.
        self.assertEqual(worker_sessions._running(), [])
        self.assertEqual(worker_places.occupied(), 0)
        self.assertTrue(worker_sessions.finished("w1"))
        self.assertIsNone(worker_sessions.merge_blocker(chat))   # It merges from its saved part ...
        self.assertEqual(worker_sessions._layouts["w1"]["revision"], 3)   # ... and its kept layout.
        self.assertEqual(worker_sessions.parts_of(self.lead)[1][1], Path(worker.part))

    def test_a_done_worker_frees_its_place_for_the_next_queued_part(self):
        self.LIMIT = 1
        self.running("w1")
        worker_queue.admit(self.lead, [item(self.lead, 2)])
        self.assertEqual(self.launched, [])
        self.park("w1")
        self.assertEqual(self.launched, [2])

    def test_a_save_that_fails_keeps_the_worker_in_its_place_until_a_verified_retry(self):
        chat, worker = self.running("w1", Process(save={"error": {"type": "RuntimeError", "message": "disk full"}}))
        self.park("w1")
        self.assertNotIn("w1", worker_queue.parked)
        self.assertFalse(worker.stopped.is_set())
        worker_queue.tick()
        self.assertEqual(len(worker.requests), 1)   # Not tried again every step.
        chat.remote_executor.document_key = ("angelo-worker", "w-w1", ("serial", 7))
        worker.save = {"value": str(worker.part), "revision": 1}
        worker_queue._held["w1"]["retry_at"] = 0   # Failure backoff elapsed: no unrelated chat turn needed.
        self.park("w1")
        self.assertIn("w1", worker_queue.parked)

    def test_a_message_that_reaches_it_while_it_saves_keeps_it_running(self):
        gate = threading.Event()
        chat, worker = self.running("w1", Process(save=lambda fields: gate.wait(5) and {"value": None}))
        worker_queue.tick()
        chat.busy = True   # The person wrote to it meanwhile.
        gate.set()
        self.assertTrue(until(lambda: worker_queue._parking["w1"]["done"].is_set()))
        worker_queue.tick()
        self.assertNotIn("w1", worker_queue.parked)
        self.assertFalse(worker.stopped.is_set())

    def test_reuse_gets_the_saved_done_worker_and_the_queued_part_first(self):
        self.LIMIT = 1
        offered = []
        worker_queue.REUSE.append(lambda session, entry: offered.append((session, entry["number"])) or True)
        chat, worker = self.running("w1")
        worker_queue.admit(self.lead, [item(self.lead, 2)])
        self.park("w1")
        self.assertEqual(offered, [("w1", 2)])
        self.assertEqual((worker_queue.queue, self.launched), ([], []))   # The warm worker took it.
        self.assertFalse(worker.stopped.is_set())
        self.assertIn("w1", worker_queue.parked)   # Its chat left the worker that took the part: parked, never ended.

    def test_a_declined_reuse_stops_the_done_worker_and_launches_the_part(self):
        self.LIMIT = 1
        worker_queue.REUSE.append(lambda session, entry: False)
        self.running("w1")
        worker_queue.admit(self.lead, [item(self.lead, 3)])
        self.park("w1")
        self.assertEqual((self.launched, "w1" in worker_queue.parked), ([3], True))


class ParkedFixture(Fixture):
    """The fixture with real parked chats (``controller``)."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(parallel_sessions, "_live_view", lambda: None)   # No Blender to read parts in.
        patcher.start()
        self.addCleanup(patcher.stop)

    def controller(self, session):
        """A real chat's binding state (``worker_routing.bind`` reads it), parked on its part."""
        from angelo_agent.checkpoints import CheckpointStore
        from angelo_agent.controller import Controller
        chat = Controller.__new__(Controller)
        chat.state = {"id": session, "messages": [{"role": "user", "text": "Build it."}]}
        chat.busy, chat.pending, chat._native_preview_cards = False, {}, {}
        chat.document, chat.document_invalid, chat.checkpoint_factory = ("angelo-worker", "w1", "x"), True, None
        chat.store = SimpleNamespace(directory=Path(tempfile.mkdtemp()))
        chat.files = None
        chat.mark_memory_stale = lambda reason, *_args, **_kwargs: None   # Controller.mark_memory_stale's arguments.
        chat.checkpoints = CheckpointStore(tempfile.mkdtemp(), session)
        chat.remote_executor = None
        self.ui._controllers[session] = chat
        parallel_sessions._order.append(session)
        worker_sessions._started.setdefault("lead", []).append(session)
        worker_sessions._parts[session] = (1, "Sofa", Path(tempfile.mkdtemp()) / "part.blend")
        worker_routing._remember(chat, worker_sessions._parts[session][2])   # A worker chat, as its bind recorded.
        worker_queue.parked.add(session)
        return chat


class ResumeTests(ParkedFixture):
    def test_a_message_to_a_parked_chat_relaunches_it_on_its_own_part_and_is_delivered_there(self):
        session = str(uuid.uuid4())
        chat = self.controller(session)
        with self.assertRaises(RefusalError) as refused:   # Between its stop and a message: nothing runs.
            worker_routing.ended_worker(chat).execute("angelo_create_cube", {})
        self.assertIn("starts it again", str(refused.exception))
        launched, sent = [], []
        with mock.patch.object(worker_routing, "launch", lambda part: launched.append(part) or Process(part)), \
                mock.patch.object(worker_dispatch, "submit", lambda controller, run, on_error: (
                    sent.append(controller), setattr(controller, "busy", True))):   # Its turn starts on its lane.
            worker_routing.deliver(chat, None, "Make it blue", lambda turn: None, self.fail)
            self.assertEqual([entry["kind"] for entry in worker_queue.queue + worker_queue._relaunching], ["resume"])
            self.assertTrue(until(lambda: worker_queue.tick() is not None and session not in worker_queue._resuming))
        self.assertEqual(launched, [worker_sessions._parts[session][2]])   # Its own part file.
        self.assertIsNotNone(worker_routing.bound(chat))
        self.assertEqual(sent, [chat])   # The message went to its lane, once it was bound again.
        self.assertNotIn(session, worker_queue.parked)
        self.assertFalse(chat.document_invalid)

    def test_a_relaunch_that_fails_says_so_and_the_chat_stays_parked(self):
        session = str(uuid.uuid4())
        chat = self.controller(session)
        errors = []
        with mock.patch.object(worker_routing, "launch", lambda part: Process(part, ready=False)):
            worker_routing.deliver(chat, None, "Make it blue", lambda turn: None, errors.append)
            self.assertTrue(until(lambda: worker_queue.tick() is not None and bool(errors)))
        self.assertIn("could not start again", str(errors[0]))
        self.assertIn(session, worker_queue.parked)
        self.assertIsNone(worker_routing.bound(chat))

    def test_a_relaunch_waits_for_a_place_ahead_of_queued_parts(self):
        self.LIMIT = 1
        self.running("w1")
        worker_queue.admit(self.lead, [item(self.lead, 2)])
        session = str(uuid.uuid4())
        chat = self.controller(session)
        worker_queue.resume(chat, lambda: None, lambda error: None)
        self.assertEqual([entry.get("kind", "part") for entry in worker_queue.queue], ["resume", "part"])
        self.assertEqual(worker_queue.resuming_kind(session), (session_rail.QUEUED, 1))


class WaitTests(Fixture):
    def setUp(self):
        super().setUp()
        worker_sessions._started["lead"] = ["w1", "w2"]
        worker_sessions._parts.update({"w1": (1, "A", Path("/a")), "w2": (2, "B", Path("/b"))})
        self.done, self.asking = {"w1": False, "w2": False}, set()
        for name, value in (("finished", lambda s: self.done[s]), ("awaiting_person", lambda s: s in self.asking),
                            ("status", lambda lead: {"workers": ["rows"]})):
            patcher = mock.patch.object(worker_sessions, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.shown = True   # A window shows the workers' rail (and so each worker's card).
        for session in ("w1", "w2"):
            self.ui._controllers[session] = SimpleNamespace(pending={}, state={"id": session})
        patcher = mock.patch.object(worker_status, "cards_shown", lambda: self.shown)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(worker_news.withheld)

    def show(self):
        """A payload naming every untold finish (its field) and unshown card reaches the lead's model."""
        cards = [worker_sessions._parts[c][0] for c in worker_news.unshown_cards("lead")]
        worker_news.noted(self.lead, {worker_news.UNSEEN: cards} if cards else {})
        worker_news.acknowledge(worker_news.packed())

    def test_a_named_wait_refuses_a_number_with_no_worker_before_waiting(self):
        """Break-it OVH-M-2: never started, mixed with a real one, or taken back in the same call."""
        worker_queue.queue.append(item(self.lead, 3))
        for numbers, back in (([999], []), ([1, 999], []), ([3], [3])):
            with self.subTest(workers=numbers, take_back=back):
                answer = worker_status.call(self.lead, dict({"wait": True, "timeout_seconds": 5, "workers": numbers},
                                                            **({"take_back": back} if back else {})))
                self.assertEqual(answer["error_code"], "VALIDATION")
                self.assertIn("nothing was waited for", answer["error"])
        self.assertEqual(answer["taken_back"], [3])

    def test_a_named_wait_says_which_named_part_can_no_longer_be_built(self):
        worker_queue.queue.append(item(self.lead, 3))
        self.done["w2"] = True
        waiting = worker_status.wait(self.lead, 60, None, [2, 3])
        self.assertIsNone(waiting.poll())   # Worker 3 waits to start.
        worker_queue.queue.clear()           # Dropped (a load replaced the lead's document).
        answer = waiting.poll()
        self.assertEqual((answer["done_since_wait"], answer["not_built"], answer["timed_out"]), ([2], [3], False))
        worker_sessions.failures["lead/4"] = "no Blender"   # A start that failed is not built either.
        self.assertEqual(worker_status.wait(self.lead, 60, None, [4]).poll()["not_built"], [4])

    def test_a_card_no_window_shows_answers_the_wait_until_an_answer_showing_it_is_delivered(self):
        """Break-it OVH-M-4 / OVH2-M-4: judged on the state at every poll, whenever the card came."""
        self.shown = False
        waiting = worker_sessions._wait(self.lead, 60)
        self.assertIsNone(waiting.poll())
        self.asking.add("w2")   # It comes during the wait.
        self.assertEqual(waiting.poll()["approval_no_window_shows"], [2])
        self.show()   # That answer (a status answer listing the card) reached the lead's model.
        self.assertIsNone(worker_sessions._wait(self.lead, 60).poll())   # Never again for that card (E2E-3 M-2).

    def test_a_card_pending_before_the_wait_that_no_answer_showed_answers_at_once(self):
        self.asking.add("w2")   # It came while the lead wrote the request that waits.
        self.shown = False
        self.assertEqual(worker_sessions._wait(self.lead, 60).poll()["approval_no_window_shows"], [2])
        self.shown = True   # A window shows the rail again: the person sees it; the wait does not answer for it.
        self.assertIsNone(worker_sessions._wait(self.lead, 60).poll())

    def test_a_named_worker_whose_brief_failed_is_not_built_never_done(self):
        """Break-it OVH2-M-3: its chat stays in the lead's list once its brief failed; status, merge and the wait read
        the one failure record first."""
        self.done["w2"] = True   # Idle: its brief never ran.
        worker_sessions.failures["lead/2"] = "the brief could not be sent"
        answer = worker_status.wait(self.lead, 60, None, [2]).poll()
        self.assertEqual((answer.get("done_since_wait"), answer["not_built"], answer["timed_out"]), (None, [2], False))
        self.assertEqual(worker_status._where(self.lead, 2), worker_status.FAILED)

    def test_a_wait_s_own_timeout_holds_while_a_background_launch_is_slow(self):
        """Break-it OVH2-M-5: a background Angelo steps its queue in the wait's poll, bounded by the wait's deadline."""
        fake = SimpleNamespace(app=SimpleNamespace(background=True))
        worker_sessions._launching.append({"lead": self.lead, "number": 9, "part": {"name": "slow"}})
        with mock.patch.dict(sys.modules, {"bpy": fake}), mock.patch.object(worker_sessions, "_tick", lambda: None), \
                mock.patch("angelo_agent.worker_pool.step", lambda: None):
            started, waiting, answer = time.monotonic(), worker_status.wait(self.lead, 1, None, [9]), None
            while answer is None and time.monotonic() - started < 30:   # The controller polls it again and again.
                answer = waiting.poll()
            took = time.monotonic() - started
        self.assertTrue(answer["timed_out"])
        self.assertLess(took, 5.0)   # Its 1 s, never the queue's own 600 s.

    def test_a_satisfied_wait_answers_before_any_background_step(self):
        """Break-it OVH3-M-1: a finish already there never waits on queue work (another lead's launch included)."""
        self.done["w1"] = True
        steps = []
        with mock.patch.object(worker_queue, "drive", lambda until=None: steps.append(until)):
            self.assertEqual(worker_status.wait(self.lead, 60, None, [1]).poll()["done_since_wait"], [1])
            self.assertEqual(worker_sessions._wait(self.lead, 60).poll()["done_since_wait"], [1])
            self.assertEqual(steps, [])
            self.done["w1"] = False
            started = time.monotonic()
            self.assertIsNone(worker_sessions._wait(self.lead, 60).poll())
        (until,) = steps   # One bounded step: at most one poll interval, then control returns.
        self.assertLessEqual(until - started, worker_sessions.POLL + 0.05)

    def test_a_named_wait_hears_a_card_on_the_worker_its_target_waits_behind(self):
        """Break-it OVH3-M-2: at one place, queued worker 3 cannot start while worker 1's card waits."""
        worker_queue.queue.append(item(self.lead, 3))
        self.shown = False
        waiting = worker_status.wait(self.lead, 60, None, [3])
        self.assertIsNone(waiting.poll())
        self.asking.add("w1")
        self.assertEqual(waiting.poll()["approval_no_window_shows"], [1])

    def test_a_finish_one_wait_s_delivered_answer_showed_never_answers_another_wait(self):
        """Break-it OVH3-M-3: two waits began before either answered; the first's answer reached the model."""
        first, second = worker_sessions._wait(self.lead, 60), worker_sessions._wait(self.lead, 60)
        self.done["w1"] = True
        self.assertEqual(first.poll()["done_since_wait"], [1])
        self.show()   # Its answer (listing worker 1) was delivered.
        self.assertIsNone(second.poll())   # Worker 2 still builds: nothing new.
        self.done["w2"] = True
        self.assertEqual(second.poll()["done_since_wait"], [2])

    def test_a_lane_whose_settle_was_cut_short_is_settled_by_a_later_step(self):
        """Break-it OVH3-M-4: kept until it settled, whatever cut it short."""
        from angelo_agent import worker_dispatch
        fake = SimpleNamespace(app=SimpleNamespace(background=True))
        self.ui._controllers["w9"] = SimpleNamespace(state={"id": "w9"})
        outcomes = [False, True]
        with mock.patch.dict(sys.modules, {"bpy": fake}), mock.patch.object(worker_sessions, "_tick", lambda: None), \
                mock.patch("angelo_agent.worker_pool.step", lambda: None), \
                mock.patch.object(worker_queue, "_unsettled", {"w9"}), \
                mock.patch.object(worker_dispatch, "settle", lambda chat, seconds: outcomes.pop(0)):
            worker_queue.drive(until=time.monotonic() + 0.5)
            self.assertEqual(worker_queue._unsettled, {"w9"})   # Timed out: still owed.
            worker_queue.drive(until=time.monotonic() + 0.5)
            self.assertEqual(worker_queue._unsettled, set())

    def test_without_blender_no_window_shows_a_card(self):
        with mock.patch.dict(sys.modules, {"bpy": None}):
            self.assertFalse(CARDS_SHOWN())

    def test_the_wait_answers_as_soon_as_any_worker_becomes_done(self):
        waiting = worker_sessions._wait(self.lead, 60)
        self.assertIsNone(waiting.poll())
        self.done["w2"] = True
        answer = waiting.poll()
        self.assertEqual((answer["done_since_wait"], answer["timed_out"], answer["workers"]), ([2], False, ["rows"]))

    def test_a_worker_shown_done_before_the_wait_never_answers_it_again(self):
        self.done["w1"] = True
        self.show()   # A status answer showed the lead w1 done (E2E-3 M-2).
        waiting = worker_sessions._wait(self.lead, 60)
        self.assertIsNone(waiting.poll())
        self.done["w2"] = True
        self.assertEqual(waiting.poll()["done_since_wait"], [2])

    def test_a_card_while_another_worker_builds_never_answers_the_wait(self):
        """Only the person answers a worker's card, on its tile; the lead cannot (the maintainer's decision)."""
        waiting = worker_sessions._wait(self.lead, 60)
        self.asking.add("w2")
        self.assertIsNone(waiting.poll())   # Worker 1 still builds.
        self.asking.add("w1")
        self.assertIsNone(waiting.poll())   # Both hold cards: still the person's to answer.
        self.asking.discard("w1")
        self.done["w1"] = True
        answer = waiting.poll()
        self.assertEqual(answer["done_since_wait"], [1])
        self.assertNotIn("needs_person_since_wait", answer)   # Worker 2's row still reads approval.

    def test_a_card_on_the_only_running_worker_never_answers_the_wait_only_its_end_or_the_timeout(self):
        self.done["w1"] = True
        self.show()
        waiting = worker_sessions._wait(self.lead, 60)
        self.asking.add("w2")   # The only running worker waits for the person.
        for _ in range(3):
            self.assertIsNone(waiting.poll())
        self.asking.clear()      # The person approves; it finishes.
        self.done["w2"] = True
        self.assertEqual(waiting.poll()["done_since_wait"], [2])
        self.done["w2"], self.asking = False, {"w2"}
        timed = worker_status.wait(self.lead, 0).poll()   # A card the person leaves: the wait's own timeout.
        self.assertEqual((timed["timed_out"], "needs_person_since_wait" in timed), (True, False))

    def test_a_worker_done_that_the_lead_was_never_shown_answers_the_wait_at_once_and_only_once(self):
        """It finished while the lead wrote the request that waits (LANE-overheads: 4 of 8 trials, 11-23 s idle)."""
        self.done["w1"] = True
        answer = worker_sessions._wait(self.lead, 60).poll()
        self.assertEqual((answer["done_since_wait"], answer["timed_out"]), ([1], False))
        self.show()   # That answer's status listed it (the real status does this: worker_facts).
        self.assertIsNone(worker_sessions._wait(self.lead, 60).poll())   # Never again: no billed round per poll.

    def test_a_wait_naming_workers_answers_once_each_named_one_is_done(self):
        waiting = worker_status.wait(self.lead, 60, None, [2])
        self.done["w1"] = True   # Another worker: not named.
        self.assertIsNone(waiting.poll())
        self.done["w2"] = True
        self.assertEqual(waiting.poll()["done_since_wait"], [2])

    def test_a_wait_naming_a_queued_worker_waits_for_it_to_start_and_finish(self):
        worker_queue.queue.append(item(self.lead, 3))
        self.done["w1"] = self.done["w2"] = True
        waiting = worker_status.wait(self.lead, 60, None, [2, 3])
        self.assertIsNone(waiting.poll())   # Worker 3 has not started.
        worker_queue.queue.clear()
        worker_sessions._started["lead"].append("w3")
        worker_sessions._parts["w3"] = (3, "C", Path("/c"))
        self.done["w3"] = False
        self.assertIsNone(waiting.poll())
        self.done["w3"] = True
        self.assertEqual(waiting.poll()["done_since_wait"], [2, 3])

    def test_status_passes_its_named_workers_to_the_wait(self):
        with mock.patch.object(worker_status, "wait", lambda *a: a):
            self.assertEqual(worker_status.call(self.lead, {"wait": True, "timeout_seconds": 5, "workers": [2]})[3], [2])

    def test_every_part_done_answers_and_a_queued_part_is_not_done(self):
        self.done["w1"] = self.done["w2"] = True
        worker_queue.queue.append(item(self.lead, 3))
        self.show()   # Shown done already: only the queued part keeps it waiting.
        waiting = worker_sessions._wait(self.lead, 60)
        self.assertIsNone(waiting.poll())
        worker_queue.queue.clear()
        self.assertFalse(waiting.poll()["timed_out"])


class DeadWorkerTests(Fixture):
    def test_a_worker_that_died_between_requests_frees_its_place_and_reads_failed(self):
        self.LIMIT = 1
        chat, worker = self.running("w1")
        chat.busy = True
        worker.running = False   # Its process exited; nothing asked it since.
        answer = worker_queue.admit(self.lead, [item(self.lead, 2)])
        self.assertEqual([entry["worker"] for entry in answer["starting"]], [2])   # Its place went to the new part.
        self.assertIn("exit code -9", worker_sessions.died["w1"])
        self.assertTrue(parallel_sessions.ended("w1"))
        self.assertTrue(worker.stopped.wait(5))
        self.assertIn("its worker ended", worker_sessions.merge_blocker(chat))   # Its turn still runs.
        chat.busy = False
        self.assertIsNone(worker_sessions.merge_blocker(chat))   # Merged with the death's warning, as a death is.

    def test_status_and_the_step_find_it_too(self):
        chat, worker = self.running("w1")
        worker.running = False
        with mock.patch.object(worker_sessions, "status", lambda lead: {"workers": []}):
            worker_status.call(self.lead, {})
        self.assertIn("w1", worker_sessions.died)
        self.assertEqual(worker_sessions._running(), [])
        chat2, other = self.running("w2", number=2)
        other.running = False
        worker_queue.tick()
        self.assertIn("w2", worker_sessions.died)

    def test_a_worker_answering_a_request_is_left_to_that_request(self):
        chat, worker = self.running("w1")
        worker.running = False
        with mock.patch.object(worker_dispatch, "waiting", lambda controller: True):
            self.assertEqual(worker_queue.reap(), 0)
        self.assertEqual(worker_sessions.died, {})


class AdoptGuardTests(Fixture):
    def test_a_chat_that_cannot_be_made_stops_its_launched_worker_and_says_why(self):
        for failing in ("_new_chat", "adopt"):
            with self.subTest(failing=failing):
                entry = item(self.lead, 7)
                entry["done"].set()
                worker_sessions._launching.append(entry)
                made = SimpleNamespace(state={"id": "made-%s" % failing})
                new_chat = (mock.patch.object(worker_sessions, "_new_chat", side_effect=OSError("disk full"))
                            if failing == "_new_chat" else mock.patch.object(worker_sessions, "_new_chat",
                                                                             lambda lead: made))
                adopt = mock.patch.object(worker_routing, "bind", side_effect=RuntimeError("cannot bind"))
                with new_chat, adopt, mock.patch.object(parallel_sessions, "note_active", lambda session: None):
                    worker_sessions._tick()
                self.assertTrue(entry["worker"].stopped.wait(5))   # Never left running, untracked.
                self.assertEqual(worker_sessions._launching, [])
                self.assertIn("disk full" if failing == "_new_chat" else "cannot bind",
                              worker_sessions.failures["lead/7"])
                self.assertNotIn(made.state["id"], self.ui._controllers)
                worker_sessions.failures.clear()


class SpecTests(unittest.TestCase):
    def test_the_start_says_parts_past_the_limit_wait_their_turn(self):
        start = next(tool for tool in worker_spec.for_launch(1, "in this run") if tool["name"] == START)
        self.assertIn("At most 1 worker runs at once in this run; the parts past that wait their turn, each starting once "
                      "a running worker is done.", start["description"])
        self.assertNotIn("keeps its place", start["description"])
        self.assertEqual(start["inputSchema"]["properties"]["parts"]["maxItems"], worker_sessions.MAX_AUTO_STARTS)
        parts = [{"name": "P%d" % n, "brief": "b"} for n in range(worker_sessions.MAX_AUTO_STARTS)]
        self.assertEqual(len(worker_spec.validate_worker(START, {"parts": parts})["parts"]), len(parts))
        with self.assertRaises(ValueError) as caught:
            worker_spec.validate_worker(START, {"parts": parts + [{"name": "X", "brief": "b"}]})
        self.assertIn("17 given", str(caught.exception))

    def test_status_names_the_workers_a_wait_is_for_only_with_wait(self):
        self.assertEqual(worker_spec.validate_worker(STATUS, {"wait": True, "workers": [3, 2, 3]}),
                         {"wait": True, "timeout_seconds": worker_spec.WAIT_DEFAULT, "workers": [2, 3]})
        for bad in ({"workers": [2]}, {"wait": True, "workers": []}, {"wait": True, "workers": [0]},
                    {"wait": True, "workers": "2"}):
            with self.subTest(arguments=bad), self.assertRaises(ValueError):
                worker_spec.validate_worker(STATUS, bad)

    def test_status_takes_back_queued_parts_by_number(self):
        schema = next(tool for tool in worker_spec.WORKER_TOOLS if tool["name"] == STATUS)["inputSchema"]
        self.assertIn("take_back", schema["properties"])
        self.assertEqual(worker_spec.validate_worker(STATUS, {"take_back": [3, 2, 3]}), {"take_back": [2, 3]})
        self.assertEqual(worker_spec.validate_worker(STATUS, {"wait": True, "take_back": [4]}),
                         {"wait": True, "timeout_seconds": worker_spec.WAIT_DEFAULT, "take_back": [4]})
        for bad in ({"take_back": [0]}, {"take_back": ["3"]}, {"take_back": 3}, {"take_back": list(range(1, 40))}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                worker_spec.validate_worker(STATUS, bad)

    def test_the_rail_says_queued_and_starting(self):
        self.assertEqual(session_rail._state_words(session_rail.QUEUED), "Waiting for a free place")
        self.assertEqual(session_rail._state_words(session_rail.STARTING), "Starting")
        self.assertEqual(session_rail.queue_text(1), "Starts once 1 running worker is done")
        self.assertEqual(session_rail.queue_text(2), "Starts once 2 running workers are done")
        source = (ADDON / "session_rail_draw.py").read_text(encoding="utf-8")
        body = source[source.index("def states("):source.index("def _worker_number(")]
        self.assertIn("worker_queue.parts_waiting()", body)   # Queued and starting parts have tiles.
        self.assertIn("worker_queue.resuming_kind(session_id)", body)   # A parked chat coming back says so.


class DocumentReplacedTests(ResumeTests):
    """A load that invalidates a lead stops every kind of entry of it, each built as production builds it (the audit of
    948d877: a queued restart holds its lead's id, and cleanup raised on it; restarts already launching bound later)."""

    def entries(self, hold):
        """The lead's bound worker, a queued part, a queued restart and a restart launching (its start held)."""
        self.ui.state_directory = lambda: tempfile.mkdtemp()
        launched = []

        def launch(part):
            worker = Process(part)
            worker.start = lambda: (hold.wait(5), worker.ready)[1]
            launched.append(worker)
            return worker
        self.running("w0")
        restarting, waiting = self.controller(str(uuid.uuid4())), self.controller(str(uuid.uuid4()))
        failures = []
        with mock.patch.object(worker_routing, "launch", launch):
            worker_queue.resume(restarting, lambda: None, failures.append)   # A free place: it launches now.
            part = worker_sessions._prepare(self.lead, {"name": "Rug", "brief": "A rug."}, 4)
            worker_queue.admit(self.lead, [part])                           # No place left: it waits.
            worker_queue.resume(waiting, lambda: None, failures.append)    # Queued ahead of the part.
        return failures, launched

    def test_every_kind_of_entry_stops_and_says_why(self):
        self.LIMIT, hold = 2, threading.Event()
        failures, launched = self.entries(hold)
        self.assertEqual(([e.get("kind") for e in worker_queue.queue], len(worker_queue._relaunching)),
                         (["resume", None], 1))
        self.lead.document_invalid = True
        worker_queue.document_replaced()
        hold.set()
        self.assertEqual((worker_queue.queue, worker_queue._relaunching), ([], []))
        self.assertEqual(len(failures), 2)
        self.assertTrue(all(worker_queue.REPLACED in str(error) for error in failures), failures)
        self.assertEqual((len(worker_sessions.died), set(worker_sessions.died.values())),
                         (3, {worker_queue.REPLACED}))   # The bound worker, and both parked chats ended (19A L-1).
        self.assertEqual(worker_queue.parked, set())
        self.assertIsNone(worker_routing.bound(self.ui._controllers["w0"]))
        self.assertTrue(until(launched[0].stopped.is_set))   # The restart that was launching (the queued part's
        # worker never started: there is nothing of it to stop).

    def test_a_restart_that_finishes_after_the_load_never_binds(self):
        self.LIMIT, hold = 1, threading.Event()
        chat, failures = self.controller(str(uuid.uuid4())), []
        with mock.patch.object(worker_routing, "launch", lambda part: Process(part)):
            worker_queue.resume(chat, lambda: None, failures.append)
            self.lead.document_invalid = True   # Replaced while it launched, before any cleanup ran.
            self.assertTrue(until(lambda: worker_queue.tick() is None or not worker_queue._relaunching))
        self.assertIsNone(worker_routing.bound(chat))
        self.assertTrue(failures and worker_queue.REPLACED in str(failures[0]), failures)

    def test_a_start_that_finishes_after_the_load_never_binds(self):
        worker, made = Process(), []
        entry = dict(item(self.lead, 5), worker=worker)
        self.lead.document_invalid = True
        with mock.patch.object(worker_sessions, "_new_chat", lambda lead: made.append(lead)), \
                self.assertRaises(RuntimeError) as refused:
            worker_sessions._adopt(entry)
        self.assertIn(worker_queue.REPLACED, str(refused.exception))
        self.assertEqual(made, [])
        self.assertTrue(until(worker.stopped.is_set))


class PlacesTests(Fixture):
    """One count of places in use, read by the queue and the pool alike, over every state a worker Blender can be in
    (the audit of 948d877: with a limit of two and two workers restarting, the pool saw two free places)."""

    def test_restarting_workers_take_places_in_the_pool_too(self):
        from angelo_agent import worker_pool, worker_process
        self.LIMIT = 2
        with mock.patch.object(worker_pool, "_pool", []), mock.patch.object(worker_process, "_live", set()):
            worker_queue._relaunching.extend({"kind": "resume", "worker": Process(), "lead": "lead"} for _ in range(2))
            self.assertEqual((worker_queue.free(), worker_pool.places()), (0, 0))

    def test_every_state_takes_one_place_whatever_else_it_is(self):
        from angelo_agent import worker_places, worker_pool, worker_process
        self.LIMIT = 6
        stopping, exited, switching = Process(), Process(), Process()
        stopping.holding, exited.holding, switching.holding = (lambda: True), (lambda: False), (lambda: True)
        with mock.patch.object(worker_pool, "_pool", [{"worker": Process()}]), \
                mock.patch.object(worker_process, "_live", {stopping, exited, switching}):
            self.running("w1")                                                          # Running, bound.
            worker_sessions._launching.append(item(self.lead, 2))                       # Launching.
            worker_sessions._launching.append(dict(item(self.lead, 3), worker=switching))   # A warm one switching.
            worker_queue._relaunching.append({"kind": "resume", "worker": Process(), "lead": "lead"})   # Restarting.
            # Running, launching, switching (live and launching: once), restarting, ahead, stopping; exited: none.
            self.assertEqual(worker_places.occupied(), 6)
            self.assertEqual((worker_queue.free(), worker_pool.places()), (0, 0))
            stopping.holding = lambda: False   # Its process exited: its place is free for both.
            self.assertEqual((worker_queue.free(), worker_pool.places()), (1, 1))

    def test_a_part_starts_by_taking_a_worker_launched_ahead_when_no_other_place_is_free(self):
        # A launched-ahead worker's place passes to the part that takes it at its start (``worker_sessions._launch``).
        from angelo_agent import worker_pool, worker_process
        self.LIMIT = 1
        ahead = Process()
        ahead.holding = lambda: ahead.running
        entry = {"worker": ahead, "lead": "lead", "since": 0.0, "done": threading.Event(), "error": None, "kept": False}
        entry["done"].set()
        part = item(self.lead, 1)
        with mock.patch.object(worker_pool, "_pool", [entry]), mock.patch.object(worker_process, "_live", {ahead}), \
                mock.patch.object(worker_sessions, "_launch", LAUNCH):
            answer = worker_queue.admit(self.lead, [part])
            self.assertEqual(([s["worker"] for s in answer["starting"]], worker_pool._pool), ([1], []))
            self.assertIs(part["worker"], ahead)
            self.assertEqual(worker_places.occupied(), 1)   # One Blender, one place.


class HeadOfLineTests(ParkedFixture):
    """Break-it E2E-19A H-1 and M-4 (``repro19a/h19_places.py``): workers launched ahead fill the places at the lead's
    lookup; a message to a parked chat queues its restart; the lead then starts two parts. Before, each part took a
    pool worker while it was prepared and queued behind the restart, which waited for a place those idle parts held:
    nothing ever started, and a part taken back or dropped kept its Blender running until Angelo quit. Now a queued
    part holds no Blender (it takes one only as it starts), and a worker launched ahead yields its place to a person's
    restart, one at a time."""

    LIMIT = 2

    def setUp(self):
        super().setUp()
        from angelo_agent import worker_pool, worker_process
        self.ahead = []
        for number in range(2):
            worker = Process()
            worker.holding = lambda worker=worker: worker.running
            done = threading.Event()
            done.set()
            self.ahead.append({"worker": worker, "lead": "lead", "since": float(number), "done": done, "error": None,
                               "kept": False})
        for patcher in (mock.patch.object(worker_pool, "_pool", list(self.ahead)),
                        mock.patch.object(worker_pool, "_stopping", []),
                        mock.patch.object(worker_process, "_live", {entry["worker"] for entry in self.ahead}),
                        mock.patch.object(worker_sessions, "_launch", LAUNCH)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_restart_and_new_parts_never_wait_on_each_other(self):
        from angelo_agent import worker_pool
        chat, failures, delivered = self.controller(str(uuid.uuid4())), [], []
        newest, gate = self.ahead[1]["worker"], threading.Event()
        newest.stop = lambda: (gate.wait(5), Process.stop(newest))   # A Blender takes a moment to exit.
        with mock.patch.object(worker_routing, "launch", lambda part: Process(part)):
            worker_queue.resume(chat, lambda: delivered.append(True), failures.append)   # No free place: queued.
            parts = [item(self.lead, 1, "Seat"), item(self.lead, 2, "Back")]
            answer = worker_queue.admit(self.lead, parts)
            self.assertEqual(answer["starting"], [])   # Behind the person's message.
            self.assertFalse(any(part["worker"] in [e["worker"] for e in self.ahead] for part in parts))   # Hold none.
            gate.set()
            self.assertTrue(until(lambda: not worker_queue.queue or worker_queue.queue[0].get("kind") != "resume",
                                  step=worker_queue.tick))
        self.assertTrue(self.ahead[1]["worker"].stopped.is_set())   # The newest launched ahead yielded its place ...
        self.assertFalse(self.ahead[0]["worker"].stopped.is_set())  # ... and only it.
        self.assertEqual([part["number"] for part in worker_queue.queue], [2])   # Waits for a place to free.
        self.assertIs(parts[0]["worker"], self.ahead[0]["worker"])   # The first part took the one left.
        self.assertEqual(worker_pool._pool, [])
        self.assertEqual(failures, [])
        self.assertLessEqual(worker_places.occupied(), self.LIMIT)

    def test_a_queued_part_holds_no_blender_so_a_take_back_or_a_load_leaves_none_running(self):
        from angelo_agent import worker_pool
        chat = self.controller(str(uuid.uuid4()))
        with mock.patch.object(worker_pool, "yield_place", lambda: None):   # The restart stays at the head.
            worker_queue.resume(chat, lambda: None, lambda error: None)
            parts = [item(self.lead, 1), item(self.lead, 2)]
            worker_queue.admit(self.lead, parts)
        self.assertEqual([entry.get("kind") or entry["number"] for entry in worker_queue.queue], ["resume", 1, 2])
        self.assertEqual(worker_pool._pool, self.ahead)   # Both launched ahead stay the pool's, released by it.
        self.assertEqual(worker_queue.take_back(self.lead, [1])[0], [1])
        self.lead.document_invalid = True
        worker_queue.document_replaced()
        self.assertEqual((worker_queue.queue, worker_pool._pool), ([], []))
        self.assertTrue(all(until(entry["worker"].stopped.is_set) for entry in self.ahead))   # Every Blender ends.
        self.assertFalse(any(part["worker"].stopped.is_set() for part in parts))   # Never started: none to stop.

    def test_a_restart_needs_a_free_place_and_never_takes_one_launched_ahead(self):
        self.assertFalse(worker_queue._room({"kind": worker_queue.RESUME}))   # Both places held ahead.
        self.assertTrue(worker_queue._room({"kind": None}))   # A part takes one at its start.


class EndedParkedTests(ParkedFixture):
    """Break-it E2E-19A M-3 and L-1: a parked chat whose part can never land again (merged, or its lead's document
    replaced or closed) ends as a stopped worker does; a message to it launches nothing and is told why."""

    def merge(self, sessions):
        from angelo_agent import worker_merge
        bpy = SimpleNamespace(app=SimpleNamespace(background=True), data=SimpleNamespace(collections={}))
        arguments = {"parts": [{"worker": worker_sessions._parts[s][0]} for s in sessions]}
        with mock.patch.object(worker_merge, "merge", lambda *args: {"merged": []}), \
                mock.patch.dict(sys.modules, {"bpy": bpy}):
            worker_sessions._merge(self.lead, arguments, None)

    def test_a_merged_worker_ends_whether_it_was_bound_or_parked(self):
        bound, _worker = self.running("w1", number=1)
        parked = self.controller(str(uuid.uuid4()))
        worker_sessions._parts[parked.state["id"]] = (2, "Back", Path(tempfile.mkdtemp()) / "part.blend")
        self.merge(["w1", parked.state["id"]])
        self.assertIsNone(worker_routing.bound(bound))
        self.assertNotIn(parked.state["id"], worker_queue.parked)
        self.assertIn(parked.state["id"], parallel_sessions._ended)
        with mock.patch.object(worker_routing, "launch", lambda part: self.fail("a merged worker relaunched")):
            self.assertFalse(worker_queue.resume(parked, lambda: None, self.fail))

    def test_a_merge_tells_a_message_waiting_for_its_restart(self):
        parked, failures = self.controller(str(uuid.uuid4())), []
        self.running("w1")[0].busy = True
        self.running("w2")[0].busy = True   # No place: the restart waits.
        worker_queue.resume(parked, lambda: self.fail("delivered"), failures.append)
        self.assertEqual([entry.get("kind") for entry in worker_queue.queue], ["resume"])
        worker_queue.end_parked(parked.state["id"], worker_queue.MERGED, worker_routing.END_MERGED)
        self.assertEqual((worker_queue.queue, worker_queue._resuming), ([], {}))
        self.assertTrue(failures and worker_queue.MERGED in str(failures[0]), failures)

    def test_a_replaced_or_closed_leads_parked_chat_launches_nothing(self):
        for label, change in (("replaced", lambda: setattr(self.lead, "document_invalid", True)),
                              ("closed", lambda: self.ui._controllers.pop("lead"))):
            with self.subTest(label):
                self.ui._controllers["lead"], self.lead.document_invalid = self.lead, False
                worker_sessions.died.clear()
                chat = self.controller(str(uuid.uuid4()))
                change()
                with mock.patch.object(worker_routing, "launch", lambda part: self.fail("launched")):
                    self.assertFalse(worker_queue.resume(chat, lambda: None, self.fail))
                self.assertNotIn(chat.state["id"], worker_queue.parked)
                self.assertEqual(worker_sessions.died.get(chat.state["id"]), worker_queue.REPLACED)

    def test_a_load_ends_the_replaced_leads_parked_chats(self):
        chat = self.controller(str(uuid.uuid4()))
        self.lead.document_invalid = True
        worker_queue.document_replaced()
        self.assertNotIn(chat.state["id"], worker_queue.parked)
        self.assertEqual(worker_sessions.died.get(chat.state["id"]), worker_queue.REPLACED)
        self.assertIsNotNone(worker_routing.ended_worker(chat))   # Refuses, saying it ended; nothing relaunches.


if __name__ == "__main__":
    unittest.main()
