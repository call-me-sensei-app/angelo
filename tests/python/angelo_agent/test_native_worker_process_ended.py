# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker Blender that ends on its own runs the ended path a stop takes, at once and exactly once (FE D5).

A real ``WorkerProcess`` starts a real process (``worker_process_fake_worker.py``) on the real channel with A′ on,
so its connection has the chat wiring (``worker_chat_wiring.attach``). Pinned without Blender: a worker that exits
mid-call hands its hosted chat back with no further request, crash-marked (``ended_on_its_own``), its runtime
service closed and its private folder removed; one whose process exits while something else holds its connection
open is noticed too; a stop of Angelo's racing the death runs the ended path once; a stop of Angelo's never counts
as ending on its own; and ``handed_back`` crash-marks the chat's binding on the main thread only then.
"""

import os
import subprocess
import sys
import tempfile
import shutil
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ADDONS = Path(__file__).resolve().parents[3] / "scripts/addons_core"
sys.path.insert(0, str(ADDONS))
from angelo_agent import (worker_chat_lifecycle as lifecycle, worker_chat_wiring, worker_journal_owner,  # noqa: E402
                          worker_mirror_events, worker_process)

FAKE = Path(__file__).resolve().parent / "worker_process_fake_worker.py"
POSIX_ONLY = unittest.skipIf(sys.platform == "win32", "The stand-in worker connects by an inherited descriptor")


def wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@POSIX_ONLY
class EndedOnItsOwn(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temporary, True)
        self.handoffs = []
        for patcher in (mock.patch.dict(os.environ, {"ANGELO_WORKER_CHAT_HOST": "1"}),
                        mock.patch.object(lifecycle, "host_available", lambda: True),
                        mock.patch.object(lifecycle, "_hosted", {}),
                        mock.patch.object(lifecycle, "HANDOFF", [lambda session, closed: self.handoffs.append(
                            (session, closed))]),
                        mock.patch.object(worker_process, "_live", set())):
            patcher.start()
            self.addCleanup(patcher.stop)

    def start(self, mode="exit"):
        def popen(command, **options):   # The documented command's channel, run by the stand-in instead of Blender.
            channel = command[command.index("--channel") + 1]
            return subprocess.Popen([sys.executable, "-B", "-I", str(FAKE), str(ADDONS), channel, mode], **options)
        worker = worker_process.WorkerProcess(Path(self.temporary) / "part.blend", binary="/unused", script="/unused",
                                              environ={"PATH": os.environ.get("PATH", "")}, popen=popen,
                                              temporary=self.temporary)
        worker._killpg = os.killpg   # Its scripts' processes share its group, as for a real worker.
        self.addCleanup(lambda: worker.process.poll() is None and worker.process.kill())
        worker.start(10)
        lifecycle._hosted["chat"] = worker   # It hosts a chat (``chat.open`` answered).
        return worker

    def test_a_worker_that_exits_mid_call_is_handed_back_with_no_further_request(self):
        worker = self.start()
        private = worker.private
        with self.assertRaises(worker_process.WorkerUnavailable):
            worker.request("die", 5)
        self.assertTrue(wait_for(lambda: self.handoffs == [("chat", False)]), self.handoffs)
        self.assertTrue(wait_for(lambda: worker._stopped is worker.process))
        self.assertEqual(worker.process.poll(), 7)
        self.assertTrue(worker.ended_on_its_own)
        self.assertTrue(worker.runtime.closed)
        self.assertFalse(Path(private).exists())
        self.assertFalse(worker.alive())

    def test_a_process_that_exits_while_its_connection_stays_open_is_noticed(self):
        worker = self.start("linger")
        threading.Thread(target=lambda: self.assertRaises(worker_process.WorkerUnavailable, worker.request, "die",
                                                          30), daemon=True).start()
        self.assertTrue(wait_for(lambda: self.handoffs == [("chat", False)]), self.handoffs)
        self.assertIn("code 7", worker.ended_on_its_own)
        self.assertTrue(wait_for(lambda: worker.client.ended.is_set()), "The lingering process kept the connection")

    def test_a_stop_racing_the_death_runs_the_ended_path_once(self):
        for _ in range(3):
            with self.subTest():
                self.handoffs.clear()
                worker = self.start()
                ended = []
                with mock.patch.object(lifecycle, "ended", lambda w: (ended.append(w), lifecycle._drop("chat", False))):
                    stops = [threading.Thread(target=worker.stop, args=(1.0,)) for _ in range(2)]
                    worker.process.kill()
                    for thread in stops:
                        thread.start()
                    for thread in stops:
                        thread.join(15)
                    self.assertTrue(wait_for(lambda: worker._stopped is worker.process))
                    time.sleep(worker_process.WATCH_SECONDS * 2)   # The watcher, too, has had its turn.
                self.assertEqual(ended, [worker])
                self.assertEqual(self.handoffs, [("chat", False)])

    def test_a_stop_of_angelos_never_counts_as_ending_on_its_own(self):
        worker = self.start()
        worker.stop(1.0)
        time.sleep(worker_process.WATCH_SECONDS * 2)
        self.assertIsNone(worker.ended_on_its_own)
        self.assertEqual(self.handoffs, [("chat", True)])   # Closed by its answered ``chat.close``.


class HandedBack(unittest.TestCase):
    """``worker_chat_wiring.handed_back`` after a process ended: the binding is crash-marked only for a worker that
    ended on its own, and ``crash_mark`` (main thread) ends only a binding to that worker."""

    def handed_back(self, why):
        worker = SimpleNamespace(chat_router=SimpleNamespace(close=lambda session: None), ended_on_its_own=why)
        mirror = SimpleNamespace(send_op=None, pending={}, store=SimpleNamespace(directory="/d"))
        put = []
        with mock.patch.dict(worker_chat_wiring._chats, {"s": (mirror, worker)}), \
                mock.patch.object(worker_journal_owner, "hosted", lambda session: True), \
                mock.patch.object(worker_journal_owner, "worker_ended",
                                  lambda *a, **k: SimpleNamespace(state=None, finished=None)), \
                mock.patch.object(worker_mirror_events, "put", lambda m, kind, data: put.append((kind, data))):
            worker_chat_wiring.handed_back("s", closed=False)
        return worker, [kind for kind, _ in put], put

    def test_only_a_worker_that_ended_on_its_own_crash_marks_its_binding(self):
        self.assertEqual(self.handed_back(None)[1], [worker_mirror_events.RECOVERED])
        worker, kinds, put = self.handed_back("its process exited (code 9)")
        self.assertEqual(kinds, [worker_mirror_events.RECOVERED, worker_chat_wiring.CRASHED])
        died = []
        executor = SimpleNamespace(worker=worker, worker_id="w2", _died=died.append)
        self.assertTrue(worker_chat_wiring.crash_mark(SimpleNamespace(remote_executor=executor), put[1][1]))
        self.assertEqual(executor.document_key, ("angelo-worker", "w2", "ended"))
        self.assertIn("code 9", died[0])
        other = SimpleNamespace(worker=object(), worker_id="w3", _died=died.append)
        self.assertFalse(worker_chat_wiring.crash_mark(SimpleNamespace(remote_executor=other), put[1][1]))
        self.assertFalse(worker_chat_wiring.crash_mark(SimpleNamespace(remote_executor=None), put[1][1]))
        self.assertEqual(len(died), 1)


if __name__ == "__main__":
    unittest.main()
