# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker Blender that ends on its own runs the ended path a stop takes, at once and exactly once (FE D5).

A real ``WorkerProcess`` starts a real process (``worker_process_fake_worker.py``) on the real channel with A′ on,
so its connection has the chat wiring (``worker_chat_wiring.attach``). Pinned without Blender: a worker that exits
mid-call hands its hosted chat back with no further request, crash-marked (``ended_on_its_own``), its runtime
service closed and its private folder removed; one whose process exits while something else holds its connection
open is noticed too; a stop of Angelo's racing the death runs the ended path once; a death is a crash whatever began
first (a stop after the death, a request that met the ended connection before the process was reaped, a signal during
Angelo's stop); a stop of Angelo's never counts as ending on its own; and ``handed_back`` crash-marks the chat's
binding on the main thread only then.
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
        self.handoffs, self.reasons, self.worker = [], [], None

        def handoff(session, closed):   # What the real ``handed_back`` decides, at the moment it runs.
            self.handoffs.append((session, closed))
            self.reasons.append(None if closed else worker_chat_wiring.crash_reason(self.worker))
        for patcher in (mock.patch.dict(os.environ, {"ANGELO_WORKER_CHAT_HOST": "1"}),
                        mock.patch.object(lifecycle, "host_available", lambda: True),
                        mock.patch.object(lifecycle, "_hosted", {}),
                        mock.patch.object(lifecycle, "HANDOFF", [handoff]),
                        mock.patch.object(worker_process, "_live", set())):
            patcher.start()
            self.addCleanup(patcher.stop)

    def start(self, mode="exit"):
        def popen(command, **options):   # The documented command's channel, run by the stand-in instead of Blender.
            channel, private = (command[command.index(flag) + 1] for flag in ("--channel", "--private"))
            return subprocess.Popen([sys.executable, "-B", "-I", str(FAKE), str(ADDONS), channel, mode, private],
                                    **options)
        self.reports = Path(self.temporary) / "reports"   # Where Angelo's crash report reads crash logs.
        self.reports.mkdir(exist_ok=True)
        worker = worker_process.WorkerProcess(Path(self.temporary) / "part.blend", binary="/unused", script="/unused",
                                              environ={"PATH": os.environ.get("PATH", "")}, popen=popen,
                                              temporary=self.temporary, crash_reports=self.reports)
        worker._killpg = os.killpg   # Its scripts' processes share its group, as for a real worker.
        self.addCleanup(lambda: worker.process.poll() is None and worker.process.kill())
        worker.start(10)
        lifecycle._hosted["chat"] = worker   # It hosts a chat (``chat.open`` answered).
        self.worker = worker
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
                # This worker's ended path only: a worker an earlier case left still ending is not this case's.
                with mock.patch.object(lifecycle, "ended", lambda w: (w is worker and ended.append(w),
                                                                      lifecycle._drop("chat", False))):
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
                self.assertTrue(worker.ended_on_its_own)   # A crash, whichever of the three decided first.

    def without_watcher(self):
        """The race forced: Angelo's stop always begins before the watcher could decide (1.0.8,
        worker_crash_mid_call/crash_binding_marked_ended under load)."""
        patcher = mock.patch.object(worker_process.WorkerProcess, "_watch", lambda self, process, client: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_stop_that_begins_after_the_death_still_marks_it_a_crash(self):
        self.without_watcher()
        worker = self.start()
        worker.process.kill()
        self.assertTrue(wait_for(lambda: worker.process.poll() is not None))
        worker.stop(1.0)
        self.assertEqual(self.handoffs, [("chat", False)])
        self.assertIn("code -9", self.reasons[0] or "")   # Decided when the chat was handed back, not after.

    def test_a_request_that_meets_the_ended_connection_first_still_marks_it_a_crash(self):
        self.without_watcher()
        worker = self.start()
        real_poll, unreaped = worker.process.poll, threading.Event()
        unreaped.set()
        worker.process.poll = lambda: None if unreaped.is_set() else real_poll()   # Dead, not yet reaped.
        stopping = threading.Event()
        stop = worker.stop
        with mock.patch.object(worker, "stop", lambda *args: (stopping.set(), stop(*args))):
            with self.assertRaises(worker_process.WorkerUnavailable) as raised:
                worker.request("die", 5)   # It dies under the request: Angelo's stop starts first (request's own).
            self.assertIn("connection ended while it still ran", str(raised.exception))
            self.assertTrue(stopping.wait(5))
            self.assertTrue(wait_for(lambda: self.handoffs == [("chat", False)]), self.handoffs)
            unreaped.clear()
            self.assertTrue(wait_for(lambda: worker._stopped is worker.process))
        self.assertTrue(self.reasons[0], "the crash was decided as the chat was handed back")

    def test_a_death_by_signal_during_angelos_stop_is_still_a_crash(self):
        self.without_watcher()
        worker = self.start()
        closing = lifecycle.closing

        def closing_while_it_dies(w):
            w.process.send_signal(11)   # It dies of its own fault while Angelo's stop closes its chats.
            closing(w)
        with mock.patch.object(lifecycle, "closing", closing_while_it_dies):
            worker.stop(1.0)
        self.assertIn("code -11", worker.ended_on_its_own or "")
        self.assertEqual(self.reasons, [worker.ended_on_its_own])   # Its chat was handed back as a crash.

    def test_a_death_by_signal_after_angelo_only_closed_its_connection_is_still_a_crash(self):
        self.without_watcher()
        worker = self.start()
        close = worker.client.close

        def close_while_it_dies():
            worker.process.send_signal(11)   # After Angelo's close, before the process's own exit.
            close()
        worker.client.close = close_while_it_dies
        with mock.patch.object(lifecycle, "closing", lambda w: None):   # Its chat stays to be handed back by ``ended``.
            worker.stop(1.0)
        self.assertEqual(self.handoffs, [("chat", False)])
        self.assertIn("code -11", self.reasons[0] or "")

    def test_a_worker_angelo_had_to_kill_is_never_a_crash(self):
        self.without_watcher()
        worker = self.start("stubborn")   # Refuses its close and ignores its connection's end: only a kill ends it.
        worker.stop(0.5)
        self.assertEqual(worker.process.poll(), -9)
        self.assertEqual(self.handoffs, [("chat", False)])
        self.assertEqual(self.reasons, [None])   # Angelo's own kill, asked at hand-back: never a crash.
        self.assertIsNone(worker.ended_on_its_own)

    def test_a_worker_angelo_kills_while_it_is_crashing_is_still_a_crash(self):
        """Blender's crash handler (up to 10 s), or macOS's ReportCrash holding the exiting process: the grace kill
        lands on a crash under way. Its crash log decides, never the kill's -9."""
        self.without_watcher()
        worker = self.start("held_crash")
        log = Path(worker.private) / "t" / "part.crash.txt"

        def closing_while_it_crashes(w):
            w.process.send_signal(11)
            self.assertTrue(wait_for(log.exists), "its crash handler wrote no log")
        with mock.patch.object(lifecycle, "closing", closing_while_it_crashes):
            worker.stop(0.5)
        self.assertEqual(worker.process.poll(), -9)   # Angelo's kill ended it ...
        self.assertEqual(self.handoffs, [("chat", False)])
        self.assertIn("crash log part.crash.txt", self.reasons[0] or "")   # ... and it was a crash all the same.
        kept = self.reports / ("angelo-worker-%d-part.crash.txt" % worker.process.pid)
        self.assertTrue(kept.is_file(), "its crash log is kept for Angelo's crash report")   # Its private folder went.
        self.assertFalse(Path(worker.private).exists())

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


class CrashEvidence(unittest.TestCase):
    """The rules a kill of Angelo's is judged by (``worker_crash_evidence``), on every platform's codes."""

    def test_only_a_crash_status_counts_never_angelos_own_kill(self):
        from angelo_agent.worker_crash_evidence import crash_status
        self.assertTrue(crash_status(-11, "darwin", killed=True))      # A signal the process was already dying of.
        self.assertFalse(crash_status(-9, "darwin", killed=True))      # Angelo's SIGKILL.
        self.assertTrue(crash_status(-9, "linux", killed=False))       # A SIGKILL Angelo never sent.
        self.assertFalse(crash_status(0, "linux", killed=False))
        self.assertTrue(crash_status(0xC0000005, "win32", killed=True))   # An access violation.
        self.assertTrue(crash_status(11, "win32", killed=True))           # Blender's crash exit.
        self.assertFalse(crash_status(1, "win32", killed=True))           # The Job Object's termination.

    def test_a_process_dying_of_a_signal_is_read_from_its_kernel_flags(self):
        from angelo_agent.worker_crash_evidence import PF_DUMPCORE, PF_SIGNALED, dying
        proc = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, proc, True)

        def stat(pid, flags, comm="Angelo (worker)"):   # A command name may hold spaces and parentheses.
            (proc / str(pid)).mkdir()
            (proc / str(pid) / "stat").write_text("%d (%s) D 1 %d %d 0 -1 %d 0 0 0 0\n" % (pid, comm, pid, pid, flags))
        stat(10, PF_SIGNALED | PF_DUMPCORE)   # A fatal signal began its core dump.
        stat(11, 0x00400040)   # A healthy process's flags.
        self.assertTrue(dying(10, proc))
        self.assertFalse(dying(11, proc))
        self.assertFalse(dying(12, proc))   # Gone, or not Linux: no evidence.

    def test_a_crash_log_counts_only_in_the_workers_own_folder(self):
        from angelo_agent.worker_crash_evidence import blender_temp, crash_log
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        mine, other = root / "angw-a", root / "angw-b"
        for private in (mine, other):
            blender_temp(private).mkdir(parents=True)
        (blender_temp(other) / "part.crash.txt").write_text("# backtrace\n", encoding="utf-8")   # Another worker's.
        (root / "part.crash.txt").write_text("# backtrace\n", encoding="utf-8")   # The shared folder's, any run's.
        self.assertIsNone(crash_log(mine))
        (blender_temp(mine) / "angelo.crash.txt.partial").write_text("# backtrace\n", encoding="utf-8")   # Its own.
        self.assertEqual(crash_log(mine).name, "angelo.crash.txt.partial")
        self.assertIsNone(crash_log(None))
        from angelo_agent.worker_crash_evidence import keep
        self.assertIsNone(keep(mine, root, 7))   # Only a complete log is kept, never an incomplete .partial.
        (blender_temp(mine) / "angelo.crash.txt").write_text("# backtrace\n", encoding="utf-8")
        self.assertEqual(keep(mine, root, 7), root / "angelo-worker-7-angelo.crash.txt")


def setUpModule():   # Journal reads run inline here, as each pump asks; the reader thread has its own tests.
    from angelo_agent import worker_mirror_reader
    worker_mirror_reader.bind(inline=True)


def tearDownModule():
    from angelo_agent import worker_mirror_reader
    worker_mirror_reader.reset()


if __name__ == "__main__":
    unittest.main()
