# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A hosted chat's journal has one writer (design A', C7): ``worker_journal_owner``.

The worker's store and Angelo's stores share this process here; in the product they are two processes. A real
``Controller`` (no provider, no Blender) writes the worker's journal records.
"""

import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import session_journal, sessions, worker_journal_owner as owner  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402
from angelo_agent.tool_finalization import journal_tool  # noqa: E402


class WorkerController(owner.HostedJournal, Controller):
    """The worker's hosted chat."""


def _row(state, text):
    state["messages"].append({"role": "user", "text": text})


class Base(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.directory = self.folder.name
        self.session = str(uuid.uuid4())
        self.addCleanup(self._forget)
        seed = SessionStore(self.directory, self.session)
        state = seed.load()
        _row(state, "first")
        seed.save(state)
        self.path = seed.path

    def _forget(self):
        owner._hosts.pop(self.session, None)
        sessions.HOSTED.pop(self.session, None)

    def records(self):
        return session_journal.read(self.path)[1].records


class HandOffTests(Base):
    def test_lead_never_writes_while_a_worker_owns_the_file(self):
        lead = SessionStore(self.directory, self.session)
        state = lead.load()
        owner.hand_off(self.session, ended=lambda: False)
        _row(state, "lead edit")
        with self.assertRaises(session_journal.JournalHosted):
            lead.save(state)
        with self.assertRaises(session_journal.JournalHosted):
            lead.compact(state)
        fresh = SessionStore(self.directory, self.session)   # A store made during the hand-off is refused too.
        with self.assertRaises(session_journal.JournalHosted):
            fresh.save(fresh.load())
        worker = owner.OwnedSessionStore(self.directory, self.session)
        hosted = worker.load()
        _row(hosted, "worker edit")
        worker.save(hosted)
        _row(hosted, "worker edit 2")
        worker.save(hosted)
        self.assertEqual(worker.last_write, "append")
        texts = [row["text"] for row in sessions.read_transcript(self.path)["messages"]]
        self.assertEqual(texts, ["first", "worker edit", "worker edit 2"])

    def test_ownership_returns_after_chat_close_and_only_to_a_store_loaded_since(self):
        lead = SessionStore(self.directory, self.session)
        stale = lead.load()
        owner.hand_off(self.session, ended=lambda: False)
        worker = owner.OwnedSessionStore(self.directory, self.session)
        hosted = worker.load()
        _row(hosted, "worker edit")
        worker.save(hosted)
        worker.release()
        with self.assertRaises(owner.JournalReleased):
            worker.save(hosted)
        owner.close_answered(self.session)
        self.assertFalse(owner.hosted(self.session))
        _row(stale, "from stale memory")
        with self.assertRaises(session_journal.JournalHosted):   # Would erase the worker's edit.
            lead.save(stale)
        state = lead.load()
        _row(state, "lead again")
        lead.save(state)
        texts = [row["text"] for row in sessions.read_transcript(self.path)["messages"]]
        self.assertEqual(texts, ["first", "worker edit", "lead again"])

    def test_a_second_hand_off_of_a_hosted_chat_is_refused(self):
        owner.hand_off(self.session, ended=lambda: False)
        with self.assertRaises(owner.JournalTaken):
            owner.hand_off(self.session, ended=lambda: False)
        with self.assertRaises(TypeError):
            owner.hand_off(str(uuid.uuid4()), ended=None)

    def test_worker_ended_changes_nothing_while_its_process_runs(self):
        alive = [True]
        owner.hand_off(self.session, ended=lambda: not alive[0])
        with self.assertRaises(session_journal.JournalHosted):
            owner.worker_ended(self.session, self.directory)
        self.assertTrue(owner.hosted(self.session))
        alive[0] = False
        recovered = owner.worker_ended(self.session, self.directory)
        self.assertFalse(owner.hosted(self.session))
        self.assertTrue(recovered.saved)
        with self.assertRaises(session_journal.JournalHosted):   # Not hosted any more.
            owner.worker_ended(self.session, self.directory)
        with self.assertRaises(session_journal.JournalHosted):
            owner.close_answered(self.session)

    def test_a_chat_never_handed_off_saves_as_before(self):
        store = SessionStore(self.directory, self.session)
        state = store.load()
        _row(state, "a")
        store.save(state)
        _row(state, "b")
        store.save(state)
        self.assertEqual(store.last_write, "append")


class InterleavedWriterTests(Base):
    def _hosted(self):
        worker = owner.OwnedSessionStore(self.directory, self.session)
        state = worker.load()
        _row(state, "worker 1")
        worker.save(state)
        return worker, state

    def _assert_unchanged_by(self, worker, state, before):
        _row(state, "worker 2")
        with self.assertRaises(owner.JournalTaken):
            worker.save(state)
        self.assertEqual(self.path.read_bytes(), before)   # Nothing written over the other writer's change.
        with self.assertRaises(owner.JournalTaken):   # And it stays refused.
            worker.save(state)

    def test_a_plain_store_never_rewrites_over_another_writer(self):
        """``FileChanged`` once made a plain store rewrite from memory, erasing the other writer's row (L5's
        finding); every store now refuses as an owned one does (``session_guard``)."""
        first = SessionStore(self.directory, self.session)
        state = first.load()
        _row(state, "one")
        first.save(state)
        other = SessionStore(self.directory, self.session)
        theirs = other.load()
        _row(theirs, "other writer")
        other.save(theirs)
        _row(state, "two")
        with self.assertRaises(owner.JournalTaken):
            first.save(state)
        texts = [row["text"] for row in sessions.read_transcript(self.path)["messages"]]
        self.assertEqual(texts, ["first", "one", "other writer"])

    def test_another_writers_rewrite_is_detected(self):
        worker, state = self._hosted()
        other = SessionStore(self.directory, self.session)
        theirs = other.load()
        _row(theirs, "other writer")
        other.save(theirs)   # Its first save rewrites.
        self._assert_unchanged_by(worker, state, self.path.read_bytes())

    def test_another_writers_append_is_detected(self):
        worker, state = self._hosted()
        other = SessionStore(self.directory, self.session)
        theirs = other.load()
        other.save(theirs)
        _row(theirs, "appended by the other writer")
        other.save(theirs)
        self.assertEqual(other.last_write, "append")
        self._assert_unchanged_by(worker, state, self.path.read_bytes())

    def test_a_raw_append_between_two_saves_is_detected(self):
        worker, state = self._hosted()
        info = session_journal.read(self.path)[1]
        with open(self.path, "ab") as handle:
            handle.write(session_journal.encode({"seq": info.records + 1, "set": {"model": "x"}}) + b"\n")
        self._assert_unchanged_by(worker, state, self.path.read_bytes())

    def test_a_replaced_or_removed_file_is_detected(self):
        worker, state = self._hosted()
        data = self.path.read_bytes()
        self.path.unlink()
        self.path.write_bytes(data)   # Same bytes, another file.
        self._assert_unchanged_by(worker, state, data)
        self.path.unlink()
        with self.assertRaises(owner.JournalTaken):
            worker.save(state)
        self.assertFalse(self.path.exists())

    def test_a_second_link_to_the_file_is_metadata_only(self):
        """A link changes no byte of the chat: the owner goes on, and its rewrite leaves the other name as it was."""
        worker, state = self._hosted()
        alias = Path(str(self.path) + ".elsewhere")
        os.link(self.path, alias)
        before = alias.read_bytes()
        _row(state, "worker 2")
        worker.save(state)
        self.assertEqual(alias.read_bytes(), before)
        texts = [row["text"] for row in sessions.read_transcript(self.path)["messages"]]
        self.assertEqual(texts, ["first", "worker 1", "worker 2"])

    def test_two_owners_the_first_writer_wins_and_the_second_writes_nothing(self):
        first = owner.OwnedSessionStore(self.directory, self.session)
        second = owner.OwnedSessionStore(self.directory, self.session)
        mine, theirs = first.load(), second.load()
        _row(mine, "first owner")
        first.save(mine)
        before = self.path.read_bytes()
        _row(theirs, "second owner")
        with self.assertRaises(owner.JournalTaken):
            second.save(theirs)
        self.assertEqual(self.path.read_bytes(), before)
        _row(mine, "first owner again")
        first.save(mine)
        texts = [row["text"] for row in sessions.read_transcript(self.path)["messages"]]
        self.assertEqual(texts, ["first", "first owner", "first owner again"])

    def test_a_change_during_the_load_refuses_to_host(self):
        worker = owner.OwnedSessionStore(self.directory, self.session)
        real = session_journal.read

        def read_then_change(path):
            value = real(path)
            with open(path, "ab") as handle:   # Another writer's record, while this one read.
                handle.write(session_journal.encode({"seq": value[1].records + 1, "set": {"model": "x"}}) + b"\n")
            return value
        with patch.object(session_journal, "read", read_then_change):
            with self.assertRaises(owner.JournalTaken):
                worker.load()

    def test_a_new_chat_file_is_created_by_its_owner(self):
        session = str(uuid.uuid4())
        worker = owner.OwnedSessionStore(self.directory, session)
        state = worker.load()
        _row(state, "hello")
        worker.save(state)
        self.assertEqual(sessions.read_transcript(worker.path)["messages"][0]["text"], "hello")

    def test_compaction_by_the_owner_is_guarded_too(self):
        worker, state = self._hosted()
        _row(state, "worker 2")
        worker.save(state)
        worker.compact_if_due(state)
        worker.compact(state)
        self.assertEqual(self.records(), 0)
        other = SessionStore(self.directory, self.session)
        other.save(other.load())
        with self.assertRaises(owner.JournalTaken):
            worker.compact(state)


def _call(identity, name, document):
    return SimpleNamespace(id=identity, name=name, document=document, invocation_digest="0" * 64, approved=True,
                           automatic=False)


class CrashTests(Base):
    def _worker_mid_call(self):
        """A worker chat that admitted three calls and crashed: one queued, one executing, one finished."""
        owner.hand_off(self.session, ended=lambda: True)
        worker = WorkerController(self.directory, session_id=self.session)
        self.assertIsInstance(worker.store, owner.OwnedSessionStore)
        worker.set_document(("document", 1))
        journal_tool(worker, _call("call-queued", "angelo_execute_python", worker.document), "queued")
        journal_tool(worker, _call("call-running", "angelo_execute_python", worker.document), "queued")
        journal_tool(worker, _call("call-running", "angelo_execute_python", worker.document), "execution_started")
        journal_tool(worker, _call("call-done", "angelo_execute_python", worker.document), "queued")
        with worker.lock:
            worker.state["results"]["call-done"] = dict(worker.state["results"]["call-done"], phase="completed",
                                                        result={"ok": True})
            worker.save()
        self.assertGreater(self.records(), 0)   # Records a crash leaves: no close compacted them.
        return self.path.read_bytes()

    def test_crash_marking_writes_the_file_a_recovered_app_crash_writes(self):
        crashed = self._worker_mid_call()
        with patch("time.time", return_value=1800000000.0):
            recovered = owner.worker_ended(self.session, self.directory, pending=["call-running", "never-admitted"])
        marked = self.path.read_bytes()
        self.path.write_bytes(crashed)
        with patch("time.time", return_value=1800000000.0):
            reopened = Controller(self.directory, session_id=self.session)   # Angelo's restart after its own crash.
            reopened.save()
        self.addCleanup(reopened.close)
        self.assertEqual(self.path.read_bytes(), marked)
        self.assertEqual(session_journal.read(self.path)[1].records, 0)
        by_id = {call["id"]: call for call in recovered.interrupted}
        self.assertEqual(set(by_id), {"call-queued", "call-running"})
        self.assertEqual(by_id["call-running"]["phase"], "execution_started")
        self.assertTrue(by_id["call-running"]["result"]["interrupted"])
        self.assertTrue(by_id["call-running"]["result"]["partial_changes_possible"])
        self.assertIs(by_id["call-queued"]["result"]["scene_changed"], False)
        self.assertEqual(recovered.unrecorded, ["never-admitted"])
        self.assertEqual(recovered.finished, {"status": "interrupted"})
        self.assertEqual(recovered.state["results"], reopened.state["results"])

    def test_a_failed_recovery_save_still_returns_the_outcome(self):
        self._worker_mid_call()
        with patch.object(SessionStore, "save", side_effect=OSError("disk full")):
            recovered = owner.worker_ended(self.session, self.directory)
        self.assertFalse(recovered.saved)
        self.assertEqual({call["id"] for call in recovered.interrupted}, {"call-queued", "call-running"})
        self.assertFalse(owner.hosted(self.session))


class LeadGoneTests(Base):
    def test_angelo_gone_stops_the_turn_seals_the_journal_and_releases_it(self):
        worker = WorkerController(self.directory, session_id=self.session)
        worker.set_document(("document", 1))
        with worker.lock:
            _row(worker.state, "worker edit")
            worker.save()
        worker.busy = True
        stops = []
        real_stop = worker.stop
        worker.stop = lambda reason=None: (stops.append(reason), real_stop(reason))
        self.assertEqual(owner.lead_gone([worker]), {self.session: True})
        self.assertEqual(len(stops), 1)
        self.assertFalse(worker.store.owned)
        self.assertEqual(self.records(), 0)   # Sealed: one base line, readable by every build.
        with self.assertRaises(owner.JournalReleased):
            worker.store.save(worker.state)
        texts = [row["text"] for row in sessions.read_transcript(self.path)["messages"]]
        self.assertIn("worker edit", texts)

    def test_one_chat_failing_to_close_never_keeps_another_open(self):
        broken = WorkerController(self.directory, session_id=self.session)
        broken.close = lambda: (_ for _ in ()).throw(RuntimeError("stuck"))
        other = WorkerController(self.directory, session_id=str(uuid.uuid4()))
        ended = owner.lead_gone([broken, other])
        self.assertEqual(ended, {self.session: False, other.state["id"]: True})
        self.assertFalse(broken.store.owned)
        self.assertFalse(other.store.owned)

    def test_end_hosting_on_chat_close(self):
        worker = WorkerController(self.directory, session_id=self.session)
        self.assertTrue(owner.end_hosting(worker))
        self.assertFalse(worker.store.owned)


if __name__ == "__main__":
    unittest.main()
