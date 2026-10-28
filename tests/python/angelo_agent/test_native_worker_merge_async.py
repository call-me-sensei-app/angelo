# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A merge never makes the lead's window wait on a worker (design A', C10; ``worker_merge_gate``). No Blender.

Through the real Controller, ``merge_hold``, the real approval and Undo operators (``test_native_merge_hold``'s
fixture) and ``worker_sessions._merge``, with fake workers on real threads behind the real seam
(``worker_chat_lifecycle.send_op``). Pinned: with a wedged worker the merge operator and every pump return at once and
nothing is merged until every hold and lease is answered; a fresh worker's answers merge on the next pump, with the
same result as the synchronous path and the switch off; a hold that times out, a refusal (hold or lease) and a worker
that dies mid-hold each end with nothing merged, everything released and the chat told why with a support code; the
same merge applied again joins its gate, another merge of the same parts is refused, neither doubles a hold; a merge
that leaves its chat while it waits is released; leases grown old are asked again; off, nothing is sent.
"""

import os
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (worker_chat_lifecycle as lifecycle, worker_merge_gate as gate,  # noqa: E402
                          worker_process, worker_sessions)
from angelo_agent import worker_reasons  # noqa: E402
from angelo_agent.worker_process import WorkerUnavailable  # noqa: E402
from test_native_merge_hold import MERGE, OperatorFixture, Scene  # noqa: E402

AT_ONCE = 0.05   # Seconds: what "returns at once" means here, far below any worker bound.
BOTH = {"parts": [{"worker": 1}, {"worker": 2}]}


def until(predicate, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class Worker:
    """A live worker serving ``part``: each request recorded (any thread), then answered ``answers[op]`` (a dict, an
    exception or a callable of the fields), else ``{"ok": True}``; an op in ``wedged`` first waits for ``unwedge``."""

    def __init__(self, part):
        self.part = self.serving = Path(part)
        self.answers, self.requests, self.code, self.granted = {}, [], None, 0
        self.wedged, self.open, self.lock = set(), threading.Event(), threading.Lock()
        self.process = SimpleNamespace(pid=8, poll=lambda: self.code)

    def alive(self):
        return self.code is None

    holding = alive

    def request(self, op, timeout, **fields):
        with self.lock:
            self.requests.append(dict(fields, op=op))
        if op in self.wedged and not self.open.wait(10):
            raise WorkerUnavailable("no answer within %g s" % timeout, timed_out=True)
        answer = self.answers.get(op, {"ok": True, "lease": "L-" + str(self.part)} if op == "part.lease" else None)
        answer = answer(fields) if callable(answer) else answer
        if isinstance(answer, BaseException):
            raise answer
        with self.lock:
            self.granted += op == "part.lease" and (answer or {}).get("ok") is True
        return answer or {"ok": True}

    def ops(self):
        with self.lock:
            return [request["op"] for request in self.requests]

    def unwedge(self):
        self.open.set()


class GateFixture(OperatorFixture):
    SWITCH = "1"

    def setUp(self):
        super().setUp()
        self.one, self.two = Worker("/p/1.blend"), Worker("/p/2.blend")
        for worker in (self.one, self.two):
            self.addCleanup(worker.unwedge)
        self.merges, self.c = [], self.chat("AUTO_BOUNDED")
        sessions = {"w1": (1, "Sofa", self.one.part), "w2": (2, "Lamp", self.two.part)}
        fake_bpy = SimpleNamespace(data=SimpleNamespace(collections={}))
        for patcher in (mock.patch.dict(os.environ, {"ANGELO_WORKER_CHAT_HOST": self.SWITCH}),
                        mock.patch.object(lifecycle, "host_available", lambda: True),
                        mock.patch.object(lifecycle, "_hosted", {"w1": self.one, "w2": self.two}),
                        mock.patch.object(worker_process, "_live", {self.one, self.two}),
                        mock.patch.object(worker_sessions, "_started", {}),
                        mock.patch.object(worker_sessions, "_parts", sessions),
                        mock.patch.object(worker_sessions, "parts_of", lambda lead: {
                            number: (None, path) for number, _name, path in sessions.values()}),
                        mock.patch.object(worker_sessions, "layouts_of", lambda lead: {}),
                        mock.patch("angelo_agent.worker_merge.merge", self.merge),
                        mock.patch("angelo_agent.merge_framing.after_merge", lambda *args: None),
                        mock.patch.dict(sys.modules, {"bpy": fake_bpy})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(gate.reset)
        self.scene = Scene()
        self.scene.execute = self.execute

    def lead(self, c):
        worker_sessions._started[c.state["id"]] = ["w1", "w2"]
        return c

    def execute(self, name, arguments):
        """The executor's merge: ``worker_sessions._merge`` itself, on the lead that runs the call."""
        self.scene.ran.append(name)
        lead = next(c for c in self.chats if c.executing_call is not None)
        return worker_sessions._merge(lead, arguments, None)

    def merge(self, lead, window, arguments, parts, name_of, layouts=None):
        """``worker_merge.merge``: what each worker had been asked when the reads ran, and a fixed answer."""
        self.merges.append({str(w.part): w.ops() for w in (self.one, self.two)})
        return {"success": True, "scene_changed": True, "merged": [
            {"worker": part["worker"], "collection": name_of(part["worker"]), "objects": 3, "placements": 1,
             "renamed": {}} for part in arguments["parts"]],
            "changes": {"added": [{"name": name_of(p["worker"]) + " body", "type": "MESH"} for p in arguments["parts"]],
                        "removed": [], "modified": []}}

    def chat(self, mode="AUTO_BOUNDED"):
        c = super().chat(mode)
        self.chats = getattr(self, "chats", []) + [c]
        return c

    def tick(self, c, apply=None):
        """One UI tick: the gates' sweep, then the chat's tools; how long it took."""
        from angelo_agent.controller import ToolDispatchBudget
        begun = time.perf_counter()
        gate.sweep()
        c.pump_tools(self.scene, c.document, budget=ToolDispatchBudget(seconds=60),
                     apply_mutation=apply or (lambda call: c.execute_tool(call, self.scene, c.document)))
        return time.perf_counter() - begun

    def settle(self, c, replies, apply=None):
        self.assertTrue(until(lambda: (self.tick(c, apply), bool(replies))[1]), "the merge never ended")
        return replies[0]

    def released(self, *workers):
        """Every hold each worker got is released, and every lease it granted given back."""
        return until(lambda: all(w.ops().count("chat.release") == w.ops().count("chat.hold")
                                 and w.ops().count("part.unlease") == w.granted for w in workers))


