# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The second external audit of A′ (e837c972c40), the channel's findings, on the real socket channel:

* #2: ``Client.request`` registered its late callback under the lock, then read the answer slot outside it. An
  answer landing between the two reached both the caller and the callback, so a lease the callback released was
  then read under by the caller. Who gets an answer is now decided once, under the lock.
* #4: the worker answered a lease request on its only reader thread and waited there for a save; the release of the
  lease that save waited for queued behind it, as did Stop and consent frames. A lease request is now recorded and
  answered as the save ends; the reader never waits.
"""

import queue
import socket
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import part_leases, worker_channel, worker_host  # noqa: E402
from angelo_agent.headless_pipe import Writer  # noqa: E402


def socket_pair(case):
    ours, theirs = socket.socketpair()
    case.addCleanup(ours.close)
    case.addCleanup(theirs.close)
    return ours, theirs


class HookLock:
    """The client's lock, with a hook run on the requesting thread right after each of its releases there."""

    def __init__(self):
        self._lock, self.hook, self.owner = threading.Lock(), None, None

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, *_exc):
        self._lock.release()
        if self.hook is not None and threading.current_thread() is self.owner:
            self.hook()
        return False


class AnswersAreDeliveredOnce(unittest.TestCase):
    def setUp(self):
        ours, theirs = socket_pair(self)
        self.client = worker_channel.Client(worker_channel.socket_endpoint(ours, 1 << 20, 1 << 20))
        self.addCleanup(self.client.close)
        self.lock = self.client._lock = HookLock()
        self.end = worker_channel.socket_endpoint(theirs, 1 << 20, 1 << 20)
        self.writer = Writer(theirs.makefile("wb", buffering=0), 1 << 20)
        self.late = queue.Queue()

    def answer_after_the_late_callback_is_registered(self):
        """The worker answers just after the request registered its ``late`` and released the lock, then waits until
        the reader has handed that answer on: the window the audit found."""
        frames = queue.Queue()
        threading.Thread(target=lambda: frames.put(self.end.receive()), daemon=True).start()

        def hook():
            waiting = [slot for slot in self.client._waiting.values() if len(slot) > 2]
            if not waiting:
                return
            self.lock.hook = None
            frame = frames.get(timeout=5)
            self.writer.send({"seq": frame["seq"], "response": {"ok": True, "lease": "token-1", "seconds": 10.0}})
            deadline = time.monotonic() + 5
            while not waiting[0][0].is_set() and time.monotonic() < deadline:
                time.sleep(0.001)
            time.sleep(0.05)   # The reader's hand-off to ``late`` (a thread of its own) runs now.
        self.lock.owner, self.lock.hook = threading.current_thread(), hook

    def test_the_audit_poc_a_late_lease_answer_reaches_the_caller_or_its_release_never_both(self):
        self.answer_after_the_late_callback_is_registered()
        answer = self.client.request({"op": "part.lease"}, 0.05, late=self.late.put)
        delivered = [answer] if answer is not None else []
        try:
            delivered.append(self.late.get(timeout=1))
        except queue.Empty:
            pass
        self.assertEqual(len(delivered), 1, "the answer reached both the caller and its late callback")

    def test_an_answer_in_time_goes_to_the_caller_only(self):
        def serve():
            frame = self.end.receive()
            self.writer.send({"seq": frame["seq"], "response": {"ok": True}})
        threading.Thread(target=serve, daemon=True).start()
        self.assertEqual(self.client.request({"op": "x"}, 5, late=self.late.put), {"ok": True})
        self.assertTrue(self.late.empty())

    def test_an_answer_after_the_caller_gave_up_goes_to_late_only(self):
        frames = queue.Queue()
        threading.Thread(target=lambda: frames.put(self.end.receive()), daemon=True).start()
        self.assertIsNone(self.client.request({"op": "x"}, 0.05, late=self.late.put))
        frame = frames.get(timeout=5)
        self.writer.send({"seq": frame["seq"], "response": {"ok": True, "n": 1}})
        self.assertEqual(self.late.get(timeout=5), {"ok": True, "n": 1})
        self.assertEqual(self.client._waiting, {})


