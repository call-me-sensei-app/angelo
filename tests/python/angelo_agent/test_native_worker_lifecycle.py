# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Worker chats' lanes and workers that end, without Blender (``worker_dispatch``, ``remote_executor``; audit P2).

Pinned here: a closed idle lane ends its thread and lets its controller go (20 forgotten lanes keep no thread and no
controller); a lane closed while its worker answers drops the answer and runs none of the chat's code after it; a chat
that left its worker during a step finishes that step, then its lane ends; a worker confirmed gone (never a timeout)
ends its parallel session and frees its slot, so a replacement starts, while its chat and its records stay; a request
Angelo cannot encode (too large, not finite) is refused with nothing sent through ``WorkerProcess.request``'s own
classification, and a send that fails under a live worker is never its death (break-it E2E-15 M-1); a death is a
failure until the chat's turn has ended, never a finish, and the merge gate says so (E2E-15 M-2); on macOS and Linux a
worker's stop ends the processes its scripts started, as the Job Object does on Windows (E2E-19A L-3).
"""

import gc
import sys
import threading
import time
import unittest
import weakref
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (parallel_access, parallel_sessions, remote_executor, worker_dispatch,  # noqa: E402
                          worker_routing, worker_sessions)
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from angelo_agent import worker_channel, worker_process  # noqa: E402
from angelo_agent.worker_process import WorkerUnavailable  # noqa: E402
import test_native_parallel_workers_transport as transport  # noqa: E402  (its socket pair; its tests run there)
from test_native_worker_dispatch import FakeChat, Gate, asking, budget  # noqa: E402


def lane_threads():
    return [thread for thread in threading.enumerate() if thread.name == "AngeloWorkerLane" and thread.is_alive()]


def until(condition, seconds=5.0, step=None):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        if step is not None:
            step()
        time.sleep(0.005)
    return condition()


class LaneLifecycleTests(unittest.TestCase):
    def test_forgotten_idle_lanes_end_and_let_their_controllers_go(self):
        before = len(lane_threads())
        references = []
        for index in range(20):
            chat = FakeChat("idle-%d" % index)
            references.append(weakref.ref(chat))
            worker_dispatch.pump(chat, budget())
            chat.remote_executor = None
            worker_dispatch.forget(chat)
        del chat
        self.assertTrue(until(lambda: len(lane_threads()) == before), len(lane_threads()) - before)
        gc.collect()
        self.assertEqual(sum(reference() is not None for reference in references), 0)

    def test_a_lane_closed_while_its_worker_answers_drops_the_answer_and_ends(self):
        chat, gate = FakeChat("closed-in-flight"), Gate()
        chat.work.append(asking(gate))
        worker_dispatch.pump(chat, budget())
        self.assertTrue(gate.sent.wait(5))
        lane = worker_dispatch.lane(chat, create=False)
        worker_dispatch.close_all()
        gate.release.set()
        lane.thread.join(5)
        self.assertFalse(lane.thread.is_alive())
        self.assertEqual(chat.log, ["before"])   # None of the chat's code ran after the close.
        self.assertEqual(chat._main_thread_block.__func__, FakeChat._main_thread_block)   # Its own clock back.

    def test_a_chat_that_left_its_worker_mid_step_finishes_it_then_its_lane_ends(self):
        chat, gate = FakeChat("left-mid-step"), Gate()
        chat.work.append(asking(gate))
        worker_dispatch.pump(chat, budget())
        self.assertTrue(gate.sent.wait(5))
        lane = worker_dispatch.lane(chat, create=False)
        chat.remote_executor = None
        worker_dispatch.forget(chat)   # A step is under way: it finishes first, and reports how its call ended.
        gate.release.set()
        self.assertTrue(until(lambda: not lane.thread.is_alive(), step=lambda: worker_dispatch.pump(chat, budget())))
        self.assertEqual(chat.log, ["before", "after"])
        self.assertNotIn("left-mid-step", worker_dispatch._lanes)


class Unavailable:
    """A worker endpoint that never answers: gone (``timed_out`` False) or only late (True)."""

    ready = None

    def __init__(self, timed_out):
        self.timed_out = timed_out

    def request(self, *_args, **_kwargs):
        raise WorkerUnavailable("The worker has ended" if not self.timed_out else "no answer", timed_out=self.timed_out,
                                sent=False)


class DeadWorkerTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("_order", []), ("_ended", set())):
            patcher = mock.patch.object(parallel_sessions, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def chat(self, identity, timed_out):
        chat = FakeChat(identity)
        executor = remote_executor.RemoteExecutor(Unavailable(timed_out), identity)
        executor.studio_context, chat.remote_executor = chat, executor
        parallel_sessions._order.append(identity)
        return chat

    def test_a_worker_confirmed_gone_frees_its_slot_and_a_timeout_does_not(self):
        dead, late = self.chat("w-dead", False), self.chat("w-late", True)
        parallel_sessions._order.extend(["w-3", "w-4"])
        for chat in (dead, late):
            with self.assertRaises(RefusalError):
                chat.remote_executor.ask("hello")
        self.assertEqual((parallel_sessions.ended("w-dead"), dead.remote_executor), (True, None))
        self.assertFalse(parallel_sessions.ended("w-late"))   # Late is not dead: it keeps its worker and its slot.
        self.assertIsNotNone(late.remote_executor)
        self.assertIn("w-dead", parallel_sessions.workers())   # Its tile stays (its last saved part, marked ended).
        self.assertEqual(worker_sessions._running(), ["w-late", "w-3", "w-4"])
        lead = SimpleNamespace(state={"id": "lead"}, remote_executor=None)
        with mock.patch.object(worker_routing, "worker_snapshots", return_value=False), \
                mock.patch.object(parallel_access, "enabled", return_value=True), \
                mock.patch.object(parallel_access, "worker_limit", return_value=4):
            self.assertEqual(worker_sessions.precondition(lead, "angelo_workers_start",
                                                          {"parts": [{"name": "Again", "brief": "Build it"}]}),
                             ("workers", ("Again",)))


class Live:
    """A live worker whose send fails (an ``OSError`` under a running process and an open connection)."""

    ready = None

    def alive(self):
        return True

    def request(self, *_args, **_kwargs):
        raise WorkerUnavailable("Could not reach the worker (No buffer space available)")


class NotDeathTests(unittest.TestCase):
    def setUp(self):
        for name, value in (("_order", []), ("_ended", set())):
            patcher = mock.patch.object(parallel_sessions, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(worker_sessions, "died", {})
        patcher.start()
        self.addCleanup(patcher.stop)

    def bound(self, worker, identity):
        chat = FakeChat(identity)
        executor = remote_executor.RemoteExecutor(worker, identity)
        executor.studio_context, chat.remote_executor = chat, executor
        parallel_sessions._order.append(identity)
        return chat, executor

    @transport.POSIX_ONLY
    def test_a_request_angelo_cannot_encode_is_refused_with_nothing_sent_and_the_worker_stays(self):
        ours, theirs = transport.ChannelTests.pair(self)
        received = []

        def answer():
            theirs.send({"seq": 0, "ready": {"ok": True}})
            while True:
                try:
                    frame = theirs.receive()
                except Exception:   # noqa: BLE001 - the connection closed at the end of the test.
                    return
                received.append(frame["request"]["op"])
                theirs.send({"seq": frame["seq"], "response": {"ok": True}})
        threading.Thread(target=answer, daemon=True).start()
        worker = worker_process.WorkerProcess("/w/part.blend")
        worker.client, worker.process = worker_channel.Client(ours), SimpleNamespace(poll=lambda: None)
        self.addCleanup(worker.client.close)
        self.assertEqual(worker.client.wait_ready(5), {"ok": True})
        for fields, code in (({"name": "x" * worker_channel.REQUEST_LIMIT}, "LIMIT_EXCEEDED"),
                             ({"value": float("nan")}, "VALIDATION")):
            with self.subTest(code=code), self.assertRaises(worker_process.RequestRefused) as refused:
                worker.request("read", 5, **fields)
            self.assertEqual(refused.exception.code, code)
        chat, executor = self.bound(worker, "w-big")
        for effects in (False, True):   # An edit too: nothing was sent, so its outcome is known.
            with self.subTest(effects=effects), self.assertRaises(RefusalError) as refused:
                executor.ask("execute", effects=effects, name="x" * worker_channel.REQUEST_LIMIT)
            self.assertEqual((refused.exception.code, refused.exception.partial_changes_possible),
                             ("LIMIT_EXCEEDED", False))
        self.assertEqual(worker.request("hello", 5), {"ok": True})   # It answers, and saw only that request.
        self.assertEqual(received, ["hello"])
        self.assertIs(chat.remote_executor, executor)
        self.assertEqual((parallel_sessions.ended("w-big"), worker_sessions.died), (False, {}))

    def test_a_send_that_fails_under_a_live_worker_is_never_its_death(self):
        chat, executor = self.bound(Live(), "w-live")
        with self.assertRaises(RefusalError) as refused:
            executor.ask("hello")
        self.assertEqual(refused.exception.code, "WRITER_BUSY")
        self.assertIn("could not be reached", str(refused.exception))   # No timeout said (break-it E2E-17 L-4).
        self.assertIs(chat.remote_executor, executor)
        self.assertTrue(executor.unresponsive)   # A later call checks it answers first.
        self.assertEqual((parallel_sessions.ended("w-live"), worker_sessions.died), (False, {}))

    def test_a_death_is_a_failure_until_the_chats_turn_has_ended(self):
        chat, executor = self.bound(Unavailable(False), "w-dead")   # Its worker is gone.
        chat.busy, chat.pending = True, {}
        chat.state["messages"] = [{"role": "user", "text": "Build the sofa."}, {"role": "assistant", "text": "On it"}]
        ui = SimpleNamespace(_controllers={"w-dead": chat})   # The registry the session code reads (no Blender here).
        with mock.patch.dict(sys.modules, {"angelo_agent.ui": ui}), \
                mock.patch.object(worker_sessions, "_parts", {}):
            with self.assertRaises(RefusalError):
                executor.ask("hello")
            self.assertIsNone(chat.remote_executor)
            self.assertIn("ended", worker_sessions.died["w-dead"])
            self.assertFalse(worker_sessions.finished("w-dead"))   # Its turn still runs: never finished at once.
            self.assertIn("its worker ended", worker_sessions.merge_blocker(chat))
            chat.busy = False
            self.assertTrue(worker_sessions.finished("w-dead"))
            self.assertIsNone(worker_sessions.merge_blocker(chat))




@transport.POSIX_ONLY
class GroupStopTests(unittest.TestCase):
    """A worker runs in a session of its own (``start_new_session``), so what its scripts start shares its process
    group and, unsignalled, outlived it and its removed private folder (break-it E2E-19A L-3)."""

    def test_a_workers_stop_ends_what_its_scripts_started(self):
        import os
        import subprocess
        import tempfile
        leader = subprocess.Popen(["/bin/sh", "-c", "sleep 60 & echo $!; wait"], stdout=subprocess.PIPE, text=True,
                                  start_new_session=True)   # The worker, and a process one of its scripts started.
        started = int(leader.stdout.readline())
        self.addCleanup(lambda: _kill_quietly(started))
        worker = worker_process.WorkerProcess(Path(tempfile.mkdtemp()) / "part.blend")
        worker.process, worker._group = leader, leader.pid
        worker.stop(grace=0.5)
        leader.stdout.close()
        self.assertTrue(until(lambda: not _alive(started)), "a script's process outlived its worker")


def _alive(pid):
    import os
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _kill_quietly(pid):
    import os
    import signal
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


if __name__ == "__main__":
    unittest.main()