class WedgedWorkerTests(GateFixture):
    def test_the_merge_operator_and_every_pump_return_at_once_while_a_worker_is_wedged(self):
        c = self.lead(self.chat("ASK"))
        click, outcomes = self.operators(c)
        self.one.wedged.add("chat.hold")
        replies, merge = self.request("merge", MERGE, BOTH, c)
        self.tick(c, click)
        begun = time.perf_counter()
        click(merge)   # The person's Approve: the real approval and Undo operators.
        self.assertLess(time.perf_counter() - begun, AT_ONCE)
        self.assertEqual((outcomes, self.scene.ran, replies), ([{'CANCELLED'}], [], []), "no Undo step, nothing read")
        self.assertTrue(until(lambda: self.two.ops() == ["chat.hold"] and self.one.ops() == ["chat.hold"]))
        for _ in range(5):
            self.assertLess(self.tick(c, click), AT_ONCE)
        self.assertEqual((self.scene.ran, self.merges), ([], []))
        self.assertNotIn("part.lease", self.two.ops(), "a lease before every hold was answered")
        self.one.unwedge()
        reply = self.settle(c, replies, click)
        self.assertTrue(reply.get("success"), reply)
        self.assertEqual(outcomes[-1], {'FINISHED'}, "the merge ran outside its Undo step")
        self.assertEqual(self.merges, [{"/p/1.blend": ["chat.hold", "part.lease"],
                                        "/p/2.blend": ["chat.hold", "part.lease"]}], "read outside its holds/leases")
        self.assertTrue(until(lambda: self.one.ops() == self.two.ops() == [
            "chat.hold", "part.lease", "part.unlease", "chat.release"]))


