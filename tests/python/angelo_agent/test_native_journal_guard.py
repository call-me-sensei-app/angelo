# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chat's store writes only over the file as it last read or wrote it (``session_guard``), on every platform.

Regressions of the A' concurrency review (``review-concurrency``): C4 (F4), a store's own failed append or another
process's metadata-only change locked a hosted chat out of saving for good; C11 (F11), on Windows a reader of the
chat's file (the lead's mirror) made the worker's compaction fail, and the failure forced the next save to rewrite.
Windows is simulated: no ``flock``, ``CreateFileW`` recorded, and an ``os.replace`` with ``MoveFileExW``'s rule: a
handle to the target without ``FILE_SHARE_DELETE`` is a sharing violation, and any other open handle to it, a
delete-sharing one and the writer's own included, is ``ERROR_ACCESS_DENIED`` (only a POSIX-semantics rename replaces
an open name). R6: the 1.0.6 rehearsal's Windows suites failed every rewrite with ``[WinError 5]``, the guard's own
check handle held across the replace; the earlier model let a delete-sharing handle pass, so it never caught that.
"""

import errno
import os
import stat
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import session_guard, session_journal, sessions, win32_replace  # noqa: E402
from angelo_agent import worker_journal_owner as owner  # noqa: E402
from angelo_agent.sessions import SessionStore  # noqa: E402


def _row(state, text):
    state["messages"].append({"role": "user", "text": text})


def _texts(path):
    return [row["text"] for row in sessions.read_transcript(path)["messages"]]


def _half_then(number):
    def write(fd, data):
        os.write(fd, data[: len(data) // 2])
        raise OSError(number, os.strerror(number))
    return write


class Case(unittest.TestCase):
    store_type = SessionStore

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = folder.name
        self.store = self.store_type(self.directory, str(uuid.uuid4()))
        self.state = self.store.load()
        self.saved = []
        self.save("first")   # A rewrite (the first save after a load).
        self.save("second")  # An append.
        self.path = self.store.path

    def save(self, text):
        _row(self.state, text)
        self.store.save(self.state)
        self.saved.append(text)


class OwnFailureTests(Case):
    """C4: a failed append is the store's own write; the file as it left it stays its own."""

    store_type = owner.OwnedSessionStore

    def test_a_failed_append_cut_back_never_locks_the_owner_out(self):
        for number in (errno.ENOSPC, errno.EIO):
            with self.subTest(errno=number):
                _row(self.state, "lost %d" % number)
                with patch.object(session_journal, "_write_all", _half_then(number)), self.assertRaises(OSError):
                    self.store.save(self.state)
                self.saved.append("lost %d" % number)   # Still in memory: the next save writes it.
                for index in range(3):   # The disk has room again.
                    self.save("after %d %d" % (number, index))
                self.assertEqual(_texts(self.path), self.saved)

    def test_a_failed_fsync_never_locks_the_owner_out(self):
        _row(self.state, "unsynced")
        with patch.object(session_journal.os, "fsync", side_effect=OSError(errno.EIO, "I/O error")), \
                self.assertRaises(OSError):
            self.store.save(self.state)
        self.saved.append("unsynced")
        self.save("after")
        self.assertEqual(_texts(self.path), self.saved)

    def test_a_write_that_could_not_be_cut_back_is_still_the_owners(self):
        _row(self.state, "torn")
        with patch.object(session_journal, "_write_all", _half_then(errno.EIO)), \
                patch.object(session_journal.os, "ftruncate", side_effect=OSError(errno.EIO, "I/O error")), \
                self.assertRaises(OSError):
            self.store.save(self.state)
        self.assertTrue(session_journal.read(self.path)[1].torn, "precondition: the half record is on disk")
        self.saved.append("torn")
        self.save("after")
        self.assertEqual(_texts(self.path), self.saved)
        self.assertFalse(session_journal.read(self.path)[1].torn)


