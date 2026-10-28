# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker chat's lifecycle as chat ops when its Controller runs in its worker (design A', C10), without Blender
(``worker_chat_lifecycle``, its hooks in ``worker_process``, ``worker_routing.adopt``, ``worker_pool.reuse``,
``worker_queue``'s park, ``worker_sessions._merge``, and the worker's ``part_leases``).

Pinned here: the switch (``ANGELO_WORKER_CHAT_HOST=0``, or a build without the worker side) sends nothing; a bound chat
opens in its worker, ``resume`` for a relaunch, and one that cannot open leaves its worker; a close seals and hands the
chat back, a failed close keeps a live worker's chat hosted and hands back a dead one's; every stop closes first and
hands back after the process ended; a switch closes every hosted chat first, a close that fails or a worker that dies
mid-switch relaunches with nothing switched, and a chat still bound moves with ``resume``; a park closes before its
save and a closed chat parks; a reuse whose close fails takes nothing; a merge holds and leases first, refuses with
nothing merged on any timeout or death, and always releases; the worker's lease holds its save back, bounded, and is
answered on its reader thread; no credential or connections folder reaches a worker.
"""

import os
import queue
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
from angelo_agent import (parallel_access, part_leases, worker_chat_lifecycle as lifecycle, worker_merge_gate,  # noqa: E402
                          worker_pool, worker_process, worker_queue, worker_routing, worker_sessions, worker_switch)
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from angelo_agent.worker_process import WorkerUnavailable  # noqa: E402
import test_native_worker_queue as queue_base  # noqa: E402

HOST_AVAILABLE = lifecycle.host_available   # The real check, before any test replaces it.


class OpWorker:
    """A worker as the ops see it: each request recorded, answered by ``answers[op]`` (a dict, an exception, or a
    callable of the fields), else ``{"ok": True}``."""

    def __init__(self, part="/w/a/part.blend", answers=None):
        self.part = self.serving = Path(part)
        self.answers, self.requests = dict(answers or {}), []
        self.code = None
        self.process = SimpleNamespace(pid=7, poll=lambda: self.code)

    def alive(self):
        return self.code is None

    holding = alive

    def request(self, op, timeout, **fields):
        self.requests.append(dict(fields, op=op))
        answer = self.answers.get(op, {"ok": True})
        answer = answer(fields) if callable(answer) else answer
        if isinstance(answer, BaseException):
            raise answer
        return answer

    def ops(self):
        return [request["op"] for request in self.requests]


def chat(session="w1", settings=None, api_key=""):
    return SimpleNamespace(state={"id": session, "options": {"approval": "ASK"}}, store=SimpleNamespace(
        directory="/state/sessions"), settings=settings or {"model": "m"}, models=["m"], api_key=api_key)


class Base(unittest.TestCase):
    """A' on, a fresh table of hosted chats and handoffs, for every test."""

    ON = True
    LEASE = {"epoch": 1, "grants": {"auto": False}}

    def setUp(self):
        self.handed = []
        for patcher in (mock.patch.object(lifecycle, "_hosted", {}),
                        mock.patch.object(lifecycle, "HANDOFF", [lambda s, closed: self.handed.append((s, closed))]),
                        mock.patch.object(lifecycle, "host_available", lambda: self.ON),
                        mock.patch.dict(os.environ, {"ANGELO_WORKER_CHAT_HOST": "1"}),
                        # The lead side of each open (worker_chat_wiring.prepare) is pinned by its own end-to-end
                        # test (test_native_worker_chat_e2e); here an open carries the lease it would register.
                        mock.patch.object(lifecycle, "_prepare", lambda controller, worker: dict(self.LEASE)),
                        mock.patch.object(worker_process, "_live", set())):
            patcher.start()
            self.addCleanup(patcher.stop)

    def host(self, session, worker):
        lifecycle._hosted[session] = worker


class SwitchTests(Base):
    def test_the_switch_off_or_a_build_without_the_worker_side_hosts_nothing(self):
        self.assertTrue(lifecycle.enabled())
        with mock.patch.dict(os.environ, {"ANGELO_WORKER_CHAT_HOST": "0"}):
            self.assertFalse(lifecycle.enabled())
        with mock.patch.dict(os.environ, {"ANGELO_WORKER_CHAT_HOST": ""}):
            self.assertTrue(lifecycle.enabled())   # Unset: A' (the default).
        with mock.patch.object(lifecycle, "host_available", lambda: False):
            self.assertFalse(lifecycle.enabled())
        with mock.patch.dict(os.environ, {"ANGELO_WORKER_CHAT_HOST": "0"}):
            worker = OpWorker()
            lifecycle.opened(chat(), worker, resume=False)
            with lifecycle.merge_guard(["w1"], [worker.part]), lifecycle.leased_read(worker.part):
                pass
            self.assertEqual((worker.requests, lifecycle._hosted), ([], {}))

    def test_the_worker_side_is_found_in_this_build_itself(self):
        self.assertEqual(HOST_AVAILABLE(), (ADDON / "worker_chat_host.py").is_file())   # Workers run this build.


