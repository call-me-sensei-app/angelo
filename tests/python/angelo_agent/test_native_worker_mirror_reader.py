# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker chat mirror reads its file on its reader thread; the UI thread only puts the state in. No Blender.

``worker_mirror_reader``: a pump that sees a journal frame asks for the read and returns; the frames after it wait
and are applied, in order, after the state; the UI thread runs none of the read (no decode, no check, no memory
clean), whatever the record's size; every read of the store holds its lock; the reader gives the GIL back between
slices of its work. ``timer_resolution``: Windows' fine timer is held while a worker chat is hosted. Regression for the 1.0.7 T4
pump stalls (``test_blender_parallel_workers``, ``a_pump_never_waits_for_the_worker``).
"""

import json
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (session_validation, sessions, task_memory, timer_resolution,  # noqa: E402
                          worker_mirror_journal, worker_mirror_reader)
from test_native_worker_mirror_reads import Reads  # noqa: E402

SETTLE_SECONDS = 10.0


class OnThreads:
    """Calls of module functions, with the thread each ran on, while active."""

    def __init__(self, *targets):
        self.calls, self._patches = [], []
        for owner, name in targets:
            real = getattr(owner, name)

            def counted(*args, _real=real, _name=name, **keywords):
                self.calls.append((_name, threading.current_thread() is threading.main_thread()))
                return _real(*args, **keywords)
            self._patches.append(mock.patch.object(owner, name, counted))

    def __enter__(self):
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in self._patches:
            patch.stop()

    def on_main(self):
        return [name for name, main in self.calls if main]


class ThreadedReads(Reads):
    def setUp(self):
        super().setUp()
        worker_mirror_reader.bind(inline=False)
        self.addCleanup(worker_mirror_reader.reset)

    def settle(self):
        """Pump as the UI timer does until the read asked for is put in."""
        deadline = time.monotonic() + SETTLE_SECONDS
        self.mirror.pump_events()
        while worker_mirror_reader.waiting(self.mirror):
            self.assertLess(time.monotonic(), deadline, "the reader never answered")
            time.sleep(0.001)
            self.mirror.pump_events()

    def frame_journal(self, *rows, compact=False):
        self.worker.state["messages"].extend(rows)
        self.worker.save(compact=compact)
        self.seq += 1
        self.frame("journal", {"seq": self.seq, "path": str(self.worker.store.path),
                               "records": self.worker.store._writer.records})


class OffThreadReadTests(ThreadedReads):
    def test_the_ui_thread_runs_none_of_the_read_whatever_the_record(self):
        self.frame_journal({"role": "user", "text": "first"}, compact=True)
        self.settle()
        for size in (10, 20000, 200000):   # A small record, a large tool result's, a huge one.
            with self.subTest(size=size):
                row = {"role": "assistant", "text": "x" * size}
                watched = OnThreads((worker_mirror_journal.MirrorStore, "_read"), (self.store_json(), "loads"),
                                    (session_validation, "messages"), (sessions, "clean_memory"),
                                    (worker_mirror_journal.MirrorStore, "_read_tail"))
                with watched:
                    self.frame_journal(row)
                    self.settle()
                self.assertEqual(watched.on_main(), [])
                self.assertIn("_read", [name for name, _main in watched.calls])
                self.assert_equals_load()

    def store_json(self):
        """The store's own ``json`` (a stand-in whose ``loads`` the test counts): only the store's decodes."""
        proxy = type("StoreJson", (), {"loads": staticmethod(json.loads), "dumps": staticmethod(json.dumps)})
        patch = mock.patch.object(worker_mirror_journal, "json", proxy)
        patch.start()
        self.addCleanup(patch.stop)
        return proxy

    def test_frames_after_a_journal_frame_wait_for_its_read_and_go_on_in_order(self):
        self.frame_journal({"role": "user", "text": "first"}, compact=True)
        self.settle()
        gate = threading.Event()
        real = worker_mirror_journal.MirrorStore._read

        def held(store):
            gate.wait(SETTLE_SECONDS)
            return real(store)
        with mock.patch.object(worker_mirror_journal.MirrorStore, "_read", held):
            self.frame_journal({"role": "assistant", "text": "second"})
            self.frame("pending", {"calls": []})
            self.frame("journal", {"seq": self.seq})   # A later journal frame waits behind the first read too.
            self.mirror.pump_events()
            self.assertTrue(worker_mirror_reader.waiting(self.mirror))
            self.assertEqual([m["text"] for m in self.mirror.state["messages"]], ["first"])   # Nothing put in yet.
            self.mirror.pump_events()   # A pump while it reads applies nothing and returns.
            self.assertTrue(worker_mirror_reader.waiting(self.mirror))
            gate.set()
            self.settle()
        self.assertEqual([m["text"] for m in self.mirror.state["messages"]], ["first", "second"])
        self.assertEqual(self.mirror.journal_seq, self.seq)
        self.assert_equals_load()

    def test_a_display_event_after_the_journal_frame_is_shown_over_its_state(self):
        self.frame_journal({"role": "user", "text": "first"}, compact=True)
        self.settle()
        self.mirror.begin_turn()
        self.frame_journal({"role": "assistant", "text": "Hel"})
        self.frame("delta", {"item": "a", "text": "Hello, all"})   # Emitted after that save.
        self.settle()
        texts = [m.get("text") for m in self.mirror.state["messages"]]
        self.assertEqual(texts[0], "first")
        self.assertIn("Hello, all", "".join(str(text) for text in texts))

    def test_a_failed_read_keeps_what_is_shown_and_is_read_again(self):
        self.frame_journal({"role": "user", "text": "first"}, compact=True)
        self.settle()
        def failing(store, path):   # As the store's own read fails: its error kept, then raised.
            store.read_error = OSError("busy")
            raise store.read_error
        with mock.patch.object(worker_mirror_journal.MirrorStore, "_journal", failing):
            self.frame_journal({"role": "assistant", "text": "second"})
            self.settle()
        self.assertEqual([m["text"] for m in self.mirror.state["messages"]], ["first"])
        self.assertIsNotNone(self.mirror.reload_retry)
        self.mirror.reload_retry = time.monotonic()   # Due now: the next pump reads again, on the reader.
        self.settle()
        self.assertEqual([m["text"] for m in self.mirror.state["messages"]], ["first", "second"])
        self.assert_equals_load()

    def test_a_main_thread_load_and_the_reader_never_read_the_store_at_once(self):
        self.frame_journal({"role": "user", "text": "first"}, compact=True)
        self.settle()
        inside, overlaps = [0], []
        real = sessions.SessionStore._read

        def counted(store):
            inside[0] += 1
            overlaps.append(inside[0])
            try:
                time.sleep(0.002)
                return real(store)
            finally:
                inside[0] -= 1
        with mock.patch.object(sessions.SessionStore, "_read", counted):
            for index in range(5):
                self.frame_journal({"role": "assistant", "text": "reply %d" % index})
                self.mirror.pump_events()   # Asks; the reader reads while this thread loads.
                self.mirror.store._store.load()
                self.settle()
        self.assertEqual(max(overlaps), 1)
        self.assert_equals_load()

    def test_a_row_the_read_rewords_is_handed_out_fresh_on_every_read(self):
        """``recovery_check`` changes an earlier build's row in place on the reader: never a row already shown."""
        store = self.store()
        old, plain = {"role": "system", "text": session_validation.RECOVERY_MODEL_TEXT}, {"role": "user", "text": "hi"}
        first, _ = store._rows([old, plain])
        second, _ = store._rows([old, plain])
        self.assertIsNot(second[0], first[0])
        self.assertIs(second[1], first[1])   # Any other row keeps the copy shown.

    def test_a_pumps_ui_thread_time_does_not_follow_the_records_size(self):
        """Timed, with a wide margin: the UI thread's pumps of a record of 2,000 rows against one of a single row."""
        self.frame_journal({"role": "user", "text": "first"}, compact=True)
        self.settle()
        spent = {}
        for rows in (1, 2000):
            pumps = []
            self.frame_journal(*({"role": "assistant", "text": "row %d %s" % (i, "x" * 80)} for i in range(rows)))
            deadline = time.monotonic() + SETTLE_SECONDS
            while True:
                started = time.perf_counter()
                self.mirror.pump_events()
                pumps.append(time.perf_counter() - started)
                if not worker_mirror_reader.waiting(self.mirror) or time.monotonic() > deadline:
                    break
                time.sleep(0.001)
            spent[rows] = max(pumps)
            self.assert_equals_load()
        inline = self.inline_pump(2000)
        self.assertLess(spent[2000], max(4 * spent[1], 0.004), (spent, inline))
        self.assertLess(spent[2000], inline / 2, (spent, inline))   # What the same record costs read inline.

    def inline_pump(self, rows):
        worker_mirror_reader.bind(inline=True)
        try:
            self.frame_journal(*({"role": "assistant", "text": "more %d %s" % (i, "y" * 80)} for i in range(rows)))
            started = time.perf_counter()
            self.mirror.pump_events()
            return time.perf_counter() - started
        finally:
            worker_mirror_reader.bind(inline=False)


