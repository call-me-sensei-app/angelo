# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Worker chats' lanes (``worker_dispatch``): Blender's main thread never waits for a worker Blender's answer.

Pinned here without Blender: a step returns while its lane waits on the worker; the chat's controller code never
runs on two threads at once; a Stop and a streamed reply may run on the main thread while the lane waits; a request
made while the controller holds its lock keeps the baton; a chat that leaves its worker finishes the step under way
first; approvals and messages run on the lane; requests outside a lane are counted; what the lane's code hands the main
thread (a saved part's listeners) runs there, in the same step (break-it E2E-1 M-3); queued work of a chat that left its
worker reaches its owner as a failure, never silently dropped, and counts as dispatchable (M-4, L-4); a Stop that lands
while a message is prepared on the lane ends the delivery (M-5). The in-Blender proof (a real worker, a real chat, a
slow edit) is ``test_blender_parallel_workers.py``.
"""

import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
from angelo_agent import tool_pump, worker_dispatch  # noqa: E402
from angelo_agent.controller import ToolDispatchBudget  # noqa: E402


class Gate:
    """A worker request that answers when the test says so."""

    def __init__(self):
        self.sent, self.release = threading.Event(), threading.Event()

    def request(self):
        self.sent.set()
        if not self.release.wait(10):
            raise AssertionError("never released")
        return {"answer": True}


class FakeChat:
    """What a lane needs of a ``Controller``, recording which thread runs its code and whether two ever overlap."""

    def __init__(self, identity="chat-1", remote=True):
        self.state = {"id": identity}
        self.remote_executor = object() if remote else None
        self.lock = threading.RLock()
        self.has_dispatchable_tools = True
        self._request_metrics = None
        self.blocks, self.inside, self.overlaps, self.threads, self.log = [], 0, 0, set(), []
        self.work = []

    def _main_thread_block(self, metrics, started):
        self.blocks.append(time.monotonic() - started)

    def enter(self, what):
        self.inside += 1
        if self.inside > 1:
            self.overlaps += 1
        self.threads.add(threading.current_thread().name)
        self.log.append(what)

    def leave(self):
        self.inside -= 1

    def pump_tools(self, executor, document, apply_mutation=None, budget=None, commit_mutation=None):
        while self.work:
            self.work.pop(0)(self)


def code(what, then=None):
    def run(chat):
        chat.enter(what)
        try:
            if then is not None:
                then(chat)
        finally:
            chat.leave()
    return run


def asking(gate):
    """Controller code that sends its worker a request in the middle of it (``RemoteExecutor.ask``)."""
    def run(chat):
        chat.enter("before")
        chat.leave()   # The lane is suspended inside the request, not inside other code of the chat.
        worker_dispatch.ask(gate.request)
        chat.enter("after")
        chat.leave()
    return run


def budget():
    return ToolDispatchBudget(seconds=60)


class LaneTests(unittest.TestCase):
    def tearDown(self):
        worker_dispatch.close_all()

    def test_a_step_returns_while_its_lane_waits_on_the_worker_and_resumes_once_answered(self):
        chat, gate = FakeChat(), Gate()
        chat.work.append(asking(gate))
        started = time.monotonic()
        self.assertTrue(worker_dispatch.pump(chat, budget()))
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertTrue(gate.sent.is_set())
        self.assertTrue(worker_dispatch.waiting(chat))
        self.assertFalse(worker_dispatch.dispatchable(chat))   # The UI pump does not spin on it meanwhile.
        self.assertFalse(worker_dispatch.pump(chat, budget()))   # Not answered: nothing runs, nothing waits.
        self.assertEqual(chat.log, ["before"])
        gate.release.set()
        deadline = time.monotonic() + 5
        while worker_dispatch.waiting(chat) and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertTrue(worker_dispatch.pump(chat, budget()))
        self.assertEqual(chat.log, ["before", "after"])
        self.assertEqual(chat.threads, {"AngeloWorkerLane"})
        self.assertEqual(chat.overlaps, 0)

    def test_a_parts_read_waits_only_while_its_worker_answers_an_edit(self):
        """The live view's rule end to end (parallel_sessions._answering on a real lane): a read request, a picture or
        a turn's preparation in flight leaves the part readable; an edit, which saves it, holds it (P1-9 P2, P1-13 U5)."""
        import angelo_agent
        from unittest import mock
        from angelo_agent import parallel_sessions
        for effects in (False, True):
            with self.subTest(effects=effects):
                chat, gate = FakeChat("part-%s" % effects), Gate()
                chat.work.append(lambda c, gate=gate, effects=effects: worker_dispatch.ask(gate.request, effects=effects))
                ui = SimpleNamespace(_controllers={chat.state["id"]: chat})   # The add-on's ui needs bpy.
                with mock.patch.dict(sys.modules, {"angelo_agent.ui": ui}), \
                        mock.patch.object(angelo_agent, "ui", ui, create=True):
                    worker_dispatch.pump(chat, budget())
                    self.assertTrue(worker_dispatch.waiting(chat))
                    self.assertIs(parallel_sessions._answering(chat.state["id"]), effects)
                    gate.release.set()
                    deadline = time.monotonic() + 5
                    while worker_dispatch.waiting(chat) and time.monotonic() < deadline:
                        time.sleep(0.005)
                    self.assertFalse(parallel_sessions._answering(chat.state["id"]))   # Answered: readable again.

    def test_the_main_thread_may_run_the_chats_events_only_while_the_lane_waits(self):
        chat, gate = FakeChat(), Gate()
        chat.work.append(asking(gate))
        worker_dispatch.pump(chat, budget())
        code("stop on the main thread")(chat)   # A Stop or a streamed reply: the lane is inside its request.
        gate.release.set()
        while worker_dispatch.waiting(chat):
            time.sleep(0.005)
        worker_dispatch.pump(chat, budget())
        self.assertEqual(chat.log, ["before", "stop on the main thread", "after"])
        self.assertEqual(chat.overlaps, 0)

    def test_the_main_thread_waits_only_for_controller_code(self):
        chat = FakeChat()
        chat.work.append(code("bookkeeping", lambda c: time.sleep(0.05)))
        started = time.monotonic()
        worker_dispatch.pump(chat, budget())
        self.assertGreaterEqual(time.monotonic() - started, 0.05)   # As the window's own chats do.
        self.assertEqual(chat.log, ["bookkeeping"])
        self.assertTrue(chat.blocks and chat.blocks[-1] >= 0.04)   # Recorded as main-thread time, once, by the step.

    def test_a_request_under_the_controllers_lock_keeps_the_baton(self):
        chat, gate = FakeChat(), Gate()
        gate.release.set()

        def locked(c):
            with c.lock:
                worker_dispatch.ask(gate.request)
        before = worker_dispatch.STATS["io_under_lock"]
        chat.work.append(locked)
        worker_dispatch.pump(chat, budget())
        self.assertFalse(worker_dispatch.waiting(chat))
        self.assertEqual(worker_dispatch.STATS["io_under_lock"], before + 1)

    def test_a_chat_that_leaves_its_worker_finishes_the_step_under_way_and_takes_no_new_work(self):
        chat, gate = FakeChat(), Gate()
        chat.work.append(asking(gate))
        worker_dispatch.pump(chat, budget())
        chat.remote_executor = None
        worker_dispatch.forget(chat)
        self.assertTrue(worker_dispatch.owns(chat))   # The UI pump still steps it, on its lane.
        gate.release.set()
        while worker_dispatch.waiting(chat):
            time.sleep(0.005)
        self.assertTrue(worker_dispatch.pump(chat, budget()))
        self.assertEqual(chat.log, ["before", "after"])
        chat.work.append(code("new work"))
        self.assertFalse(worker_dispatch.pump(chat, budget()))
        self.assertFalse(worker_dispatch.owns(chat))
        self.assertEqual(chat.log, ["before", "after"])

    def test_approvals_and_messages_run_on_the_lane_and_their_failures_reach_the_main_thread(self):
        chat = FakeChat()
        chat.has_dispatchable_tools = False
        ran, failures = [], []
        chat.approve_tool = lambda call, executor, document: ran.append(("approve", threading.current_thread().name))
        chat.execute_tool = lambda call, executor, document: ran.append(("execute", call))
        worker_dispatch.approve(chat, "call-1")

        def send():
            raise RuntimeError("no brief")
        worker_dispatch.submit(chat, send, lambda error: failures.append((str(error), threading.current_thread())))
        worker_dispatch.pump(chat, budget())
        self.assertEqual(ran, [("approve", "AngeloWorkerLane"), ("execute", "call-1")])
        self.assertEqual(failures, [("no brief", threading.main_thread())])

    def test_requests_outside_a_lane_still_answer_and_are_counted_on_the_main_thread(self):
        before = worker_dispatch.STATS["main_asks"]
        self.assertEqual(worker_dispatch.ask(lambda: 7), 7)
        self.assertEqual(worker_dispatch.STATS["main_asks"], before + 1)


class MainThreadTests(unittest.TestCase):
    def tearDown(self):
        worker_dispatch.close_all()

    def test_a_saved_parts_listeners_run_on_the_main_thread_in_the_same_step(self):
        from angelo_agent import remote_executor
        executor = remote_executor.RemoteExecutor(SimpleNamespace(ready=None), "w1")
        seen = []
        listener = lambda found: seen.append((found, threading.current_thread()))   # noqa: E731
        remote_executor.part_listeners.append(listener)
        self.addCleanup(remote_executor.part_listeners.remove, listener)
        chat = FakeChat()
        chat.work.append(lambda c: executor.note_layout({"revision": 1, "collections": [], "objects": []}))
        self.assertTrue(worker_dispatch.pump(chat, budget()))
        self.assertEqual(seen, [(executor, threading.main_thread())])
        executor.note_layout({"revision": 2, "collections": [], "objects": []})   # Off every lane: at once.
        self.assertEqual(len(seen), 2)

    def test_only_a_request_that_may_write_the_part_holds_its_read_back(self):
        chat, read, edit = FakeChat(), Gate(), Gate()
        chat.work.append(lambda c: worker_dispatch.ask(read.request))
        worker_dispatch.pump(chat, budget())
        self.assertTrue(worker_dispatch.waiting(chat))
        self.assertFalse(worker_dispatch.writing(chat))   # A read in flight: the part stays readable (P1-9 P2).
        read.release.set()
        while worker_dispatch.waiting(chat):
            time.sleep(0.005)
        worker_dispatch.pump(chat, budget())
        chat.work.append(lambda c: worker_dispatch.ask(edit.request, effects=True))
        worker_dispatch.pump(chat, budget())
        self.assertTrue(worker_dispatch.writing(chat))    # An edit may save the part: its read waits.
        edit.release.set()
        while worker_dispatch.waiting(chat):
            time.sleep(0.005)
        self.assertFalse(worker_dispatch.writing(chat))

    def test_a_save_without_a_layout_still_reaches_the_listeners(self):
        from angelo_agent import remote_executor
        executor = remote_executor.RemoteExecutor(
            SimpleNamespace(ready={"layout": {"revision": 1, "collections": ["Parts"], "objects": []}}), "w1")
        executor._ask_bound = lambda *args, **kwargs: {"result": {"success": True}, "saved": "/part.blend",
                                                       "layout": None, "revision": 2}
        seen = []
        listener = lambda found: seen.append(found.part_layout["revision"])   # noqa: E731
        remote_executor.part_listeners.append(listener)
        self.addCleanup(remote_executor.part_listeners.remove, listener)
        executor.execute("angelo_create_cube", {"name": "Cube", "size": 1, "location": [0, 0, 0]})
        self.assertEqual(seen, [2])   # Read again at the new revision, the last known tree (P1-9 P9).
        self.assertEqual(executor.part_layout["collections"], ["Parts"])

    def test_on_main_runs_at_once_off_every_lane(self):
        ran = []
        worker_dispatch.on_main(lambda: ran.append(threading.current_thread()))
        self.assertEqual(ran, [threading.main_thread()])


class QueuedWorkTests(unittest.TestCase):
    def tearDown(self):
        worker_dispatch.close_all()

    def test_queued_work_of_a_chat_that_leaves_its_worker_reaches_its_owner(self):
        chat = FakeChat()
        chat.has_dispatchable_tools = False
        ran, failures = [], []
        worker_dispatch.submit(chat, lambda: ran.append("message"), lambda error: failures.append(
            (type(error).__name__, threading.current_thread())))
        self.assertTrue(worker_dispatch.dispatchable(chat))   # Queued work brings the UI pump back at once (L-4).
        chat.remote_executor = None
        worker_dispatch.forget(chat)
        self.assertEqual(ran, [])
        self.assertEqual(failures, [("WorkerGone", threading.main_thread())])

    def test_work_queued_behind_a_step_under_way_is_reported_when_that_step_ends(self):
        chat, gate = FakeChat(), Gate()
        chat.work.append(asking(gate))
        worker_dispatch.pump(chat, budget())
        ran, failures = [], []
        worker_dispatch.submit(chat, lambda: ran.append("message"), failures.append)
        chat.remote_executor = None
        worker_dispatch.forget(chat)   # Busy: the step under way finishes first.
        gate.release.set()
        while worker_dispatch.waiting(chat):
            time.sleep(0.005)
        worker_dispatch.pump(chat, budget())
        self.assertEqual(ran, [])
        self.assertEqual([type(error).__name__ for error in failures], ["WorkerGone"])

    def test_an_approval_its_worker_never_got_ends_its_call(self):
        chat = FakeChat()
        chat.has_dispatchable_tools = False
        call = SimpleNamespace(id="c1", executing=False)
        chat.pending, finished = {"c1": call}, []
        chat.finish_tool = lambda target, result: finished.append((target, result["error_code"]))
        worker_dispatch.approve(chat, call)
        chat.remote_executor = None
        worker_dispatch.forget(chat)
        self.assertEqual(finished, [(call, "CANCELLED")])


class DeliveryStopTests(unittest.TestCase):
    """A Stop the main thread runs while the lane prepares a message (``worker_routing.deliver``)."""

    def tearDown(self):
        worker_dispatch.close_all()

    def deliver(self, chat, prepare):
        from unittest.mock import patch
        from angelo_agent import worker_routing
        sent, failures = [], []
        with patch.object(worker_routing, "prepare_turn", prepare):
            worker_routing.deliver(chat, None, "hello", lambda turn: sent.append(chat._delivery_stops), failures.append)
            worker_dispatch.settle(chat)
        return sent, failures

    def test_a_stop_during_preparation_ends_the_delivery_before_the_turn(self):
        chat = FakeChat()
        chat.has_dispatchable_tools, chat.stops = False, 0

        def prepare(controller, window, message):
            controller.stops += 1   # The person's Stop, run on the main thread while the lane waited.
            return SimpleNamespace(commit=lambda: None)
        sent, failures = self.deliver(chat, prepare)
        self.assertEqual(sent, [])
        self.assertEqual([type(error).__name__ for error in failures], ["DeliveryStopped"])

    def test_without_a_stop_the_turn_is_sent_with_its_fence_set(self):
        chat = FakeChat()
        chat.has_dispatchable_tools, chat.stops = False, 4
        sent, failures = self.deliver(chat, lambda controller, window, message: SimpleNamespace(commit=lambda: None))
        self.assertEqual((sent, failures), ([4], []))
        self.assertIsNone(chat._delivery_stops)

    def test_a_stop_during_the_request_snapshot_ends_it_too(self):
        from angelo_agent import worker_routing
        target = SimpleNamespace(stops=1, _delivery_stops=0)
        executor = SimpleNamespace(capture_checkpoint=lambda chat: {"id": "snap"})
        with self.assertRaises(worker_routing.DeliveryStopped):
            worker_routing.capture(executor, target)
        target._delivery_stops = None   # Outside a delivery the snapshot is only a snapshot.
        self.assertEqual(worker_routing.capture(executor, target), {"id": "snap"})


class ToolPumpTests(unittest.TestCase):
    def tearDown(self):
        worker_dispatch.close_all()

    def test_the_ui_pump_steps_worker_chats_on_their_lanes_and_pumps_the_rest_here(self):
        window_chat, worker_chat, gate = FakeChat("window", remote=False), FakeChat("worker"), Gate()
        window_chat.work.append(code("window"))
        worker_chat.work.append(asking(gate))
        calls = []
        original = window_chat.pump_tools
        window_chat.pump_tools = lambda *a, **k: (calls.append(threading.current_thread().name), original(*a, **k))
        started = time.monotonic()
        tool_pump.pump_controller_tools([window_chat, worker_chat], 0, "executor", "document",
                                        lambda target, call: None, budget=budget())
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(calls, [threading.main_thread().name])
        self.assertTrue(worker_dispatch.waiting(worker_chat))
        gate.release.set()


    def test_lanes_share_the_ticks_budget_and_the_rest_go_first_next_tick(self):
        chats = [FakeChat("worker-%d" % n) for n in range(4)]
        for chat in chats:
            chat.work.append(code(chat.state["id"], lambda c: time.sleep(0.005)))
        started = time.monotonic()
        cursor = tool_pump.pump_controller_tools(chats, 0, None, None, lambda target, call: None,
                                                 budget=ToolDispatchBudget(seconds=0.008))
        spent = time.monotonic() - started
        served = [chat for chat in chats if chat.log]
        self.assertLess(len(served), 4)   # Not one budget per lane (break-it E2E-1 L-2).
        self.assertLess(spent, 0.008 + 0.006 * 2)
        self.assertEqual(cursor, len(served))   # The first one not served goes first next tick.
        tool_pump.pump_controller_tools(chats, cursor, None, None, lambda target, call: None,
                                        budget=ToolDispatchBudget(seconds=0.008))
        self.assertTrue(chats[len(served)].log)


class SourceTests(unittest.TestCase):
    def test_every_worker_request_goes_through_the_lane(self):
        source = (ADDON / "remote_executor.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("self.worker.request("), source.count("off_main(lambda: self.worker.request("))

    def test_the_lane_never_imports_blender(self):
        source = (ADDON / "worker_dispatch.py").read_text(encoding="utf-8")
        self.assertNotIn("import bpy", source)


if __name__ == "__main__":
    unittest.main()