class SeamTests(Base):
    def test_only_an_ok_answer_succeeds(self):
        worker = OpWorker(answers={"chat.stop": {"error": {"message": "no"}}, "chat.hold": {"value": True},
                                   "chat.release": WorkerUnavailable("no answer", timed_out=True)})
        self.assertEqual(lifecycle.send_op("w1", "chat.close", worker=worker, timeout=1), {"ok": True})
        self.assertEqual(worker.requests[0], {"op": "chat.close", "session": "w1"})
        for op in ("chat.stop", "chat.hold"):
            with self.subTest(op=op), self.assertRaises(lifecycle.ChatOpFailed):
                lifecycle.send_op("w1", op, worker=worker, timeout=1)
        with self.assertRaises(lifecycle.ChatOpFailed) as caught:
            lifecycle.send_op("w1", "chat.release", worker=worker, timeout=1)
        self.assertTrue(caught.exception.timed_out)
        refused = OpWorker(answers={"chat.open": worker_process.RequestRefused("too large", "LIMIT_EXCEEDED")})
        with self.assertRaises(lifecycle.ChatOpFailed):
            lifecycle.send_op("w1", "chat.open", worker=refused, timeout=1)

    def test_no_credential_or_connections_folder_reaches_a_worker(self):
        settings = {"model": "m", "api_key": "sk-1", "auth_directory": "/c", "team_token": "t", "Client_Secret": "s",
                    "extra": "sk-1", "connection_id": "c1", "reuse_codex_login": True}
        worker = OpWorker()
        lifecycle.open_chat(chat(settings=settings, api_key="sk-1"), worker, resume=False)
        sent = worker.requests[0]
        self.assertEqual(sent["settings"], {"model": "m", "connection_id": "c1", "reuse_codex_login": True})
        self.assertNotIn("sk-1", repr(sent))
        self.assertEqual((sent["op"], sent["session"], sent["store_directory"], sent["resume"], sent["models"]),
                         ("chat.open", "w1", "/state/sessions", False, ["m"]))
        self.assertEqual(sent["consent"], self.LEASE)   # The lease registered for it (worker_consent_lead.register).