class NormalCaseTests(GateFixture):
    def run_merge(self, identity):
        c = self.lead(self.chat())
        replies = []
        c.request_tool(identity, MERGE, BOTH, replies.append)
        self.tick(c)
        return c, replies

    def test_a_fresh_worker_merges_on_the_next_pump_with_the_synchronous_result(self):
        # The holds are answered only once the pump that asked them returned. Answered at once on their own threads,
        # they could beat the asking pump's own look at the gate (a busy CI runner did): that pump then merges, the
        # order the next test pins. Either way the merge reads only under every answer.
        for worker in (self.one, self.two):
            worker.wedged.add("chat.hold")
        c, replies = self.run_merge("merge")
        self.assertEqual(replies, [])
        for worker in (self.one, self.two):
            worker.unwedge()
        self.assertTrue(until(lambda: not gate.any_asking()), "a fresh worker answers at once")
        self.assertEqual(replies, [])
        self.tick(c)   # The very next tick.
        self.assertEqual(len(replies), 1)
        concurrent = replies[0]
        self.assertTrue(until(lambda: self.one.ops() == ["chat.hold", "part.lease", "part.unlease", "chat.release"]))
        with mock.patch.dict(os.environ, {"ANGELO_WORKER_CHAT_HOST": "0"}):
            for worker in (self.one, self.two):
                worker.requests.clear()
            _c, off = self.run_merge("merge-off")
            self.assertEqual((self.one.requests, self.two.requests), ([], []), "off: nothing is sent")
        with mock.patch.object(lifecycle, "_background", lambda function, name: function()):
            synchronous = worker_sessions._merge(self.lead(self.chat()), BOTH, None)   # No call: asked and waited.
        timing = "native_dispatch_wait_ms"   # How long its dispatch waited in this run: measured, never compared.
        self.assertEqual({k: v for k, v in concurrent.items() if k != timing}, {k: v for k, v in off[0].items() if k != timing})
        for key in ("merged", "changes", "success", "scene_changed"):
            self.assertEqual(concurrent[key], synchronous[key], key)

    def test_answers_in_before_the_asking_pump_looks_merge_in_that_pump(self):
        with mock.patch.object(gate.Gate, "start", lambda g, start=gate.Gate.start: (start(g), g.done.wait(5))[0]):
            c, replies = self.run_merge("merge")   # Every hold and lease answered before ``waits`` reads the gate.
        self.assertEqual(len(replies), 1)
        self.assertTrue(replies[0].get("success"), replies[0])
        self.assertEqual(self.merges, [{"/p/1.blend": ["chat.hold", "part.lease"],
                                        "/p/2.blend": ["chat.hold", "part.lease"]}], "read outside its holds/leases")
        self.assertTrue(self.released(self.one, self.two))


class FailureTests(GateFixture):
    def fails(self, which, op, answer):
        worker = getattr(self, which)
        if answer == "wedge":
            worker.wedged.add(op)
        elif answer == "die":
            worker.answers[op] = lambda fields: (setattr(worker, "code", 0), WorkerUnavailable("The worker ended"))[1]
        else:
            worker.answers[op] = answer
        c = self.lead(self.chat())
        emitted = []
        c.emit = lambda kind, data=None, original=c.emit: (emitted.append((kind, data)), original(kind, data))[1]
        replies, merge = self.request("merge", MERGE, BOTH, c)
        with mock.patch.object(lifecycle, "HOLD_SECONDS", 0.3):
            reply = self.settle(c, replies)
        self.assertEqual(reply.get("error_code"), "WRITER_BUSY", reply)
        self.assertIn("nothing was merged", reply["error"])
        self.assertEqual(self.merges, [], "read without its holds and leases")
        self.assertNotIn(merge.id, c.pending)
        worker.unwedge()   # A wedged hold answered late is released too.
        self.assertTrue(self.released(self.one, self.two), (self.one.ops(), self.two.ops()))
        told = [data for kind, data in emitted if kind == "message" and data.get("role") == "error"]
        self.assertEqual(len(told), 1, emitted)
        self.assertIn("support code", told[0]["text"])
        self.assertEqual(told[0]["i18n"][0], gate.FAILED)
        for internal in ("w1", "w2", "chat.hold", "part.lease"):   # No chat id, no op name.
            self.assertNotIn(internal, told[0]["text"])
        self.assertEqual(gate._gates, {})
        return told[0]["text"]

    def test_a_hold_that_times_out(self):
        self.fails("one", "chat.hold", "wedge")

    def test_a_hold_refused(self):
        text = self.fails("two", "chat.hold", {"error": {"message": "This chat is closed", "code": "OWNERSHIP"}})
        self.assertIn(worker_reasons.MOVED_ON, text)   # A plain reason (worker_reasons); its words are logged.
        self.assertNotIn("This chat is closed", text)

    def test_a_worker_that_dies_mid_hold(self):
        self.fails("one", "chat.hold", "die")

    def test_a_lease_refused(self):
        self.fails("two", "part.lease", {"error": {"message": "The part is being saved"}})