class MetadataTests(Case):
    """C4: what changes no byte of the file is not another writer, for every store."""

    def _metadata_changes(self):
        yield "times", lambda: os.utime(self.path, ns=(1, 1))
        yield "mode", lambda: os.chmod(self.path, 0o644)
        yield "link", lambda: os.link(self.path, str(self.path) + ".%s.link" % uuid.uuid4().hex)

    def test_metadata_only_changes_never_lock_a_store_out(self):
        for store_type in (SessionStore, owner.OwnedSessionStore):
            for name, change in self._metadata_changes():
                with self.subTest(store=store_type.__name__, change=name):
                    self.store = store_type(self.directory, self.store.session_id)
                    self.state = self.store.load()
                    change()   # Before the first save: the load's view is checked by its bytes.
                    self.save("%s %s 1" % (store_type.__name__, name))
                    change()   # Between two saves of this store.
                    self.save("%s %s 2" % (store_type.__name__, name))
                    self.assertEqual(_texts(self.path), self.saved)

    def test_a_change_in_place_of_the_same_size_is_another_writer(self):
        for store_type in (SessionStore, owner.OwnedSessionStore):
            with self.subTest(store=store_type.__name__):
                self.store = store_type(self.directory, self.store.session_id)
                self.state = self.store.load()
                data = bytearray(self.path.read_bytes())
                data[data.index(b"irst") - 1] ^= 0x20   # first <-> First
                with open(self.path, "r+b") as handle:   # Same inode and size, other bytes.
                    handle.write(data)
                _row(self.state, "refused")
                with self.assertRaises(owner.JournalTaken):
                    self.store.save(self.state)
                self.assertEqual(self.path.read_bytes(), bytes(data))


class LeadChatTests(Case):
    """L5's finding: the base store's own paths fail closed for every chat, and a lead-only chat saves as before."""

    def test_a_lead_only_chat_appends_compacts_and_reloads(self):
        self.assertEqual(self.store.last_write, "append")
        self.store.compact(self.state)
        self.save("third")
        self.assertEqual(self.store.last_write, "append")
        self.assertEqual(_texts(self.path), self.saved)
        self.assertEqual(SessionStore(self.directory, self.store.session_id).load()["messages"][-1]["text"], "third")

    def test_every_rewrite_path_refuses_another_writers_file(self):
        other = SessionStore(self.directory, self.store.session_id)
        theirs = other.load()
        _row(theirs, "theirs")
        other.save(theirs)
        before = self.path.read_bytes()
        _row(self.state, "mine")
        for write in (self.store.save, self.store.compact, self.store.compact_if_due, self.store.leave):
            with self.subTest(path=write.__name__), self.assertRaises(owner.JournalTaken):
                write(self.state)
            self.assertEqual(self.path.read_bytes(), before)
        self.store.invalidate()   # Forgetting the file's records never forgets whose file it is.
        with self.assertRaises(owner.JournalTaken):
            self.store.save(self.state)

    def test_a_file_that_appeared_after_a_load_found_none_is_another_writers(self):
        store = SessionStore(self.directory, str(uuid.uuid4()))
        state = store.load()
        theirs = SessionStore(self.directory, store.session_id)
        theirs.save(theirs.load())
        before = store.path.read_bytes()
        _row(state, "mine")
        with self.assertRaises(owner.JournalTaken):
            store.save(state)
        self.assertEqual(store.path.read_bytes(), before)

    def test_a_store_never_loaded_writes_the_whole_state_it_is_given_then_is_guarded(self):
        fresh = SessionStore(self.directory, self.store.session_id)
        _row(self.state, "third")
        fresh.save(self.state)   # As SessionCollection.create and a migration do: the caller's whole state.
        _row(self.state, "fourth")
        fresh.save(self.state)
        self.assertEqual(fresh.last_write, "append")
        with self.assertRaises(owner.JournalTaken):   # The file is the fresh store's now, not this one's.
            self.store.save(self.state)
        other = SessionStore(self.directory, self.store.session_id)
        other.save(other.load())
        with self.assertRaises(owner.JournalTaken):   # And the fresh store is guarded like any other.
            fresh.save(self.state)


