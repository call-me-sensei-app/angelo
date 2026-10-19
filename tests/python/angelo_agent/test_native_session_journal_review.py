# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Append-only conversation journal: reproductions of its concurrency, redaction and compatibility edge cases.

Each test states the contract it holds the journal to and failed before its
fix; the whole-file save the journal replaced passes them unless noted.
``test_native_session_journal_pins.py`` pins the behaviour verified alongside.

1. A row appended without the controller lock while another thread saves (the
   snapshot of ``len(rows)`` is taken twice in ``JournalWriter._row_changes``)
   writes a record whose row count does not fit: the next load closes the
   ledger and its first save replaces the whole chat. ``job_ui._finished``
   appends its notice this way on the main thread while a provider thread's
   admission save (``journal_tool``) can be encoding rows. The C encoder of the
   whole-file save held the GIL for the whole document, so it never raced.
2. A key prefix streamed in one delta, saved, then completed and redacted in
   memory, stays in an earlier record on disk until the next rewrite. The
   whole-file save overwrote it with the next save (at most 0.5 s later).
3. An earlier build that opens a chat holding records (any chat the app left
   without closing: a crash, a forced quit) loads an empty unverified history
   and its next save writes that over the file: every row and mutation ID is
   gone, also for this build afterwards. Turn ends compact only past 1 MiB.
   The reader therefore keeps a chat it cannot fully read before writing
   (``EarlierBuildTests``).
4. ``doc/angelo/agent_execution.rst`` and ``TranscriptRows.save`` say a chat
   switch compacts the file; ``ui.show_session`` saved without ``compact``.
"""

import ast
import json
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import session_journal, session_kept, session_writer  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.invocations import reserve_invocation  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402

FAKE_KEY = "sk-test-fake-0123456789abcdef"  # A fake key: no credential is read.


def activity(index):
    """A row as an agent edit leaves it: arguments and a nested read-back."""
    return {"role": "activity", "call_id": "call-%d" % index, "tool": "angelo_scene_program", "text": "",
            "status": "completed",
            "arguments": {"mode": "APPLY", "operations": [
                {"kind": "transform", "target": "Cube.%03d" % i, "parameters": {"location": [i, 2.5, 0.25]}}
                for i in range(12)]},
            "result": {"success": True, "changes": {"modified": [{"name": "Cube.%03d" % i, "fields": ["location"]}
                                                                for i in range(40)]}}}


class UnlockedRowAppendTests(unittest.TestCase):
    """Case 1: rows appended outside the controller lock while another thread saves."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = folder.name
        self.store = SessionStore(self.directory, str(uuid.uuid4()))
        self.state = self.store.new("claude_subscription")
        self.state["messages"].extend(activity(index) for index in range(70))
        self.store.save(self.state)

    def test_a_row_appended_while_a_save_encodes_its_rows_never_corrupts_the_journal(self):
        """Deterministic: the other thread's append lands between the window loop and ``len(rows)``."""
        rows, encode, appended = self.state["messages"], session_journal.encode, []

        def encode_then_other_thread_appends(value):
            data = encode(value)
            if value is rows[-1] and not appended:  # The last window row: what job_ui._finished does meanwhile.
                appended.append(True)
                rows.append({"role": "system", "notice": True, "text": "Render finished"})
            return data
        self.state["results"]["call-x"] = {"document": "d", "phase": "queued", "result": {}}
        with patch.object(session_writer.journal, "encode", encode_then_other_thread_appends), \
                patch("angelo_agent.sessions.VERIFY_JOURNAL", False):
            self.store.save(self.state)  # The provider thread's admission save.
            self.assertTrue(appended)
            self.store.save(self.state)  # The main thread's own save, after it appended.
        try:
            session_journal.read(self.store.path)
        except ValueError as error:
            self.fail("The journal no longer loads: %s" % error)
        loaded = SessionStore(self.directory, self.state["id"]).load()
        self.assertFalse(loaded["invocations"]["closed"], "the ledger stays verified")
        self.assertEqual(len(loaded["messages"]), len(rows), "every row restores")

    def test_threads_appending_without_the_lock_never_corrupt_the_journal(self):
        """Real threads: a provider thread saving under the lock, the main thread appending without it.

        Measured: 8 of 30 trials corrupt at a 0.1 ms switch interval, 3-4 of 40 at Python's default 5 ms with
        random job-finish timing; 0 of 40 with the whole-file save.
        """
        previous = sys.getswitchinterval()
        sys.setswitchinterval(0.0001)
        self.addCleanup(sys.setswitchinterval, previous)
        corrupt = []
        for trial in range(12):
            with tempfile.TemporaryDirectory() as folder, patch("angelo_agent.sessions.VERIFY_JOURNAL", False):
                store = SessionStore(folder, str(uuid.uuid4()))
                state = store.new("claude_subscription")
                state["messages"].extend(activity(index) for index in range(70))
                store.save(state)
                lock, stop = threading.RLock(), threading.Event()

                def provider():
                    count = 0
                    while not stop.is_set():
                        with lock:  # journal_tool: a results record, then a strict save, under controller.lock.
                            state["results"]["k%d" % count] = {"document": "d", "phase": "queued", "result": {}}
                            store.save(state)
                        count += 1
                worker = threading.Thread(target=provider)
                worker.start()
                try:
                    for index in range(60):  # job_ui._finished: append without the lock, then a locked save.
                        state["messages"].append({"role": "system", "notice": True, "text": "job %d" % index})
                        time.sleep(0.0005)
                finally:
                    stop.set()
                    worker.join()
                with lock:
                    store.save(state)
                try:
                    session_journal.read(store.path)
                except ValueError as error:
                    corrupt.append((trial, str(error)))
        self.assertEqual(corrupt, [], "journals that no longer load (their chats reopen empty, IDs closed)")