class OpenCloseTests(Base):
    def test_a_bound_chat_opens_in_its_worker_and_a_relaunch_resumes(self):
        for again in (False, True):
            with self.subTest(again=again):
                worker = OpWorker()
                worker.process = SimpleNamespace(pid=9, poll=lambda: None)
                controller = chat("c%d" % again)
                with mock.patch.object(worker_routing, "bind", lambda *a: "executor"), \
                        mock.patch.object(worker_routing, "_workers", {}):
                    self.assertEqual(worker_routing.adopt(controller, worker, again=again), "executor")
                    self.assertIs(worker_routing._workers[controller.state["id"]], worker)
                self.assertEqual([(r["op"], r["resume"]) for r in worker.requests], [("chat.open", again)])
                self.assertIs(lifecycle.hosted(controller.state["id"]), worker)

    def test_a_chat_that_cannot_open_leaves_its_worker_and_is_ended_with_it(self):
        worker = OpWorker(answers={"chat.open": WorkerUnavailable("no answer within 15 s", timed_out=True)})
        controller, unbound = chat(), []
        with mock.patch.object(worker_routing, "unbind", lambda c, reason, parked=False: unbound.append(reason)), \
                self.assertRaises(lifecycle.ChatOpFailed):
            lifecycle.opened(controller, worker, resume=True)
        self.assertEqual(unbound, [worker_routing.END_NOT_BRIEFED])
        self.assertIs(lifecycle.hosted("w1"), worker)   # It may have opened late: closed or ended with its worker.

    def test_a_close_hands_the_chat_back_and_a_failed_one_only_once_its_process_ended(self):
        self.assertFalse(lifecycle.close_chat("nobody", "x"))
        worker = OpWorker()
        self.host("w1", worker)
        self.assertTrue(lifecycle.close_chat("w1", "done"))
        self.assertEqual((worker.ops(), self.handed, lifecycle.hosted("w1")), (["chat.close"], [("w1", True)], None))
        self.assertFalse(lifecycle.close_chat("w1", "done"))   # Twice: nothing more is sent.
        wedged = OpWorker(answers={"chat.close": WorkerUnavailable("no answer", timed_out=True)})
        self.host("w2", wedged)
        with self.assertRaises(lifecycle.ChatOpFailed):
            lifecycle.close_chat("w2", "done")
        self.assertIs(lifecycle.hosted("w2"), wedged)   # Its live worker may still write: not handed back.
        wedged.code = -9
        with self.assertRaises(lifecycle.ChatOpFailed):
            lifecycle.close_chat("w2", "done")
        self.assertEqual((self.handed[-1], lifecycle.hosted("w2")), (("w2", False), None))

    def test_every_stop_closes_first_and_hands_back_what_its_process_ended(self):
        order = []
        worker = worker_process.WorkerProcess("/w/a/part.blend")
        worker.process = SimpleNamespace(pid=7, poll=lambda: None, wait=lambda grace: order.append("exit"))
        worker.client = SimpleNamespace(close=lambda: order.append("connection closed"))

        def request(op, timeout, **fields):
            order.append((op, fields["session"]))
            return {"ok": True} if fields["session"] == "a" else None
        worker.request = request
        self.host("a", worker)
        self.host("b", worker)
        worker.stop()
        self.assertEqual(order, [("chat.close", "a"), ("chat.close", "b"), "connection closed", "exit"])
        self.assertEqual(self.handed, [("a", True), ("b", False)])   # b only after its process ended.
        self.assertEqual(lifecycle._hosted, {})


class WorkerSwitchTests(Base):
    def worker(self, answers):
        worker = worker_process.WorkerProcess("/w/b/part.blend")
        worker.serving = Path("/w/a/part.blend")
        worker.alive = lambda: True
        worker.process = SimpleNamespace(pid=7, poll=lambda: None)
        log = []

        def request(op, timeout, **fields):
            log.append(op)
            answer = answers.get(op, {"ok": True})
            if isinstance(answer, BaseException):
                raise answer
            return answer
        worker.request, worker.log = request, log
        worker.stop = lambda: log.append("stop")
        worker._launch = lambda timeout: log.append("launch") or {"ok": True}
        return worker

    def test_every_hosted_chat_closes_before_the_switch(self):
        worker = self.worker({"switch_part": {"layout": {}}})
        self.host("done", worker)
        with mock.patch.object(worker_routing, "_workers", {}):
            worker.start()
        self.assertEqual((worker.log, worker.how), (["chat.close", "switch_part"], "switched"))
        self.assertEqual(self.handed, [("done", True)])

    def test_a_close_that_fails_or_a_worker_that_dies_mid_switch_relaunches_with_nothing_switched(self):
        for answers, sent in (({"chat.close": WorkerUnavailable("no answer", timed_out=True)}, ["chat.close"]),
                              ({"switch_part": WorkerUnavailable("The worker ended before it answered")},
                               ["chat.close", "switch_part"])):
            with self.subTest(sent=sent):
                worker = self.worker(answers)
                self.host("done", worker)
                with mock.patch.object(worker_routing, "_workers", {}):
                    self.assertEqual(worker.start(), {"ok": True})
                self.assertEqual(worker.log, sent + ["stop", "launch"])
                self.assertEqual(worker.how, "launched")
                lifecycle._hosted.clear()

    def test_a_chat_still_bound_moves_with_its_worker_and_resumes_there(self):
        worker = self.worker({"switch_part": {"layout": {}}})
        moving = chat("moving")
        self.host("moving", worker)
        opened = []
        with mock.patch.object(worker_routing, "_workers", {"moving": worker}), \
                mock.patch.object(lifecycle, "_controller", lambda session: moving), \
                mock.patch.object(lifecycle, "open_chat", lambda c, w, resume: opened.append((c, w, resume))):
            worker.start()
        self.assertEqual(worker.log, ["chat.close", "switch_part"])
        self.assertEqual(opened, [(moving, worker, True)])

    def test_a_moving_chat_that_cannot_open_fails_the_start(self):
        worker = self.worker({"switch_part": {"layout": {}}})
        self.host("moving", worker)
        with mock.patch.object(worker_routing, "_workers", {"moving": worker}), \
                mock.patch.object(lifecycle, "_controller", lambda session: None), \
                self.assertRaises(WorkerUnavailable):
            worker.start()

    def test_the_worker_refuses_a_switch_while_a_chat_it_hosts_is_open(self):
        host = SimpleNamespace(shadows={}, hosted_sessions=lambda: ["w1"])
        with mock.patch("angelo_agent.native_programs.runtime_is_staging", return_value=False, create=True), \
                self.assertRaises(RefusalError):
            worker_switch.switch_part(host, "/w/b/part.blend")


