# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Worker lanes run while the main thread is away in another chat's tools (``worker_dispatch.away``, lane g3b).

The lead's tool pump (a view, a script, a merge) is an away region: once it has lasted a tick, each worker chat's lane
takes the baton itself. Pinned here with real threads, ordered by events (a negative check waits a bounded time):

* a lane carries on when its worker answers during the region, and delivers; an idle lane dispatches a call admitted
  meanwhile; a brief queued before the region goes out during it;
* no chat's code runs on two threads at once: the main thread touching a worker chat mid-region (a step, a forget, the
  worker family's tools) first waits out the lane's current section (``reclaim``), never more: a lane waiting on its
  worker is not waited for;
* nothing a lane leaves for the main thread (``on_main``) runs inside the region; it runs on the main thread after;
* a short pump never lets a lane run (the grace), and a chat whose event queue is half full does not run on;
* with no worker chat, a region is one check and changes nothing;
* a real ``Controller`` bound to a worker: its calls run on its lane in a region without touching ``bpy`` (a recording
  ``bpy`` module), and a Stop after the region ends the turn exactly as without regions.

The in-Blender proof (a real lead script, a real worker) is ``test_blender_parallel_away.py``.
"""

import ast
import queue
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_worker_dispatch import ADDON, FakeChat, Gate, asking, budget, code  # noqa: E402
from angelo_agent import tool_pump, worker_dispatch  # noqa: E402
try:   # Absent before break-it 19C's fixes: only the tests that use them fail there.
    from angelo_agent import event_queue, worker_away  # noqa: E402
except ImportError:
    event_queue = worker_away = None

WAIT = 5.0   # Generous: every positive wait ends on an event long before it.


class Lead:
    """A chat that is not a worker's, with a call to run: its tool pump holds the main thread while ``during`` runs (a
    view, a script)."""

    def __init__(self, during=None, work=True):
        self.state, self.remote_executor, self.has_dispatchable_tools = {"id": "lead"}, None, work
        self.during, self.thread = during, None

    def pump_tools(self, executor, document, apply_mutation=None, budget=None, commit_mutation=None):
        self.thread = threading.current_thread()
        if self.during is not None:
            self.during()


def tick(*chats):
    """One UI tick: the lead first, then the worker chats (``tool_pump``)."""
    return tool_pump.pump_controller_tools(list(chats), 0, None, None, lambda target, call: None, budget=budget())


def a_lane(chat):
    """The chat's lane, made by a first idle step (as the UI pump's first tick of a bound chat), its chat in a turn
    (a lane with no queued action looks for work only then: calls are admitted only in a turn)."""
    chat.busy = getattr(chat, "busy", True)
    work, chat.work = chat.work, []
    worker_dispatch.pump(chat, budget())
    chat.work = work
    return worker_dispatch.lane(chat, create=False)


def until(predicate, timeout=WAIT):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.001)
    return predicate()


class AwayTests(unittest.TestCase):
    def tearDown(self):
        worker_dispatch.close_all()

    def test_a_lane_carries_on_and_delivers_when_its_worker_answers_during_the_region(self):
        chat, gate, delivered, seen = FakeChat(), Gate(), threading.Event(), {}

        def call(c):
            c.enter("before")
            c.leave()
            worker_dispatch.ask(gate.request)
            c.enter("delivered")
            delivered.set()
            c.leave()
        chat.work.append(call)
        worker_dispatch.pump(chat, budget())
        self.assertTrue(worker_dispatch.waiting(chat))

        def during():   # The lead's long view: its worker answers meanwhile.
            gate.release.set()
            seen["delivered"] = delivered.wait(WAIT)
        tick(Lead(during), chat)
        self.assertTrue(seen["delivered"])   # Before the region ended, not at the next step.
        self.assertEqual(chat.log, ["before", "delivered"])
        self.assertEqual((chat.threads, chat.overlaps), ({"AngeloWorkerLane"}, 0))

    def test_a_call_admitted_during_the_region_is_dispatched_in_it(self):
        chat, ran, seen = FakeChat(), threading.Event(), {}
        chat.has_dispatchable_tools, chat.busy = False, True   # In a turn: its runtime may admit a call any time.
        a_lane(chat)

        def admit():   # A provider thread admits the runtime's next call.
            chat.work.append(code("admitted", lambda c: ran.set()))
            chat.has_dispatchable_tools = True

        def during():
            threading.Thread(target=admit).start()
            seen["ran"] = ran.wait(WAIT)
        tick(Lead(during), chat)
        self.assertTrue(seen["ran"])
        self.assertEqual((chat.log, chat.overlaps), (["admitted"], 0))

    def test_a_brief_queued_before_a_long_call_goes_out_during_it(self):
        chat, sent, seen = FakeChat(), threading.Event(), {}
        chat.has_dispatchable_tools = False
        a_lane(chat)
        worker_dispatch.submit(chat, lambda: sent.set())   # ``worker_routing.deliver`` after an adoption.

        def during():
            seen["sent"] = sent.wait(WAIT)
        tick(Lead(during), chat)
        self.assertTrue(seen["sent"])

    def test_the_main_thread_touching_a_worker_chat_mid_region_waits_out_the_lanes_section(self):
        chat, inside, go, seen = FakeChat(), threading.Event(), threading.Event(), {}
        lane = a_lane(chat)
        chat.work.append(code("lane section", lambda c: (inside.set(), go.wait(WAIT))))

        def release_once_reclaiming():   # The section ends only once the main thread waits for it.
            until(lambda: not worker_dispatch._away["on"])
            go.set()

        def during():
            seen["inside"] = inside.wait(WAIT)
            threading.Thread(target=release_once_reclaiming).start()
            worker_dispatch.pump(chat, budget())   # A worker chat's step, from inside the region (``settle``).
            code("main thread")(chat)
            seen["free"] = lane._free
        tick(Lead(during), chat)
        self.assertTrue(seen["inside"])
        self.assertEqual(chat.log[:2], ["lane section", "main thread"])
        self.assertEqual((chat.overlaps, seen["free"]), (0, False))

    def test_the_chats_own_main_thread_entries_end_the_region_first(self):
        """Its events (a re-entered UI pump), a Stop, and a document load's ``set_document`` (break-it 19C L-1)."""
        for entry in ("pump_events", "stop", "set_document"):
            with self.subTest(entry=entry):
                chat, inside, go, seen = FakeChat(entry), threading.Event(), threading.Event(), {}
                setattr(chat, entry, lambda *args, chat=chat: code("main: " + entry)(chat))
                a_lane(chat)
                chat.work.append(code("lane section", lambda c: (inside.set(), go.wait(WAIT))))

                def during():
                    seen["inside"] = inside.wait(WAIT)
                    threading.Thread(target=lambda: (until(lambda: not worker_dispatch._away["on"]), go.set())).start()
                    getattr(chat, entry)(*(("document",) if entry == "set_document" else ()))
                tick(Lead(during), chat)
                self.assertTrue(seen["inside"])
                self.assertEqual((chat.log, chat.overlaps), (["lane section", "main: " + entry], 0))
                worker_dispatch.close_all()

    def test_what_a_free_section_left_for_the_main_thread_runs_when_its_lane_ends_in_the_region(self):
        """Forgotten (an unbind in a merge or a load), replaced or closed in the same region: it still runs, once, on
        the main thread (break-it 19C M-2: five of five were lost in a real worker)."""
        for ending in ("forget", "replace", "close_all"):
            with self.subTest(ending=ending):
                chat, queued, ran = FakeChat(ending), threading.Event(), []
                a_lane(chat)
                chat.work.append(code("saved its part", lambda c: (worker_dispatch.on_main(
                    lambda: ran.append(threading.current_thread())), queued.set())))

                def during():
                    self.assertTrue(queued.wait(WAIT))
                    self.assertTrue(until(lambda: not worker_dispatch.lane(chat, create=False)._free))
                    if ending == "forget":
                        chat.remote_executor = None
                        worker_dispatch.forget(chat)
                    elif ending == "replace":
                        worker_dispatch.lane(FakeChat(ending))   # Another chat object with this id (a reload).
                    else:
                        worker_dispatch.close_all()
                    self.assertEqual(ran, [threading.main_thread()])   # At once, never after the region.
                tick(Lead(during), chat)
                self.assertEqual(ran, [threading.main_thread()])
                worker_dispatch.close_all()

    def test_forget_mid_region_waits_out_the_section_and_reports_queued_work(self):
        chat, inside, go, failures = FakeChat(), threading.Event(), threading.Event(), []
        lane = a_lane(chat)
        chat.work.append(code("lane section", lambda c: (inside.set(), go.wait(WAIT))))

        def during():   # A merge unbinds the chat while its lane runs on (``worker_routing.unbind``).
            self.assertTrue(inside.wait(WAIT))
            worker_dispatch.submit(chat, lambda: None, failures.append)
            threading.Thread(target=lambda: (until(lambda: not worker_dispatch._away["on"]), go.set())).start()
            chat.remote_executor = None
            worker_dispatch.forget(chat)
            code("unbound")(chat)
        tick(Lead(during), chat)
        self.assertEqual((chat.log, chat.overlaps), (["lane section", "unbound"], 0))
        self.assertTrue(lane.closed)
        self.assertEqual([type(error).__name__ for error in failures], ["WorkerGone"])

    def test_the_regions_end_waits_for_a_section_never_for_a_worker(self):
        chat, gate, seen = FakeChat(), Gate(), {}
        a_lane(chat)
        chat.work.append(asking(gate))

        def during():
            seen["sent"] = gate.sent.wait(WAIT)   # The lane ran its step up to its worker request, by itself.
        started = time.monotonic()
        tick(Lead(during), chat)
        self.assertTrue(seen["sent"])
        self.assertLess(time.monotonic() - started, WAIT)
        self.assertTrue(worker_dispatch.waiting(chat))   # Still waiting on its worker: nobody waited for it.
        gate.release.set()
        self.assertTrue(until(lambda: not worker_dispatch.waiting(chat)))
        worker_dispatch.pump(chat, budget())
        self.assertEqual((chat.log, chat.overlaps), (["before", "after"], 0))

    def test_what_a_lane_leaves_for_the_main_thread_runs_there_after_the_region(self):
        chat, queued, calls, seen = FakeChat(), threading.Event(), [], {}
        a_lane(chat)
        bpy = MagicMock()

        def section(c):
            worker_dispatch.on_main(lambda: calls.append((threading.current_thread(), bpy.data.objects)))
            queued.set()
        chat.work.append(section)

        def during():
            seen["queued"] = queued.wait(WAIT)
            seen["inside"] = list(calls)
        tick(Lead(during), chat)
        self.assertTrue(seen["queued"])
        self.assertEqual(seen["inside"], [])   # Never inside the lead's call (an operator's Undo step).
        self.assertEqual([thread for thread, _ in calls], [threading.main_thread()])

    def test_a_chat_whose_work_cannot_be_read_still_has_a_lane(self):
        chat, seen = FakeChat(), {}
        a_lane(chat)

        class Broken(FakeChat):
            @property
            def has_dispatchable_tools(self):
                raise RuntimeError("unreadable")
        chat.__class__ = Broken
        chat.work.append(code("ran"))

        def during():   # The free step runs and reports; the lane lives on for the next step.
            seen["ran"] = until(lambda: "ran" in chat.log)
        tick(Lead(during), chat)
        self.assertTrue(seen["ran"])
        self.assertTrue(worker_dispatch.lane(chat, create=False).thread.is_alive())

    def test_lanes_in_a_turn_cost_nothing_outside_a_region(self):
        """Eight worker chats whose models think: no lane wakes, no CPU, until a region (break-it 19C M-1: they woke
        every 10 ms, 15 ms of CPU a second)."""
        chats = [FakeChat("thinking-%d" % n) for n in range(8)]
        wakes = [0]
        for chat in chats:
            chat.has_dispatchable_tools = False
            lane = a_lane(chat)
            wait = lane._turn.wait

            def counted(timeout=None, wait=wait):
                wakes[0] += 1
                return wait(timeout)
            lane._turn.wait = counted
        for chat in chats:   # Each lane is back in its wait (a step's end notified it once).
            lane = worker_dispatch.lane(chat, create=False)
            with lane._turn:
                lane._turn.notify_all()
        time.sleep(0.05)
        wakes[0], cpu, wall = 0, time.process_time(), time.perf_counter()
        time.sleep(1.0)
        cpu, wall = time.process_time() - cpu, time.perf_counter() - wall
        self.assertEqual(wakes[0], 0)
        self.assertLess(cpu / wall * 1000, 2.0)   # ms of CPU a second, the whole process.
        region = worker_dispatch.away(Lead())   # A short region with work: the clock's one wake, after it ended.
        worker_dispatch.back(region)
        time.sleep(worker_away.AWAY_AFTER * 4)
        self.assertEqual(wakes[0], 0)   # The clock found it over and woke nobody.

    def test_two_lanes_counting_lose_nothing(self):
        before = worker_dispatch.STATS["io"]
        threads = [threading.Thread(target=lambda: [worker_dispatch._count("io") for _ in range(20000)])
                   for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(worker_dispatch.STATS["io"] - before, 80000)   # Break-it 19C L-3.

    def test_a_short_pump_never_lets_a_lane_run(self):
        chat, sent, seen = FakeChat(), threading.Event(), {}
        chat.has_dispatchable_tools = False
        a_lane(chat)
        worker_dispatch.submit(chat, lambda: sent.set())
        with patch.object(worker_away, "AWAY_AFTER", 60.0):   # Every region is "short" now.
            def during():
                seen["sent"] = sent.wait(0.3)
            tool_pump.pump_controller_tools([Lead(during)], 0, None, None, lambda t, c: None, budget=budget())
        self.assertFalse(seen["sent"])   # Ran at the next step instead, as before regions.
        worker_dispatch.pump(chat, budget())
        self.assertTrue(sent.is_set())

    def test_a_chat_whose_event_queue_is_half_full_waits_for_the_main_thread(self):
        chat, sent, seen = FakeChat(), threading.Event(), {}
        chat.has_dispatchable_tools, chat.events = False, queue.Queue(maxsize=8)
        for n in range(4):
            chat.events.put(("delta", {"n": n}))
        a_lane(chat)
        worker_dispatch.submit(chat, lambda: sent.set())

        def during():
            seen["sent"] = sent.wait(0.3)
        tool_pump.pump_controller_tools([Lead(during)], 0, None, None, lambda t, c: None, budget=budget())
        self.assertFalse(seen["sent"])

    def test_without_worker_chats_a_region_is_one_check_and_changes_nothing(self):
        self.assertEqual(worker_dispatch._lanes, {})
        before, lead = dict(worker_dispatch._away), Lead()
        self.assertIsNone(worker_dispatch.away())
        tool_pump.pump_controller_tools([lead], 0, None, None, lambda t, c: None, budget=budget())
        self.assertIs(lead.thread, threading.main_thread())
        self.assertEqual((worker_dispatch._away, worker_dispatch._lanes), (before, {}))

    def test_reclaim_off_the_main_thread_does_nothing(self):
        chat, inside, go, seen = FakeChat(), threading.Event(), threading.Event(), {}
        a_lane(chat)
        chat.work.append(code("progress", lambda c: (worker_dispatch.reclaim(), inside.set(), go.wait(WAIT))))

        def during():   # A worker's own angelo_workers_progress calls it on its lane: the region stays open.
            seen["inside"] = inside.wait(WAIT)
            seen["on"] = worker_dispatch._away["on"]
            go.set()
        tick(Lead(during), chat)
        self.assertEqual((seen["inside"], seen["on"]), (True, True))


class EventQueueTests(unittest.TestCase):
    """A chat's events while the main thread is away: none blocks its sender, none is lost, order kept (break-it 19C
    M-3: a full queue blocked the provider 2 s per event, then dropped it)."""

    def setUp(self):
        from angelo_agent.controller import Controller
        self.directory = tempfile.TemporaryDirectory()
        self.chat = Controller(self.directory.name)
        self.chat.events = queue.Queue(maxsize=8)

    def tearDown(self):
        self.chat.close()
        self.directory.cleanup()

    def drained(self):
        """The main thread's turns: flush, then take what the queue has (``pump_events`` takes 256 a turn)."""
        found = []
        while True:
            event_queue.flush(self.chat)
            if self.chat.events.empty():
                return found
            while not self.chat.events.empty():
                found.append(self.chat.events.get_nowait())

    def test_a_full_queue_never_blocks_its_sender_nor_drops_the_turns_end(self):
        for n in range(8):
            self.chat.emit("activity", {"id": "call-%d" % n, "status": "queued"})
        started = time.monotonic()
        self.chat.emit("finished", {"status": "completed"})
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertNotIn("Event queue full", str(self.chat.status))
        events = self.drained()
        self.assertEqual([kind for kind, _ in events], ["activity"] * 8 + ["finished"])

    def test_a_long_stream_joins_its_fragments_and_keeps_its_order(self):
        for n in range(4):
            self.chat.emit("activity", {"id": "call-%d" % n, "status": "queued"})
        for n in range(4):
            self.chat.emit("delta", {"item": "reply", "text": "%d," % n})
        self.assertEqual(self.chat.events.qsize(), 4)   # Past the mark they are held (before: the queue filled).
        for n in range(4, 1000):
            self.chat.emit("delta", {"item": "reply", "text": "%d," % n})
        self.chat.emit("thinking", {"item": "think", "text": "a"})
        self.chat.emit("thinking", {"item": "think", "text": "b"})
        self.chat.emit("delta", {"item": "reply", "text": "X", "replace": True})   # Never joined: it replaces.
        self.chat.emit("finished", {"status": "completed"})
        self.assertLessEqual(len(self.chat._held_events), 4)
        events = self.drained()
        self.assertEqual([kind for kind, _ in events], ["activity"] * 4 + ["delta", "thinking", "delta", "finished"])
        self.assertEqual(events[4][1]["text"], "".join("%d," % n for n in range(1000)))
        self.assertEqual(events[5][1]["text"], "ab")
        self.assertEqual(events[6][1]["text"], "X")


class SourceTests(unittest.TestCase):
    def test_a_lanes_counts_take_the_lock(self):
        source = (ADDON / "worker_dispatch.py").read_text(encoding="utf-8")
        self.assertNotIn('STATS["io"] += 1', source)
        self.assertNotIn('STATS["free_steps"] += 1', source)
        self.assertNotIn('STATS["free_resumes"] += 1', source)

    def test_the_worker_familys_tools_reclaim_before_anything_else(self):
        tree = ast.parse((ADDON / "worker_sessions.py").read_text(encoding="utf-8"))
        for name in ("precondition", "execute"):
            function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
            calls = [node for node in function.body if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)]
            first = ast.unparse(calls[0].value) if calls else ""
            self.assertIn(first, {"reclaim()", "worker_dispatch.reclaim()"}, name)
            self.assertIs(calls[0], next(node for node in function.body if not isinstance(node, ast.ImportFrom)), name)

    def test_the_tool_pump_marks_every_other_chats_pump_as_a_region(self):
        source = (ADDON / "tool_pump.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("controller.pump_tools("), 1)
        self.assertIn("region = worker_dispatch.away(controller)", source)
        self.assertIn("worker_dispatch.back(region)", source)


class Recorder(types.ModuleType):
    """A ``bpy`` that records which thread touched it."""

    def __init__(self):
        super().__init__("bpy")
        self.touched = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        self.touched.append((threading.current_thread().name, name))
        return MagicMock()


class FakeRemote:
    """A worker chat's executor (``RemoteExecutor``'s interface): each call waits on its lane for the test's answer."""

    def __init__(self, gates):
        self.document_key = ("angelo-worker", "w1", ("serial", 1))
        self.window = self.studio_context = self.script_file = self.observation_store = None
        self.gates, self.calls = gates, 0

    def precondition(self, name, arguments):
        return None

    @staticmethod
    def supports_native_job(name, arguments=None):
        return False

    def usage_label(self, name, arguments, precondition=None):
        return ""

    def execute(self, name, arguments):
        gate = self.gates[self.calls]
        self.calls += 1
        return dict(worker_dispatch.ask(gate.request), call=self.calls)


class RealControllerTests(unittest.TestCase):
    """A real ``Controller`` bound to a worker: call 1's answer arrives while the lead holds the main thread, the
    runtime then asks call 2, and the person's Stop is processed after the region (Blender's event loop cannot run
    during it). The outcome is the one the chat has without regions."""

    def setUp(self):
        from angelo_agent.controller import Controller
        self.directory = tempfile.TemporaryDirectory()
        self.gates = [Gate(), Gate()]
        self.controller = Controller(self.directory.name)
        self.remote = FakeRemote(self.gates)
        self.controller.remote_executor, self.remote.studio_context = self.remote, self.controller
        self.controller.busy = True
        self.controller.set_document(None)   # The worker's own document (``bound_target``).
        self.controller.state["options"]["approval"] = "AUTO_BOUNDED"

    def tearDown(self):
        worker_dispatch.close_all()
        self.controller.remote_executor = None
        self.controller.close()
        self.directory.cleanup()

    def scenario(self, regions):
        chat, first, second = self.controller, [], []
        answered = threading.Event()

        def runtime_next(result):   # The runtime's next request after the result, on a thread of its own.
            first.append(result)
            threading.Thread(target=lambda: (chat.request_tool("two", "angelo_inspect_scene", {}, second.append),
                                             answered.set())).start()
        chat.request_tool("one", "angelo_inspect_scene", {}, runtime_next)
        worker_dispatch.pump(chat, budget())
        self.assertTrue(self.gates[0].sent.wait(WAIT))
        if regions:
            seen = {}

            def during():
                self.gates[0].release.set()
                seen["second_sent"] = answered.wait(WAIT) and self.gates[1].sent.wait(WAIT)
            tick(Lead(during), chat)
            self.assertTrue(seen["second_sent"])   # Call 1 delivered and call 2 sent while the lead held the thread.
        else:
            self.gates[0].release.set()
            self.assertTrue(until(lambda: not worker_dispatch.waiting(chat)))
            worker_dispatch.pump(chat, budget())
            self.assertTrue(answered.wait(WAIT))
            worker_dispatch.pump(chat, budget())
            self.assertTrue(self.gates[1].sent.wait(WAIT))
        events = chat.events.qsize()
        chat.stop()   # The person's Stop, processed once the main thread is back.
        self.gates[1].release.set()
        self.assertTrue(until(lambda: second or (worker_dispatch.pump(chat, budget()) and False)))
        return ([(r.get("call"), r.get("error_code")) for r in first], [(r.get("call"), r.get("error_code"))
                                                                       for r in second], events, chat.pending)

    def test_a_real_chats_calls_run_on_its_lane_in_a_region_without_touching_bpy(self):
        recorder = Recorder()
        with patch.dict(sys.modules, {"bpy": recorder}):
            first, second, events, pending = self.scenario(regions=True)
            worker_dispatch.on_main(lambda: recorder.data)   # The instrument records main-thread use too.
        self.assertEqual(first, [(1, None)])
        self.assertEqual(len(second), 1)   # Exactly once: never lost, never duplicated by the Stop.
        self.assertEqual(pending, {})
        self.assertGreater(events, 0)   # Its rows' events waited for the main thread, which drew them after.
        self.assertEqual({who for who, _ in recorder.touched}, {"MainThread"})   # Never a lane, nor any other.
        worker_dispatch.close_all()
        self.assertNotIn("pump_events", vars(self.controller))   # Its class's own entries again once unbound.

    def test_the_outcome_equals_the_one_without_regions(self):
        with_regions = self.scenario(regions=True)
        self.tearDown()
        self.setUp()
        without = self.scenario(regions=False)
        self.assertEqual(with_regions[:2], without[:2])
        self.assertEqual((with_regions[3], without[3]), ({}, {}))


if __name__ == "__main__":
    unittest.main()
