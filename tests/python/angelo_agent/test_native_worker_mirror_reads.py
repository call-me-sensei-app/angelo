# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How a worker chat's mirror reads its file and draws its typed-edit cards (A′ fix loop FC1). No Blender.

Regression tests for the break-it reviews of 086452d1bdb (``release-1.0.6.noindex/review-concurrency`` F5,
``review-ui`` items 1 and 12):

* C5: a read that fails (an I/O error) keeps what the mirror shows and is tried again; content the reader does not
  understand still shows as a load shows it.
* U12: after each worker write only the new records are read; the whole file only when the journal frame says
  ``records == 0`` (or gives no count), the file's identity changed, a record is torn, or the bytes before the offset
  differ. Whichever path read it, the mirror's state equals a load of the file.
* U1: an ``angelo_scene_program`` / ``angelo_apply_recipe`` call waiting for the person draws its card: the review is
  prepared on the lead from the pending summary (program arguments, precondition, recovery fields).
"""

import contextlib
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (approval_review as review, session_journal, sessions, worker_mirror_cards,  # noqa: E402
                          worker_values)
from angelo_agent.program_context import ProgramBinding  # noqa: E402
from angelo_agent.worker_chat_events import plain  # noqa: E402
from angelo_agent.worker_mirror_journal import MirrorStore  # noqa: E402
from test_native_complete_checks import maximum_material_program  # noqa: E402
from test_native_review_preparation import call_for, drain  # noqa: E402
from test_native_worker_mirror import TURN, Fixture  # noqa: E402


class Reads(Fixture):
    def write(self, *rows, compact=False, reading=None):
        """The worker adds rows and saves; the journal frame carries the records since the last rewrite. ``reading``
        (a context manager, e.g. a patch that makes reads fail) wraps only the mirror's read of the frame: the worker
        and the mirror share this process, and the worker's own save reads its file back (``sessions.VERIFY_JOURNAL``,
        on under run_host_tests), so a patch around the save would fail the worker instead of the mirror."""
        self.worker.state["messages"].extend(rows)
        self.worker.save(compact=compact)
        self.seq += 1
        records = self.worker.store._writer.records
        with reading or contextlib.nullcontext():
            self.frame("journal", {"seq": self.seq, "path": str(self.worker.store.path), "records": records})
            self.mirror.pump_events()

    def store(self):
        return self.mirror.store._store

    def assert_equals_load(self):
        self.assertEqual(self.mirror.state, self.loaded().state)


class IncrementalReadTests(Reads):
    """U12."""

    def test_only_new_records_are_read_after_a_write(self):
        self.write({"role": "user", "text": "first"}, compact=True)
        whole = self.store().whole_reads
        for index in range(5):
            self.write({"role": "assistant", "text": "reply %d" % index})
            self.assert_equals_load()
        self.assertEqual(self.store().whole_reads, whole)
        self.assertEqual(self.store().tail_reads, 5)

    def test_a_rewrite_says_records_zero_and_is_read_whole(self):
        self.write({"role": "user", "text": "first"})
        self.write({"role": "assistant", "text": "second"})
        whole = self.store().whole_reads
        self.write({"role": "assistant", "text": "third"}, compact=True)   # records == 0
        self.assertEqual(self.store().whole_reads, whole + 1)
        self.assert_equals_load()

    def test_a_frame_without_a_count_is_read_whole(self):
        self.write({"role": "user", "text": "first"})
        whole = self.store().whole_reads
        self.worker.state["messages"].append({"role": "assistant", "text": "x"})
        self.worker.save()
        self.frame("journal", {"seq": 99})
        self.mirror.pump_events()
        self.assertEqual(self.store().whole_reads, whole + 1)
        self.assert_equals_load()

    def test_a_replaced_file_with_a_high_count_is_read_whole(self):
        self.write({"role": "user", "text": "first"})
        self.write({"role": "assistant", "text": "second"})
        self.worker.save(compact=True)   # Rewritten (a new inode), then more records than were read before.
        for text in ("a", "b", "c"):
            self.write({"role": "assistant", "text": text})
        self.assert_equals_load()

    def test_a_reused_inode_is_caught_by_the_bytes_before_the_offset(self):
        self.write({"role": "user", "text": "first"})
        self.write({"role": "assistant", "text": "second"})
        tail = self.store()._tail
        tail.overlap = b"x" * len(tail.overlap)   # As if the same inode now held another file's bytes there.
        whole = self.store().whole_reads
        self.write({"role": "assistant", "text": "third"})
        self.assertEqual(self.store().whole_reads, whole + 1)
        self.assert_equals_load()

    def test_a_torn_last_record_reads_the_whole_file_without_it(self):
        self.write({"role": "user", "text": "first"})
        with open(self.worker.store.path, "ab") as handle:
            handle.write(b'{"seq": 9, "rows": ')   # A record still being written.
        whole = self.store().whole_reads
        self.frame("journal", {"seq": 50, "records": 5})
        self.mirror.pump_events()
        self.assertEqual(self.store().whole_reads, whole + 1)
        self.assertEqual(self.mirror.state["messages"][-1]["text"], "first")

    def test_a_record_that_does_not_apply_drops_what_was_kept(self):
        self.write({"role": "user", "text": "first"})
        with open(self.worker.store.path, "ab") as handle:
            handle.write(b'{"seq": 77}\n')   # Out of sequence: corruption, as a load reads it.
        self.frame("journal", {"seq": 50, "records": 9})
        self.mirror.pump_events()
        self.assertIsNone(self.store()._tail)
        self.assertEqual(self.mirror.state["messages"][-1]["text"], sessions.HISTORY_WARNING)

    def test_a_row_changed_in_place_by_a_display_event_never_reaches_the_next_read(self):
        self.write({"role": "user", "text": "first"}, compact=True)
        self.mirror.begin_turn()
        self.mirror.state["messages"][0]["text"] = "changed on the lead's display"
        self.write({"role": "assistant", "text": "second"})
        self.assertEqual(self.mirror.state["messages"][0]["text"], "first")
        self.assert_equals_load()

    def test_rows_and_results_equal_a_load_through_many_appends(self):
        for index in range(30):
            self.worker.state["results"]["call-%d" % index] = {"document": "d", "result": {"n": index}}
            self.write({"role": "activity", "call_id": "call-%d" % index, "tool": "angelo_create_cube",
                        "arguments": {"i": index}, "result": {"ok": True}, "status": "completed", "text": ""})
            if index % 7 == 3:
                self.worker.state["messages"][index - 2] = dict(self.worker.state["messages"][index - 2], text="edited")
                self.write()
        self.assert_equals_load()


class DeleteSharingTests(Reads):
    """FD's C11 in the mirror: each of its reads opens the file sharing delete (``session_journal.open_shared``), so
    an open mirror read never stops the worker's compaction replacing the file on Windows."""

    def test_whole_and_tail_reads_open_through_open_shared(self):
        shared, real = [], session_journal.open_shared
        builtin = open

        def guarded(target, *args, **kwargs):
            if not isinstance(target, int) and Path(target) == Path(self.mirror.store.path):
                raise AssertionError("the mirror opened its chat's file without delete sharing")
            return builtin(target, *args, **kwargs)

        def recording(path, follow=True):
            shared.append(Path(path))
            return real(path, follow)

        @contextlib.contextmanager
        def reading():   # Only the mirror's reads: the worker's own saves (and their read-back) are not under test.
            with mock.patch.object(session_journal, "open_shared", recording), mock.patch("builtins.open", guarded):
                yield

        self.write({"role": "user", "text": "first"}, compact=True, reading=reading())
        self.write({"role": "assistant", "text": "reply"}, reading=reading())
        self.assertGreaterEqual(self.store().whole_reads, 1)
        self.assertGreaterEqual(self.store().tail_reads, 1)
        self.assertIn(Path(self.mirror.store.path), shared)
        self.assert_equals_load()