class FakeWindows:
    """``CreateFileW`` and ``MoveFileExW`` as Windows applies them (module docstring): a replace fails while any
    handle to the target is open. Handles are real descriptors, so the file is really read."""

    def __init__(self, posix=False):
        self.posix = posix     # Whether the rename has POSIX semantics (win32_replace); else it is unsupported here.
        self.handles = []      # (descriptor, share mode, (device, index) of the file it opened)
        self.shares = []       # Every CreateFileW share mode asked for.
        self.replaces = 0
        self.refusals = []     # The Windows error of every refused replace.
        self.real_replace = os.replace
        self.invalid = -1

    def _held(self, fd, share):
        status = os.fstat(fd)
        self.handles.append((fd, share, (status.st_dev, status.st_ino)))

    def create(self, path, access, share, security, disposition, flags, template):
        self.shares.append(share)
        try:
            fd = os.open(path, os.O_RDONLY)
        except FileNotFoundError:
            return self.invalid
        self._held(fd, share)
        return fd

    def hold_without_delete_sharing(self, path):
        """Another process's handle (an antivirus scan, Python's own ``open``): read and write sharing only."""
        fd = os.open(path, os.O_RDONLY)
        self._held(fd, 0x3)
        return fd

    def let_go(self, fd):
        """Close a handle (a closed descriptor's number is reused: it is never looked at again)."""
        self.handles = [entry for entry in self.handles if entry[0] != fd]
        os.close(fd)

    def _open_on(self, target):
        try:
            status = os.stat(target)
        except FileNotFoundError:
            return []
        found = []
        for fd, share, opened in self.handles:
            try:
                held = os.fstat(fd)
            except OSError:
                continue   # Closed.
            if (held.st_dev, held.st_ino) == opened == (status.st_dev, status.st_ino):
                found.append(share)
        return found

    def replace(self, source, target):
        self.replaces += 1
        shares = self._open_on(target)
        if shares:
            code = 32 if any(not share & session_journal.FILE_SHARE_DELETE for share in shares) else 5
            self.refusals.append(code)
            raise PermissionError(13, "[WinError %d] the target is open" % code)
        return self.real_replace(source, target)

    def posix_replace(self, source, target):
        """``FileRenameInfoEx`` with POSIX semantics: only a handle that does not share delete refuses it."""
        if not self.posix:
            raise win32_replace.Unsupported("Windows error 87")
        self.replaces += 1
        shares = self._open_on(target)
        if any(not share & session_journal.FILE_SHARE_DELETE for share in shares):
            self.refusals.append(32)
            raise PermissionError(13, "[WinError 32] the target is open")
        return self.real_replace(source, target)   # POSIX: open readers keep the file they opened.

    def api(self):
        return SimpleNamespace(create=self.create, close=os.close, last_error=lambda: 2,
                               descriptor=lambda handle, _flags: handle, invalid=self.invalid)

    def patches(self, case):
        for target, name, value in ((session_journal, "_WINDOWS", True), (session_journal, "_win32", self.api),
                                    (session_journal, "fcntl", None), (session_guard, "fcntl", None),
                                    (session_journal.os, "replace", self.replace),
                                    (win32_replace, "_posix", self.posix_replace), (win32_replace, "_logged", False),
                                    (session_journal.time, "sleep", lambda _seconds: None)):
            patcher = patch.object(target, name, value)
            patcher.start()
            case.addCleanup(patcher.stop)


