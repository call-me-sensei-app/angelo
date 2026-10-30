# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The external audit of A′ (c3c9a14a1fd), the lead's findings: #1 a revocation classed against acknowledged grants
only, #4 a lease granted after its request timed out, #5 a count read after its lease expired, #6 a part save that
failed and no chat said so, #7 a scene signature that read every object's matrix to keep a thousand.

Each class is one finding's reproduction and failed while the finding stood.
"""

import os
import queue
import socket
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (scene_signature, worker_channel, worker_chat_lifecycle as lifecycle,  # noqa: E402
                          worker_chat_wiring as wiring, worker_consent_lead as lead, worker_merge_gate,
                          worker_part_counts as counts, worker_process)
from angelo_agent.headless_pipe import Writer  # noqa: E402


class RevocationCountsGrantsInFlight(unittest.TestCase):
    """#1: Trust scripts on, its ack delayed, then off with that send lost: both sends were classed against the last
    acknowledged lease (no scripts), so neither was a revocation; 2 s later the worker could still hold the trust,
    with no kill and no needs-approval."""

    def setUp(self):
        switch = patch.dict(os.environ, {"ANGELO_WORKER_CHAT_HOST": "1"})
        switch.start()
        self.addCleanup(switch.stop)
        lead.reset()
        self.addCleanup(lead.reset)
        self.now, self.held, self.killed, self.current = [1000.0], [], [], {"auto": True}
        lead.bind(send=self.send, kill=self.killed.append, clock=lambda: self.now[0], connection=lambda c: True,
                  spawn=self.held.append)
        granting = patch.object(lead, "grants", lambda controller: dict(self.current))
        granting.start()
        self.addCleanup(granting.stop)
        self.mirror = NS(state={"id": "chat-1"}, emit=Mock(), busy=True)
        lead.register(self.mirror)

    @staticmethod
    def send(session, request, timeout):
        return {"ok": True, "epoch": request.get("epoch")}

    def change(self, grants):
        self.current = grants
        return lead.sync()

    def test_the_audit_poc_a_lost_revocation_of_an_unacknowledged_trust_stops_the_chat(self):
        for acknowledged in (False, True):   # The trust's ack delayed past the deadline, or in time.
            with self.subTest(trust_acknowledged=acknowledged):
                self.setUp()
                self.change({"auto": True, "scripts": "nonce-1"})
                trust = self.held.pop()
                if acknowledged:
                    trust()   # The worker holds the trust.
                self.change({"auto": True})   # Trust scripts off: this send is lost (never answered).
                self.held.clear()
                self.now[0] += lead.ACK_SECONDS + 0.5
                lead.settle()
                self.assertEqual(self.killed, ["chat-1"])
                self.assertTrue(lead.needs_approval("chat-1"))

    def test_a_widening_over_a_widening_in_flight_is_not_a_revocation(self):
        self.change({"auto": True, "scripts": "nonce-1"})
        records = self.change({"auto": True, "scripts": "nonce-1", "always_allow": "nonce-2"})
        self.assertFalse(records[-1].revoking)
        self.held.clear()
        self.now[0] += lead.ACK_SECONDS + 0.5
        lead.settle()
        self.assertEqual(self.killed, [])


class Pair:
    """A real ``worker_channel.Client`` over a socket pair, inside a ``WorkerProcess`` shell, and the worker's end."""

    def __init__(self, case):
        ours, theirs = socket.socketpair()
        case.addCleanup(ours.close)
        case.addCleanup(theirs.close)
        self.client = worker_channel.Client(worker_channel.socket_endpoint(ours, 1 << 20, 1 << 20))
        case.addCleanup(self.client.close)
        self.end = worker_channel.socket_endpoint(theirs, 1 << 20, 1 << 20)
        self.writer = Writer(theirs.makefile("wb", buffering=0), 1 << 20)
        self.worker = object.__new__(worker_process.WorkerProcess)
        self.worker.client, self.worker.process = self.client, NS(poll=lambda: None, pid=-31)
        self.stops = []   # A shell has no process to stop: a stop it starts (an ended connection) is recorded only,
        self.worker.stop = lambda grace=None: self.stops.append(grace)   # never run on its fake pid's group.
        self.got = queue.Queue()

    def serve(self, delay):
        def run():
            frame = self.end.receive()
            self.got.put(frame["request"])
            time.sleep(delay)   # After the lead gave up on it.
            self.writer.send({"seq": frame["seq"], "response": {"ok": True, "lease": "token-1", "seconds": 10.0}})
            frame = self.end.receive()
            self.got.put(frame["request"])
            self.writer.send({"seq": frame["seq"], "response": {"ok": True}})
        threading.Thread(target=run, daemon=True).start()