class SliceTests(unittest.TestCase):
    def test_the_reader_gives_the_gil_back_between_slices_and_the_ui_thread_never_sleeps(self):
        slices = worker_mirror_reader.Slices()
        with mock.patch.object(worker_mirror_reader.time, "sleep") as sleep:
            slices._since -= 1
            slices.pause()   # The main thread: never.
            self.assertEqual(sleep.call_count, 0)
            thread = threading.Thread(target=slices.pause)
            thread.start()
            thread.join()
            self.assertEqual(sleep.call_count, 1)
            thread = threading.Thread(target=slices.pause)   # Within its slice: no pause.
            thread.start()
            thread.join()
            self.assertEqual(sleep.call_count, 1)

    def test_an_idle_reader_ends_and_the_next_read_starts_another(self):
        store = mock.Mock()
        store.io = threading.RLock()
        store._read.return_value = ({"messages": []}, False)
        store.read_error = None
        with mock.patch.object(worker_mirror_reader, "IDLE_SECONDS", 0.01):
            reader = worker_mirror_reader.Reader(store)
            number = reader.ask(3)
            deadline = time.monotonic() + SETTLE_SECONDS
            while reader.done(number) is None and time.monotonic() < deadline:
                time.sleep(0.001)
            self.assertEqual(reader.done(number), ({"messages": []}, None))
            while reader._running and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertFalse(reader._running)
            again = reader.ask(None)
            while reader.done(again) is None and time.monotonic() < deadline:
                time.sleep(0.001)
            self.assertIsNotNone(reader.done(again))
            self.assertIsNone(reader.done(number))   # Only the newest answer is kept.


