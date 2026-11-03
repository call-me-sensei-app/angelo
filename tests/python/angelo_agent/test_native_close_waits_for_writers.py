# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Once ``Controller.close`` returns, no background writer of its state folder writes into it.

CI run 41234975609: two tests' ``TemporaryDirectory`` cleanups failed with "Directory not empty". close() waited
0.2 s for the performance writer and 0.2 s for the usage writer, then returned while they still wrote; their
temporary files landed while the folder was being removed. At quit the same race loses the last receipts and usage.

The writers are held at a gate inside their write (a slow disk), so the order is forced, not hoped for.
"""

import os
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import machine_clock

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from angelo_agent import telemetry, usage_meter, write_turn  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.write_turn import WriteTurn  # noqa: E402
from test_native_usage_meter import receipt as usage_receipt  # noqa: E402

WRITERS = ("AngeloPerformanceWriter", "AngeloUsageWriter")
NEVER = 30  # A test bound only: no wait here is meant to run out.


def performance_receipt():
    return {"status": "completed", "runtime": "codex", "runtime_start": "cold", "session_id": str(uuid.uuid4()),
            "phases": {"total_ms": 12}, "counts": {"tool_invocations": 1}}


class FolderWrites:
    """Every write into ``folder`` by a thread other than the test's, and a gate that holds the background writers."""

    def __init__(self, folder, held=WRITERS):
        self.folder = os.path.realpath(folder)
        self.gate = threading.Event()
        self.arrived = {name: threading.Event() for name in held}   # Held at the gate: these threads' writes.
        self.landed = {name: threading.Event() for name in held}    # Their file replaced.
        self.closed = False
        self.late = []   # (thread, call, name) after close returned
        self.lock = threading.Lock()
        real_open, real_replace = os.open, os.replace

        def guarded(call, real):
            def wrapper(path, *args, **kwargs):
                self._seen(call, path)
                value = real(path, *args, **kwargs)
                if call == "replace" and threading.current_thread().name in self.landed:
                    self.landed[threading.current_thread().name].set()
                return value
            return wrapper
        self.patches = [mock.patch.object(os, "open", guarded("open", real_open)),
                        mock.patch.object(os, "replace", guarded("replace", real_replace))]

    def _seen(self, call, path):
        thread = threading.current_thread()
        if thread is threading.main_thread():
            return
        try:
            inside = os.path.realpath(os.fsdecode(path)).startswith(self.folder + os.sep)
        except (TypeError, ValueError):
            inside = False
        if not inside:
            return
        if thread.name in self.arrived and call == "open" and not self.gate.is_set():
            self.arrived[thread.name].set()
            self.gate.wait(NEVER)   # The write is under way: its disk is slow.
        with self.lock:
            if self.closed:
                self.late.append((thread.name, call, os.path.basename(os.fsdecode(path))))

    def __enter__(self):
        for patcher in self.patches:
            patcher.start()
        return self

    def __exit__(self, *exc):
        self.gate.set()
        for patcher in reversed(self.patches):
            patcher.stop()
        return False


class CloseWaitsForWritersTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.controller = Controller(self.directory.name)
        self.folder = self.controller.telemetry.directory

    def record_both(self):
        self.assertTrue(self.controller.telemetry.record(performance_receipt()))
        usage_meter.record(self.folder, "chat-a", usage_receipt())

    def test_close_returns_only_after_a_write_under_way_and_nothing_writes_after_it(self):
        with FolderWrites(self.folder) as writes, \
                mock.patch.object(write_turn, "CLOSE_SECONDS", NEVER):
            self.record_both()
            for name in WRITERS:
                self.assertTrue(writes.arrived[name].wait(NEVER), name + " never started its write")
            # Both writes are under way and held. The disk answers a second later: longer than the old 0.2 s waits,
            # far shorter than close's bound.
            disk = threading.Timer(1.0, writes.gate.set)
            disk.start()
            self.controller.close()
            with writes.lock:
                writes.closed = True
            disk.join(NEVER)
            # A background write that runs now finds nothing owed: give each store one to prove it.
            store = telemetry.TelemetryStore.shared(self.folder)
            late = threading.Thread(target=lambda: (store._drain(), usage_meter.drain(self.folder, timeout=NEVER)),
                                    name="AngeloLateWriter")
            late.start()
            late.join(NEVER)
            store.flush(timeout=NEVER)   # Settled before the patches go (the test's own thread is not counted).
            usage_meter.drain(self.folder, timeout=NEVER)
        self.assertEqual(writes.late, [], "a background writer wrote into the folder after close returned")
        self.assertEqual(len(telemetry.TelemetryStore(self.folder).receipts), 1, "close left a receipt unwritten")
        self.assertIsNotNone(usage_meter.UsageLedger(self.folder).chat("chat-a"), "close left usage unwritten")

    def test_a_write_scheduled_before_close_but_run_after_it_writes_nothing(self):
        scheduled = []
        store = telemetry.TelemetryStore.shared(self.folder, submit=scheduled.append)   # The app's memory worker.
        self.addCleanup(setattr, store, "submit", None)
        with FolderWrites(self.folder) as writes:
            writes.gate.set()
            self.assertTrue(store.record(performance_receipt()))
            self.assertEqual(len(scheduled), 1)
            self.controller.close()
            with writes.lock:
                writes.closed = True
            worker = threading.Thread(target=scheduled[0], name="AngeloMemoryWorker")
            worker.start()
            worker.join(NEVER)
        self.assertEqual(writes.late, [])
        self.assertEqual(len(telemetry.TelemetryStore(self.folder).receipts), 1, "close writes what is owed itself")

    def test_a_stalled_disk_under_closes_own_write_never_hangs_close(self):
        scheduled = []
        telemetry.TelemetryStore.shared(self.folder, submit=scheduled.append)   # No background write holds the turn.
        self.addCleanup(setattr, telemetry.TelemetryStore.shared(self.folder), "submit", None)
        with FolderWrites(self.folder, held=("AngeloCloseWrites",)) as writes, \
                mock.patch.object(write_turn, "CLOSE_SECONDS", 0.3):
            self.assertTrue(self.controller.telemetry.record(performance_receipt()))
            started = machine_clock.start(self)
            self.controller.close()   # Its own write is the one the disk stalls.
            closing = started.stop()
            self.assertTrue(writes.arrived["AngeloCloseWrites"].is_set(), "close wrote on the caller's thread")
            writes.gate.set()
            self.assertTrue(writes.landed["AngeloCloseWrites"].wait(NEVER))
        machine_clock.assert_within(self, closing, 0.3 + 2.0, "close waited on its own write past its bound")
        self.assertEqual(len(telemetry.TelemetryStore(self.folder).receipts), 1, "the stalled write still landed")

    def test_quit_waits_for_a_codex_warm_up_ending_and_writes_its_receipt(self):
        from angelo_agent import codex_bases, codex_runtime
        self.controller.close()
        release = threading.Event()

        def warm_up():   # An app-server just closed: its warm-up ends and records its receipt (record_warm_up).
            try:
                release.wait(NEVER)
                telemetry.TelemetryStore.shared(self.folder).record(dict(performance_receipt(), kind="warm_up"))
            finally:
                with codex_bases._WARM_UPS_LOCK:
                    codex_bases._WARM_UPS.discard(threading.current_thread())
        worker = threading.Thread(target=warm_up, name="AngeloCodexWarmUp", daemon=True)
        with codex_bases._WARM_UPS_LOCK:
            codex_bases._WARM_UPS.add(worker)
        worker.start()
        threading.Timer(0.3, release.set).start()
        runtime = SimpleNamespace(close=lambda: None, store=SimpleNamespace(path=self.folder / "codex_bases.json"))
        with FolderWrites(self.folder, held=()) as writes, \
                mock.patch.dict(codex_runtime._POOL, {("app-server",): runtime}, clear=True):
            codex_runtime.close_all(timeout=NEVER)   # Every app-server closed; then the warm-ups still ending.
            self.assertFalse(worker.is_alive(), "close_all returned while a warm-up still ran")
            with writes.lock:
                writes.closed = True
            self.assertTrue(write_turn.close_all(NEVER))   # Quit's last fence: nothing left to write.
            store = telemetry.TelemetryStore.shared(self.folder)
            late = threading.Thread(target=store._drain, name="AngeloLateWriter")
            late.start()
            late.join(NEVER)
        self.assertEqual(writes.late, [])
        self.assertEqual([r.get("kind") for r in telemetry.TelemetryStore(self.folder).receipts], ["warm_up"])

    def test_a_stalled_disk_never_hangs_close_and_is_waited_for_once(self):
        with FolderWrites(self.folder) as writes, \
                mock.patch.object(write_turn, "CLOSE_SECONDS", 0.3):
            self.record_both()
            for name in WRITERS:
                self.assertTrue(writes.arrived[name].wait(NEVER), name + " never started its write")
            started = machine_clock.start(self)
            self.controller.close()
            closing = started.stop()
            # The next chat's close at quit: the same stalled writes are never waited for again.
            started = machine_clock.start(self)
            self.assertFalse(telemetry.TelemetryStore.shared(self.folder).flush(timeout=NEVER))
            self.assertFalse(usage_meter.drain(self.folder, timeout=NEVER))
            again = started.stop()
            writes.gate.set()   # The disk answers: the stalled writes land; nothing is lost.
            for name in WRITERS:
                self.assertTrue(writes.landed[name].wait(NEVER), name + " never finished its write")
            # Settled (on the test's thread) before the folder goes; the writers' own turns may still be ending.
            for _ in range(int(NEVER / .01)):
                if telemetry.TelemetryStore.shared(self.folder).flush(timeout=NEVER) \
                        and usage_meter.drain(self.folder, timeout=NEVER):
                    break
                time.sleep(.01)
        # 0.3 s is one bound for both writers; the margin is only for a loaded machine.
        machine_clock.assert_within(self, closing, 0.3 + 2.0)
        machine_clock.assert_within(self, again, 2.0, "a write a close gave up on was waited for again")
        self.assertEqual(len(telemetry.TelemetryStore(self.folder).receipts), 1)
        self.assertIsNotNone(usage_meter.UsageLedger(self.folder).chat("chat-a"))