class TheReaderNeverWaitsForASave(unittest.TestCase):
    """#4: lease L1 held; the worker's save waits for it; a second lease request arrives, then L1's release and a
    Stop. The reader blocked in the second grant (up to 5 s), so the release and the Stop waited behind it."""

    def setUp(self):
        ours, theirs = socket_pair(self)
        self.client = worker_channel.Client(worker_channel.socket_endpoint(ours, 1 << 20, 1 << 20))
        self.addCleanup(self.client.close)
        self.part = str(Path(__file__).resolve())
        self.host = object.__new__(worker_host.WorkerHost)   # Its real reader, without a Blender behind it.
        self.host.endpoint = worker_channel.socket_endpoint(theirs, 1 << 20, 1 << 20)
        self.host.part, self.host.inbox, self.host.chats = self.part, queue.Queue(), None
        self.host.start_reader()

    def ask(self, request, timeout=5.0):
        return self.client.request(request, timeout)

    def test_the_audit_poc_a_release_and_a_stop_pass_a_lease_request_waiting_for_a_save(self):
        first = self.ask({"op": "part.lease", "part": self.part, "seconds": 10})
        self.assertTrue(first["ok"])
        saved, saving = threading.Event(), threading.Event()

        def save():   # The worker's main thread: its save waits for the lease the lead holds.
            saving.set()
            with part_leases.leases_of(self.host).saving(self.part):
                saved.set()
        threading.Thread(target=save, daemon=True).start()
        self.assertTrue(saving.wait(5))
        time.sleep(0.05)
        second = []
        threading.Thread(target=lambda: second.append(self.ask({"op": "part.lease", "part": self.part,
                                                                 "seconds": 10}, 10)), daemon=True).start()
        time.sleep(0.1)   # The second request is on the reader now.
        began = time.monotonic()
        released = self.ask({"op": "part.unlease", "lease": first["lease"]}, 2.0)
        self.assertEqual(released, {"ok": True, "released": True}, "the release waited behind the lease request")
        self.assertLess(time.monotonic() - began, 1.0)
        self.assertTrue(saved.wait(2), "the save waited for the lease past its release")
        stop = (1 << 30)
        self.client.endpoint.send({"seq": stop, "request": {"op": "chat.stop", "session": "s"}})
        self.assertEqual(self.host.inbox.get(timeout=1), (stop, {"op": "chat.stop", "session": "s"}))
        deadline = time.monotonic() + 5
        while not second and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(second and second[0]["ok"], "the lease asked during the save was never granted")

    def test_a_save_grants_the_leases_asked_before_it_then_waits_for_them(self):
        table, granted = part_leases.leases_of(self.host), []
        with table.saving(self.part):   # A lease asked while a save runs: answered as it ends.
            table.ask(self.part, 0.2, granted.append)
            self.assertEqual(granted, [])
        self.assertEqual(len(granted), 1)
        table.unlease(granted[0][0])
        table._saving, asked = 1, []   # One asked while another save ran (as on the main thread, one at a time).
        table.ask(self.part, 0.2, asked.append)
        table._saving = 0
        began = time.monotonic()
        with table.saving(self.part):   # It grants it first, so a stream of saves never starves a read.
            waited = time.monotonic() - began
        self.assertEqual(len(asked), 1)
        self.assertGreaterEqual(waited, 0.15)

    def test_a_lease_for_another_file_is_still_refused_at_once(self):
        answer = self.ask({"op": "part.lease", "part": "/elsewhere.blend", "seconds": 1}, 2.0)
        self.assertIn("error", answer)


if __name__ == "__main__":
    unittest.main()