class ParkTests(queue_base.Fixture, Base):
    def setUp(self):
        Base.setUp(self)
        queue_base.Fixture.setUp(self)

    def worker(self, close=None):
        process = queue_base.Process(save={"value": None})
        answer = close if close is not None else {"ok": True}
        original = process.request
        process.request = lambda op, timeout, **fields: (process.requests.append(dict(fields, op=op)), answer)[1] \
            if op == "chat.close" else original(op, timeout, **fields)
        return process

    def park(self, session):
        worker_queue.tick()
        self.assertTrue(worker_queue._parking[session]["done"].wait(1))

    def test_a_park_closes_the_chat_before_its_save_then_parks(self):
        chat_, process = self.running("w1", self.worker())
        self.host("w1", process)
        self.park("w1")
        worker_queue.tick()
        self.assertEqual([r["op"] for r in process.requests], ["chat.close", "save"])
        self.assertIn("w1", worker_queue.parked)
        self.assertEqual(self.handed, [("w1", True)])

    def test_a_close_that_fails_is_a_failed_park_kept_in_its_place_with_nothing_saved(self):
        _chat, process = self.running("w1", self.worker(close={"error": {"message": "wedged"}}))
        self.host("w1", process)
        self.park("w1")
        worker_queue.tick()
        self.assertEqual([r["op"] for r in process.requests], ["chat.close"])
        self.assertIn("w1", worker_queue._held)
        self.assertNotIn("w1", worker_queue.parked)
        self.assertFalse(process.stopped.is_set())

    def test_a_closed_chat_parks_whatever_reached_it_since(self):
        chat_, process = self.running("w1", self.worker())
        self.host("w1", process)
        self.park("w1")
        chat_.busy = True   # A message raced the close: the worker refused it (its chat is closed).
        worker_queue._finish_parks(self.ui)
        self.assertIn("w1", worker_queue.parked)

    def test_off_a_park_is_todays(self):
        with mock.patch.dict(os.environ, {"ANGELO_WORKER_CHAT_HOST": "0"}):
            _chat, process = self.running("w1", self.worker())
            self.park("w1")
            worker_queue.tick()
        self.assertEqual([r["op"] for r in process.requests], ["save"])
        self.assertIn("w1", worker_queue.parked)


class ReuseTests(Base):
    def test_a_reuse_whose_close_fails_takes_nothing_and_changes_no_binding(self):
        worker = OpWorker(answers={"chat.close": WorkerUnavailable("no answer", timed_out=True)})
        worker.stopped = 0
        done = SimpleNamespace(state={"id": "done"}, busy=False, pending={})
        done.remote_executor = SimpleNamespace(worker=worker, unresponsive=False)
        ui = SimpleNamespace(_controllers={"done": done})
        self.host("done", worker)
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        item = {"lead": None, "part": {"name": "T", "brief": "b"}, "number": 2, "error": None,
                "worker": worker_process.WorkerProcess(Path(folder) / "2" / "part.blend")}
        detached = []
        with mock.patch.object(worker_routing, "_workers", {"done": worker}), \
                mock.patch.object(parallel_access, "enabled", lambda: True), \
                mock.patch.object(worker_sessions, "died", {}), \
                mock.patch.object(worker_sessions, "finished", lambda s: True), \
                mock.patch.object(worker_sessions, "awaiting_person", lambda s: False), \
                mock.patch.object(worker_routing, "detach", detached.append), \
                mock.patch.dict(sys.modules, {"angelo_agent.ui": ui}), \
                mock.patch.object(angelo_agent, "ui", ui, create=True):
            self.assertFalse(worker_pool.reuse("done", item))
            self.assertIs(worker_routing._workers["done"], worker)
        self.assertEqual((detached, worker.ops()), ([], ["chat.close"]))
        self.assertIsNot(item["worker"], worker)


