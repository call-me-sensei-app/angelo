# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Adversarial pins of the append-only conversation journal.

Companion of ``test_native_session_journal_review.py`` (the repros).
These pin: crash tails of every kind, strict
saves under injected ENOSPC/EIO through the real controller path, other writers
and links, earlier builds' file formats, the redaction rewrite, the restore's
badges, and the purity the writer's task-memory cache relies on.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import session_journal, task_memory  # noqa: E402
from angelo_agent.checkpoint_restore_ui import _mark_scene_history  # noqa: E402
from angelo_agent.controller import Controller, ToolDispatchBudget  # noqa: E402
from angelo_agent.invocations import reserve_invocation  # noqa: E402
from angelo_agent.sessions import HISTORY_WARNING, SessionStore, read_transcript  # noqa: E402

CUBE = {"name": "Journal Cube", "size": 1, "location": [0, 0, 0]}
FAKE_KEY = "sk-test-fake-0123456789abcdef"  # A fake key: no credential is read.


def plain(value):
    return json.loads(json.dumps(value))


class Case(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = folder.name
        self.store = SessionStore(self.directory, str(uuid.uuid4()))
        self.state = self.store.new("claude_subscription")
        self.store.save(self.state)

    def edit(self, index, text="Réglé ✓ 日本語"):
        """One edit: a row with multibyte text, a result record and a mutation ID, saved."""
        reserve_invocation(self.state, self.state["id"] + "/call-%d" % index, "%064x" % index, "('document', 1)")
        self.state["results"][self.state["id"] + "/call-%d" % index] = {"document": "d", "phase": "queued", "result": {}}
        self.state["messages"].append({"role": "assistant", "text": "%s %d" % (text, index)})
        self.store.save(self.state)

    def reload(self):
        return SessionStore(self.directory, self.state["id"]).load()

    def last_record_start(self, data):
        return data.rfind(b"\n", 0, len(data) - 1) + 1


class CrashTailTests(Case):
    """What a crash in the middle of an append can leave, and what it must never hide."""

    def journal(self):
        for index in range(4):
            self.edit(index)
        return self.store.path.read_bytes()

    def assert_torn_and_verified(self, data, records):
        self.store.path.write_bytes(data)
        raw, info = session_journal.read(self.store.path)
        self.assertTrue(info.torn)
        self.assertEqual(info.records, records)
        loaded = self.reload()
        self.assertFalse(loaded["invocations"]["closed"], "a torn last record is not corruption")
        self.assertEqual(len(loaded["invocations"]["entries"]), records)
        return loaded

    def test_zero_filled_and_delayed_allocation_tails_are_torn(self):
        data = self.journal()
        start = self.last_record_start(data)
        record = data[start:]
        for name, tail in (("zeros, no newline", b"\x00" * len(record)),
                           ("first page lost, rest and newline written", b"\x00" * (len(record) // 2) + record[len(record) // 2:]),
                           ("first part written, rest zeros", record[:len(record) // 2] + b"\x00" * (len(record) - len(record) // 2))):
            with self.subTest(tail=name):
                self.assert_torn_and_verified(data[:start] + tail, 3)

    def test_a_cut_inside_every_byte_of_a_multibyte_character_is_torn(self):
        data = self.journal()
        start = self.last_record_start(data)
        character = data.index("日".encode(), start)
        for cut in range(character + 1, character + 3):
            with self.subTest(cut=cut - character):
                self.assert_torn_and_verified(data[:cut], 3)

    def test_a_complete_record_without_its_newline_is_torn_and_its_save_never_returned(self):
        data = self.journal()
        loaded = self.assert_torn_and_verified(data[:-1], 3)
        self.assertNotIn("%s/call-3" % self.state["id"], loaded["invocations"]["entries"])

    def test_garbage_ending_in_a_newline_is_torn_only_as_the_last_line(self):
        data = self.journal()
        self.assert_torn_and_verified(data + b"\x01garbage\n", 4)
        start = self.last_record_start(data)
        self.store.path.write_bytes(data[:start] + b"\x01garbage\n" + data[start:])  # Not last: corruption.
        with self.assertRaises(ValueError):
            session_journal.read(self.store.path)

    def test_corruption_before_the_last_record_closes_the_ledger_and_is_never_skipped(self):
        data = self.journal()
        lines = data.split(b"\n")  # base, records 1..4, ""
        cases = {
            "an earlier record zeroed, newline included": b"\n".join(lines[:2]) + b"\x00" * (len(lines[2]) + 1)
                                                         + b"\n".join(lines[3:]),
            "an earlier record duplicated": b"\n".join(lines[:3] + [lines[2]] + lines[3:]),
            "the last record written twice": data + lines[4] + b"\n",
            "the last record's sequence replayed": b"\n".join(lines[:4] + [lines[3]]) + b"\n",
            "a boolean sequence": b"\n".join(lines[:1] + [lines[1].replace(b'"seq": 1', b'"seq": true')] + lines[2:]),
        }
        for name, corrupt in cases.items():
            with self.subTest(case=name):
                self.store.path.write_bytes(corrupt)
                with self.assertRaises(ValueError):
                    session_journal.read(self.store.path)
                loaded = self.reload()
                self.assertTrue(loaded["invocations"]["closed"])
                self.assertEqual(loaded["messages"][-1]["text"], HISTORY_WARNING)


class Executor:
    def __init__(self):
        self.creates = 0

    def precondition(self, name, arguments):
        return self.creates

    def execute(self, name, arguments):
        self.creates += 1
        return {"success": True, "verified": {"object_count": self.creates}, "scene_changed": True,
                "changes": {"added": [{"name": arguments["name"], "type": "MESH"}], "removed": [], "modified": []}}


class StrictSaveFaultTests(unittest.TestCase):
    """ENOSPC and EIO inside the journal's own write and sync, through the controller's strict saves.

    Each fault strikes once (the disk then has room again), so what follows is the journal's own recovery.
    """

    def controller(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        controller = Controller(folder.name)
        self.addCleanup(controller.close)
        controller.set_document(("journal-faults", 3))
        controller._begin_metrics()
        controller.busy = True
        controller.save()
        controller.state["messages"].append({"role": "user", "text": "Add a cube"})
        controller.save()
        self.assertEqual(controller.store.last_write, "append")
        return controller, Executor(), []

    @staticmethod
    def faults():
        write, fsync = os.write, os.fsync

        def once(real, fault):
            struck = []

            def call(*args):
                if struck:
                    return real(*args)
                struck.append(True)
                return fault(*args)
            return call

        def short_write_then_enospc(descriptor, data):
            write(descriptor, bytes(data[:7]))
            raise OSError(28, "No space left on device")

        def eio(descriptor):
            raise OSError(5, "Input/output error")
        return {"ENOSPC mid-write": lambda: ("write", once(write, short_write_then_enospc)),
                "EIO on sync": lambda: ("fsync", once(fsync, eio))}

    def assert_reloads(self, controller, call_id):
        store = controller.store
        loaded = SessionStore(store.directory, store.session_id).load()
        self.assertFalse(loaded["invocations"]["closed"])
        self.assertIn(call_id, loaded["invocations"]["entries"], "the reserved mutation ID is durable")
        self.assertEqual(loaded["invocations"], plain(controller.state["invocations"]))

    def test_a_failed_admission_save_runs_no_native_work_and_the_next_save_rewrites(self):
        for name, fault in self.faults().items():
            with self.subTest(fault=name):
                controller, executor, replies = self.controller()
                before = controller.store.path.read_bytes()
                function, injected = fault()
                with patch.object(session_journal.os, function, injected):
                    controller.request_tool("admit", "angelo_create_cube", CUBE, replies.append)
                self.assertIn("no native work", replies[-1]["error"])
                controller.pump_tools(executor, controller.document, budget=ToolDispatchBudget(seconds=60))
                self.assertEqual(executor.creates, 0)
                self.assertFalse(controller.pending)
                self.assertEqual(controller.store.path.read_bytes(), before, "the failed append was cut back")
                self.assertFalse(controller.store._writer.ready, "the writer forgot the file")
                controller.save()
                self.assertEqual(controller.store.last_write, "rewrite", "the next save rewrites the whole file")
                self.assert_reloads(controller, controller.state["id"] + "/admit")

    def test_a_failed_execution_start_save_runs_no_native_work_and_the_next_save_rewrites(self):
        for name, fault in self.faults().items():
            with self.subTest(fault=name):
                controller, executor, replies = self.controller()
                controller.request_tool("run", "angelo_create_cube", CUBE, replies.append)
                pending = next(iter(controller.pending.values()))
                before = controller.store.path.read_bytes()
                function, injected = fault()
                writes, real_rewrite = [], session_journal.rewrite

                def recorded(path, data):
                    writes.append(path)
                    return real_rewrite(path, data)
                with patch.object(session_journal.os, function, injected), \
                        patch.object(session_journal, "rewrite", recorded):
                    controller.execute_tool(pending, executor, controller.document)
                self.assertEqual(executor.creates, 0)
                self.assertIn("error", replies[-1])
                self.assertTrue(writes, "the completion save after the failure rewrote the whole file")
                saved = read_transcript(controller.store.path)["results"][pending.id]
                self.assertEqual(saved["phase"], "completed")
                self.assertIn("error", saved["result"])
                self.assertNotEqual(controller.store.path.read_bytes(), before)
                self.assert_reloads(controller, pending.id)


class WriterTests(Case):
    """Other writers, links and forged times never get a record appended to them."""

    def test_two_stores_in_one_process_never_interleave_records(self):
        other = SessionStore(self.directory, self.state["id"])
        foreign = other.load()
        for index in range(6):
            self.edit(index)
            foreign["messages"].append({"role": "user", "text": "Other %d" % index})
            other.save(foreign)
            self.assertEqual(other.last_write, "rewrite", "never appended after another store's write")
        self.state["model"] = "changed"  # A save with nothing to write returns without looking at the file.
        self.store.save(self.state)
        self.assertEqual(self.store.last_write, "rewrite")
        self.assertEqual(read_transcript(self.store.path)["messages"], plain(self.state["messages"]))

    def test_a_rewrite_between_an_appends_check_and_write_is_healed_by_the_next_save(self):
        """NOTE: another app on the same chat renames its rewrite in after this append's identity check (the
        check sees one link; an inode already unlinked is refused). The append lands in the unlinked inode and its
        save returns; until this store's next save the file is the other app's. Last writer wins, as with the
        whole-file save, whose rename could land just after ours."""
        self.edit(1)
        other = SessionStore(self.directory, self.state["id"])
        foreign = other.load()
        foreign["messages"].append({"role": "user", "text": "The other app's row"})
        write, raced = os.write, []

        def other_app_renames_first(descriptor, data):
            if not raced and os.fstat(descriptor).st_ino == self.store.path.stat().st_ino:
                raced.append(True)
                other.save(foreign)  # A rewrite: temporary file, then os.replace over the path.
            return write(descriptor, data)
        with patch.object(session_journal.os, "write", other_app_renames_first), \
                patch("angelo_agent.sessions.VERIFY_JOURNAL", False):
            self.edit(2)
        self.assertTrue(raced)
        self.assertEqual(self.store.last_write, "append", "the append returned")
        on_disk = read_transcript(self.store.path)
        self.assertNotIn(self.state["id"] + "/call-2", on_disk["invocations"]["entries"], "but went to the old inode")
        self.state["model"] = "next save"
        self.store.save(self.state)
        self.assertEqual(self.store.last_write, "rewrite")
        self.assertIn(self.state["id"] + "/call-2", read_transcript(self.store.path)["invocations"]["entries"])

    def test_a_hard_link_to_the_journal_is_never_appended_through(self):
        self.edit(1)
        alias = Path(self.directory) / "alias.json"
        os.link(self.store.path, alias)
        before = alias.read_bytes()
        self.edit(2)
        self.assertEqual(self.store.last_write, "rewrite")
        self.assertEqual(alias.read_bytes(), before, "the other name keeps the old file")

    def test_a_link_to_another_chats_journal_is_replaced_and_that_chat_untouched(self):
        neighbour = SessionStore(self.directory, str(uuid.uuid4()))
        neighbour_state = neighbour.new("claude_subscription")
        neighbour_state["messages"].append({"role": "user", "text": "The neighbour's chat"})
        neighbour.save(neighbour_state)
        before = neighbour.path.read_bytes()
        self.edit(1)
        os.replace(self.store.path, Path(self.directory) / "aside.json")
        os.symlink(neighbour.path, self.store.path)
        self.edit(2)
        self.assertEqual(neighbour.path.read_bytes(), before)
        self.assertFalse(self.store.path.is_symlink())

    def test_a_same_size_change_with_its_modification_time_put_back_is_detected(self):
        self.edit(1)
        status = self.store.path.stat()
        data = bytearray(self.store.path.read_bytes())
        data[data.index(b"call-1")] = ord("C")  # Same size, other bytes, written in place.
        with open(self.store.path, "r+b") as handle:
            handle.write(data)
        os.utime(self.store.path, ns=(status.st_atime_ns, status.st_mtime_ns))
        self.edit(2)
        self.assertEqual(self.store.last_write, "rewrite", "the change time moved (APFS, ext4)")


class RedactionAndRestoreTests(unittest.TestCase):
    """In-place changes to old rows that invalidate, and what then stays on disk."""

    def test_binding_a_key_old_rows_held_leaves_no_copy_of_it_in_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            controller = Controller(folder)
            try:
                controller.set_document(("journal-keys", 1))
                controller.emit("activity", {"id": "call-1", "tool": "angelo_execute_python", "status": "queued",
                                             "arguments": {"code": "key = '%s'" % FAKE_KEY}})
                controller.pump_events()
                for index in range(80):  # The row ends up far below the window.
                    controller.state["messages"].append({"role": "assistant", "text": "row %d" % index})
                    controller.save()
                self.assertIn(FAKE_KEY.encode(), controller.store.path.read_bytes())
                controller.api_key = FAKE_KEY
                controller.save()
                self.assertEqual(controller.store.last_write, "rewrite")
                self.assertNotIn(FAKE_KEY.encode(), controller.store.path.read_bytes())
            finally:
                controller.close()

    def test_a_restores_badges_on_old_rows_reach_the_file_through_a_rewrite(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SessionStore(folder, str(uuid.uuid4()))
            state = store.new("claude_subscription")
            state["messages"].append({"role": "user", "text": "Move it", "checkpoint": "a"})
            state["messages"].append({"role": "activity", "call_id": "c", "status": "completed",
                                      "result": {"changes": {"modified": [{"name": "Cube"}]}}})
            state["messages"].extend({"role": "assistant", "text": "row %d" % i} for i in range(100))
            store.save(state)
            store.seal(len(state["messages"]))
            store.save(state)
            checkpoints = SimpleNamespace(load=lambda identity: {"created": 10.0})
            _mark_scene_history(SimpleNamespace(store=store, state=state, checkpoints=checkpoints, lock=threading.RLock()),
                                {"created": 5.0}, 1)
            store.save(state)
            self.assertEqual(store.last_write, "rewrite")
            reloaded = SessionStore(folder, state["id"]).load()
            self.assertIs(reloaded["messages"][1]["reverted"], True)


class FormatTests(Case):
    """Every earlier writer's formats, and downgrade then upgrade."""

    def legacy(self):
        legacy = dict(self.state, messages=[{"role": "user", "text": "Café"}])
        reserve_invocation(legacy, legacy["id"] + "/call-1", "f" * 64, "('document', 1)")
        return legacy

    def test_indented_and_crlf_legacy_files_load_and_a_bom_fails_closed_as_before(self):
        legacy = self.legacy()
        for name, data in (("indented", json.dumps(legacy, indent=2).encode()),
                           ("CRLF", json.dumps(legacy, indent=1).replace("\n", "\r\n").encode() + b"\r\n"),
                           ("trailing blank lines", json.dumps(legacy).encode() + b"\n\n")):
            with self.subTest(format=name):
                self.store.path.write_bytes(data)
                loaded = self.reload()
                self.assertEqual(loaded["messages"], legacy["messages"])
                self.assertFalse(loaded["invocations"]["closed"])
        self.store.path.write_bytes(b"\xef\xbb\xbf" + json.dumps(legacy).encode())
        with self.assertRaises(ValueError):
            json.loads(self.store.path.read_text(encoding="utf-8"))  # The earlier loader fails on a BOM too.
        self.assertTrue(self.reload()["invocations"]["closed"])

    def test_a_file_an_earlier_build_replaced_stays_closed_here(self):
        self.edit(1)
        unverified = SessionStore(self.directory, self.state["id"])._unverified_history()
        self.store.path.write_text(json.dumps(unverified), encoding="utf-8")  # The earlier build's next save.
        loaded = self.reload()
        self.assertTrue(loaded["invocations"]["closed"], "a downgrade then upgrade never reopens mutation IDs")


class TaskMemoryPurityTests(unittest.TestCase):
    """The writer re-cleans the task memory only when it is another object.

    Verification compares the file with the fields the save cleaned, so a memory changed in place would be lost
    without a verification failure. These pin the purity that cache relies on: every helper installs a new
    memory, and a cleaned memory shares no container with its source.
    """

    def test_every_task_memory_helper_returns_another_memory_object(self):
        state = {"task_memory": task_memory.new_memory("/profile")}
        document = ("memory-doc", 1)
        for name, call in (("on_restore", lambda s: task_memory.on_restore(s, document, profile_id="/profile")),
                           ("on_verified_result", lambda s: task_memory.on_verified_result(
                               s, document, call_id="c1", tool="angelo_inspect_scene", result={"verified": {}},
                               profile_id="/profile"))):
            with self.subTest(helper=name):
                updated = call(state)
                self.assertIsNot(updated["task_memory"], state["task_memory"])
                state = updated

    def test_a_cleaned_memory_shares_no_container_with_its_source(self):
        state = task_memory.on_verified_result({"task_memory": task_memory.new_memory("/profile")}, ("doc", 1),
                                               call_id="c1", tool="angelo_inspect_scene",
                                               result={"verified": {"object_count": 2}}, profile_id="/profile")
        source = state["task_memory"]
        cleaned = task_memory.clean_memory(source, profile_id="/profile")

        def containers(value):
            if isinstance(value, dict):
                yield id(value)
                for item in value.values():
                    yield from containers(item)
            elif isinstance(value, list):
                yield id(value)
                for item in value:
                    yield from containers(item)
        self.assertFalse(set(containers(source)) & set(containers(cleaned)))


if __name__ == "__main__":
    unittest.main()
