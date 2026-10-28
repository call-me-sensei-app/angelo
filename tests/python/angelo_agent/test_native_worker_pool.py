# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Warm workers without Blender: a running worker taking a new part file, workers launched ahead, and a done worker's
process taking a waiting part (``worker_process``, ``worker_switch``, ``worker_pool``, ``worker_routing.detach``).

Pinned here: a started worker whose part changed switches its document over its connection instead of a new launch,
and one that cannot switch is stopped and launched afresh on the new part (a part never fails for want of a warm
worker); the worker's switch saves nothing of the earlier part, drops every per-chat state and reloads the factory
start-up before making the new part, and refuses while anything stages or waits; workers launch ahead only for a
lead's own turn on a machine at the bar, up to the free places, and stop at the turn's end without a start, at the
idle bound, when live workers would pass the limit and with the add-on; a start takes one before launching; a done
worker's process takes a waiting part only when its chat is finished, leaving that chat with its part and layout.
The in-Blender proof is ``test_blender_worker_pool.py``.
"""

import ast
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import angelo_agent  # noqa: E402
from angelo_agent import (parallel_access, parallel_capability, worker_channel, worker_host, worker_pool,  # noqa: E402
                          worker_process, worker_routing, worker_sessions, worker_switch)
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
import test_native_parallel_workers_transport as transport  # noqa: E402

CAPABLE = parallel_capability.Capability(True, "GPU", 64.0, 16, 8)
BELOW = parallel_capability.Capability(False, "", 64.0, 16, 0, ("gpu",))


def a_fake_worker(answer, gate=None):
    """A worker's end of a real socket pair: ready (once ``gate`` is set), then ``answer(request)`` for each request
    (None: no answer), ``hello`` answered as a worker does (not listed in ``seen``)."""
    listener = worker_channel.SocketPairListener()
    theirs = worker_channel.connect("fd:%d" % __import__("os").dup(listener.pass_fds()[0]))
    seen = []

    def serve():
        if gate is not None:
            gate.wait(5)
        theirs.send({"seq": 0, "ready": {"ok": True, "pid": 4321, "layout": {"revision": 0, "first": True}}})
        while True:
            try:
                frame = theirs.receive()
            except Exception:   # noqa: BLE001 - the connection closed: the worker ends.
                return
            if frame["request"].get("op") == "hello":   # Every worker answers it (a part's launch asks it).
                theirs.send({"seq": frame["seq"], "response": {"document": ["serial", 1]}})
                continue
            seen.append(frame["request"])
            response = answer(frame["request"])
            if response is not None:
                theirs.send({"seq": frame["seq"], "response": response})
    thread = threading.Thread(target=serve, daemon=True)
    return SimpleNamespace(child_argument=lambda: "fd:99", pass_fds=lambda: (),
                           started=lambda: (listener.started(), thread.start()),
                           accept=lambda pid, alive=None: listener.accept(process_id=None), close=listener.close), seen


class Process:
    def __init__(self):
        self.pid, self.code = 4321, None

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        self.code = 0
        return 0

    def kill(self):
        self.code = -9


@transport.POSIX_ONLY
class SwitchTests(unittest.TestCase):
    def setUp(self):
        # Every worker a test starts joins the process-wide set of live workers, each a place in use under the
        # worker limit (worker_places), until its process is seen to exit; a fresh set per test keeps the ones
        # started here from filling the places of every later test file.
        patcher = mock.patch.object(worker_process, "_live", set())
        patcher.start()
        self.addCleanup(patcher.stop)

    def worker(self, answer, launched, gate=None):
        temporary = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temporary, True)
        listeners = []

        def listener():
            made, seen = a_fake_worker(answer, gate)
            listeners.append(seen)
            return made

        def popen(command, **_options):
            launched.append(command)
            return Process()
        worker = worker_process.WorkerProcess(Path(temporary) / "a" / "part.blend", binary="/app/Angelo",
                                              script="/s/h.py", environ={}, platform="darwin", popen=popen,
                                              listener=listener, temporary=temporary)
        self.addCleanup(worker.stop)
        return worker, listeners, Path(temporary)

    def test_a_running_worker_takes_a_new_part_without_a_launch(self):
        launched = []
        worker, seen, root = self.worker(lambda request: {"value": request["part"], "document": [2, 9],
                                                          "layout": {"revision": 0, "new": True}}, launched)
        worker.start(5)
        scratch = root / "scratch"
        scratch.mkdir()
        worker.scratch = scratch
        worker.part = root / "b" / "part.blend"
        self.assertEqual(worker.start(5)["layout"], {"revision": 0, "new": True})
        self.assertEqual(len(launched), 1)   # One process, two parts.
        self.assertEqual([r["op"] for r in seen[0]], ["switch_part"])
        self.assertEqual(seen[0][0]["part"], str(root / "b" / "part.blend"))
        self.assertEqual((worker.serving, worker.parts, worker.how), (root / "b" / "part.blend", 1, "switched"))
        self.assertFalse(scratch.exists())   # A scratch part it left is removed.
        self.assertEqual(worker.start(5)["layout"], {"revision": 0, "new": True})   # Already there: nothing sent.
        self.assertEqual((len(seen[0]), worker.how), (1, "ready"))

    def test_a_worker_that_cannot_switch_is_launched_afresh_on_the_new_part(self):
        launched = []
        refusal = {"error": {"type": "RefusalError", "message": "A program stages", "code": "UNSUPPORTED"}}
        worker, seen, root = self.worker(lambda request: refusal, launched)
        worker.start(5)
        first = worker.private
        worker.part = root / "b" / "part.blend"
        worker.start(5)
        self.assertEqual(len(launched), 2)
        self.assertEqual(launched[1][launched[1].index("--file") + 1], str(root / "b" / "part.blend"))
        self.assertFalse(Path(first).exists())   # The old process was stopped and its private folder removed.
        self.assertEqual((worker.how, worker.serving), ("launched", root / "b" / "part.blend"))

    def test_a_part_taking_a_worker_still_launching_waits_for_it_then_switches(self):
        launched, gate = [], threading.Event()
        worker, seen, root = self.worker(lambda request: {"value": request["part"], "layout": {}}, launched, gate)
        thread = threading.Thread(target=worker.start, args=(5,), daemon=True)
        thread.start()
        time.sleep(0.05)
        worker.part = root / "b" / "part.blend"   # Taken for a part while its launch is under way.
        second = threading.Thread(target=worker.start, args=(5,), daemon=True)
        second.start()
        time.sleep(0.05)
        self.assertTrue(second.is_alive())   # It waits for the launch, never launches beside it.
        gate.set()
        thread.join(5), second.join(5)
        self.assertEqual(len(launched), 1)
        self.assertEqual((worker.serving, worker.how), (root / "b" / "part.blend", "switched"))
        self.assertEqual([r["op"] for r in seen[0]], ["switch_part"])


class HostSwitchTests(unittest.TestCase):
    def host(self):
        calls = []
        ops = SimpleNamespace(wm=SimpleNamespace(
            read_homefile=lambda **kw: calls.append(("read_homefile", kw)) or {"FINISHED"},
            save_as_mainfile=lambda **kw: calls.append(("save", kw)) or {"FINISHED"}))
        bpy = SimpleNamespace(ops=ops, data=SimpleNamespace(is_dirty=True))
        controllers = {"old-chat": object(), "other": object()}
        ui = SimpleNamespace(_controllers=controllers, document_key=lambda: (1, 2), Controller=object)
        host = worker_host.WorkerHost(ui, bpy, endpoint=None, part="/w/a/part.blend")
        host.shadows = {"old-chat": SimpleNamespace(pending={})}
        host.tables, host.turns, host.revision = {"old-chat": ("d", "t")}, {"token": object()}, 7
        host.layout = lambda: {"revision": host.revision}
        return host, calls

    def test_a_switch_keeps_nothing_of_the_earlier_part_and_never_saves_it(self):
        host, calls = self.host()
        opened = []
        with mock.patch("angelo_agent.worker_main.open_part", lambda bpy, part: opened.append(part)), \
                mock.patch("angelo_agent.native_programs.has_active_programs", return_value=False, create=True), \
                mock.patch("angelo_agent.native_programs.runtime_is_staging", return_value=False, create=True), \
                mock.patch("angelo_agent.view_cache.clear") as cleared:
            answer = host.handle({"op": "switch_part", "part": "/w/b/part.blend"})
        self.assertEqual(calls, [("read_homefile", {"use_factory_startup": True, "load_ui": False})])   # No save.
        self.assertEqual(opened, ["/w/b/part.blend"])
        self.assertEqual((host.part, host.revision, answer["layout"]), ("/w/b/part.blend", 0, {"revision": 0}))
        self.assertEqual((host.shadows, host.tables, host.turns), ({}, {}, {}))
        self.assertEqual(set(host.ui._controllers), {"other"})
        cleared.assert_called_once_with()

    def test_a_switch_is_refused_while_anything_stages_or_waits_and_changes_nothing(self):
        for staging, pending in ((True, {}), (False, {"chat/1": object()})):
            with self.subTest(staging=staging):
                host, calls = self.host()
                host.shadows["old-chat"].pending = pending
                with mock.patch("angelo_agent.native_programs.runtime_is_staging", return_value=staging,
                                create=True), self.assertRaises(RefusalError):
                    worker_switch.switch_part(host, "/w/b/part.blend")
                self.assertEqual((calls, host.part, host.revision), ([], "/w/a/part.blend", 7))
                self.assertIn("old-chat", host.shadows)
        host, calls = self.host()
        for part in ("relative/part.blend", "/w/b/part.txt", None):
            with self.subTest(part=part), self.assertRaises(RefusalError):
                worker_switch.switch_part(host, part)


class FakeWorker:
    def __init__(self, alive=True):
        self.part, self.process, self.serving = None, None, None
        self._alive, self.stopped = alive, 0

    def alive(self):
        return self._alive

    def start(self, timeout=None):
        return {"ok": True}

    def stop(self):
        self.stopped += 1
        self._alive = False


class PoolTests(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.state, True)
        self.lead = SimpleNamespace(state={"id": "lead"}, busy=True, voice=None, external_client="",
                                    _runtime_context=SimpleNamespace(allowed_tools=frozenset({"angelo_workers_start"})))
        self.ui = SimpleNamespace(_controllers={"lead": self.lead}, state_directory=lambda: self.state)
        self.bpy = SimpleNamespace(app=SimpleNamespace(background=True))
        self.launched = []

        def launch(part, **_options):
            worker = FakeWorker()
            worker.part = Path(part)
            self.launched.append(worker)
            return worker
        for target, name, value in ((worker_pool, "_pool", []), (worker_pool, "_starters", set()),
                                    (worker_pool, "_stopping", []), (worker_sessions, "_launching", []),
                                    (worker_routing, "launch", launch), (parallel_access, "enabled", lambda: True),
                                    (parallel_access, "worker_limit", lambda: 4),
                                    (parallel_capability, "_measured", [CAPABLE]),
                                    (worker_routing, "worker_snapshots", lambda controller: False),
                                    (worker_sessions, "_running", lambda: [])):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (mock.patch.dict(sys.modules, {"angelo_agent.ui": self.ui, "bpy": self.bpy}),
                        mock.patch.object(angelo_agent, "ui", self.ui, create=True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def settle(self):
        for entry in worker_pool._pool:
            self.assertTrue(entry["done"].wait(5))

    def test_a_leads_lookup_of_its_workers_launches_them_ahead_in_the_free_places_only(self):
        worker_pool.looked_up(self.lead, {"family": "TOOLS", "names": ["angelo_view"]})
        self.assertEqual(self.launched, [])   # Not its workers' tools.
        with mock.patch.object(worker_sessions, "_running", lambda: ["w1"]):
            worker_pool.looked_up(self.lead, {"family": "TOOLS", "names": ["angelo_workers_start"]})
        self.assertEqual(len(self.launched), 3)   # 4 places, 1 running: a launched-ahead worker counts as live.
        self.settle()
        self.assertTrue(all(w.ahead and w.scratch.parent.name == "ahead" for w in self.launched))
        worker_pool.looked_up(self.lead, {"family": "TOOLS", "names": ["angelo_workers_status"]})
        self.assertEqual(len(self.launched), 4)   # The next lookup fills only what freed.

    def test_never_ahead_below_the_bar_for_a_worker_voice_an_app_or_outside_the_leads_turn(self):
        names = {"family": "TOOLS", "names": ["angelo_workers_start"]}
        cases = [("below the bar", mock.patch.object(parallel_capability, "_measured", [BELOW])),
                 ("off", mock.patch.object(parallel_access, "enabled", lambda: False)),
                 ("worker chat", mock.patch.object(worker_routing, "worker_snapshots", lambda controller: True)),
                 ("voice", mock.patch.object(self.lead, "voice", object())),
                 ("app", mock.patch.object(self.lead, "external_client", "app")),
                 ("not offered", mock.patch.object(self.lead._runtime_context, "allowed_tools", frozenset())),
                 ("not in a turn", mock.patch.object(self.lead, "busy", False))]
        for label, patcher in cases:
            with self.subTest(label), patcher:
                worker_pool.looked_up(self.lead, names)
                self.assertEqual(self.launched, [])

    def test_a_start_takes_a_ready_worker_first_and_moves_it_to_its_part(self):
        worker_pool.prelaunch(self.lead)
        self.settle()
        worker_pool._pool[0]["done"].clear()   # The oldest is still launching: a ready one goes first.
        part = Path(self.state) / "parallel" / "lead" / "1-sofa" / "part.blend"
        taken = worker_pool.take(part, self.lead)
        self.assertIs(taken, self.launched[1])
        self.assertEqual(taken.part, part)
        self.assertTrue(part.parent.is_dir())
        self.assertEqual(len(worker_pool._pool), 3)
        self.assertIn("lead", worker_pool._starters)
        worker_pool._pool.clear()
        self.assertIsNone(worker_pool.take(part, self.lead))

    def test_a_part_takes_a_worker_launched_ahead_only_as_it_starts(self):
        # A queued part holds no Blender (break-it E2E-19A H-1, M-4): its preparation launches nothing, its start takes
        # a worker launched ahead, and a done worker's reused process is never swapped for one.
        worker_pool.prelaunch(self.lead)
        self.settle()
        item = worker_sessions._prepare(self.lead, {"name": "Sofa", "brief": "A sofa."}, 1)
        self.assertEqual((len(worker_pool._pool), item["worker"] in self.launched[:4]), (4, False))
        part = item["worker"].part
        worker_sessions._launch(item)
        item["thread"].join(5)
        self.assertIs(item["worker"], self.launched[0])
        self.assertEqual((item["worker"].part, len(worker_pool._pool)), (part, 3))
        self.assertEqual(item["worker"].part.parent.parent.name, "lead")
        reused = dict(item, worker=FakeWorker(), thread=None)
        worker_sessions._launch(reused, take=False)
        reused["thread"].join(5)
        self.assertNotIn(reused["worker"], self.launched)
        self.assertEqual(len(worker_pool._pool), 3)

    def test_a_restart_waiting_for_a_place_stops_one_worker_launched_ahead_at_a_time(self):
        worker_pool.prelaunch(self.lead)
        self.settle()
        gate = threading.Event()
        newest = worker_pool._pool[-1]   # Launched at one lookup: the last launched is the newest.
        newest["worker"].stop = lambda: (gate.wait(5), FakeWorker.stop(newest["worker"]))
        worker_pool.yield_place()
        worker_pool.yield_place()   # Asked again while that one still stops: nothing more stops.
        self.assertEqual(len(worker_pool._pool), 3)
        gate.set()
        for thread in list(worker_pool._stopping):
            thread.join(5)
        self.assertEqual([w.stopped for w in self.launched], [0, 0, 0, 1])
        worker_pool.yield_place()   # Its place is free now: the next newest may yield.
        self.assertEqual(len(worker_pool._pool), 2)

    def test_untaken_workers_stop_at_the_turns_end_after_the_idle_bound_and_past_the_limit(self):
        worker_pool.prelaunch(self.lead)
        self.settle()
        self.lead.busy = False   # The turn ended without a start.
        self.assertIsNone(worker_pool.tick())
        for thread in list(worker_pool._stopping):
            thread.join(5)
        self.assertEqual([w.stopped for w in self.launched], [1, 1, 1, 1])
        self.launched.clear()
        self.lead.busy = True
        worker_pool.prelaunch(self.lead)
        self.settle()
        worker_pool.take(Path(self.state) / "p" / "part.blend", self.lead)   # This turn started a part ...
        self.lead.busy = False
        self.assertEqual(worker_pool.tick(), worker_pool.TICK)   # ... so the rest wait for a later start,
        self.assertEqual(len(worker_pool._pool), 3)
        now = time.monotonic()
        with mock.patch.object(worker_sessions, "_running", lambda: ["a", "b"]):   # A relaunch took two places.
            worker_pool.tick(now)
        self.assertEqual(len(worker_pool._pool), 2)
        self.assertIsNone(worker_pool.tick(now + worker_pool.IDLE_SECONDS))   # ... up to the idle bound.
        worker_pool.release_all()
        self.assertEqual(sum(w.stopped for w in self.launched), 3)   # The taken one is its part's, never stopped here.


    def test_only_a_start_in_the_same_turn_keeps_workers_past_its_end(self):
        self.assertIsNone(worker_pool.take(Path(self.state) / "early" / "part.blend", self.lead))   # Nothing ahead.
        worker_pool._starters.add("lead")   # Left by an earlier turn whose workers were all taken.
        worker_pool.prelaunch(self.lead)
        self.settle()
        self.lead.busy = False   # This turn started none.
        self.assertIsNone(worker_pool.tick())
        self.assertEqual(worker_pool._starters, set())

    def test_a_failing_timer_stops_the_workers_it_watched(self):
        worker_pool.prelaunch(self.lead)
        self.settle()
        with mock.patch.object(worker_pool, "tick", side_effect=RuntimeError("boom")):
            self.assertIsNone(worker_pool._timer())
        for thread in list(worker_pool._stopping):
            thread.join(5)
        self.assertEqual(([w.stopped for w in self.launched], worker_pool._pool), ([1, 1, 1, 1], []))


class ReuseTests(unittest.TestCase):
    def setUp(self):
        self.done = SimpleNamespace(state={"id": "done"}, busy=False, pending={}, cancelled=False)
        self.worker = FakeWorker()
        self.worker.serving = Path("/w/a/part.blend")
        self.executor = SimpleNamespace(worker=self.worker, unresponsive=False, part_layout={"revision": 3})
        self.done.remote_executor = self.executor
        self.ui = SimpleNamespace(_controllers={"done": self.done})
        self.unbound, self.parked = [], []
        for target, name, value in ((worker_routing, "_workers", {"done": self.worker}),
                                    (parallel_access, "enabled", lambda: True),
                                    (worker_sessions, "_launching", []), (worker_sessions, "died", {}),
                                    (worker_sessions, "finished", lambda session: True),
                                    (worker_sessions, "awaiting_person", lambda session: False),
                                    (worker_routing, "unbind", self.unbind)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for patcher in (mock.patch.dict(sys.modules, {"angelo_agent.ui": self.ui,
                                                      "bpy": SimpleNamespace(app=SimpleNamespace(background=True))}),
                        mock.patch.object(angelo_agent, "ui", self.ui, create=True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def unbind(self, controller, reason, parked=False):
        self.assertEqual(reason, worker_routing.END_DONE)   # A done worker's chat says its part is done and saved.
        self.unbound.append(controller.state["id"])
        self.parked.append(parked)
        controller.remote_executor = None

    def item(self, folder):
        cold = worker_process.WorkerProcess(Path(folder) / "2-table" / "part.blend")
        return {"lead": SimpleNamespace(state={"id": "lead"}), "part": {"name": "Table", "brief": "b"}, "number": 2,
                "worker": cold, "error": None, "done": threading.Event()}

    def test_a_done_workers_process_takes_the_waiting_part_and_its_chat_keeps_its_own(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        item = self.item(folder)
        self.assertTrue(worker_pool.reuse("done", item))
        self.assertIs(item["worker"], self.worker)
        self.assertEqual(self.worker.part, Path(folder) / "2-table" / "part.blend")
        self.assertEqual(self.worker.stopped, 0)   # Never stopped: it is the waiting part's now.
        self.assertEqual(self.unbound, ["done"])   # The done chat left it (its part file and layout are kept there).
        self.assertNotIn("done", worker_routing._workers)
        self.assertEqual(worker_sessions._launching, [item])   # Switched and adopted as any launch: a new chat.

    def test_a_worker_whose_chat_is_not_finished_or_whose_part_is_already_launching_is_never_taken(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        cases = [("turn or lane still busy", mock.patch.object(worker_sessions, "finished", lambda s: False)),
                 ("a card waits for the person", mock.patch.object(worker_sessions, "awaiting_person", lambda s: True)),
                 ("its worker died", mock.patch.object(worker_sessions, "died", {"done": "gone"})),
                 ("unresponsive", mock.patch.object(self.executor, "unresponsive", True)),
                 ("ended", mock.patch.object(self.worker, "_alive", False)),
                 ("not ours", mock.patch.object(worker_routing, "_workers", {})),
                 ("off", mock.patch.object(parallel_access, "enabled", lambda: False))]
        for label, patcher in cases:
            with self.subTest(label), patcher:
                item = self.item(folder)
                self.assertFalse(worker_pool.reuse("done", item))
                self.assertIsNot(item["worker"], self.worker)
        for label, change in (("launched already", lambda item: item.update(thread=object())),
                              ("taken from those launched ahead", lambda item: setattr(item["worker"], "ahead", True))):
            with self.subTest(label):
                item = self.item(folder)
                change(item)
                self.assertFalse(worker_pool.reuse("done", item))
        self.assertEqual((self.unbound, worker_sessions._launching), ([], []))

    def test_a_blender_a_parts_script_ran_in_is_never_reused(self):
        # A part's Python can leave persistent handlers, timers, classes or module state that no document reset
        # removes (the audit of 948d877: a handler built into the next part): such a Blender stops instead.
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        self.worker.ran_python = True
        self.assertFalse(worker_pool.reuse("done", self.item(folder)))
        self.assertEqual((self.unbound, self.worker.stopped), ([], 0))   # Nothing changed: the queue stops it as usual.

    def test_detach_leaves_the_worker_running(self):
        stopped = []
        with mock.patch.object(worker_routing, "stop_later", stopped.append):
            self.assertIs(worker_routing.detach(self.done), self.worker)
        self.assertEqual((stopped, self.unbound), ([], ["done"]))
        self.assertEqual(self.parked, [True])   # Its part is done and saved: its tile says how it ended, never ended.

    def test_the_queue_offers_its_waiting_parts_to_reuse(self):
        from angelo_agent import worker_queue
        self.assertIn(worker_pool.reuse, worker_queue.REUSE)


class HookTests(unittest.TestCase):
    def function(self, module, name, holding=""):
        source = (ADDON / module).read_text(encoding="utf-8")
        found = [ast.get_source_segment(source, node) for node in ast.walk(ast.parse(source))
                 if isinstance(node, ast.FunctionDef) and node.name == name]
        return next(text for text in found if holding in text)

    def test_the_capability_lookup_and_the_add_ons_end_reach_the_pool(self):
        execute = self.function("blender_tools.py", "_execute", "agent_capabilities(arguments['family']")
        # The lookup reaches the pool, and its answer carries how many workers were launched ahead (LANE-overheads).
        self.assertIn("return worker_pool.looked_up(self.studio_context, arguments, agent_capabilities(arguments['family'],"
                      "arguments.get('names'),self.studio_context))", execute)
        self.assertIn("worker_pool.release_all()", self.function("worker_routing.py", "stop_all"))
        self.assertIn("worker_pool.take(item[\"worker\"].part, item[\"lead\"]) or item[\"worker\"]",
                      self.function("worker_sessions.py", "_launch"))   # At the start, never while queued.
        self.assertNotIn("worker_pool", self.function("worker_sessions.py", "_prepare"))


if __name__ == "__main__":
    unittest.main()
