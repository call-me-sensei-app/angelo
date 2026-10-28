# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A merge right after a park is never refused because the park's close was under way (``worker_chat_closing``).
No Blender.

Evidence: Mac 2 under load (3c351c9, parallel_resume@hosted ``in_ask_its_merge_runs_with_no_card``): the merge sent
``chat.hold`` while the park's ``chat.close`` was in flight, the closed chat answered "This worker hosts no such
chat", and the merge was refused WRITER_BUSY. Pinned, with a fake worker on real threads behind the real seam
(``worker_chat_lifecycle.send_op``) whose ``chat.close`` blocks on an Event and whose ``chat.hold`` answers that it
hosts no such chat: the merge waits for the close (no hold is sent), then leases the part and runs; a hold answered
"no such chat" because the chat closed meanwhile holds nothing and refuses nothing, and its release is quiet; the same
answer for a chat Angelo still counts as hosted, or a close that never ends, still refuses (fail closed); a second
close joins the one under way; the worker marks its refusal by details, never words.
"""

import io
import os
import sys
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (worker_chat_closing as closing, worker_chat_holds, worker_chat_lifecycle as lifecycle,  # noqa: E402
                          worker_merge_gate as gate, worker_process, worker_values)
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from test_native_worker_merge_async import Worker, until  # noqa: E402

PART = "/p/1.blend"


def not_hosted():
    """The worker's own answer for an op of a chat it does not host (``WorkerChatHost.handle``)."""
    return {"error": worker_values.encode_error(worker_chat_holds.refused(
        "This worker hosts no such chat", details=worker_chat_holds.NOT_HOSTED))}