class ReentrancyTests(GateFixture):
    def test_the_same_merge_applied_again_joins_its_gate(self):
        c = self.lead(self.chat("ASK"))
        click, outcomes = self.operators(c)
        self.one.wedged.add("chat.hold")
        replies, merge = self.request("merge", MERGE, BOTH, c)
        self.tick(c, click)
        click(merge)
        click(merge)   # A second click while its holds are asked.
        self.tick(c, click)
        self.assertEqual(outcomes, [{'CANCELLED'}, {'CANCELLED'}])
        self.assertTrue(until(lambda: self.two.ops() == ["chat.hold"]))
        self.one.unwedge()
        self.assertTrue(self.settle(c, replies, click).get("success"))
        self.assertEqual(self.scene.ran, [MERGE])
        self.assertEqual((self.one.ops().count("chat.hold"), self.two.ops().count("chat.hold")), (1, 1))

    def test_another_merge_of_the_same_parts_is_refused_and_never_doubles_a_hold(self):
        c = self.lead(self.chat())
        self.one.wedged.add("chat.hold")
        first, _merge = self.request("merge-1", MERGE, {"parts": [{"worker": 1}]}, c)
        self.tick(c)
        second, _again = self.request("merge-2", MERGE, {"parts": [{"worker": 1}]}, c)
        self.tick(c)
        self.assertEqual(second[0].get("error_code"), "WRITER_BUSY", second)
        self.assertIn("Another merge of these parts is under way", second[0]["error"])
        self.assertEqual(first, [])
        self.one.unwedge()
        self.assertTrue(self.settle(c, first).get("success"))
        self.assertTrue(until(lambda: self.one.ops() == ["chat.hold", "part.lease", "part.unlease", "chat.release"]))


    def test_a_later_hold_of_the_same_chat_never_overtakes_the_earlier_release(self):
        c = self.lead(self.chat())
        self.one.wedged.add("chat.release")
        first = []
        c.request_tool("merge-1", MERGE, {"parts": [{"worker": 1}]}, first.append)
        self.assertTrue(self.settle(c, first).get("success"))
        self.assertTrue(until(lambda: self.one.ops()[-1:] == ["chat.release"]))   # Sent, not yet answered.
        second = []
        c.request_tool("merge-2", MERGE, {"parts": [{"worker": 1}]}, second.append)
        self.assertLess(self.tick(c), AT_ONCE)
        self.assertFalse(until(lambda: self.one.ops().count("chat.hold") > 1, 0.2), "a hold ahead of the release")
        self.one.unwedge()
        self.assertTrue(self.settle(c, second).get("success"), second)
        ops = self.one.ops()
        self.assertLess(ops.index("chat.release"), len(ops) - 1 - ops[::-1].index("chat.hold"))


class LifetimeTests(GateFixture):
    def test_a_merge_that_leaves_its_chat_while_asking_is_released(self):
        c = self.lead(self.chat())
        self.one.wedged.add("chat.hold")
        replies, merge = self.request("merge", MERGE, BOTH, c)
        self.tick(c)
        self.assertTrue(until(lambda: self.two.ops() == ["chat.hold"]))
        merge.expires = 0
        self.tick(c)
        self.tick(c)   # The sweep after its expiry.
        self.assertEqual(replies[0].get("error_code"), "EXPIRED")
        self.assertTrue(until(lambda: self.two.ops() == ["chat.hold", "chat.release"]))
        self.one.unwedge()
        self.assertTrue(until(lambda: self.one.ops() == ["chat.hold", "chat.release"]))
        self.assertEqual((self.merges, gate._gates), ([], {}))

    def test_leases_grown_old_while_the_merge_waited_are_asked_again(self):
        c = self.lead(self.chat())
        replies, merge = self.request("merge", MERGE, BOTH, c)
        with mock.patch.object(gate.Gate, "_ready", lambda self: None):   # Answered, not yet ready.
            self.tick(c)
            self.assertTrue(until(lambda: "part.lease" in self.two.ops()))
        found = gate._gates[(c.state["id"], merge.id)]
        found.leases = [lease[:2] + (time.monotonic() - 60,) + lease[3:] for lease in found.leases]
        found.done.set()
        self.tick(c)
        reply = self.settle(c, replies)
        self.assertTrue(reply.get("success"), reply)
        read = self.merges[0]["/p/1.blend"]   # Read under its one hold and its second lease.
        self.assertEqual((read.count("chat.hold"), read.count("part.lease"), read.count("chat.release")), (1, 2, 0))
        self.assertTrue(until(lambda: sorted(self.one.ops()) == sorted(
            ["chat.hold", "part.lease", "part.unlease", "part.lease", "part.unlease", "chat.release"])))


class SwitchOffTests(GateFixture):
    SWITCH = "0"

    def test_off_nothing_is_sent_and_the_merge_runs_at_once(self):
        c = self.lead(self.chat("ASK"))
        click, outcomes = self.operators(c)
        replies, merge = self.request("merge", MERGE, BOTH, c)
        self.tick(c, click)
        click(merge)
        self.assertEqual((outcomes, self.scene.ran), ([{'FINISHED'}], [MERGE]))
        self.assertTrue(replies[0].get("success"))
        self.assertEqual((self.one.requests, self.two.requests, gate._gates), ([], [], {}))


if __name__ == "__main__":
    unittest.main()