class LateLeaseGrantsAreReleased(unittest.TestCase):
    """#4: a read's lease request timed out (1 s) while the worker granted it later (it may wait 5 s): its answer
    slot was gone, so the grant was dropped and its 10 s lease stalled the worker's next save."""

    def check(self, ask):
        pair = Pair(self)
        pair.serve(0.4)
        with patch.object(lifecycle, "_serving", return_value=pair.worker):
            ask(pair.worker)
        self.assertEqual(pair.got.get(timeout=5)["op"], "part.lease")
        released = pair.got.get(timeout=5)
        self.assertEqual((released["op"], released.get("lease")), ("part.unlease", "token-1"))

    def test_the_audit_poc_a_read_lease_granted_late_is_released(self):
        def ask(worker):
            with self.assertRaises(lifecycle.ChatOpFailed):
                lifecycle._lease("/parts/worker-1.blend", 0.15)
        self.check(ask)

    def test_a_merge_gates_lease_granted_late_is_released(self):
        answers = []

        def ask(worker):
            with patch.object(lifecycle, "_background", side_effect=lambda function, name: function()):
                worker_merge_gate._ask(None, "part.lease", worker, 0.15, {"part": "/parts/worker-1.blend",
                                                                          "seconds": 10.0}, answers.append)
            self.assertIsInstance(answers[0], lifecycle.ChatOpFailed)
        self.check(ask)