class WindowsTests(Case):
    """C11: a worker's compaction while the lead's mirror reads the chat's file, on (simulated) Windows."""

    store_type = owner.OwnedSessionStore

    def setUp(self):
        self.windows = FakeWindows()
        self.windows.patches(self)
        super().setUp()

    def test_readers_and_the_writers_own_check_share_delete(self):
        mirror = SessionStore(self.directory, self.store.session_id)
        state, _partial = mirror._read()   # The mirror's reader (worker_mirror_events.reload).
        self.assertEqual(state["messages"][-1]["text"], "second")
        self.assertTrue(self.windows.shares)
        self.assertTrue(all(share & session_journal.FILE_SHARE_DELETE for share in self.windows.shares))
        with self.assertRaises(FileNotFoundError):
            session_journal.open_shared(Path(self.directory) / "missing.json")

    def test_no_handle_of_the_writer_is_open_when_it_replaces(self):
        """R6: every rewrite (setUp's first save after the load, a compaction) lands with no other handle open."""
        self.assertEqual(self.windows.refusals, [])
        self.store.compact(self.state)
        self.assertEqual(self.store.last_write, "rewrite")
        self.assertEqual(self.windows.refusals, [])
        self.assertEqual(session_journal.read(self.path)[1].records, 0)
        self.assertEqual(_texts(self.path), self.saved)

    def test_a_readers_open_handle_defers_the_compaction_never_fails_the_save(self):
        reader = session_journal.open_shared(self.path)   # A mirror read in progress during the compaction.
        records = session_journal.read(self.path)[1].records
        self.store.compact(self.state)   # Refused (ERROR_ACCESS_DENIED, retried), the file as it was.
        self.assertEqual(set(self.windows.refusals), {5})
        self.assertEqual(self.store.last_write, "append")
        self.assertEqual(os.read(reader, 1), b"{", "the open reader goes on reading the file it opened")
        self.save("while read")
        self.assertEqual(session_journal.read(self.path)[1].records, records + 1)
        self.windows.let_go(reader)
        self.store.compact(self.state)
        self.assertEqual(self.store.last_write, "rewrite")
        self.assertEqual(_texts(self.path), self.saved)

    def test_another_writers_change_during_the_rewrite_is_never_written_over(self):
        """With no handle (and no flock) held across the replace, the file is checked again just before it."""
        written = session_journal._write_all

        def meanwhile(fd, data):
            written(fd, data)
            if os.fstat(fd).st_ino != os.stat(self.path).st_ino:   # The temporary file: another writer appends.
                with open(self.path, "ab") as other:
                    other.write(b'{"other": true}\n')
        before = Path(self.path).read_bytes()
        with patch.object(session_journal, "_write_all", meanwhile):
            with self.assertRaises(session_journal.JournalTaken):
                self.store.compact(self.state)
        self.assertEqual(Path(self.path).read_bytes(), before + b'{"other": true}\n')
        self.assertEqual([name for name in os.listdir(Path(self.path).parent) if name.endswith(".tmp")], [])

    def test_a_refused_replace_keeps_appending_and_compacts_when_next_due(self):
        held = self.windows.hold_without_delete_sharing(self.path)
        records = session_journal.read(self.path)[1].records
        self.store.compact(self.state)   # Refused (retried), with the file as it was: nothing lost, no reset.
        self.assertGreater(self.windows.replaces, 1)
        self.assertEqual(self.store.last_write, "append")
        self.windows.replaces = 0
        for index in range(3):   # Saves go on appending: none is forced to rewrite (an admission's save).
            self.save("held %d" % index)
            self.assertEqual(self.store.last_write, "append")
        self.assertEqual(self.windows.replaces, 0)
        self.assertEqual(session_journal.read(self.path)[1].records, records + 3)
        self.windows.let_go(held)
        self.store.compact(self.state)   # The holder let go: the compaction is retried and lands.
        self.assertEqual(self.store.last_write, "rewrite")
        self.assertEqual(session_journal.read(self.path)[1].records, 0)
        self.assertEqual(_texts(self.path), self.saved)

    def test_a_save_that_must_rewrite_falls_back_to_appending_when_the_replace_is_refused(self):
        held = self.windows.hold_without_delete_sharing(self.path)
        self.addCleanup(self.windows.let_go, held)
        with patch.object(self.store._writer, "force_due", return_value=True):   # The save's own compaction.
            self.save("forced")
        self.assertEqual(self.store.last_write, "append")
        self.assertEqual(_texts(self.path), self.saved)

    def test_a_lead_only_chat_saves_normally_without_flock(self):
        store = SessionStore(self.directory, str(uuid.uuid4()))
        state = store.load()
        for text in ("a", "b", "c"):
            _row(state, text)
            store.save(state)
        self.assertEqual(store.last_write, "append")
        store.compact(state)
        self.assertEqual(store.last_write, "rewrite")
        self.assertEqual(_texts(store.path), ["a", "b", "c"])