class WriteTurnTests(unittest.TestCase):
    def test_a_wait_takes_the_turn_once_the_write_in_progress_ends(self):
        turn, entered, release = WriteTurn(), threading.Event(), threading.Event()

        def write():
            with turn:
                entered.set()
                release.wait(NEVER)
        writer = threading.Thread(target=write)
        writer.start()
        self.assertTrue(entered.wait(NEVER))
        threading.Timer(0.2, release.set).start()
        self.assertTrue(turn.acquire(NEVER))
        turn.release()
        writer.join(NEVER)

    def test_any_bound_is_taken_without_raising(self):
        turn = WriteTurn()
        for bound in (None, float("inf"), float("nan"), -1, 0, 1e300):
            self.assertTrue(turn.acquire(bound), bound)
            turn.release()
        with turn:
            for bound in (None, float("nan"), -1, 0):
                self.assertFalse(turn.acquire(bound), bound)   # No wait: given up on at once.

    def test_a_check_without_a_wait_never_gives_up_on_a_write_for_a_waiting_close(self):
        turn, entered, release = WriteTurn(), threading.Event(), threading.Event()

        def write():
            with turn:
                entered.set()
                release.wait(NEVER)
        writer = threading.Thread(target=write)
        writer.start()
        self.assertTrue(entered.wait(NEVER))
        waited = []
        waiter = threading.Thread(target=lambda: waited.append(turn.acquire(NEVER)))
        waiter.start()
        self.assertFalse(turn.acquire(0))   # A spent budget (close_all's later stores): a check, nothing more.
        self.assertFalse(turn.acquire(0))
        threading.Timer(0.2, release.set).start()
        waiter.join(NEVER)
        self.assertEqual(waited, [True], "a check gave up on a healthy write for a close that waits")
        turn.release()
        writer.join(NEVER)

    def test_a_later_write_is_waited_for_after_an_earlier_one_was_given_up_on(self):
        turn = WriteTurn()
        for _ in range(2):
            entered, release = threading.Event(), threading.Event()

            def write():
                with turn:
                    entered.set()
                    release.wait(NEVER)
            writer = threading.Thread(target=write)
            writer.start()
            self.assertTrue(entered.wait(NEVER))
            self.assertFalse(turn.acquire(0.05))
            self.assertFalse(turn.acquire(NEVER), "the same stalled write: given up on at once")
            release.set()
            writer.join(NEVER)
        self.assertTrue(turn.acquire(0))
        turn.release()


if __name__ == "__main__":
    unittest.main()
