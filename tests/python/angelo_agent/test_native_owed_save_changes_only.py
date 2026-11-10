# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Ruling B1 (1.0.8): the pump's owed save writes only what changed, and still never drops a durable change.

#113 added a throttled save on the UI thread every 0.5 s while a request is open (dogfood B-004: a request still
thinking must reach disk). Each re-encoded every row of the current request (up to 64) to find what changed:
8.3 ms of UI-thread time per save in a worker-chat suite on a Windows T4 VM, growing with the request, against
0.1-0.4 ms per pump in 1.0.7. Since every change in place to a row is touched (``TranscriptRows._touched``), the
owed save (``changes_only``) encodes the rows new or touched since the last write, plus a bounded scrub over every
row; every other save (tool rows, the turn's end, a completion, close, a switch) still compares the window.

Dependency-free: the controller's own pump and journal, no Blender, provider process or model.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import session_journal, session_writer, sessions  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402

REQUEST = "Build a harbour: a pier, three boats and a lighthouse."


def plain(value):
    return json.loads(json.dumps(value))


class RowEncodes:
    """Rows ``session_journal.encode`` encodes while active (the touch check and verification off: both are
    test-time nets that encode every row by design)."""

    def __init__(self):
        self.rows = 0
        encode = session_journal.encode

        def counted(value):
            self.rows += isinstance(value, dict) and "role" in value
            return encode(value)
        self.patches = (mock.patch.object(session_journal, "encode", counted),
                        mock.patch.object(session_writer, "CHECK_TOUCHES", False, create=True),
                        mock.patch.object(sessions, "VERIFY_JOURNAL", False))

    def __enter__(self):
        for item in self.patches:
            item.start()
        return self

    def __exit__(self, *exc):
        for item in self.patches:
            item.stop()


class ChatCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = folder.name
        self.controller = Controller(self.directory)
        self.controller.set_document(("owed-save-b1", 1))
        self.saves = []
        save = self.controller.store.save

        def recording(state, **options):
            self.saves.append(options.get("changes_only", False))
            self.assertTrue(self.controller.lock._is_owned(), "every save holds the chat's lock")
            return save(state, **options)
        self.controller.store.save = recording

    def pump(self, *events):
        for kind, data in events:
            self.controller.emit(kind, data)
        self.controller.pump_events()

    def owed_save(self):
        """The pump's throttled save, as one past the throttle (``save_if_owed``)."""
        self.saves.clear()
        self.controller._last_save -= 1.0
        self.controller.pump_events()
        self.assertEqual(self.saves, [True], "the pump's owed save compares only what changed (B1)")

    def crash_and_reload(self):
        """The process dies: no further save, no close. A relaunch's store loads the file."""
        return SessionStore(self.directory).load()

    def activity(self, index, **fields):
        return ("activity", dict({"id": "call-%d" % index, "tool": "angelo_scene_program", "status": "running",
                                  "arguments": {"operations": [{"kind": "transform", "location": [index, 0, 0]}]}},
                                 **fields))


class CrashMidRequest(ChatCase):
    def test_a_request_still_thinking_survives_a_crash_after_an_owed_save(self):
        self.controller.save()   # A file-bound chat's save just before its request row (B-004).
        self.pump(("message", {"role": "user", "text": REQUEST}),
                  ("thinking_started", {"item": "t1"}), ("thinking", {"item": "t1", "text": "Planning the pier"}))
        self.owed_save()
        loaded = self.crash_and_reload()
        self.assertEqual(loaded["messages"], plain(self.controller.state["messages"]))
        self.assertEqual([row["text"] for row in loaded["messages"]], [REQUEST, "Planning the pier"])

    def test_rows_changed_in_place_mid_tool_survive_a_crash_after_an_owed_save(self):
        self.controller.save()
        self.pump(("message", {"role": "user", "text": REQUEST}),
                  ("delta", {"item": "reply", "text": "Starting with "}), self.activity(1), self.activity(2))
        self.owed_save()
        # In place, each touched: a reply growing, a call finishing, another still running with a new result.
        self.pump(("delta", {"item": "reply", "text": "the pier."}),
                  self.activity(1, status="completed", result={"success": True, "objects": ["Pier"]}),
                  self.activity(2, result={"progress": 0.5}))
        self.owed_save()
        loaded = self.crash_and_reload()
        self.assertEqual(loaded["messages"], plain(self.controller.state["messages"]))
        rows = {row.get("call_id") or row["role"]: row for row in loaded["messages"]}
        self.assertEqual(rows["assistant"]["text"], "Starting with the pier.")
        self.assertEqual((rows["call-1"]["status"], rows["call-1"]["result"]), ("completed", {"success": True, "objects": ["Pier"]}))
        self.assertEqual(rows["call-2"]["result"], {"progress": 0.5})

    def test_rows_an_insert_moves_survive_a_crash_after_an_owed_save(self):
        self.controller.save()
        self.pump(("message", {"role": "user", "text": REQUEST}), self.activity(1), self.activity(2),
                  ("delta", {"item": "reply", "text": "Done."}))
        self.owed_save()
        # A reviewed preview's accept goes just before its own activity (_insert_request_message), moving the rest.
        self.controller.emit("message", {"role": "user", "text": "Accept this preview.", "call_id": "call-1",
                                         "request_kind": "native_preview_accept", "preview_id": "preview-1"})
        self.owed_save()
        loaded = self.crash_and_reload()
        self.assertEqual(loaded["messages"], plain(self.controller.state["messages"]))
        self.assertEqual([row.get("call_id") or row["role"] for row in loaded["messages"]],
                         ["user", "user", "call-1", "call-2", "assistant"])


    def test_an_unmarked_change_to_a_window_row_survives_a_crash_within_the_scrubs_bound(self):
        """A change no caller marked (a defect the touch check refuses) still reaches the file by owed saves alone."""
        self.controller.save()
        self.pump(("message", {"role": "user", "text": REQUEST}), *(self.activity(index) for index in range(20)))
        self.owed_save()
        rows = self.controller.state["messages"]
        self.assertLess(len(rows), session_writer.WINDOW_ROWS, "every row is in the current request's window")
        with mock.patch.object(session_writer, "CHECK_TOUCHES", False, create=True), \
                mock.patch.object(sessions, "VERIFY_JOURNAL", False):
            with self.controller.lock:
                rows[12]["status"] = "changed, never touched"
            saves = 0
            while self.crash_and_reload()["messages"][12]["status"] != "changed, never touched":
                saves += 1
                self.pump(self.activity(saves % 3, result={"progress": saves}))   # Durable, touched: a save is owed.
                self.owed_save()
                self.assertLessEqual(saves, -(-len(rows) // session_writer.SCRUB_ROWS), "the scrub's bound")
        self.assertEqual(self.crash_and_reload()["messages"], plain(rows))


class Interleaving(ChatCase):
    """A row's mark follows its change, both under the chat's lock (``TranscriptRows._touched``)."""

    def test_a_save_between_a_change_and_its_mark_never_drops_the_change(self):
        self.controller.save()
        self.pump(("message", {"role": "user", "text": REQUEST}), self.activity(1))
        self.owed_save()
        store, row = self.controller.store, self.controller.state["messages"][1]
        # The lock forbids this order; were it broken, that save (its scrub aside) would miss the change, made
        # inside a field's value, where only a mark shows it...
        location = row["arguments"]["operations"][0]["location"]
        with mock.patch.object(session_writer, "CHECK_TOUCHES", False, create=True), mock.patch.object(session_writer, "SCRUB_ROWS", 0), \
                mock.patch.object(sessions, "VERIFY_JOURNAL", False):
            location[0] = 9   # The change is made...
            SessionStore.save(store, self.controller.state, changes_only=True)   # ...a save runs before the mark...
        on_disk = lambda: self.crash_and_reload()["messages"][1]["arguments"]["operations"][0]["location"][0]  # noqa: E731
        self.assertEqual(on_disk(), 1)
        with self.controller.lock:
            self.controller._touched(1)   # ...and the mark that follows it is never cleared by that save.
        self.controller._save_owed = True
        self.owed_save()
        self.assertEqual(on_disk(), 9)

    def test_a_mark_without_the_chats_lock_fails_the_touch_check(self):
        self.pump(("message", {"role": "user", "text": REQUEST}))
        with mock.patch.object(session_writer, "CHECK_TOUCHES", True, create=True):
            with self.assertRaises(AssertionError):
                self.controller._touched(0)
            with self.controller.lock:
                self.controller._touched(0)

    def test_an_owed_save_waits_for_a_change_and_its_mark_under_the_lock(self):
        self.controller.save()
        self.pump(("message", {"role": "user", "text": REQUEST}), self.activity(1))
        self.owed_save()
        changed, saved = threading.Event(), threading.Event()
        self.controller._save_owed = True
        self.controller._last_save -= 1.0

        def owed_save_now():
            changed.wait(5)
            self.controller.pump_events()   # Blocks on the lock the change holds.
            saved.set()
        thread = threading.Thread(target=owed_save_now)
        thread.start()
        with self.controller.lock:
            self.controller.state["messages"][1]["status"] = "completed"
            changed.set()
            self.assertFalse(saved.wait(0.2), "no save runs between a change and its mark")
            self.controller._touched(1)
        thread.join(5)
        self.assertTrue(saved.is_set())
        self.assertEqual(self.crash_and_reload()["messages"][1]["status"], "completed")


class LongRequest(ChatCase):
    def long_request(self, calls=70):
        self.controller.save()
        self.pump(("message", {"role": "user", "text": REQUEST}), *(self.activity(index) for index in range(calls)))
        self.controller.save()   # A boundary save: everything written, the window compared.

    def test_an_owed_save_encodes_the_touched_and_new_rows_not_the_window(self):
        self.long_request()
        self.assertGreater(len(self.controller.state["messages"]), session_writer.WINDOW_ROWS)
        with RowEncodes() as counts:
            self.pump(self.activity(5, status="completed"), self.activity(60, status="completed"), self.activity(70))
            self.owed_save()
        # Two rows touched, one new, and the scrub's bounded share: never the 64 rows of the window.
        self.assertLessEqual(counts.rows, 3 + session_writer.SCRUB_ROWS)
        self.assertEqual(self.crash_and_reload()["messages"], plain(self.controller.state["messages"]))

    def test_a_boundary_save_compares_the_whole_window(self):
        self.long_request()
        with RowEncodes() as owed:
            self.pump(self.activity(60, status="completed"))
            self.owed_save()
        with RowEncodes() as boundary:
            self.pump(self.activity(61, status="completed"))
            self.saves.clear()
            self.controller.save()   # A tool row's, a turn end's, a completion's: every save but the pump's owed one.
        self.assertEqual(self.saves, [False])
        self.assertLessEqual(owed.rows, 1 + session_writer.SCRUB_ROWS)
        self.assertGreaterEqual(boundary.rows, session_writer.WINDOW_ROWS)

    def test_only_the_pumps_owed_save_compares_changes_alone(self):
        self.long_request(calls=3)
        self.pump(self.activity(1, status="completed"))
        self.owed_save()
        self.saves.clear()
        self.pump(("finished", {"status": "completed"}))   # The turn's end (compact_if_due).
        self.assertNotIn(True, self.saves)
        self.saves.clear()
        self.controller.close()
        self.assertNotIn(True, self.saves, "a close is a whole rewrite")


class UnmarkedChanges(unittest.TestCase):
    """A change in place nobody touched (a defect the host tests' touch check refuses) still reaches the file."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = SessionStore(folder.name)
        self.state = self.store.new("claude_subscription")
        self.store.save(self.state)
        for index in range(80):
            self.state["messages"].append({"role": "activity", "call_id": "call-%d" % index, "status": "queued",
                                           "result": {"status": "queued"}})
            self.store.save(self.state)
        writer = self.store._writer   # A row of the window (the last 64) the scrub's next save does not reach.
        end = getattr(writer, "_scrub_end", 80)   # (A build before scrub cycles: its pointer ran over every row.)
        upcoming = (range(writer._scrub, min(end, writer._scrub + session_writer.SCRUB_ROWS))
                    if writer._scrub < end else range(session_writer.SCRUB_ROWS))
        self.unmarked = next(index for index in range(79, 15, -1) if index not in upcoming)
        patches = (mock.patch.object(session_writer, "CHECK_TOUCHES", False, create=True),
                   mock.patch.object(sessions, "VERIFY_JOURNAL", False))
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        # Inside a field's value: what only a mark shows an owed save (a field set or replaced is seen by identity).
        self.state["messages"][self.unmarked]["result"]["status"] = "never touched"

    def on_disk(self, index=None):
        row = session_journal.read(self.store.path)[0]["messages"][self.unmarked if index is None else index]
        return row["result"]["status"] == "never touched" if index is None else row["status"] == "set, never touched"

    def test_an_unmarked_field_set_on_a_window_row_reaches_the_next_owed_save(self):
        """A field set or replaced on a row of the current request is seen without its mark (B1 break-it review,
        F1: a restore's notice changed so reached disk only with the scrub, or never before a crash)."""
        window = next(index for index in range(78, 15, -1) if index != self.unmarked)
        writer = self.store._writer
        end = getattr(writer, "_scrub_end", 80)
        if writer._scrub <= window < min(end, writer._scrub + session_writer.SCRUB_ROWS):
            window -= session_writer.SCRUB_ROWS   # Away from the scrub's next rows, which would find it anyway.
        self.state["messages"][window].update(status="set, never touched")
        self.store.save(self.state, changes_only=True)
        self.assertTrue(self.on_disk(window))

    def test_a_boundary_save_writes_an_unmarked_window_row_at_once(self):
        self.store.save(self.state, changes_only=True)
        self.assertFalse(self.on_disk(), "an owed save compares no window")
        self.store.save(self.state)
        self.assertTrue(self.on_disk())

    def test_the_owed_saves_scrub_reaches_an_unmarked_window_row_within_bounded_saves(self):
        saves, held = 0, -(-len(self.state["messages"]) // session_writer.SCRUB_ROWS)
        while not self.on_disk():
            self.state["updated"] += 1
            self.store.save(self.state, changes_only=True)
            saves += 1
            # The rest of the scrub's cycle, then one over every row the file holds, the window too.
            self.assertLessEqual(saves, 2 * held, "the scrub's two-cycle bound")
        self.assertEqual(self.store.last_write, "append")


    def test_the_scrub_reaches_an_unmarked_row_while_each_save_adds_more_rows_than_it_scrubs(self):
        """A scrub cycle covers the rows held when it began, then starts again at row 0 (break-it review, F3: a
        pointer over the growing count never wrapped while each save added SCRUB_ROWS rows or more)."""
        for _ in range(2):   # The pointer moves past the rows changed next.
            self.state["updated"] += 1
            self.store.save(self.state, changes_only=True)
        writer, behind = self.store._writer, 5
        self.assertGreater(writer._scrub, behind)
        self.state["messages"][behind]["status"] = "changed behind the scrub"
        rows, growth = session_writer.SCRUB_ROWS, 2 * session_writer.SCRUB_ROWS
        # The rest of this cycle (at most all of it), then the next one over the rows held when it began.
        rest = -(-getattr(writer, "_scrub_end", len(self.state["messages"])) // rows)
        bound, saves = rest + -(-(len(self.state["messages"]) + growth * rest) // rows), 0
        while session_journal.read(self.store.path)[0]["messages"][behind]["status"] != "changed behind the scrub":
            saves += 1
            self.assertLessEqual(saves, bound, "the scrub's two-cycle bound")
            self.state["messages"] += [{"role": "activity", "call_id": "grown-%d-%d" % (saves, k)} for k in range(growth)]
            self.state["updated"] += 1
            self.store.save(self.state, changes_only=True)


class TouchCheck(unittest.TestCase):
    """The host tests' touch check (``ANGELO_JOURNAL_TOUCHES``) names a row changed in place without a touch."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = SessionStore(folder.name)
        self.state = self.store.new("claude_subscription")
        self.state["messages"] += [{"role": "user", "text": REQUEST}, {"role": "assistant", "text": "On it."}]
        self.store.save(self.state)
        check = mock.patch.object(session_writer, "CHECK_TOUCHES", True, create=True)
        check.start()
        self.addCleanup(check.stop)

    def test_an_untouched_change_in_the_current_request_fails_the_save(self):
        self.state["messages"][1]["text"] = "On it, starting now."
        for changes_only in (True, False):
            with self.assertRaises(AssertionError) as raised:
                self.store.save(self.state, changes_only=changes_only)
            self.assertIn("row 1 ", str(raised.exception))
            self.assertIn("messages[1].text: 'On it.' on disk, 'On it, starting now.' in memory", str(raised.exception))

    def test_a_test_file_run_alone_has_the_checks_and_the_product_never_does(self):
        """``journal_checks.enabled`` (break-it review, F4): the variable when set; else on under unittest outside
        Blender, as for ``python -m unittest <file>``, and off inside Blender, where the product always runs."""
        from angelo_agent import journal_checks
        for name in ("ANGELO_JOURNAL_TOUCHES", "ANGELO_JOURNAL_VERIFY"):
            with mock.patch.dict("os.environ", {name: "0"}):
                self.assertFalse(journal_checks.enabled(name))
            with mock.patch.dict("os.environ", {name: "1"}):
                self.assertTrue(journal_checks.enabled(name))
            with mock.patch.dict("os.environ", {}, clear=False):
                os.environ.pop(name, None)
                self.assertTrue(journal_checks.enabled(name), "a host test file run alone")
                inside = sys.builtin_module_names + ("_bpy_path",)
                with mock.patch.object(sys, "builtin_module_names", inside):
                    self.assertFalse(journal_checks.enabled(name), "inside Blender: the product, its workers")

    def test_a_touched_change_saves(self):
        self.state["messages"][1]["text"] = "On it, starting now."
        self.store.touch(1)
        self.store.save(self.state, changes_only=True)
        self.assertEqual(session_journal.read(self.store.path)[0]["messages"][1]["text"], "On it, starting now.")


if __name__ == "__main__":
    unittest.main()
