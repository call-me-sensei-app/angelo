# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Adversarial pins of the journal's kept copies, locking, disks and maintenance; all passing.

``test_native_session_journal_review2.py`` holds the matching repros.
The manual tools for these checks are in ``journal_review/`` (``kill_compact.py``,
``lock_wait.py``, ``save_costs.py``, ``keep_disks.py``).
"""

import ast
import contextlib
import errno
import glob
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(HERE))
from angelo_agent import background_pauses, checkpoint_transactions, session_journal  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.invocations import reserve_invocation  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402

ROW_MUTATORS = {"append", "insert", "extend", "pop", "remove", "clear", "sort", "reverse"}
# States no other thread holds yet: the loader's own, and a new External conversation before its first save.
FRESH_STATES = {("sessions.py", "_read"), ("sessions.py", "_unverified_history"), ("sessions.py", "load"),
                ("external_host.py", "create_conversation")}
# Helpers whose every caller holds the chat's lock (checked at their call sites below).
CALLER_LOCKED = {"_insert_request_message"}


def _rows(node, aliases):
    """``<anything>["messages"]``, or a local name bound to one."""
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value == "messages":
        return True
    return isinstance(node, ast.Name) and node.id in aliases


def unlocked_row_changes():
    """Every change to a chat's row list (or a call of a CALLER_LOCKED helper) outside ``with <x>.lock:``."""
    problems = []
    for path in sorted(ADDON.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            if (path.name, function.name) in FRESH_STATES or function.name in CALLER_LOCKED:
                continue
            aliases = {target.id for node in ast.walk(function) if isinstance(node, ast.Assign)
                       and _rows(node.value, set()) for target in node.targets if isinstance(target, ast.Name)}

            def visit(node, locked):
                if node is not function and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    return  # Visited as its own function.
                if isinstance(node, ast.With) and any(ast.unparse(item.context_expr).endswith(".lock")
                                                      for item in node.items):
                    locked = True
                call = isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                change = call and (node.func.attr in ROW_MUTATORS and _rows(node.func.value, aliases)
                                   or node.func.attr in CALLER_LOCKED)
                targets = (node.targets if isinstance(node, (ast.Assign, ast.Delete))
                           else [node.target] if isinstance(node, ast.AugAssign) else [])
                change = change or any(isinstance(target, ast.Subscript) and _rows(target.value, aliases)
                                       for target in targets)
                if change and not locked:
                    problems.append("%s:%d %s" % (path.name, node.lineno, ast.unparse(node).splitlines()[0][:80]))
                for child in ast.iter_child_nodes(node):
                    visit(child, locked)
            visit(function, False)
    return problems


class LockAuditTests(unittest.TestCase):
    """An audit of the source: rows change only under the chat's lock, which every save holds."""

    def test_every_row_list_change_holds_the_chats_lock(self):
        # Before the fix this listed job_ui._finished, Controller._after_turn (three) and _rebase_context_if_due.
        self.assertEqual(unlocked_row_changes(), [])

    def test_the_audit_finds_an_unlocked_append(self):
        source = "def finished(controller):\n    controller.state['messages'].append({})\n"
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "probe.py").write_text(source, encoding="utf-8")
            with patch(__name__ + ".ADDON", Path(folder)):
                self.assertEqual(len(unlocked_row_changes()), 1)


def unreadable_chat(folder, rows=5):
    store = SessionStore(folder, str(uuid.uuid4()))
    state = store.new("claude_subscription")
    store.save(state)
    for index in range(rows):
        state["messages"].append({"role": "user", "text": "Request %d" % index})
        reserve_invocation(state, state["id"] + "/call-%d" % index, "%064x" % index, "('document', 1)")
        store.save(state)
    with store.path.open("ab") as handle:
        handle.write(b'{"seq": %d, "projects": {}}\n' % (rows + 1))
    return store, state["id"]


def kept(store):
    return sorted(store.path.parent.glob(glob.escape(store.path.name) + ".*.unreadable"))


@contextlib.contextmanager
def a_new_second_each_time():
    """Each kept name gets another UTC second, as loads seconds apart would."""
    stamps = ("20261015T10%04dZ" % number for number in range(10000))
    with patch.object(session_journal.time, "strftime", lambda *args: next(stamps)):
        yield