class LingeringKeyTests(unittest.TestCase):
    """Case 2: a streamed key prefix stays on disk after the row is redacted."""

    def test_a_key_prefix_saved_mid_stream_is_gone_from_disk_once_the_row_is_redacted(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        controller = Controller(folder.name)
        self.addCleanup(controller.close)
        controller.set_document(("journal-review", 1))
        controller.api_key = FAKE_KEY
        controller.emit("delta", {"item": "codex-plan", "text": "Your key is " + FAKE_KEY[:20]})
        controller._last_save = 0  # Over half a second since the last save: a durable event saves.
        controller.pump_events()  # Saved with the prefix in the row, as the stream had it.
        self.assertIn(FAKE_KEY[:20].encode(), controller.store.path.read_bytes())
        controller.emit("delta", {"item": "codex-plan", "text": FAKE_KEY[20:] + " done."})
        controller._last_save = 0
        controller.pump_events()
        controller.save()  # The next save: the row now reads "[REDACTED]".
        row = next(m for m in controller.state["messages"] if m["role"] == "assistant")
        self.assertEqual(row["text"], "Your key is [REDACTED] done.")
        self.assertNotIn(FAKE_KEY[:20].encode(), controller.store.path.read_bytes(),
                         "20 of the key's 29 characters stay in an earlier record until the next rewrite")


class EarlierBuildTests(unittest.TestCase):
    """Case 3, as resolved: a build that cannot fully read a chat never writes over it.

    An earlier build loads a chat that holds records as an empty unverified history and saves that over it.
    Earlier builds cannot change, and compacting at every turn's end would bring back the per-turn cost the
    journal removed, so the rule holds in the reader: a chat this build cannot fully read is kept beside it,
    unchanged, before its first rewrite (``SessionStore.load``, ``session_kept.preserve``). These tests
    play the later build by writing what it could: a record part, a field or a version this build does not
    know.
    """

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        self.store = SessionStore(self.directory, str(uuid.uuid4()))
        self.state = self.store.new("claude_subscription")
        self.store.save(self.state)
        for index in range(5):
            self.state["messages"].append({"role": "user", "text": "Request %d" % index})
            reserve_invocation(self.state, self.state["id"] + "/call-%d" % index, "%064x" % index, "('document', 1)")
            self.store.save(self.state)

    def kept(self):
        return sorted(self.store.path.parent.glob(self.store.path.name + ".*.unreadable"))

    def assert_kept_before_the_first_rewrite(self, original, verified):
        store = SessionStore(self.directory, self.state["id"])
        loaded = store.load()
        kept = self.kept()
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_bytes(), original, "kept unchanged, before anything was written")
        self.assertEqual(loaded["invocations"]["closed"], not verified)
        self.assertTrue(any(kept[0].name in m.get("text", "") for m in loaded["messages"]), "the chat names it")
        store.save(loaded)  # The first save after a load rewrites the chat whole.
        self.assertEqual(store.last_write, "rewrite")
        self.assertEqual(kept[0].read_bytes(), original, "the rewrite never reaches the kept file")
        SessionStore(self.directory, self.state["id"]).load()
        self.assertEqual(len(self.kept()), 1, "a chat it can read now is not kept again")

    def test_a_record_part_this_build_does_not_know_keeps_the_file(self):
        with self.store.path.open("ab") as handle:  # A later build's record: a part this build rejects.
            handle.write(b'{"seq": 6, "projects": {"set": {"p1": {}}}}\n')
        self.assert_kept_before_the_first_rewrite(self.store.path.read_bytes(), verified=False)

    def test_a_field_this_build_does_not_know_keeps_the_file_and_the_chat_stays_usable(self):
        self.store.compact(self.state)
        raw = json.loads(self.store.path.read_text(encoding="utf-8"))
        raw["projects"] = {"p1": {"name": "A later build's field"}}  # Its next save here would drop it.
        self.store.path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
        self.assert_kept_before_the_first_rewrite(self.store.path.read_bytes(), verified=True)

    def test_a_later_version_keeps_the_file(self):
        self.store.compact(self.state)
        raw = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.store.path.write_text(json.dumps(dict(raw, version=2)) + "\n", encoding="utf-8")
        self.assert_kept_before_the_first_rewrite(self.store.path.read_bytes(), verified=False)

    def test_without_hard_links_a_synced_copy_is_kept_and_no_temporary_file_stays(self):
        with self.store.path.open("ab") as handle:
            handle.write(b'{"seq": 6, "projects": {}}\n')
        with patch.object(session_journal.os, "link", side_effect=OSError("no hard links (FAT, exFAT)")):
            self.assert_kept_before_the_first_rewrite(self.store.path.read_bytes(), verified=False)
        self.assertNotEqual(self.kept()[0].stat().st_ino, self.store.path.stat().st_ino)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_a_file_that_cannot_be_kept_is_never_written_over(self):
        with self.store.path.open("ab") as handle:
            handle.write(b'{"seq": 6, "projects": {}}\n')
        original = self.store.path.read_bytes()
        store = SessionStore(self.directory, self.state["id"])
        with patch.object(session_kept, "preserve", side_effect=OSError("read-only folder")):
            loaded = store.load()
        self.assertTrue(loaded["invocations"]["closed"])
        for write in (store.save, store.compact):
            with self.assertRaises(OSError):
                write(loaded)
        self.assertEqual(self.store.path.read_bytes(), original)

    def test_a_chat_that_cannot_be_kept_still_closes_without_writing(self):
        with self.store.path.open("ab") as handle:
            handle.write(b'{"seq": 6, "projects": {}}\n')
        original = self.store.path.read_bytes()
        with patch.object(session_kept, "preserve", side_effect=OSError("read-only folder")):
            controller = Controller(str(self.directory), session_id=self.state["id"])
        self.assertTrue(controller.state["invocations"]["closed"])
        controller.close()  # Quitting or unregistering closes every chat; a refused save never stops it.
        self.assertTrue(controller.persistence_warning)
        self.assertEqual(self.store.path.read_bytes(), original)


class SwitchCompactionTests(unittest.TestCase):
    """Case 4: the documented compaction at a chat switch, which once did not happen.

    A switch leaves the chat one base line (``SessionStore.leave``): it compacts a file
    holding records or changes and writes nothing when the file already is one (``..._fixes2.LeaveTests``).
    """

    def test_switching_chats_compacts_the_chat_left(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/ui.py").read_text(encoding="utf-8")
        function = next(node for node in ast.walk(ast.parse(source))
                        if isinstance(node, ast.FunctionDef) and node.name == "show_session")
        # The chat left is saved through save_observed_state (a failure never stops the switch).
        saves = [node for node in ast.walk(function) if isinstance(node, ast.Call)
                 and any(isinstance(arg, ast.Name) and arg.id == "old" for arg in node.args)
                 and isinstance(node.func, ast.Name) and node.func.id == "save_observed_state"]
        self.assertTrue(saves, "show_session saves the chat it leaves")
        self.assertTrue(all(any(keyword.arg == "leave" and getattr(keyword.value, "value", None) is True
                                for keyword in call.keywords) for call in saves),
                        "agent_execution.rst: a switch to another chat leaves it one base line")


if __name__ == "__main__":
    unittest.main()
