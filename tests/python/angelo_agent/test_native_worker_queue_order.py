# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Who takes a freed place, and when a part is done, without Blender (``worker_queue``, ``worker_pool``; break-it
E2E-20 M-1, M-2, L-1, L-2, L-3, whose repros these are).

Pinned here: a person's message to a parked chat waits for a place ahead of queued parts, and a done worker's warm
process (the ``REUSE`` seam) never passes it; a parked chat whose message waits for its restart is not finished, so
its part is neither mergeable nor merged and the lead's status wait does not answer "all done"; one place over the
limit stops one worker launched ahead, not all; a background Angelo steps its pool, so an untaken worker launched
ahead stops at its turn's end there too.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_native_worker_queue as base  # noqa: E402
from angelo_agent import (parallel_capability, worker_places, worker_pool, worker_process,  # noqa: E402
                          worker_queue, worker_routing, worker_sessions, worker_status)

CAPABLE = parallel_capability.Capability(True, "GPU", 64.0, 16, 8)
BPY = SimpleNamespace(app=SimpleNamespace(background=True))   # No timers: every step here is driven by hand.


def kinds():
    return [entry.get("kind") or entry.get("number") for entry in worker_queue.queue]


class Warm(base.Process):
    """A worker Blender that started: in worker_process._live, holding its place until its process has exited;
    ``exit_after`` seconds pass between its stop and its exit, and its stop returns then (``WorkerProcess.stop``)."""

    def __init__(self, part="/parts/w/part.blend", exit_after=0.0):
        super().__init__(part)
        self.serving, self.ran_python, self.exit_after = Path(part), False, exit_after

    def holding(self):
        return self.running

    def stop(self):
        time.sleep(self.exit_after)   # As WorkerProcess.stop, it returns once the process has exited.
        base.Process.stop(self)


class Cold:
    """A prepared part's worker, not started: no process yet."""

    def __init__(self, part):
        self.part, self.process, self.ready, self.ahead = Path(part), None, None, False

    def holding(self):
        return False

    def alive(self):
        return False


def cold_item(lead, number, name):
    entry = base.item(lead, number, name)
    entry["worker"] = Cold(Path(tempfile.mkdtemp()) / "part.blend")
    return entry


class Base(base.ParkedFixture):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        self.ui.state_directory = lambda: self.tmp
        self.lead.busy, self.lead.voice, self.lead.external_client = True, None, ""
        self.lead._runtime_context, self.lead._request_message_index = None, 4
        patches = [mock.patch.object(worker_pool, "_pool", []), mock.patch.object(worker_pool, "_stopping", []),
                   mock.patch.object(worker_pool, "_starters", set()), mock.patch.object(worker_process, "_live", set()),
                   mock.patch.object(parallel_capability, "_measured", [CAPABLE]),
                   mock.patch.dict(sys.modules, {"bpy": BPY}),
                   # The lanes' path (A′ off): its chats bind in Angelo; the hosted path is test_native_worker_chat_*.
                   mock.patch.dict(os.environ, {"ANGELO_WORKER_CHAT_HOST": "0"})]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cold = []

        def launch(part, **_options):
            worker = Warm(part)
            self.cold.append(worker)
            return worker
        patcher = mock.patch.object(worker_routing, "launch", launch)
        patcher.start()
        self.addCleanup(patcher.stop)

    def bound_worker(self, session, number, lead="lead"):
        worker = Warm(Path(self.tmp) / str(number) / "part.blend")
        worker_process._live.add(worker)
        chat, _ = self.running(session, worker, number=number, lead=lead)
        return chat, worker

    def park(self, session):
        worker_queue.tick()   # It became done: its save starts.
        base.until(lambda: session not in worker_queue._parking or worker_queue._parking[session]["done"].is_set())
        worker_queue.tick()   # Saved: it stops, or its warm process takes the next part.

    def ahead(self):
        return [worker for worker in self.cold if getattr(worker, "ahead", False)]


class ReuseOrderTests(Base):
    LIMIT = 1

    def scenario(self, reuse):
        with mock.patch.object(worker_queue, "REUSE", [worker_pool.reuse] if reuse else []):
            chat, worker = self.bound_worker("w1", 1)
            chat.busy = True   # Building part 1: the one place is in use.
            worker_queue.admit(self.lead, [cold_item(self.lead, 2, "Back"), cold_item(self.lead, 3, "Arm")])
            parked = self.controller(str(uuid.uuid4()))   # An earlier part, parked; the person messages it.
            worker_sessions._parts[parked.state["id"]] = (0, "Seat", Path(self.tmp) / "0" / "part.blend")
            worker_queue.resume(parked, lambda: None, lambda error: None)
            self.assertEqual(kinds(), ["resume", 2, 3])
            chat.busy = False   # Part 1's turn ended: done.
            self.park("w1")
            base.until(lambda: not worker.running or any(i.get("worker") is worker for i in worker_sessions._launching))
            worker_queue.tick()   # Its stop is off the main thread: the next step gives the freed place.
            restarted = parked.state["id"] not in worker_queue._resuming or any(
                entry["session"] == parked.state["id"] for entry in worker_queue._relaunching)
            return restarted, worker

    def test_without_reuse_the_restart_takes_the_freed_place_first(self):
        restarted, _worker = self.scenario(reuse=False)
        self.assertTrue(restarted)
        self.assertEqual(kinds(), [2, 3])

    def test_a_warm_reuse_never_passes_a_restart_waiting_at_the_head(self):
        restarted, worker = self.scenario(reuse=True)
        self.assertTrue(restarted)   # The person's message first; the parts keep waiting, in order.
        self.assertEqual(kinds(), [2, 3])
        self.assertFalse(any(item.get("worker") is worker for item in worker_sessions._launching))