class KeptCopyPins(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name

    def test_loads_without_a_save_keep_one_copy(self):
        for links in (True, False):
            with self.subTest(hard_links=links):
                store, chat = unreadable_chat(tempfile.mkdtemp(dir=self.folder))
                no_links = patch.object(session_journal.os, "link", side_effect=OSError(errno.EPERM, "no links"))
                with a_new_second_each_time(), (contextlib.nullcontext() if links else no_links):
                    for _ in range(5):  # A crash before the first save, five times.
                        SessionStore(store.directory, chat).load()
                self.assertEqual(len(kept(store)), 1)

    def test_failed_rewrites_keep_one_copy_and_the_chat_unchanged(self):
        store, chat = unreadable_chat(self.folder)
        original = store.path.read_bytes()
        with a_new_second_each_time():
            for _ in range(5):
                again = SessionStore(self.folder, chat)
                loaded = again.load()
                with patch.object(session_journal, "_write_all", side_effect=OSError(errno.ENOSPC, "full")):
                    with self.assertRaises(OSError):
                        again.save(loaded)
        self.assertEqual(len(kept(store)), 1)
        self.assertEqual(store.path.read_bytes(), original)

    def test_a_full_or_read_only_folder_never_writes_over_the_chat(self):
        """Measured on full HFS+, APFS and exFAT images and a read-only folder (journal_review/keep_disks.py):
        the link and the copy both fail, the chat refuses every save over the file, and closing still works."""
        store, chat = unreadable_chat(self.folder)
        original = store.path.read_bytes()
        real_open = os.open

        def full(path, flags, *args, **kwargs):
            if str(path).endswith(".tmp"):
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_open(path, flags, *args, **kwargs)
        with patch.object(session_journal.os, "link", side_effect=OSError(errno.ENOSPC, "full")), \
                patch.object(session_journal.os, "open", full):
            controller = Controller(self.folder, session_id=chat)
        self.assertTrue(controller.state["invocations"]["closed"])
        self.assertFalse(kept(store))
        controller.close()
        self.assertTrue(controller.persistence_warning)
        self.assertEqual(store.path.read_bytes(), original)

    def test_a_copy_that_failed_part_way_leaves_nothing_behind(self):
        """Regression: preserve's copy left its 0-byte temporary file until a load a minute later
        (discard_stale). Now a failed copy removes its temporary file and the
        kept name it had created (session_kept._copy_new)."""
        store, chat = unreadable_chat(self.folder)
        with patch.object(session_journal.os, "link", side_effect=OSError(errno.EPERM, "no links")), \
                patch.object(session_journal.os, "fsync", side_effect=OSError(errno.EIO, "I/O error")):
            loaded = SessionStore(self.folder, chat).load()
        self.assertTrue(loaded["invocations"]["closed"], "not kept: the chat refuses every save over the file")
        self.assertEqual(list(store.path.parent.glob("*.tmp")), [])
        self.assertEqual(kept(store), [])


class CorruptionPins(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        self.store = SessionStore(self.folder, str(uuid.uuid4()))
        self.state = self.store.new("claude_subscription")
        self.store.save(self.state)
        for index in range(8):
            self.state["messages"].append({"role": "user", "text": "Request %d" % index})
            reserve_invocation(self.state, self.state["id"] + "/c%d" % index, "%064x" % index, "('document', 1)")
            self.store.save(self.state)

    def test_a_corrupt_record_before_the_last_is_kept_and_the_chat_opens_empty_and_closed(self):
        """NOTE: the records before the corrupt one are readable but not shown; the kept copy holds them all."""
        lines = self.store.path.read_bytes().split(b"\n")
        lines[3] = lines[3].replace(b'"seq"', b'"seX"', 1)
        self.store.path.write_bytes(b"\n".join(lines))
        original = self.store.path.read_bytes()
        loaded = SessionStore(self.folder, self.state["id"]).load()
        self.assertTrue(loaded["invocations"]["closed"])
        self.assertFalse([m for m in loaded["messages"] if m["role"] == "user"])
        self.assertEqual([path.read_bytes() for path in kept(self.store)], [original])

    def test_a_newline_terminated_last_line_that_is_not_utf8_is_a_torn_write_and_not_kept(self):
        with self.store.path.open("ab") as handle:
            handle.write(b'{"seq": 9, "rows": {"n": 9, "at": [[8, {"role": "user", "text": "\xff"}]]}}\n')
        store = SessionStore(self.folder, self.state["id"])
        loaded = store.load()
        self.assertFalse(loaded["invocations"]["closed"])
        self.assertEqual(len(loaded["messages"]), 8)
        self.assertEqual(kept(self.store), [])


class LaterBuildBoundaryPins(unittest.TestCase):
    def test_nested_fields_a_later_build_adds_are_normalized_without_a_copy(self):
        """NOTE: ``KNOWN_FIELDS`` is checked at the top level only. A later build's option, task-memory or
        runtime-session field (or an approval mode this build lacks, read as Ask) is dropped by the first save
        here, and nothing is kept: a later build must add what an earlier one may not drop at the top level."""
        with tempfile.TemporaryDirectory() as folder:
            store = SessionStore(folder, str(uuid.uuid4()))
            state = store.new("claude_subscription")
            store.compact(state)
            raw = json.loads(store.path.read_text(encoding="utf-8"))
            raw["options"].update(later_setting=True, approval="SUPERVISED")
            raw["task_memory"]["later_field"] = {"x": 1}
            store.path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
            again = SessionStore(folder, state["id"])
            again.save(again.load())
            saved = json.loads(store.path.read_text(encoding="utf-8"))
            self.assertEqual(kept(store), [])
            self.assertNotIn("later_setting", saved["options"])
            self.assertEqual(saved["options"]["approval"], "ASK")
            self.assertNotIn("later_field", saved["task_memory"])


class PausePins(unittest.TestCase):
    def test_no_pause_sleeps_inside_a_store_writer_or_a_profile_transaction(self):
        """The worker's whole maintenance pass with pauses on: probes, accounting, retention under and over the
        count limit (a removal under the transaction), orphan probe, garbage collection and compaction."""
        from snapshot_fixtures import build_history
        from angelo_agent import snapshot_maintenance, snapshot_store_writer
        from angelo_agent.checkpoints import CheckpointStore
        from angelo_agent.snapshot_ingest import ingest_snapshot
        from angelo_agent.snapshot_retention import enforce_retention
        from angelo_agent.snapshot_store import SnapshotStore
        with tempfile.TemporaryDirectory() as folder:
            root = build_history(Path(folder) / "state", 24)
            for item in CheckpointStore(root, str(uuid.uuid4())).inspect()["checkpoints"]:
                ingest_snapshot(CheckpointStore(root, item["session_id"]), item["id"])
            inside, sleeps = threading.local(), []
            writer = SnapshotStore.writer

            @contextlib.contextmanager
            def tracked(self, *args, **kwargs):
                with writer(self, *args, **kwargs) as session:
                    inside.depth = getattr(inside, "depth", 0) + 1
                    try:
                        yield session
                    finally:
                        inside.depth -= 1

            def sleep(seconds):
                sleeps.append((getattr(inside, "depth", 0), checkpoint_transactions.holds_any_transaction()))
                time.sleep(seconds)

            def run():
                background_pauses.enable()
                background_pauses.WORK_SECONDS = 0  # Pause at every item: every place a pause can happen.
                checkpoints = CheckpointStore(root, str(uuid.uuid4()))
                for limit in (100, 10):
                    probe = {}
                    enforce_retention(checkpoints, {"max_count": limit, "max_bytes": 0, "max_age_days": 0},
                                      probe=probe)
                    snapshot_store_writer._COLLECTED.clear()
                    snapshot_maintenance.clean_store(root, probed=probe.get("inventory"))
            with patch.object(SnapshotStore, "writer", tracked), \
                    patch.object(background_pauses, "time", type("Time", (), {
                        "perf_counter": staticmethod(time.perf_counter), "sleep": staticmethod(sleep)})), \
                    patch.object(background_pauses, "WORK_SECONDS", background_pauses.WORK_SECONDS):
                thread = threading.Thread(target=run)
                thread.start()
                thread.join()
            self.assertTrue(sleeps, "the pass paused")
            self.assertEqual([state for state in sleeps if state != (0, False)], [])


if __name__ == "__main__":
    unittest.main()
