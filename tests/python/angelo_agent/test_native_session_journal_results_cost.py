# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A save's Python work never grows with the results table it compares.

Results (at most 256 records) are compared by identity on every save (``session_writer``). Before the fix each save
walked the whole table in Python several times (removed keys, changed values, their order, the copy kept): about
1,500 instructions per record, so an edit's admission save cost 1.2x as much once the table held its 256 records as
at edit 50 (release candidate 00110f294d4: ``benchmark_blender_session_journal.py``, "admission_save grew over the
session"). Here the Python lines a save runs in the journal's own modules are counted, for a table of 32 records and
of 200, in every shape a save meets: one record replaced (and marked, as ``tool_finalization.put_result`` marks it),
one added, the oldest dropped by the cap, and nothing changed. The counts must match; the file must still reproduce
memory. A record removed from the middle (no product path does it) and a replacement without its mark are saved too.
"""

import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import session_journal, session_writer, sessions  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402

JOURNAL_FILES = tuple(str(Path(module.__file__).resolve()) for module in (session_writer, sessions, session_journal))


def record(index, phase="completed"):
    return {"document": "('benchmark-document', 1)", "phase": phase, "result": {"success": True, "index": index}}


def lines_run(function):
    """The Python lines ``function()`` runs in the journal's modules."""
    count = 0

    def local(frame, event, arg):
        nonlocal count
        count += event == "line"
        return local

    def tracer(frame, event, arg):
        return local if frame.f_code.co_filename in JOURNAL_FILES else None
    sys.settrace(tracer)
    try:
        function()
    finally:
        sys.settrace(None)
    return count


class ResultsTableCost(unittest.TestCase):
    def setUp(self):
        for module, name in ((sessions, "VERIFY_JOURNAL"), (session_writer, "CHECK_TOUCHES")):
            check = patch.object(module, name, False)   # Both checks walk the whole file or every row.
            check.start()
            self.addCleanup(check.stop)

    def session(self, size):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        store = SessionStore(folder.name, str(uuid.uuid4()))
        state = store.new("claude_subscription")
        state["results"].update(("call-%d" % index, record(index)) for index in range(size))
        store.save(state)
        store.save(state)   # The first save after a load rewrites; this one appends.
        self.folder = folder.name
        return store, state

    def cost(self, size, change):
        store, state = self.session(size)
        change(state["results"], size, store)
        lines = lines_run(lambda: store.save(state))
        with patch.object(sessions, "VERIFY_JOURNAL", True):   # The file reproduces memory.
            store.save(state)
        self.assertEqual(SessionStore(self.folder, state["id"]).load()["results"], state["results"])
        return lines

    def assert_flat(self, change):
        small, large = self.cost(32, change), self.cost(200, change)
        self.assertEqual(large, small, "a save ran %d Python lines with 200 records, %d with 32" % (large, small))

    def test_a_replaced_record(self):
        def change(results, size, store):
            results["call-%d" % (size - 1)] = record(size, "staging")
            store.touch_result("call-%d" % (size - 1))   # As every replacement (tool_finalization.put_result).
        self.assert_flat(change)

    def test_an_added_record(self):
        self.assert_flat(lambda results, size, store: results.__setitem__("call-new", record(size, "queued")))

    def test_the_oldest_dropped_by_the_cap_and_one_added(self):
        def change(results, size, store):
            results.pop(next(iter(results)))
            results["call-new"] = record(size)
        self.assert_flat(change)

    def test_nothing_changed(self):
        self.assert_flat(lambda results, size, store: None)

    def test_a_record_removed_from_the_middle_is_still_saved(self):
        # Not a shape a save meets in the product (records leave by the cap, from the front): correct, not flat.
        store, state = self.session(16)
        del state["results"]["call-7"]
        state["results"]["call-new"] = record(16)
        with patch.object(sessions, "VERIFY_JOURNAL", True):
            store.save(state)
        self.assertEqual(SessionStore(self.folder, state["id"]).load()["results"], state["results"])


    def test_a_replacement_without_its_mark_is_named_by_the_check_and_found_by_the_scrub(self):
        store, state = self.session(64)
        state["results"]["call-40"] = record(40, "staging")   # No touch_result.
        with patch.object(session_writer, "CHECK_TOUCHES", True), self.assertRaisesRegex(AssertionError, "call-40"):
            store.save(state)
        saves = 0
        while SessionStore(self.folder, state["id"]).load()["results"]["call-40"]["phase"] != "staging":
            store.save(state)
            saves += 1
            self.assertLessEqual(saves, -(-64 // session_writer.RESULT_SCRUB), "the scrub's bound")


    def test_an_unmarked_replacement_is_found_while_the_cap_drops_a_record_every_save(self):
        # Break-it review: the scrub's place must follow the records the cap drops from the front, and a cycle must
        # end at the table's end (it once stayed on the last record for good).
        for target in (5, 40, 63):
            store, state = self.session(64)
            results, key, added = state["results"], "call-%d" % target, 64
            results[key] = record(target, "staging")   # No touch_result.
            for saves in range(1, 2 * -(-64 // session_writer.RESULT_SCRUB) + 1):
                if next(iter(results)) == key:
                    break   # Dropped by the cap from memory and the file alike.
                results.pop(next(iter(results)))
                results["call-%d" % added] = record(added)
                store.touch_result("call-%d" % added)
                added += 1
                store.save(state)
                if SessionStore(self.folder, state["id"]).load()["results"][key]["phase"] == "staging":
                    break
            else:
                self.fail("record %s not written within two scrub cycles" % key)

if __name__ == "__main__":
    unittest.main()