class DoneWhileAMessageWaitsTests(Base):
    LIMIT = 2

    def test_a_parked_chat_whose_message_waits_is_not_done_and_never_merges(self):
        for session in ("o1", "o2"):   # Another lead's workers fill both places.
            self.bound_worker(session, 1, lead="other")[0].busy = True
        parked = self.controller(str(uuid.uuid4()))   # The lead's worker 1, done and parked.
        worker_sessions._parts[parked.state["id"]][2].write_bytes(b"BLENDER")
        worker_queue.resume(parked, lambda: self.fail("delivered"), lambda error: None)
        session = parked.state["id"]
        self.assertFalse(worker_sessions.finished(session))
        self.assertIn("waits for its worker to start again", worker_sessions.merge_blocker(parked))
        self.assertTrue(worker_queue.waiting(self.lead))
        from angelo_agent import worker_facts
        row = worker_facts.annotate("lead", {"workers": [{"worker": 1, "name": "Sofa", "state": "queued"}]})
        self.assertEqual((row["workers"][0]["mergeable"], "mergeable_not_in_this_scene" in row), (False, False))
        with mock.patch.object(worker_sessions, "status", lambda lead: {"workers": []}):
            self.assertIsNone(worker_status.wait(self.lead, 30).poll())   # Not "everything done".
        from angelo_agent.refusal_codes import RefusalError
        with self.assertRaises(RefusalError) as refused:
            worker_sessions.precondition(self.lead, "angelo_workers_merge", {"parts": [{"worker": 1}]})
        self.assertIn("waits for its worker to start again", str(refused.exception))


class PoolTrimTests(Base):
    LIMIT = 3

    def test_one_place_over_the_limit_stops_one_worker_launched_ahead(self):
        for number in range(3):
            worker = Warm(Path(self.tmp) / "ahead" / str(number) / "part.blend", exit_after=1.0)
            worker_process._live.add(worker)
            done = threading.Event()
            done.set()
            worker_pool._pool.append({"worker": worker, "lead": "lead", "since": time.monotonic() + number,
                                      "done": done, "error": None, "kept": False})   # Within the idle bound.
        self.LIMIT = 2   # The person lowers Workers at once (a live preference).
        worker_pool.tick()
        self.assertEqual(len(worker_pool._pool), 2)
        worker_pool.tick()   # Still exiting: nothing more stops.
        self.assertEqual(len(worker_pool._pool), 2)
        self.assertTrue(base.until(lambda: worker_places.free() == 0, seconds=3))


class BackgroundPoolTests(Base):
    LIMIT = 2

    def test_a_background_angelo_releases_an_untaken_worker_launched_ahead_at_its_turns_end(self):
        # No timer runs the pool there: the queue's drive steps it (break-it E2E-20 L-3).
        worker = Warm(Path(self.tmp) / "ahead" / "part.blend")
        worker_process._live.add(worker)
        done = threading.Event()
        done.set()
        worker_pool._pool.append({"worker": worker, "lead": "lead", "since": time.monotonic(), "done": done,
                                  "error": None, "kept": False})
        self.lead.busy = False   # The lead's turn ended without a start.
        with mock.patch.object(worker_sessions, "_tick", lambda: worker_queue.tick() and worker_sessions.POLL):
            worker_queue.drive()
        self.assertEqual(worker_pool._pool, [])
        self.assertTrue(worker.stopped.wait(5))



class LeadInvalidatedTests(Base):
    LIMIT = 1

    def test_a_lead_invalidated_without_a_load_stops_its_workers_and_starts_nothing(self):
        # Controller.set_document on another scene's key invalidates a lead with no load (break-it E2E-20 R4-M-1).
        chat, worker = self.bound_worker("w1", 1)
        chat.busy = True   # Building part 1.
        worker_queue.admit(self.lead, [cold_item(self.lead, 2, "Table")])
        self.assertEqual(kinds(), [2])
        worker_queue.tick()
        self.assertIsNotNone(worker_routing.bound(chat))   # A current lead's step stops nothing.
        self.lead.document_invalid = True   # What set_document does to a lead that holds document work.
        worker_queue.tick()
        self.assertIsNone(worker_routing.bound(chat))
        self.assertTrue(worker.stopped.wait(5))
        self.assertEqual((worker_queue.queue, worker_sessions._launching), ([], []))
        self.assertEqual(worker_sessions.died.get("w1"), worker_queue.REPLACED)


if __name__ == "__main__":
    unittest.main()