class FailedReadTests(Reads):
    """C5 (review-concurrency F5)."""

    def test_a_read_error_keeps_what_is_shown_and_reads_again(self):
        self.write({"role": "user", "text": "build a house"})
        shown = [dict(row) for row in self.mirror.state["messages"]]
        failing = mock.patch.object(session_journal, "read", side_effect=PermissionError(13, "sharing violation"))
        self.write({"role": "assistant", "text": "on it"}, compact=True, reading=failing)
        self.assertEqual(self.mirror.state["messages"], shown)
        self.assertFalse(self.mirror.state["invocations"]["closed"])
        self.assertIsNotNone(self.mirror.reload_retry)
        self.mirror.reload_retry = time.monotonic()   # Due now.
        self.mirror.pump_events()   # No journal frame: the retry reads it.
        self.assertIsNone(self.mirror.reload_retry)
        self.assert_equals_load()

    def test_a_file_that_vanished_for_a_moment_keeps_what_is_shown(self):
        self.write({"role": "user", "text": "build a house"})
        os.replace(self.worker.store.path, str(self.worker.store.path) + ".aside")
        self.frame("journal", {"seq": 9, "records": 0})
        self.mirror.pump_events()
        self.assertEqual(self.mirror.state["messages"][-1]["text"], "build a house")
        os.replace(str(self.worker.store.path) + ".aside", self.worker.store.path)
        self.mirror.reload_retry = time.monotonic()
        self.mirror.pump_events()
        self.assert_equals_load()

    def test_content_it_cannot_verify_still_shows_as_a_load_does(self):
        self.write({"role": "user", "text": "build a house"})
        Path(self.worker.store.path).write_text("not json\n")
        self.frame("journal", {"seq": 9, "records": 0})
        self.mirror.pump_events()
        self.assertEqual(self.mirror.state["messages"][-1]["text"], sessions.HISTORY_WARNING)
        self.assertIsNone(self.mirror.reload_retry)


