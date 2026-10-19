# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The append-only conversation journal (session_journal, session_writer): records, torn tails, writers, compaction.

Each test saves through the product ``SessionStore`` and checks the file the
way a restart reads it (``SessionStore.load``, ``read_transcript``) and the
way an earlier build reads it (``json.loads`` of the whole file).
``test_native_session_journal_cost.py`` pins what one save costs.
"""

import json
import os
import random
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import session_journal, session_writer  # noqa: E402
from angelo_agent.invocations import reserve_invocation  # noqa: E402
from angelo_agent.sessions import HISTORY_WARNING, SessionStore, read_transcript  # noqa: E402


def plain(value):
    return json.loads(json.dumps(value))


def row(index, status="queued"):
    return {"role": "activity", "call_id": "call-%d" % index, "tool": "angelo_scene_program", "text": "",
            "arguments": {"mode": "APPLY",
                          "operations": [{"kind": "transform", "parameters": {"location": [index, 0, 0]}}]},
            "result": {}, "status": status}


def earlier_build_reads(path):
    """What every earlier build's loader does first: the whole file must be one JSON document."""
    return json.loads(path.read_text(encoding="utf-8"))


class JournalCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        self.store = SessionStore(self.directory, str(uuid.uuid4()))
        self.state = self.store.new("claude_subscription")

    def edit(self, index):
        """One edit as the controller journals it: a row, its result record and a mutation ID."""
        key = self.state["id"] + "/call-%d" % index
        reserve_invocation(self.state, key, "%064x" % index, "('document', 1)")
        self.state["results"][key] = {"document": "('document', 1)", "phase": "queued", "result": {}}
        self.state["messages"].append(row(index))
        self.store.save(self.state)
        self.state["results"][key] = {"document": "('document', 1)", "phase": "completed", "result": {"success": True}}
        self.state["messages"][-1] = dict(self.state["messages"][-1], status="completed", result={"success": True})
        self.store.save(self.state)

    def assert_restores(self, store=None):
        store = store or SessionStore(self.directory, self.state["id"])
        loaded = store.load()
        self.assertEqual(loaded["messages"], plain(self.state["messages"]))
        self.assertEqual(loaded["results"], plain(self.state["results"]))
        self.assertEqual(loaded["invocations"], plain(self.state["invocations"]))
        return loaded


class AppendTests(JournalCase):
    def test_saves_after_the_first_append_records_and_a_restart_restores_them(self):
        self.store.save(self.state)
        self.assertEqual(self.store.last_write, "rewrite")
        for index in range(20):
            self.edit(index)
            self.assertEqual(self.store.last_write, "append")
        lines = self.store.path.read_bytes().split(b"\n")
        self.assertEqual(lines[-1], b"", "every record ends with its newline")
        self.assertEqual([json.loads(line)["seq"] for line in lines[1:-1]], list(range(1, 41)))
        self.assertFalse(self.assert_restores()["invocations"]["closed"])

    def test_an_earlier_build_fails_closed_on_records_and_reads_a_compacted_file(self):
        self.store.save(self.state)
        self.edit(1)
        with self.assertRaises(ValueError):  # "Extra data": its loader makes the history unverified, IDs closed.
            earlier_build_reads(self.store.path)
        self.store.compact(self.state)
        self.assertEqual(earlier_build_reads(self.store.path)["invocations"], plain(self.state["invocations"]))
        self.assertEqual(earlier_build_reads(self.store.path)["messages"], plain(self.state["messages"]))

    def test_an_earlier_builds_whole_file_loads_and_its_first_save_rewrites(self):
        legacy = dict(self.state, messages=[row(1, "completed")], results={})
        reserve_invocation(legacy, legacy["id"] + "/call-1", "f" * 64, "('document', 1)")
        for text in (json.dumps(legacy), json.dumps(legacy, indent=2) + "\n"):  # Every earlier writer's format.
            with self.subTest(indented="\n" in text.strip()):
                self.store.path.write_text(text, encoding="utf-8")
                loaded = self.store.load()
                self.assertEqual(loaded["messages"], legacy["messages"])
                self.assertEqual(loaded["invocations"], legacy["invocations"])
                loaded["messages"].append(row(2))
                self.store.save(loaded)
                self.assertEqual(self.store.last_write, "rewrite", "the first save after a load rewrites")
                self.assertEqual(earlier_build_reads(self.store.path)["messages"], plain(loaded["messages"]))

    def test_a_record_is_synced_before_the_save_returns(self):
        self.store.save(self.state)
        steps, fsync = [], os.fsync

        def synced(descriptor):
            steps.append(os.fstat(descriptor).st_size)
            fsync(descriptor)
        with patch.object(session_journal.os, "fsync", synced):
            self.edit(1)
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[-1], self.store.path.stat().st_size, "the last append was synced whole")


