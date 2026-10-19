# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The conversation journal's whole-file rewrite: what every build reads, no partial file, the same durability.

The first save of a store, and every compaction, rewrites the whole
conversation (``session_writer.JournalWriter.rewrite``): the document every
earlier build wrote, encoded in one pass of Python's C encoder per row, as one
line, written to a temporary file, synced and swapped in. These tests pin what
must not change with the journal (``test_native_session_journal.py`` covers the
appended records): the values on disk, a compacted file that ``json.loads``
reads as before, the old file when a value cannot be saved, and the
write-sync-replace order.
"""

import enum
import json
import os
import stat
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import sessions  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402


class Label(str):
    """A str subclass, as display text can be."""


class Phase(enum.IntEnum):
    QUEUED = 1


def journal_state(store):
    state = store.new("claude_subscription")
    text = "Café ✓ 日本語 😀 مرحبا   tab\t newline\n quote\" backslash\\ nul\x00 unit\x1f"
    state["messages"] = [
        {"role": "user", "text": text, "checkpoint_scope": "before_request"},
        {"role": "assistant", "text": Label("Ein Würfel – fertig"), "provider": "claude_subscription"},
        {"role": "activity", "call_id": "chat/plan/1", "tool": "angelo_apply_recipe", "status": "completed",
         "arguments": {"recipe": "material-setup", "parameters": {"base_color": (0.25, 0.5, 0.75, 1),
                                                                  "roughness": 0.3, "metallic": 0.4}},
         "result": {"success": True, "phase": Phase.QUEUED,
                    "verification": {"status": "complete", "checks": [
                        {"kind": "transform", "observed": [0.1, 1e-05, 1e300, -0.0, 3.0, 1 / 3, 5e-324],
                         "counts": {1: 2**70, 2: True, 3: None}}]}}},
        {"role": "thinking", "text": "", "status": "completed", "duration_ms": 899},
    ]
    state["results"] = {"chat/plan/1": {"document": "('document', 1)", "invocation_digest": "a" * 64,
                                        "phase": "completed", "result": {"value": -12.5e-7, "name": "Évaluation"},
                                        "approved": False, "automatic": True}}
    return state


class SessionSaveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = SessionStore(self.directory.name, str(uuid.uuid4()))
        self.temporaries = lambda: sorted(self.store.path.parent.glob("*.tmp"))  # Each rewrite names its own.

    def test_a_rewrite_is_the_whole_document_on_one_line_that_every_build_reads(self):
        state = journal_state(self.store)
        self.store.save(state)
        self.assertEqual(self.store.last_write, "rewrite")
        data = self.store.path.read_bytes()
        self.assertEqual(data.count(b"\n"), 1, "one base line and its newline")
        self.assertTrue(data.endswith(b"\n"))
        saved = json.loads(data.decode("utf-8"))  # What an earlier build's json.loads reads.
        # The same values the streaming encoder wrote with the same arguments (ensure_ascii=False, allow_nan=False).
        expected = json.loads(json.dumps(dict(
            {key: state[key] for key in ("version", "id", "provider", "native_id", "connection_id", "model",
                                         "messages", "results", "updated")},
            options=sessions.stored_options(state.get("options")), toolset_version=state["toolset_version"],
            runtime_sessions={}, context_slot=None, next_route=None,
            task_memory=sessions.clean_memory(state["task_memory"],
                                              profile_id=str(Path(self.directory.name).resolve())),
            invocations=state["invocations"], approval_version=state["approval_version"]),
            ensure_ascii=False, allow_nan=False))
        self.assertEqual(saved, expected)
        self.assertEqual(list(saved)[-1], "messages", "rows are last: the base can be built from row bytes")
        self.assertEqual(saved["messages"][0]["text"], journal_state(self.store)["messages"][0]["text"])
        self.assertEqual(saved["messages"][2]["result"]["verification"]["checks"][0]["counts"]["1"], 2**70)
        self.assertIn("Café ✓ 日本語".encode("utf-8"), data, "UTF-8, not escaped ASCII")
        self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)

    def test_a_value_that_cannot_be_saved_leaves_no_temporary_file_and_the_old_journal(self):
        state = journal_state(self.store)
        self.store.save(state)
        for kind in ("rewrite", "append"):
            for value, error in ((float("nan"), ValueError), (object(), TypeError),
                                 ("lone \ud800 surrogate", UnicodeEncodeError)):
                with self.subTest(kind=kind, error=error.__name__):
                    if kind == "rewrite":
                        self.store.invalidate()  # The next save rewrites the whole file.
                    else:
                        self.store.save(state)  # Written: the next save appends.
                    before = self.store.path.read_bytes()
                    state["messages"].append({"role": "assistant", "text": value})
                    with self.assertRaises(error):
                        self.store.save(state)
                    state["messages"].pop()
                    self.assertEqual(self.temporaries(), [])
                    self.assertEqual(self.store.path.read_bytes(), before)
        state["messages"].append({"role": "assistant", "text": "Saved after the failures"})
        self.store.save(state)  # The next save describes the state the failed ones never wrote.
        saved = sessions.read_transcript(self.store.path)["messages"]
        self.assertEqual(saved, json.loads(json.dumps(state["messages"])))

    def test_the_temporary_file_is_synced_before_it_replaces_the_journal(self):
        steps, fsync, replace = [], os.fsync, os.replace

        def synced(descriptor):
            steps.append(("fsync", os.fstat(descriptor).st_size))
            fsync(descriptor)

        def replaced(source, target):
            steps.append(("replace", Path(source), Path(target)))
            replace(source, target)
        with patch.object(sessions.os, "fsync", synced), patch.object(sessions.os, "replace", replaced):
            self.store.save(journal_state(self.store))
        size = self.store.path.stat().st_size
        self.assertEqual([step[0] for step in steps], ["fsync", "replace"])
        self.assertEqual(steps[0][1], size)
        temporary = steps[1][1]
        self.assertEqual((temporary.parent, steps[1][2]), (self.store.path.parent, self.store.path))
        self.assertTrue(temporary.name.startswith(self.store.path.name + ".") and temporary.suffix == ".tmp")
        self.assertEqual(self.temporaries(), [])

    def test_two_rewrites_never_share_a_temporary_file_and_a_crash_leftover_is_removed(self):
        names, replace = [], os.replace

        def recorded(source, target):
            names.append(Path(source).name)
            replace(source, target)
        with patch.object(sessions.os, "replace", recorded):
            self.store.save(journal_state(self.store))
            self.store.invalidate()
            self.store.save(journal_state(self.store))
        self.assertEqual(len(set(names)), 2)
        leftover = self.store.path.parent / (self.store.path.name + ".deadbeef.tmp")
        leftover.write_bytes(b"{")
        os.utime(leftover, (1, 1))  # Long ago: a crashed rewrite's.
        SessionStore(self.directory.name, self.store.session_id).load()
        self.assertFalse(leftover.exists())


if __name__ == "__main__":
    unittest.main()
