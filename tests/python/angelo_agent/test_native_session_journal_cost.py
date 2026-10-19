# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A conversation save costs the current request and what changed, never the whole session.

Before the append-only journal every save encoded, wrote and synced the whole
conversation (3.6 MB after 627 tasks) and revalidated the whole mutation-ID
ledger, four times per edit on the main thread and once more on the provider
thread under the controller lock (doc/angelo/performance.rst, "Per-edit cost
over a long session"). These counts pin the replacement on any host: the rows
a save encodes, the bytes it appends and the whole-ledger validations it runs
are the same after 2,000 rows and 2,000 mutation IDs as after 20. The instruction ratchet
(``conversation_journal/*`` in ``benchmark_blender_session_journal.py``) pins
the time.
"""

import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import session_journal, session_writer, sessions  # noqa: E402
from angelo_agent.invocations import reserve_invocation  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402


def activity(index, status="completed"):
    return {"role": "activity", "call_id": "call-%d" % index, "tool": "angelo_scene_program", "text": "",
            "arguments": {"mode": "APPLY",
                          "operations": [{"kind": "transform", "parameters": {"location": [index, 0, 0]}}]},
            "result": {"success": True, "objects": [{"name": "Eval Cube", "handle": "h%d" % index}]}, "status": status}


class Counts:
    """Rows encoded, bytes encoded and whole-ledger validations while it is active."""

    def __init__(self):
        self.rows = self.encoded = self.ledgers = 0
        encode, clean = session_journal.encode, sessions.clean_invocations

        def counted_encode(value):
            data = encode(value)
            self.rows += isinstance(value, dict) and "role" in value
            self.encoded += len(data)
            return data

        def counted_clean(value):
            self.ledgers += 1
            return clean(value)
        self.patches = (patch.object(session_journal, "encode", counted_encode),
                        patch.object(sessions, "clean_invocations", counted_clean),
                        patch.object(sessions, "VERIFY_JOURNAL", False))  # Verification reads the whole file.

    def __enter__(self):
        for item in self.patches:
            item.start()
        return self

    def __exit__(self, *exc):
        for item in self.patches:
            item.stop()


class SaveCostTests(unittest.TestCase):
    def session(self, rows):
        """A store whose chat already holds ``rows`` rows, as many mutation IDs and 256 results."""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        store = SessionStore(folder.name, str(uuid.uuid4()))
        state = store.new("claude_subscription")
        for index in range(rows):
            state["messages"].append(activity(index))
            reserve_invocation(state, state["id"] + "/call-%d" % index, "%064x" % index, "('document', 1)")
            state["results"]["call-%d" % index] = {"document": "d", "phase": "completed", "result": {"index": index}}
            while len(state["results"]) > 256:
                state["results"].pop(next(iter(state["results"])))
        store.save(state)  # The first save after opening rewrites the whole file once.
        return store, state

    def edit(self, store, state, index):
        """One edit's saves: the request's user row, admission (row, result, mutation ID), commit, completion."""
        store.seal(len(state["messages"]))
        state["messages"].append({"role": "user", "text": "Move it to %d" % index})
        store.save(state)
        key = state["id"] + "/next-%d" % index
        reserve_invocation(state, key, "%064x" % (10 ** 6 + index), "('document', 1)")
        state["results"][key] = {"document": "d", "phase": "queued", "result": {}}
        state["messages"].append(activity(10 ** 6 + index, "queued"))
        store.save(state)
        state["results"][key] = {"document": "d", "phase": "commit_started", "result": {}}
        store.save(state)
        state["results"][key] = {"document": "d", "phase": "completed", "result": {"success": True}}
        state["messages"][-1] = activity(10 ** 6 + index)
        store.save(state)

    def measure(self, rows):
        store, state = self.session(rows)
        self.edit(store, state, 0)  # Warm: the seal and the window settle.
        before = store.path.stat().st_size
        with Counts() as counts:
            self.edit(store, state, 1)
        self.assertEqual(store.last_write, "append")
        return counts, store.path.stat().st_size - before

    def test_an_edits_saves_cost_the_same_after_2000_rows_as_after_20(self):
        small, small_bytes = self.measure(20)
        large, large_bytes = self.measure(2000)
        # Rows: each save's request rows (the previous request's two once more in the first save, then this
        # request's one or two) plus SCRUB_ROWS older ones per save; never the history.
        self.assertLessEqual(large.rows, (3 + 2 + 2 + 2) + 4 * session_writer.SCRUB_ROWS)
        self.assertLessEqual(large.rows, small.rows + 4 * session_writer.SCRUB_ROWS)
        self.assertLess(large.encoded, small.encoded + 4 * session_writer.SCRUB_ROWS * 1024)
        # What an edit appends does not depend on the history, but for the digits of row numbers and counts.
        self.assertLessEqual(abs(large_bytes - small_bytes), 64)
        self.assertEqual((small.ledgers, large.ledgers), (0, 0), "no save revalidates the whole ledger")

    def test_the_whole_file_is_written_only_by_a_rewrite(self):
        store, state = self.session(500)
        size = store.path.stat().st_size
        for index in range(30):
            self.edit(store, state, index)
            self.assertEqual(store.last_write, "append")
        self.assertLess(store.path.stat().st_size - size, size, "thirty edits append less than the history")


if __name__ == "__main__":
    unittest.main()