class TornRecordTests(JournalCase):
    def journal(self):
        self.store.save(self.state)
        for index in range(3):
            self.edit(index)
        return self.store.path.read_bytes()

    def test_a_record_torn_at_any_byte_is_ignored_and_the_rest_restores(self):
        data = self.journal()
        last = data.rfind(b"\n", 0, len(data) - 1) + 1  # Start of the last record.
        previous = session_journal.read(self.store.path)[0]
        before = dict(previous, messages=previous["messages"][:-1] + [dict(previous["messages"][-1], status="queued",
                                                                          result={})])
        for cut in sorted({last + 1, last + 7, (last + len(data)) // 2, len(data) - 2, len(data) - 1}):
            with self.subTest(cut=cut - last):
                self.store.path.write_bytes(data[:cut])
                raw, info = session_journal.read(self.store.path)
                self.assertTrue(info.torn)
                self.assertEqual(raw["messages"], before["messages"])
                loaded = SessionStore(self.directory, self.state["id"]).load()
                self.assertFalse(loaded["invocations"]["closed"], "a torn tail is not corruption")

    def test_a_torn_record_of_garbage_or_split_utf8_is_ignored(self):
        data = self.journal()
        last = data.rfind(b"\n", 0, len(data) - 1) + 1
        for tail in (b"\x00" * 40 + b"\n", b'{"seq": 7, "rows": "\xe6\x97', b'{"seq": 6, "set": {"model": "\xe6\x97\n'):
            with self.subTest(tail=tail[:12]):
                self.store.path.write_bytes(data[:last] + tail)
                raw, info = session_journal.read(self.store.path)
                self.assertTrue(info.torn)
                self.assertEqual(info.records, 5)

    def test_the_first_save_after_a_torn_tail_rewrites_so_no_record_follows_it(self):
        data = self.journal()
        self.store.path.write_bytes(data[:-5])
        store = SessionStore(self.directory, self.state["id"])
        state = store.load()
        state["messages"].append(row(9))
        store.save(state)
        self.assertEqual(store.last_write, "rewrite")
        self.assertEqual(earlier_build_reads(self.store.path)["messages"], plain(state["messages"]))

    def test_a_malformed_record_before_the_last_is_corruption_and_closes_mutations(self):
        data = self.journal().split(b"\n")
        for name, line in (("garbage", b"{not json"), ("gap", data[2].replace(b'"seq": 2', b'"seq": 9')),
                           ("unknown part", data[2][:-1] + b', "drop": true}'),
                           ("row past the end", b'{"seq": 2, "rows": {"n": 99, "at": [[99, {}]]}}'),
                           ("ledger entry twice", data[1].replace(b'"seq": 1', b'"seq": 2'))):
            with self.subTest(name=name):
                corrupt = b"\n".join(data[:2] + [line] + data[3:])
                self.store.path.write_bytes(corrupt)
                with self.assertRaises(ValueError):
                    session_journal.read(self.store.path)
                loaded = SessionStore(self.directory, self.state["id"]).load()
                self.assertTrue(loaded["invocations"]["closed"])
                self.assertEqual(loaded["messages"][-1]["text"], HISTORY_WARNING)


class WriterTests(JournalCase):
    def save_row(self, index):
        self.state["messages"].append(row(index))
        self.store.save(self.state)

    def test_another_writers_rewrite_is_never_appended_to(self):
        self.store.save(self.state)
        self.edit(1)
        other = SessionStore(self.directory, self.state["id"])
        foreign = other.load()
        foreign["messages"].append({"role": "user", "text": "Written by another writer"})
        other.save(foreign)
        self.save_row(2)
        self.assertEqual(self.store.last_write, "rewrite", "the changed file is rewritten, not appended to")
        self.assertEqual(read_transcript(self.store.path)["messages"], plain(self.state["messages"]))

    def test_another_writers_append_and_a_same_size_change_in_place_are_detected(self):
        self.store.save(self.state)
        self.edit(1)
        other = SessionStore(self.directory, self.state["id"])
        foreign = other.load()
        other.save(foreign)
        foreign["messages"].append({"role": "user", "text": "Appended by another writer"})
        other.save(foreign)  # Appends to the file this store last wrote.
        self.save_row(2)
        self.assertEqual(self.store.last_write, "rewrite")
        os.utime(self.store.path, ns=(1, 1))  # Same bytes and size, other times: still not this writer's file.
        self.save_row(3)
        self.assertEqual(self.store.last_write, "rewrite")
        self.assert_restores()

    @unittest.skipIf(session_journal.fcntl is None, "advisory locks are POSIX")
    def test_an_append_checks_and_writes_under_an_exclusive_lock(self):
        self.store.save(self.state)
        held, flock = [], session_journal.fcntl.flock

        def locking(descriptor, operation):
            held.append((os.fstat(descriptor).st_ino, operation))
            return flock(descriptor, operation)
        with patch.object(session_journal.fcntl, "flock", locking):
            self.save_row(1)
        self.assertEqual(held, [(self.store.path.stat().st_ino, session_journal.fcntl.LOCK_EX)])

    def test_a_replace_windows_refuses_while_another_process_holds_the_file_is_retried(self):
        replace, calls = os.replace, []

        def busy_twice(source, target):
            calls.append(target)
            if len(calls) <= 2:
                raise PermissionError(13, "The process cannot access the file")  # Held without delete sharing.
            return replace(source, target)
        with patch.object(session_journal.os, "name", "nt"), patch.object(session_journal.os, "replace", busy_twice), \
                patch.object(session_journal.time, "sleep"):
            self.store.save(self.state)
        self.assertEqual(len(calls), 3)
        with patch.object(session_journal.os, "replace", side_effect=PermissionError(13, "denied")), \
                self.assertRaises(PermissionError):  # Elsewhere a refusal is reported at once.
            self.store.compact(self.state)

    def test_a_link_in_place_of_the_journal_is_replaced_and_its_target_untouched(self):
        self.store.save(self.state)
        target = self.directory / "elsewhere.json"
        target.write_text("private", encoding="utf-8")
        os.replace(self.store.path, self.directory / "moved.json")
        os.symlink(target, self.store.path)
        self.save_row(1)
        self.assertEqual(self.store.last_write, "rewrite")
        self.assertEqual(target.read_text(encoding="utf-8"), "private")
        self.assertFalse(self.store.path.is_symlink())
        self.assert_restores()

    def test_a_failed_append_is_cut_back_raised_and_the_next_save_rewrites(self):
        self.store.save(self.state)
        self.edit(1)
        before = self.store.path.read_bytes()
        write = os.write

        def partial(descriptor, data):
            write(descriptor, bytes(data[:5]))
            raise OSError(28, "No space left on device")
        self.state["messages"].append(row(2))
        with patch.object(session_journal.os, "write", partial), self.assertRaises(OSError):
            self.store.save(self.state)
        self.assertEqual(self.store.path.read_bytes(), before, "the partial record was cut back")
        self.store.save(self.state)
        self.assertEqual(self.store.last_write, "rewrite")
        self.assert_restores()

    def test_a_failed_sync_leaves_no_acknowledged_record_and_the_next_save_rewrites(self):
        self.store.save(self.state)
        self.state["messages"].append(row(1))
        with patch.object(session_journal.os, "fsync", side_effect=OSError(5, "I/O error")), self.assertRaises(OSError):
            self.store.save(self.state)
        self.store.save(self.state)
        self.assertEqual(self.store.last_write, "rewrite")
        self.assert_restores()


class CompactionTests(JournalCase):
    def test_records_past_the_base_compact_at_a_turns_end_into_one_line(self):
        self.store.save(self.state)
        with patch.object(session_writer, "COMPACT_MIN_BYTES", 0):
            compacted = []
            for index in range(40):
                self.edit(index)
                compacted.append(self.store.compact_if_due(self.state))
        self.assertTrue(any(compacted) and not all(compacted))
        size = self.store.path.stat().st_size
        self.assertLess(size, 2 * len(json.dumps(plain(self.state)).encode()) + 4096)
        self.assert_restores()

    def test_a_compaction_restores_exactly_and_appending_continues_after_it(self):
        self.store.save(self.state)
        for index in range(5):
            self.edit(index)
        self.store.compact(self.state)
        self.assertEqual(self.store.path.read_bytes().count(b"\n"), 1)
        self.assert_restores()
        self.edit(6)
        self.assertEqual(self.store.last_write, "append")
        self.assert_restores()

    def test_a_save_compacts_by_itself_only_far_past_the_base(self):
        self.store.save(self.state)
        with patch.object(session_writer, "FORCE_MIN_BYTES", 0):
            for index in range(30):
                self.edit(index)
        self.assertLess(self.store.path.stat().st_size, 6 * len(json.dumps(plain(self.state)).encode()))
        self.assert_restores()


class TrackingTests(JournalCase):
    def test_an_old_row_is_written_when_touched_and_found_by_the_scrub_when_not(self):
        self.store.save(self.state)
        for index in range(100):
            self.state["messages"].append(row(index, "completed"))
        self.store.seal(len(self.state["messages"]))
        self.store.save(self.state)
        self.state["messages"][3]["status"] = "touched"
        self.store.touch(3)
        self.store.save(self.state)
        self.assertEqual(read_transcript(self.store.path)["messages"][3]["status"], "touched")
        self.state["messages"][40]["status"] = "never touched"  # A change no caller marked.
        saves = 0
        while read_transcript(self.store.path)["messages"][40]["status"] != "never touched":
            self.state["updated"] += 1
            with patch.object(session_writer, "SCRUB_ROWS", 4), patch("angelo_agent.sessions.VERIFY_JOURNAL", False):
                self.store.save(self.state)
            saves += 1
        self.assertLessEqual(saves, 100 // 4 + 1, "the scrub reaches every old row in turn")

    def test_an_insert_below_the_window_or_a_new_list_rewrites(self):
        self.store.save(self.state)
        for index in range(100):
            self.state["messages"].append(row(index))
        self.store.seal(100)
        self.store.save(self.state)
        self.state["messages"].insert(2, {"role": "user", "text": "Inserted below the window"})
        self.store.save(self.state)
        self.assertEqual(self.store.last_write, "rewrite")
        self.state["messages"] = list(self.state["messages"])
        self.store.save(self.state)
        self.assertEqual(self.store.last_write, "rewrite")
        self.assert_restores()

    def test_results_keep_their_order_through_eviction_and_a_key_that_comes_back(self):
        self.store.save(self.state)
        results = self.state["results"]
        for index in range(300):
            results["k%d" % index] = {"document": "d", "result": {"index": index}}
            while len(results) > 256:
                results.pop(next(iter(results)))
            self.store.save(self.state)
        results.pop("k100")
        results["k100"] = {"document": "d", "result": {"back": True}}
        self.store.save(self.state)
        self.assertEqual(list(read_transcript(self.store.path)["results"]), list(results))
        self.assert_restores()

    def test_ledger_entries_are_checked_as_written_and_an_irregular_ledger_is_rewritten_whole(self):
        self.store.save(self.state)
        self.edit(1)
        self.edit(2)
        entries = self.state["invocations"]["entries"]
        entries.pop(next(iter(entries)))  # The ledger never shrinks; one that did is stated whole.
        reserve_invocation(self.state, self.state["id"] + "/call-3", "%064x" % 3, "('document', 1)")
        self.store.save(self.state)
        self.assertEqual(self.store.last_write, "rewrite")
        self.assert_restores()
        self.edit(4)
        self.assertEqual(self.store.last_write, "append")
        self.state["invocations"]["entries"]["damaged"] = {"digest": "not a digest", "document": None}
        self.store.save(self.state)
        self.assertEqual(self.store.last_write, "rewrite", "an invalid entry is never appended")
        self.assertTrue(self.state["invocations"]["closed"], "and the whole-ledger check closes it, as before")
        self.assertTrue(self.assert_restores()["invocations"]["closed"])

    def test_random_sessions_restore_exactly_after_every_save(self):
        generator = random.Random(20261015)
        self.store.save(self.state)
        for step in range(400):
            choice = generator.random()
            messages, results = self.state["messages"], self.state["results"]
            if choice < .35:
                messages.append(row(step))
            elif choice < .55 and messages:
                index = generator.randrange(len(messages))
                messages[index] = dict(messages[index], status="step %d" % step)
                self.store.touch(index)
            elif choice < .65:
                results["r%d" % generator.randrange(300)] = {"document": "d", "result": {"step": step}}
                while len(results) > 256:
                    results.pop(next(iter(results)))
            elif choice < .75:
                reserve_invocation(self.state, "id-%d" % step, "%064x" % step, "doc")
            elif choice < .8:
                self.store.seal(len(messages))
            elif choice < .85 and messages:
                messages.insert(generator.randrange(len(messages)), {"role": "system", "text": "inserted %d" % step})
            elif choice < .9:
                self.state["model"] = "model-%d" % generator.randrange(3)
            elif choice < .93:
                self.store.compact_if_due(self.state)
            else:
                self.store.save(self.state)
            self.store.save(self.state)  # Verification (run_host_tests sets ANGELO_JOURNAL_VERIFY) checks each.
            self.assertEqual(read_transcript(self.store.path)["messages"], plain(messages))
        self.assert_restores()


if __name__ == "__main__":
    unittest.main()