class ParkThenMerge(unittest.TestCase):
    def setUp(self):
        self.worker, self.closed = Worker(PART), threading.Event()
        self.addCleanup(self.closed.set)
        self.addCleanup(self.worker.unwedge)
        self.worker.answers["chat.close"] = lambda fields: (self.closed.wait(10), {"ok": True, "closed": True})[1]
        for patcher in (mock.patch.dict(os.environ, {"ANGELO_WORKER_CHAT_HOST": "1"}),
                        mock.patch.object(lifecycle, "host_available", lambda: True),
                        mock.patch.object(lifecycle, "_hosted", {"w1": self.worker}),
                        mock.patch.object(lifecycle, "_closing", {}),
                        mock.patch.object(lifecycle, "HANDOFF", []),
                        mock.patch.object(worker_process, "_live", {self.worker})):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(gate.reset)
        self.threads = []
        self.addCleanup(self.join)   # Runs first: no thread of this test outlives its patches.

    def join(self):
        self.closed.set()
        self.worker.unwedge()
        for thread in self.threads:
            thread.join(5)

    def start(self, target):
        thread = threading.Thread(target=target, daemon=True)
        self.threads.append(thread)
        thread.start()
        return thread

    def park(self):
        """A park's close (``worker_queue._park``) on its own thread; its result once it ended."""
        result = {}
        thread = self.start(lambda: result.update(closed=lifecycle.close_chat("w1", "parked")))
        self.assertTrue(until(lambda: "chat.close" in self.worker.ops()))
        return thread, result

    def merge(self):
        """A merge's reads under its gate (``worker_merge_gate.waited``) on its own thread."""
        outcome = {}

        def run():
            try:
                with gate.waited(["w1"], [PART]):
                    outcome["ops"] = self.worker.ops()
            except RefusalError as error:
                outcome["refused"] = error
        return self.start(run), outcome

    def test_a_merge_right_after_a_park_waits_for_its_close_and_is_never_refused(self):
        self.worker.answers["chat.hold"] = lambda fields: not_hosted()
        parking, parked = self.park()
        self.assertEqual(closing.state("w1"), closing.CLOSING)   # Closing from before chat.close was sent.
        merging, outcome = self.merge()
        time.sleep(0.2)
        self.assertNotIn("chat.hold", self.worker.ops())   # Nothing is asked of a chat whose close is under way.
        self.assertTrue(merging.is_alive())
        self.closed.set()
        parking.join(5)
        merging.join(5)
        self.assertTrue(parked["closed"])
        self.assertIsNone(closing.state("w1"))
        self.assertNotIn("refused", outcome)
        self.assertEqual(outcome["ops"], ["chat.close", "part.lease"])   # Only the part's lease.
        self.assertTrue(until(lambda: "part.unlease" in self.worker.ops()))
        self.assertNotIn("chat.release", self.worker.ops())   # Nothing was held, so nothing is released.

    def test_a_hold_answered_no_such_chat_after_the_chat_closed_holds_nothing(self):
        """The hold left before the park marked the chat closing, and reached the worker after its close."""
        self.closed.set()

        def closed_first(fields):
            lifecycle.close_chat("w1", "parked")   # The park's close lands in the worker before the hold.
            return not_hosted()
        self.worker.answers["chat.hold"] = closed_first
        merging, outcome = self.merge()
        merging.join(5)
        self.assertNotIn("refused", outcome)
        self.assertEqual(outcome["ops"], ["chat.hold", "chat.close", "part.lease"])

    def test_no_such_chat_for_a_chat_angelo_still_hosts_refuses(self):
        self.worker.answers["chat.hold"] = lambda fields: not_hosted()
        merging, outcome = self.merge()
        merging.join(5)
        self.assertEqual(outcome["refused"].code, "WRITER_BUSY")
        self.assertNotIn("part.lease", self.worker.ops())

    def test_a_close_that_does_not_end_in_time_refuses_the_merge(self):
        self.park()
        with mock.patch.object(lifecycle, "CLOSE_SECONDS", 0.2):
            merging, outcome = self.merge()
            merging.join(5)
        self.assertEqual(outcome["refused"].code, "WRITER_BUSY")
        self.assertNotIn("chat.hold", self.worker.ops())

    def test_a_release_answered_no_such_chat_after_a_close_is_quiet(self):
        self.closed.set()
        self.worker.answers["chat.release"] = lambda fields: not_hosted()
        out = io.StringIO()
        with redirect_stdout(out):
            with gate.waited(["w1"], [PART]):
                lifecycle.close_chat("w1", "parked")   # The chat parks while the merge reads.
            self.assertTrue(until(lambda: "chat.release" in self.worker.ops()))
            time.sleep(0.05)
        self.assertNotIn("could not be released", out.getvalue())

    def test_a_release_refused_for_a_chat_still_hosted_is_still_said(self):
        self.worker.answers["chat.release"] = lambda fields: not_hosted()
        out = io.StringIO()
        with redirect_stdout(out):
            with gate.waited(["w1"], [PART]):
                pass
            self.assertTrue(until(lambda: "could not be released" in out.getvalue()))

    def test_a_second_close_joins_the_one_under_way(self):
        parking, parked = self.park()
        joined = {}
        second = self.start(lambda: joined.update(closed=lifecycle.close_chat("w1", "closed")))
        time.sleep(0.05)
        self.closed.set()
        parking.join(5)
        second.join(5)
        self.assertEqual((parked["closed"], joined["closed"]), (True, True))
        self.assertEqual(self.worker.ops().count("chat.close"), 1)


class NotHostedAnswers(unittest.TestCase):
    def test_the_worker_marks_a_chat_it_does_not_host_by_details(self):
        from angelo_agent.worker_chat_host import WorkerChatHost
        host = SimpleNamespace(hosted=lambda session: None, _hold=None, _close=None)
        with self.assertRaises(RefusalError) as raised:
            WorkerChatHost.handle(host, {"op": "chat.hold", "session": "w1"})
        self.assertEqual(raised.exception.details, worker_chat_holds.NOT_HOSTED)
        self.assertEqual(WorkerChatHost.handle(host, {"op": "chat.close", "session": "w1"}),
                         {"ok": True, "closed": False})

    def test_only_the_details_mark_it_never_the_words(self):
        record = not_hosted()["error"]
        self.assertTrue(closing.not_hosted(lifecycle.ChatOpFailed("chat.hold", "w1", "x", record=record)))
        words = dict(record, details=None)
        self.assertFalse(closing.not_hosted(lifecycle.ChatOpFailed("chat.hold", "w1", "x", record=words)))
        self.assertFalse(closing.not_hosted(lifecycle.ChatOpFailed("chat.hold", "w1", "no answer", timed_out=True)))


if __name__ == "__main__":
    unittest.main()