class MergeTests(Base):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(lifecycle, "_background", lambda function, name: function())   # In order here.
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_merge_holds_every_hosted_chat_and_leases_every_live_part_then_releases(self):
        one, two = OpWorker("/p/1.blend", {"part.lease": {"ok": True, "lease": "L1"}}), OpWorker("/p/2.blend")
        worker_process._live.update({one, two})
        self.host("w1", one)
        ran = []
        with lifecycle.merge_guard(["w1", "parked"], [one.part, Path("/p/parked.blend")]):
            ran.append(one.ops())
        self.assertEqual(ran, [["chat.hold", "part.lease"]])
        self.assertEqual(one.ops(), ["chat.hold", "part.lease", "part.unlease", "chat.release"])
        self.assertEqual(one.requests[2]["lease"], "L1")
        self.assertEqual(two.requests, [])   # Not one of this merge's parts.

    def test_a_hold_that_times_out_or_a_worker_that_dies_mid_hold_refuses_and_still_releases(self):
        for answer in (WorkerUnavailable("no answer within 10 s", timed_out=True),
                       WorkerUnavailable("The worker ended before it answered")):
            with self.subTest(answer=str(answer)):
                first, second = OpWorker("/p/1.blend"), OpWorker("/p/2.blend", {"chat.hold": answer})
                self.host("w1", first)
                self.host("w2", second)
                ran = []
                with self.assertRaises(RefusalError) as caught:
                    with lifecycle.merge_guard(["w1", "w2"], []):
                        ran.append(True)
                self.assertEqual((ran, caught.exception.code), ([], "WRITER_BUSY"))
                self.assertIn("nothing was merged", str(caught.exception))
                self.assertFalse(caught.exception.partial_changes_possible)
                self.assertEqual(first.ops(), ["chat.hold", "chat.release"])
                self.assertEqual(second.ops(), ["chat.hold", "chat.release"])   # Late holds are released too.

    def test_a_lease_not_granted_refuses_and_releases_the_holds(self):
        worker = OpWorker("/p/1.blend", {"part.lease": {"error": {"message": "The part is being saved"}}})
        worker_process._live.add(worker)
        self.host("w1", worker)
        with self.assertRaises(RefusalError), lifecycle.merge_guard(["w1"], [worker.part]):
            self.fail("nothing is read")
        self.assertEqual(worker.ops(), ["chat.hold", "part.lease", "chat.release"])

    def test_a_worker_that_ended_before_its_lease_leaves_its_file_to_read(self):
        worker = OpWorker("/p/1.blend")
        worker.answers["part.lease"] = lambda fields: (setattr(worker, "code", 0), WorkerUnavailable("ended"))[1]
        worker_process._live.add(worker)   # Seen live, then its process ends before it answers.
        with lifecycle.merge_guard([], [worker.part]):
            pass
        self.assertEqual(worker.ops(), ["part.lease"])

    def test_a_fault_inside_the_merge_still_releases_everything(self):
        worker = OpWorker("/p/1.blend")
        worker_process._live.add(worker)
        self.host("w1", worker)
        with self.assertRaises(ValueError), lifecycle.merge_guard(["w1"], [worker.part]):
            raise ValueError("append failed")
        self.assertEqual(worker.ops(), ["chat.hold", "part.lease", "part.unlease", "chat.release"])

    def test_the_lead_merges_only_inside_the_guard_and_a_refusal_merges_nothing(self):
        lead = SimpleNamespace(state={"id": "lead"})
        events, refused = [], RefusalError("held", code="WRITER_BUSY")

        class Guard:
            def __init__(self, lead, sessions, paths):
                events.append(("guard", list(sessions), [str(p) for p in paths]))

            def __enter__(self):
                if refused is not None:
                    raise refused

            def __exit__(self, *exc):
                events.append("released")
        with mock.patch.object(worker_sessions, "_started", {"lead": ["w1"]}), \
                mock.patch.object(worker_sessions, "_parts", {"w1": (1, "Sofa", Path("/p/1.blend"))}), \
                mock.patch.object(worker_sessions, "parts_of", lambda c: {1: (None, Path("/p/1.blend"))}), \
                mock.patch.object(worker_sessions, "layouts_of", lambda c: {}), \
                mock.patch.object(worker_merge_gate, "guard", Guard), \
                mock.patch("angelo_agent.parallel_sessions.merging", lambda s: events.append("merging")), \
                mock.patch("angelo_agent.parallel_sessions.merge_failed", lambda s: events.append("failed")), \
                mock.patch("angelo_agent.worker_merge.merge", lambda *a: events.append("merged")):
            with self.assertRaises(RefusalError):
                worker_sessions._merge(lead, {"parts": [{"worker": 1}]}, None)
        self.assertEqual(events, ["merging", ("guard", ["w1"], ["/p/1.blend"]), "failed"])

    def test_another_read_of_a_live_part_takes_a_lease_and_one_not_granted_reads_nothing(self):
        worker = OpWorker("/p/1.blend", {"part.lease": {"ok": True, "lease": "L"}})
        worker_process._live.add(worker)
        with lifecycle.leased_read(worker.part):
            self.assertEqual(worker.ops(), ["part.lease"])
        self.assertEqual(worker.ops(), ["part.lease", "part.unlease"])
        worker.answers["part.lease"] = WorkerUnavailable("no answer", timed_out=True)
        with self.assertRaises(lifecycle.ChatOpFailed), lifecycle.leased_read(worker.part):
            self.fail("nothing is read")