class PosixWindowsTests(Case):
    """R6 follow-up: with POSIX rename semantics (``win32_replace``), a reader holding the file never delays a
    rewrite, and a first rewrite after a load lands; only a handle without delete sharing refuses it (retried)."""

    store_type = owner.OwnedSessionStore

    def setUp(self):
        self.windows = FakeWindows(posix=True)
        self.windows.patches(self)
        super().setUp()

    def test_a_compaction_lands_while_a_reader_holds_the_file(self):
        reader = session_journal.open_shared(self.path)   # The mirror's read in progress.
        self.addCleanup(self.windows.let_go, reader)
        self.store.compact(self.state)
        self.assertEqual(self.store.last_write, "rewrite")
        self.assertEqual((self.windows.refusals, win32_replace.last), ([], win32_replace.POSIX))
        self.assertEqual(os.read(reader, 1), b"{", "the reader goes on reading the file it opened")
        self.assertEqual(session_journal.read(self.path)[1].records, 0)
        self.assertEqual(_texts(self.path), self.saved)

    def test_a_first_rewrite_after_a_load_lands_while_a_reader_holds_the_file(self):
        first = SessionStore(self.directory, str(uuid.uuid4()))   # A lead chat, saved, then opened again.
        state = first.load()
        for text in ("a", "b"):
            _row(state, text)
            first.save(state)
        store = SessionStore(self.directory, first.session_id)
        state = store.load()
        reader = session_journal.open_shared(store.path)
        self.addCleanup(self.windows.let_go, reader)
        _row(state, "after load")
        store.save(state)   # The first save after a load rewrites: it lands, and never raises.
        self.assertEqual(store.last_write, "rewrite")
        self.assertEqual(self.windows.refusals, [])
        self.assertEqual(_texts(store.path), ["a", "b", "after load"])

    def test_a_handle_without_delete_sharing_still_refuses_and_the_save_appends(self):
        held = self.windows.hold_without_delete_sharing(self.path)
        self.addCleanup(self.windows.let_go, held)
        self.store.compact(self.state)
        self.assertEqual(set(self.windows.refusals), {32})
        self.assertEqual(self.store.last_write, "append")
        self.assertEqual(_texts(self.path), self.saved)


class UnsupportedPosixTests(Case):
    """Where POSIX semantics are unsupported the replace is ``os.replace`` (``MoveFileExW``), logged once."""

    def test_the_fallback_replaces_classically_and_says_so_once(self):
        self.windows = FakeWindows(posix=False)
        self.windows.patches(self)
        with patch("sys.stderr") as stderr:
            for text in ("third", "fourth"):
                self.save(text)
                self.store.compact(self.state)
                self.assertEqual(self.store.last_write, "rewrite")
        self.assertEqual(win32_replace.last, win32_replace.CLASSIC)
        logged = [str(call) for call in stderr.write.call_args_list if "POSIX semantics" in str(call)]
        self.assertEqual(len(logged), 1, logged)
        self.assertEqual(_texts(self.path), self.saved)


class _Lagging:
    """A path's stat as Windows may give it: the directory entry's size and times, behind the file's own."""

    def __init__(self, status):
        for name in ("st_dev", "st_ino", "st_mode", "st_nlink", "st_ctime_ns", "st_mtime", "st_uid", "st_gid"):
            setattr(self, name, getattr(status, name))
        self.st_size, self.st_mtime_ns = max(0, status.st_size - 1), status.st_mtime_ns - 1000


class LaggingPathStatTests(Case):
    """R6 (build4 repeats, 16 of 20 failing on random cases): on Windows a path's stat can lag the file it names, and
    every identity the journal compares is a descriptor's. Mixed, each lag read as another writer's change: a save
    rewrote instead of appending, and a rewrite's check could refuse the chat's own file."""

    store_type = owner.OwnedSessionStore

    def setUp(self):
        self.windows = FakeWindows()
        self.windows.patches(self)
        real = os.stat

        def lagging(path, *args, **keywords):   # Every chat file's path stat lags (its descriptor's never does).
            status = real(path, *args, **keywords)
            return _Lagging(status) if str(path).endswith(".json") and stat.S_ISREG(status.st_mode) else status
        patcher = patch.object(os, "stat", lagging)
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()

    def test_saves_append_and_compact_while_path_stats_lag(self):
        writes = []
        for text in ("third", "fourth"):
            self.save(text)
            writes.append(self.store.last_write)
        self.store.compact(self.state)
        writes.append(self.store.last_write)
        self.save("fifth")
        writes.append(self.store.last_write)
        self.assertEqual(writes, ["append", "append", "rewrite", "append"])
        self.assertEqual(_texts(self.path), self.saved)


if __name__ == "__main__":
    unittest.main()