class CountReadsStayInsideTheirLease(unittest.TestCase):
    """#5: the count's lease kept no grant time, so a UI pump delayed past its 10 s still read the file."""

    def setUp(self):
        counts.reset()
        self.addCleanup(counts.reset)
        handle, self.path = tempfile.mkstemp(suffix=".blend")
        os.close(handle)
        self.addCleanup(os.unlink, self.path)
        self.clock, self.sent, self.reads, self.spawned = [100.0], [], [], []
        worker = NS(alive=lambda: True)

        def send(session, op, worker, timeout, **fields):
            self.sent.append((op, fields.get("lease")))
            return {"ok": True, "lease": "token-%d" % len(self.sent), "seconds": 10.0} if op == "part.lease" else {
                "ok": True}

        def read(key):
            self.reads.append(key)
            return 3
        for patcher in (patch.dict(sys.modules, {"bpy": NS()}), patch.object(lifecycle, "enabled", return_value=True),
                        patch.object(lifecycle, "_serving", return_value=worker),
                        patch.object(lifecycle, "_send", side_effect=send),
                        patch.object(counts, "_spawn", side_effect=self.spawned.append),
                        patch.object(counts, "_read", side_effect=read),
                        patch.object(counts, "time", NS(monotonic=lambda: self.clock[0]))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def count(self):
        """``count``, then its lease threads (spawned under the module's lock) run to their end."""
        found = counts.count(self.path)
        while self.spawned:
            self.spawned.pop(0)()
        return found

    def pump(self):
        counts.pump(self.clock[0])
        while self.spawned:
            self.spawned.pop(0)()

    def test_the_audit_poc_a_pump_after_the_lease_expired_reads_nothing_and_asks_again(self):
        self.assertEqual(self.count(), counts.UNKNOWN)
        self.clock[0] += 11.0   # The UI pump was held up past the lease.
        self.pump()
        self.assertEqual(self.reads, [], "the file was read after its lease expired")
        self.assertIn(("part.unlease", "token-1"), self.sent)
        self.assertEqual(self.count(), counts.UNKNOWN)   # Asked again.
        self.pump()
        self.assertEqual(len(self.reads), 1)
        self.assertEqual([op for op, _lease in self.sent].count("part.lease"), 2)

    def test_a_pump_in_time_reads_under_its_lease(self):
        self.count()
        self.clock[0] += 0.3
        self.pump()
        self.assertEqual(len(self.reads), 1)
        self.assertIn(("part.unlease", "token-1"), self.sent)


class PartSaveFailuresReachTheChats(unittest.TestCase):
    """#6: the worker said ``part_save_failed`` with no session; only ``part_saved`` was special-cased, so the
    failure went to the chat router, which dropped it: a full disk, and no chat said its edits were unsaved."""

    def setUp(self):
        from angelo_agent import worker_mirror, worker_mirror_ops
        from angelo_agent.controller import Controller
        from angelo_agent.worker_mirror import MirrorController
        from test_native_worker_mirror import Ops
        worker_mirror_ops.bind(spawn=lambda run: run())
        self.addCleanup(worker_mirror_ops.reset)
        self.worker = NS(chat_router=worker_mirror.EventRouter())
        self.mirrors = []
        for _ in range(2):
            folder = tempfile.TemporaryDirectory()
            self.addCleanup(folder.cleanup)
            session = str(uuid.uuid4())
            Controller(Path(folder.name), session_id=session).save()
            mirror = MirrorController(Path(folder.name), session, Ops())
            self.worker.chat_router.open(mirror)
            self.mirrors.append(mirror)
        for patcher in (patch.dict(wiring._chats, {m.state["id"]: (m, self.worker) for m in self.mirrors}),
                        patch.dict(lifecycle._hosted, {m.state["id"]: self.worker for m in self.mirrors})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def frame(self, kind, data):
        wiring.route(self.worker, {"session": None, "kind": kind, "t": 0.0, "data": data})
        for mirror in self.mirrors:
            mirror.pump_events()

    def rows(self, mirror):
        return [row for row in mirror.state.get("messages") or ()
                if row.get("role") == "error" and "support code WRK-" in row.get("text", "")]

    def test_the_audit_poc_a_failed_save_is_a_kept_row_in_every_mirror_once_per_episode(self):
        failure = {"error": "OSError: [Errno 28] No space left on device"}
        self.frame("part_save_failed", failure)
        self.frame("part_save_failed", failure)   # The retry a second later: the same episode.
        for mirror in self.mirrors:
            self.assertEqual(len(self.rows(mirror)), 1)
            self.assertNotIn("Errno", self.rows(mirror)[0]["text"])   # The raw words are only logged.
            mirror.pump_events()
            self.assertEqual(len(self.rows(mirror)), 1)   # Kept across the next pump.
        self.frame("part_saved", {"path": "/p.blend", "revision": 2, "layout": None, "part": None})
        self.frame("part_save_failed", failure)   # A new episode after a save that worked.
        for mirror in self.mirrors:
            self.assertEqual(len(self.rows(mirror)), 2)


class SignatureReadsOnlyWhatItKeeps(unittest.TestCase):
    """#7: the bulk matrix read took every object (100,000 objects: a 6.4 MB buffer and its copies) to keep the
    first 1,000."""

    def test_the_audit_poc_a_large_scene_reads_at_most_the_kept_objects(self):
        from test_native_scene_signature import Obj, Objects, rows
        objects = [Obj("Cube.%05d" % i, rows(i, 0, 0)) for i in range(3000)]
        floats = []

        class Counted(Objects):
            def foreach_get(self, name, buffer):
                floats.append(len(buffer))
                return Objects.foreach_get(self, name, buffer)
        signature = scene_signature.scene_signature(NS(objects=Counted(objects)))
        self.assertTrue(signature["truncated"])
        self.assertEqual(len(signature["objects"]), 1000)
        self.assertTrue(all(count <= 16 * 1000 for count in floats), floats)
        kept = signature["objects"][str(objects[999].session_uid)]["matrix"]
        self.assertEqual(kept.raw, scene_signature._one_matrix(objects[999]))

    def test_a_scene_within_the_bound_is_still_one_bulk_read(self):
        from test_native_scene_signature import Obj, Objects, rows
        objects = [Obj("Cube.%03d" % i, rows(i, 0, 0)) for i in range(40)]
        Objects.reads = 0
        signature = scene_signature.scene_signature(NS(objects=Objects(objects)))
        self.assertEqual(Objects.reads, 1)
        self.assertFalse(signature["truncated"])


def setUpModule():   # Journal reads run inline here, as each pump asks; the reader thread has its own tests.
    from angelo_agent import worker_mirror_reader
    worker_mirror_reader.bind(inline=True)


def tearDownModule():
    from angelo_agent import worker_mirror_reader
    worker_mirror_reader.reset()


if __name__ == "__main__":
    unittest.main()