class ReaderLifecycleTests(unittest.TestCase):
    def test_a_reader_whose_thread_cannot_start_reads_in_the_asking_thread(self):
        store = mock.Mock()
        store.io = threading.RLock()
        store._read.return_value = ({"messages": []}, False)
        store.read_error = None
        reader = worker_mirror_reader.Reader(store)
        with mock.patch.object(worker_mirror_reader.threading, "Thread", side_effect=RuntimeError("no thread")):
            number = reader.ask(1)
        self.assertEqual(reader.done(number), ({"messages": []}, None))   # Read already: nothing is ever stuck.
        self.assertFalse(reader._running)

    def test_a_read_off_the_ui_thread_switches_the_gil_quickly_and_puts_the_interval_back(self):
        before, seen = sys.getswitchinterval(), []
        store = mock.Mock()
        store.io = threading.RLock()
        store.read_error = None
        store._read.side_effect = lambda: seen.append(sys.getswitchinterval()) or ({"messages": []}, False)
        reader = worker_mirror_reader.Reader(store)
        number = reader.ask(1)
        deadline = time.monotonic() + SETTLE_SECONDS
        while reader.done(number) is None and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertEqual(seen, [min(before, worker_mirror_reader.SWITCH_SECONDS)])
        self.assertEqual(sys.getswitchinterval(), before)

    def test_the_ui_timer_comes_back_quickly_while_a_read_waits(self):
        mirror = mock.Mock()
        mirror.__dict__["_reading"] = 3
        self.assertTrue(worker_mirror_reader.any_reading([mock.Mock(), mirror]))
        mirror.__dict__["_reading"] = None
        self.assertFalse(worker_mirror_reader.any_reading([mirror]))


class TimerResolutionTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        api = mock.Mock()
        api.timeBeginPeriod.side_effect = lambda ms: self.calls.append(("begin", ms)) or 0
        api.timeEndPeriod.side_effect = lambda ms: self.calls.append(("end", ms)) or 0
        patch = mock.patch.object(timer_resolution, "_api", return_value=api)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(timer_resolution._holders.clear)

    def test_windows_asks_once_while_any_worker_holds_it_and_gives_it_back_after_the_last(self):
        first, second = object(), object()
        self.assertTrue(timer_resolution.hold(first, platform="win32"))
        self.assertFalse(timer_resolution.hold(first, platform="win32"))   # Once per worker.
        self.assertTrue(timer_resolution.hold(second, platform="win32"))
        self.assertEqual(self.calls, [("begin", 1)])
        timer_resolution.release(first, platform="win32")
        self.assertEqual(self.calls, [("begin", 1)])
        timer_resolution.release(second, platform="win32")
        timer_resolution.release(second, platform="win32")   # Never given back twice.
        self.assertEqual(self.calls, [("begin", 1), ("end", 1)])
        self.assertEqual(timer_resolution.held(), 0)

    def test_elsewhere_nothing_is_asked(self):
        self.assertFalse(timer_resolution.hold(object(), platform="darwin"))
        self.assertFalse(timer_resolution.hold(object(), platform="linux"))
        self.assertEqual(self.calls, [])

    def test_a_hosted_chat_holds_the_timer_from_its_open_to_its_close(self):
        from angelo_agent import worker_mirror
        with mock.patch.object(timer_resolution.sys, "platform", "win32"):
            first, second = worker_mirror.EventRouter(), worker_mirror.EventRouter()
            one, two = mock.Mock(state={"id": "chat-1"}), mock.Mock(state={"id": "chat-2"})
            first.open(one)
            second.open(two)
            second.open(two)   # Opened again (a resume): still one hold.
            self.assertEqual((timer_resolution.held(), self.calls), (2, [("begin", 1)]))
            first.close("chat-1")
            first.close("chat-1")   # Closed twice: given back once.
            self.assertEqual(self.calls, [("begin", 1)])
            second.close("chat-2")
        self.assertEqual((timer_resolution.held(), self.calls), (0, [("begin", 1), ("end", 1)]))

    def test_a_chat_moving_to_another_worker_keeps_the_timer_until_its_last_close(self):
        from angelo_agent import worker_mirror
        with mock.patch.object(timer_resolution.sys, "platform", "win32"):
            old, new, chat = worker_mirror.EventRouter(), worker_mirror.EventRouter(), mock.Mock(state={"id": "chat"})
            old.open(chat)
            new.open(chat)   # Opened on its next worker before the old one hands it back.
            old.close("chat")
            self.assertEqual(self.calls, [("begin", 1)])
            new.close("chat")
        self.assertEqual(self.calls, [("begin", 1), ("end", 1)])

    def test_a_worker_that_ends_hands_its_chats_back_through_their_router(self):
        from angelo_agent import worker_chat_wiring
        mirror, worker = mock.Mock(state={"id": "chat"}), mock.Mock()
        with mock.patch.dict(worker_chat_wiring._chats, {"chat": (mirror, worker)}), \
                mock.patch("angelo_agent.worker_journal_owner.hosted", return_value=False):
            worker_chat_wiring.handed_back("chat", closed=False)
        worker.chat_router.close.assert_called_once_with("chat")

    def test_a_worker_that_ended_gives_back_its_chats_holds_even_one_opened_after(self):
        from angelo_agent import worker_chat_lifecycle, worker_mirror
        with mock.patch.object(timer_resolution.sys, "platform", "win32"):
            router = worker_mirror.EventRouter()
            worker = mock.Mock(chat_router=router)
            router.open(mock.Mock(state={"id": "chat"}))
            with mock.patch.object(worker_chat_lifecycle, "hosted_on", return_value=[]):
                worker_chat_lifecycle.ended(worker)   # Its process ended: nothing is handed back from here.
            self.assertEqual(timer_resolution.held(), 0)
            router.open(mock.Mock(state={"id": "late"}))   # An open that finished after the end: no hold.
            self.assertEqual(timer_resolution.held(), 0)
        self.assertEqual(self.calls, [("begin", 1), ("end", 1)])

    def test_a_hand_back_gives_the_hold_back_even_when_the_consent_lane_fails(self):
        from angelo_agent import worker_chat_wiring
        mirror, worker = mock.Mock(state={"id": "chat"}), mock.Mock()
        with mock.patch.dict(worker_chat_wiring._chats, {"chat": (mirror, worker)}), \
                mock.patch("angelo_agent.worker_consent_lead.forget", side_effect=RuntimeError("changed size")):
            with self.assertRaises(RuntimeError):
                worker_chat_wiring.handed_back("chat", closed=False)
        worker.chat_router.close.assert_called_once_with("chat")

    def test_angelos_exit_gives_back_whatever_is_held(self):
        with mock.patch.object(timer_resolution.sys, "platform", "win32"):
            timer_resolution.hold("chat")
            timer_resolution.release_all()
            timer_resolution.release_all()
        self.assertEqual((timer_resolution.held(), self.calls), (0, [("begin", 1), ("end", 1)]))


if __name__ == "__main__":
    unittest.main()