class ProgramCardTests(Fixture):
    """U1 (review-ui item 1): what the worker sends for the call, as its summary carries it
    (``worker_chat_events.review_fields``: exact host values, ``worker_values.encode``)."""

    def summary(self, worker_call, **extra):
        return dict({"arguments": plain(worker_call.arguments), "approval": True,
                     "program_arguments": plain(worker_call.program_arguments),
                     "recovery_review": bool(worker_call.recovery_review),
                     "precondition": worker_values.encode(worker_call.precondition),
                     "recovery_evidence": worker_values.encode(worker_call.recovery_evidence)}, **extra)

    def shown(self, worker_call, **extra):
        self.pending({"id": "c1", "name": worker_call.name, "digest": "d" * 64, "needs_person": True,
                      "expires": 120, "summary": self.summary(worker_call, **extra)})
        self.mirror.pump_events()
        return self.mirror.pending["c1"]

    def test_a_scene_program_card_draws_as_the_worker_would(self):
        worker_call = call_for()
        rows, _details = drain(worker_call)
        call = self.shown(worker_call)
        self.assertIsNotNone(review._current_review(call))
        self.assertEqual(drain(call), (rows, _details))

    def test_a_recipe_card_shows_its_expanded_program(self):
        worker_call = call_for(recipe=True)
        call = self.shown(worker_call)
        self.assertEqual(call.program_arguments, plain(worker_call.program_arguments))
        self.assertTrue(drain(call)[0])

    def test_a_material_program_draws_from_its_bound_obligations(self):
        worker_call = call_for(maximum_material_program())
        call = self.shown(worker_call)
        self.assertEqual(call.precondition, worker_call.precondition)
        self.assertEqual(review._current_review(call).checks_digest, worker_call.precondition.checks_digest)
        self.assertEqual(drain(call), drain(worker_call))

    def test_a_recovery_review_carries_its_fields(self):
        worker_call = call_for()
        call = self.shown(worker_call, recovery_review=True,
                          recovery_evidence=worker_values.encode({"snapshot": "s1"}))
        self.assertTrue(call.recovery_review)
        self.assertEqual(call.recovery_evidence, {"snapshot": "s1"})
        self.assertIsNotNone(review._current_review(call))

    def assert_no_review(self):
        self.assertIsNone(review._current_review(self.mirror.pending["c1"]))
        self.assertEqual(self.mirror.state["messages"][-1]["role"], "error")
        self.assertIn(worker_mirror_cards.NO_REVIEW.split(" (")[0][:40], self.mirror.state["messages"][-1]["text"])

    def test_checks_that_differ_from_the_binding_draw_no_card_and_say_so(self):
        worker_call = call_for()
        other = call_for(maximum_material_program()).precondition.obligations
        worker_call.precondition = ProgramBinding(*worker_call.precondition[:-1], other)
        self.shown(worker_call)
        self.assert_no_review()

    def test_a_value_its_worker_could_not_send_draws_no_card_and_says_so(self):
        self.shown(call_for(), precondition=None, unencoded=["precondition"])
        self.assert_no_review()

    def test_a_value_that_does_not_decode_draws_no_card_and_says_so(self):
        self.shown(call_for(), precondition={"pb": "not a list"})
        self.assert_no_review()

    def test_an_unchanged_card_keeps_its_review(self):
        worker_call = call_for()
        call = self.shown(worker_call)
        prepared = review._current_review(call)
        self.assertIs(review._current_review(self.shown(worker_call)), prepared)


def setUpModule():   # Journal reads run inline here, as each pump asks; the reader thread has its own tests.
    from angelo_agent import worker_mirror_reader
    worker_mirror_reader.bind(inline=True)


def tearDownModule():
    from angelo_agent import worker_mirror_reader
    worker_mirror_reader.reset()


if __name__ == "__main__":
    unittest.main()