class PartLeaseTests(unittest.TestCase):
    def test_a_save_waits_for_the_lease_and_goes_on_at_its_release(self):
        table = part_leases.PartLeases()
        token, _seconds = table.lease("/p.blend", 5)
        saved = threading.Event()

        def save():
            with table.saving("/p.blend"):
                saved.set()
        thread = threading.Thread(target=save, daemon=True)
        thread.start()
        self.assertFalse(saved.wait(0.1))
        self.assertTrue(table.unlease(token))
        self.assertTrue(saved.wait(2))
        self.assertFalse(table.unlease(token))   # Twice: harmless.

    def test_a_lease_holds_a_save_back_at_most_its_bound(self):
        table = part_leases.PartLeases()
        with mock.patch.object(part_leases, "MAX_SECONDS", 0.2):
            _token, seconds = table.lease("/p.blend", 3600)
        self.assertEqual(seconds, 0.2)
        began = time.monotonic()
        with table.saving("/p.blend"):
            waited = time.monotonic() - began
        self.assertGreaterEqual(waited, 0.15)
        self.assertLess(waited, 2.0)
        for bad in (0, -1, float("nan"), float("inf")):
            with self.subTest(seconds=bad), self.assertRaises(ValueError):
                table.lease("/p.blend", bad)

    def test_a_lease_asked_while_a_save_runs_is_granted_as_it_ends_never_waited_for(self):
        table = part_leases.PartLeases()
        granted = []
        with table.saving("/p.blend"):
            table.ask("/p.blend", 5, granted.append)   # Returns at once: the reader never waits on a save.
            self.assertEqual(granted, [])
            self.assertFalse(table.held("/p.blend"))
        self.assertEqual(len(granted), 1)
        self.assertTrue(table.held("/p.blend"))
        self.assertTrue(table.lease("/p.blend", 5)[0])
        with table.saving("/other.blend"):   # Another file's save is not held back by it either.
            pass

    def test_the_worker_answers_on_its_reader_thread_and_refuses_another_file(self):
        sent = queue.Queue()   # Sent in order by the replies thread, never the reader (part_leases.Replies).
        host = SimpleNamespace(part="/w/a/part.blend", endpoint=SimpleNamespace(send=sent.put))
        self.assertFalse(part_leases.answers(host, 3, {"op": "execute"}))
        self.assertTrue(part_leases.answers(host, 4, {"op": "part.lease", "part": "/w/a/part.blend", "seconds": 1}))
        frame = sent.get(timeout=5)
        answer = frame["response"]
        self.assertEqual((frame["seq"], answer["ok"], answer["seconds"]), (4, True, 1.0))
        self.assertTrue(part_leases.leases_of(host).held("/w/a/part.blend"))
        part_leases.answers(host, 5, {"op": "part.unlease", "lease": answer["lease"]})
        self.assertEqual(sent.get(timeout=5)["response"], {"ok": True, "released": True})
        part_leases.answers(host, 6, {"op": "part.lease", "part": "/w/b/part.blend"})
        self.assertIn("error", sent.get(timeout=5)["response"])
        self.assertFalse(part_leases.leases_of(host).held())

    def test_the_worker_host_routes_lease_ops_before_its_inbox_and_saves_under_the_table(self):
        source = (ADDON / "worker_host.py").read_text(encoding="utf-8")
        self.assertIn("if not part_leases.answers(self, seq, request):", source)
        self.assertIn("part_leases.leases_of(self).saving(self.part)", source)


if __name__ == "__main__":
    unittest.main()
