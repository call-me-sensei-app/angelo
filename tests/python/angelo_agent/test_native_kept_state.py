# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A store never writes over a state file it could not read, and never takes "can't tell" for "not there".

Found by the add-on sweep for the release audit's class (1.0.4/1.0.5: unknown read as absent):

* ``JobStore.load`` read an unreadable or damaged ``jobs.json`` as no jobs, and the next save (every start saves)
  replaced it: the records that say where a crashed render's frames are, gone.
* ``SessionCollection`` read an unreadable or damaged ``conversations.json`` as no chats and saved a list with one
  chat over it: every other chat vanished from the switcher; and ``Path.is_file`` dropped a chat whose transcript it
  could not stat.
* ``ConnectionStore`` read an unreadable ``connections.json`` as no connections, and the next add replaced it.
* ``UsageBuffer`` read an unreadable, damaged or later-version ``team-usage.json`` as nothing pending, and the next
  count replaced it: usage the team console was never told about, gone.
* ``ClientRegistry`` read an unreadable ``cli/clients.json`` as no local apps, and the next one replaced it (each
  other app then started a new conversation).
* Checked and not a defect: the file tools' journal asks ``Path.is_file``, which in Python 3.13 raises on EACCES/EIO
  (only ENOENT, ENOTDIR, EBADF and ELOOP read as "no"), so the write is refused; ``FileJournal`` pins that.

Each now tells "can't read it now" from "damaged" (``kept_state``). A file that can't be read now is read again before
any write and what it holds is taken in; a store that must write meanwhile (the job records) keeps it beside it
first and reads that copy back at its next load, with IDs that never collide; the connections, team usage and local
apps refuse to write instead, and say so. A damaged file (or another version's, or one too large) is kept beside it,
then replaced. The chat list is rebuilt from the chats on disk. EIO and EACCES and a damaged file are the developed
cases; ESTALE, ETIMEDOUT, a later version's file, a real ``chmod 000`` and a copy that can't be made are held out.
"""

import contextlib
import errno
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import kept_state, session_kept  # noqa: E402
from angelo_agent import job_store, team_usage_buffer  # noqa: E402
from angelo_agent.connections import ConnectionStore  # noqa: E402
from angelo_agent.external_requests import ClientRegistry  # noqa: E402
from angelo_agent.job_store import JobStore, new_record  # noqa: E402
from angelo_agent.sessions import SessionCollection, SessionStore  # noqa: E402

NUMBERS = (errno.EIO, errno.EACCES, errno.ESTALE, errno.ETIMEDOUT)
REAL_READ = Path.read_bytes


def unreadable(target, number):
    """``Path.read_bytes`` failing with ``number`` for the file at ``target`` now (a file written there later reads)."""
    inode = os.stat(target).st_ino

    def read_bytes(path):
        if os.fspath(path) == os.fspath(target) and os.stat(path).st_ino == inode:
            raise OSError(number, os.strerror(number))
        return REAL_READ(path)
    return mock.patch.object(Path, "read_bytes", read_bytes)


def kept(path):
    return [Path(copy) for copy in session_kept.kept_copies(Path(path))]


class Folder(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()


class Jobs(Folder):
    def saved(self, count=3):
        store = JobStore(self.directory)
        for number in range(count):
            record = new_record(store.allocate(), "chat", "call", {"kind": "RENDER_ANIMATION", "output": "a",
                                                                  "format": "PNG", "minutes": 1.0}, now=1.0)
            record["detail"]["staging"] = "/somewhere/.angelo-job-%s" % record["id"]
            store.add(record)
        return store.path.read_bytes()

    def test_records_that_cant_be_read_are_kept_and_come_back_at_the_next_load(self):
        for number in NUMBERS:
            with self.subTest(errno=errno.errorcode[number]):
                self.setUp()
                before = self.saved()
                path = self.directory / "jobs.json"
                with unreadable(path, number), mock.patch("builtins.print"):
                    store = JobStore(self.directory)
                    self.assertEqual(store.records, {})
                    record = new_record(store.allocate(), "chat", "call", {"kind": "EXPORT_USD", "output": "b.usd",
                                                                          "minutes": 1.0}, now=9.0)
                    self.assertGreaterEqual(int(record["id"][1:]), job_store.UNREAD_IDS, "never one of its IDs")
                    store.add(record)   # A save must happen this session: kept beside it first.
                self.assertEqual([copy.read_bytes() for copy in kept(path)], [before])
                again = JobStore(self.directory)   # The next start: every record is back, oldest first ...
                self.assertEqual(len(again.records), 4)
                self.assertEqual(list(again.records)[-1], record["id"])
                self.assertEqual(len(again.interrupted(now=10.0)), 4, "... and reconciled like any other")
                self.assertEqual(kept(path), [], "the copy goes once a save holds its records")
                self.assertEqual(len(JobStore(self.directory).records), 4)

    def test_a_copy_sharing_an_id_with_another_record_is_never_removed(self):
        self.saved()
        path = self.directory / "jobs.json"
        copy = path.with_name(path.name + ".20261024T000000Z.unreadable")
        other = json.loads(path.read_text())
        other["jobs"][0]["detail"]["staging"] = "/elsewhere/.angelo-job-j1"   # The same ID, another job.
        copy.write_text(json.dumps(other))
        store = JobStore(self.directory)
        self.assertTrue(store.save())
        self.assertTrue(copy.exists(), "its j1 was not taken in: kept")

    def test_trimming_never_drops_a_record_that_still_holds_frames(self):
        store = JobStore(self.directory)
        holding = []
        for number in range(job_store.MAX_RECORDS + 5):
            record = new_record(store.allocate(), "chat", "call", {"kind": "RENDER_ANIMATION", "output": "a",
                                                                  "format": "PNG", "minutes": 1.0}, now=float(number))
            record["status"] = "failed"
            if number < 3:
                record["detail"]["staging"] = "/somewhere/.angelo-job-%s" % record["id"]
                holding.append(record["id"])
            store.add(record)
        self.assertEqual(len(store.records), job_store.MAX_RECORDS)
        self.assertTrue(all(identity in store.records for identity in holding))

    def test_a_damaged_or_later_version_file_is_kept_then_replaced(self):
        for case in ("damaged", "empty", "later_version", "not_a_document"):
            with self.subTest(case=case):
                self.setUp()
                saved = self.saved()
                path = self.directory / "jobs.json"
                before = {"damaged": saved[:len(saved) // 2], "empty": b"", "not_a_document": b"[1, 2]",
                          "later_version": json.dumps({"version": 2, "jobs": []}).encode()}[case]
                path.write_bytes(before)
                with mock.patch("builtins.print"):
                    store = JobStore(self.directory)
                    self.assertEqual(store.records, {})
                    self.assertTrue(store.save())
                self.assertEqual([copy.read_bytes() for copy in kept(path)], [before])
                self.assertTrue(JobStore(self.directory).save())
                self.assertEqual(len(kept(path)), 1, "kept once, never read back as records")
                self.assertEqual(JobStore(self.directory).records, {})

    def test_records_are_never_written_over_when_they_cant_be_kept(self):
        before = self.saved()
        path = self.directory / "jobs.json"
        with unreadable(path, errno.EIO), mock.patch.object(session_kept, "preserve",
                                                            side_effect=OSError(errno.EIO, "I/O error")):
            store = JobStore(self.directory)
            self.assertFalse(store.save())
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(JobStore(self.directory).records), 3, "readable again: every record is back")

    def test_no_file_and_a_good_file_keep_nothing(self):
        JobStore(self.directory).save()
        self.saved()
        self.assertEqual(len(JobStore(self.directory).records), 3)
        self.assertEqual(kept(self.directory / "jobs.json"), [])
        self.assertLess(int(JobStore(self.directory).peek()[1:]), job_store.UNREAD_IDS)

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "POSIX permissions as an ordinary user")
    def test_a_real_unreadable_file(self):
        before = self.saved()
        path = self.directory / "jobs.json"
        path.chmod(0)
        with mock.patch("builtins.print"):
            self.assertTrue(JobStore(self.directory).save())
        (copy,) = kept(path)
        self.assertEqual(len(JobStore(self.directory).records), 0, "the kept copy is unreadable too")
        copy.chmod(0o600)
        self.assertEqual(copy.read_bytes(), before)
        self.assertEqual(len(JobStore(self.directory).records), 3, "readable: read back")
        self.assertTrue(JobStore(self.directory).save())
        self.assertEqual(kept(path), [])


class Chats(Folder):
    def chats(self, titles=("Make a cube", "Make a rig", "Light the shot")):
        collection = SessionCollection(self.directory)
        identities = []
        for index, title in enumerate(titles):
            identity = collection.active if index == 0 else collection.create("claude_subscription")
            state = SessionStore(self.directory, identity).load()
            state["messages"] = [{"role": "user", "text": title}]
            SessionStore(self.directory, identity).save(state)
            collection.update(state)
            identities.append(identity)
        collection.save()
        return identities, collection.path.read_bytes()

    def test_a_chat_list_that_cant_be_read_lists_every_chat_again(self):
        for case in ("EIO", "EACCES", "ESTALE", "damaged", "empty", "not_a_list"):
            with self.subTest(case=case):
                self.setUp()
                identities, before = self.chats()
                path = self.directory / "conversations.json"
                if case in ("damaged", "empty", "not_a_list"):
                    before = {"damaged": before[:len(before) // 2], "empty": b"", "not_a_list": b"[1, 2]"}[case]
                    path.write_bytes(before)
                    hook = contextlib.nullcontext()   # The file itself is what is wrong.
                else:
                    hook = unreadable(path, getattr(errno, case))
                with hook, mock.patch("builtins.print"):
                    collection = SessionCollection(self.directory)
                    self.assertEqual(set(collection.records), set(identities))
                    self.assertIn(collection.active, identities)
                    collection.save()
                self.assertEqual([copy.read_bytes() for copy in kept(path)], [before])
                again = SessionCollection(self.directory)
                self.assertEqual(set(again.records), set(identities))
                state = SessionStore(self.directory, identities[1]).load()   # Opening a chat names it again.
                again.update(state)
                self.assertEqual(again.records[identities[1]]["title"], "Make a rig")

    def test_a_chat_list_readable_again_at_the_save_gives_its_titles_back(self):
        identities, before = self.chats()
        path = self.directory / "conversations.json"
        with unreadable(path, errno.EIO):
            collection = SessionCollection(self.directory)
        self.assertEqual({r["title"] for r in collection.records.values()}, {"Recovered chat"})
        collection.save()   # Readable now: taken in, nothing kept or lost.
        self.assertEqual(kept(path), [])
        again = SessionCollection(self.directory)
        self.assertEqual({again.records[i]["title"] for i in identities}, {"Make a cube", "Make a rig", "Light the shot"})
        del before

    def test_a_kept_chat_list_is_read_back_even_when_the_chats_cant_be_listed(self):
        identities, before = self.chats()
        path = self.directory / "conversations.json"
        real = os.scandir

        def scandir(folder):   # The chats' folder alone can't be listed.
            if Path(folder).name == "sessions":
                raise OSError(errno.EIO, "I/O error")
            return real(folder)
        with unreadable(path, errno.EIO), mock.patch("builtins.print"), mock.patch(
                "angelo_agent.session_kept.os.scandir", scandir):
            collection = SessionCollection(self.directory)   # Nothing to list: a new chat, and a save.
        self.assertEqual([copy.read_bytes() for copy in kept(path)], [before])
        again = SessionCollection(self.directory)   # The next start: every chat is back with its title.
        self.assertTrue(set(identities) <= set(again.records))
        self.assertEqual(again.records[identities[1]]["title"], "Make a rig")
        again.save()
        self.assertEqual(kept(path), [])
        del collection

    def test_a_chat_whose_file_cant_be_stated_is_kept_and_a_deleted_one_goes(self):
        identities, _before = self.chats()
        unknown = SessionStore(self.directory, identities[1]).path
        os.unlink(SessionStore(self.directory, identities[2]).path)
        real = os.stat

        def stat(path, *args, **kwargs):
            if isinstance(path, (str, os.PathLike)) and os.fspath(path) == os.fspath(unknown):
                raise OSError(errno.EACCES, os.strerror(errno.EACCES))
            return real(path, *args, **kwargs)
        with mock.patch("angelo_agent.kept_state.os.stat", stat):
            collection = SessionCollection(self.directory)
        self.assertEqual(set(collection.records), {identities[0], identities[1]})

    def test_a_chat_list_is_never_written_over_when_it_cant_be_kept(self):
        identities, before = self.chats()
        path = self.directory / "conversations.json"
        with unreadable(path, errno.EIO), mock.patch.object(session_kept, "preserve",
                                                            side_effect=OSError(errno.EIO, "I/O error")):
            collection = SessionCollection(self.directory)
            collection.save()
            collection.activate(identities[0])
        self.assertEqual(path.read_bytes(), before)


class Connections(Folder):
    def saved(self):
        store = ConnectionStore(self.directory)
        with mock.patch("angelo_agent.connections.managed_policy.personal_refusal", return_value=None):
            first = store.add("ANTHROPIC", "SUBSCRIPTION")
            second = store.add("OPENAI", "SUBSCRIPTION")
        return {first["id"], second["id"]}, store.path.read_bytes()

    def test_connections_that_cant_be_read_are_kept_and_come_back(self):
        for number in NUMBERS:
            with self.subTest(errno=errno.errorcode[number]):
                self.setUp()
                identities, before = self.saved()
                path = self.directory / "connections.json"
                with unreadable(path, number), mock.patch("builtins.print"), mock.patch(
                        "angelo_agent.connections.managed_policy.personal_refusal", return_value=None):
                    store = ConnectionStore(self.directory)
                    self.assertEqual(store.records, {})
                    added = store.add("ANTHROPIC", "API_KEY")   # Kept beside it, then written; read back at once.
                self.assertEqual([copy.read_bytes() for copy in kept(path)], [before])
                self.assertEqual(set(store.records), identities | {added["id"]})
                store.activate(added["id"])   # A save that holds them all removes the copy.
                self.assertEqual(kept(path), [])
                self.assertEqual(set(ConnectionStore(self.directory).records), identities | {added["id"]})

    def test_a_file_that_stays_unreadable_still_lists_what_this_window_read(self):
        """Break-it round 3: a file nobody can read (another owner) keeps a copy that is just as unreadable; the file
        written in its place still holds every account this window had read, not only the one it changed."""
        identities, _before = self.saved()
        path = self.directory / "connections.json"
        store = ConnectionStore(self.directory)   # Read while it could be.
        with unreadable(path, errno.EACCES), mock.patch.object(kept_state, "kept_values", return_value=[]), \
                mock.patch("builtins.print"), mock.patch(
                    "angelo_agent.connections.managed_policy.personal_refusal", return_value=None):
            added = store.add("ANTHROPIC", "API_KEY")
        self.assertEqual(set(json.loads(path.read_text())["connections"][i]["id"] for i in range(3)),
                         identities | {added["id"]})
        self.assertEqual(len(kept(path)), 1, "the unreadable one is kept beside it")

    def test_a_connection_removed_after_the_read_back_stays_removed(self):
        identities, _before = self.saved()
        path = self.directory / "connections.json"
        with unreadable(path, errno.EIO), mock.patch("builtins.print"), mock.patch(
                "angelo_agent.connections.managed_policy.personal_refusal", return_value=None):
            ConnectionStore(self.directory).add("ANTHROPIC", "API_KEY")
        store = ConnectionStore(self.directory)
        gone = sorted(identities)[0]
        store.remove(gone)
        self.assertNotIn(gone, ConnectionStore(self.directory).records)
        self.assertEqual(kept(path), [])

    def test_damaged_connections_are_kept_before_they_are_replaced(self):
        _identities, saved = self.saved()
        path = self.directory / "connections.json"
        before = saved[:len(saved) // 2]
        path.write_bytes(before)
        with mock.patch("builtins.print"), mock.patch(
                "angelo_agent.connections.managed_policy.personal_refusal", return_value=None):
            store = ConnectionStore(self.directory)
            self.assertEqual(store.records, {})
            store.add("ANTHROPIC", "API_KEY")
        self.assertEqual([copy.read_bytes() for copy in kept(path)], [before])
        self.assertEqual(len(ConnectionStore(self.directory).records), 1)

    def test_connections_are_never_written_over_when_they_cant_be_kept(self):
        _identities, saved = self.saved()
        path = self.directory / "connections.json"
        before = saved[:-1]   # Damaged, and the copy can't be made.
        path.write_bytes(before)
        with mock.patch.object(session_kept, "preserve",
                                                            side_effect=OSError(errno.EIO, "I/O error")), \
                mock.patch("angelo_agent.connections.managed_policy.personal_refusal", return_value=None):
            store = ConnectionStore(self.directory)
            with self.assertRaises(OSError):
                store.add("ANTHROPIC", "API_KEY")
            self.assertEqual(store.records, {}, "memory is the file again: nothing changed")
        self.assertEqual(path.read_bytes(), before)


class TeamUsage(Folder):
    OWNER = ("https://console.angelo.studio", "6b0f3c1e-2d4a-4c8e-9f10-1a2b3c4d5e6f",
             "f1e2d3c4-b5a6-4978-8a9b-0c1d2e3f4a5b")

    def counts(self, now):
        return {(team_usage_buffer.day_of(now), "anthropic", "claude-opus-5-5"): (3, 1000)}

    def test_pending_counts_that_cant_be_read_now_are_never_written_over(self):
        now = 1_791_633_600.0
        buffer = team_usage_buffer.UsageBuffer(self.directory)
        buffer.add(self.OWNER, self.counts(now), now)
        path = Path(buffer.path)
        before = path.read_bytes()
        for number in NUMBERS:
            failing = mock.patch.object(team_usage_buffer, "open", create=True,
                                        side_effect=OSError(number, os.strerror(number)))
            with self.subTest(errno=errno.errorcode[number]), failing:
                with self.assertRaises(OSError):   # The caller keeps the count in memory and tries again.
                    buffer.add(self.OWNER, self.counts(now), now)
                self.assertIsNone(buffer.take(self.OWNER, now + 1), "nothing to send, nothing written")
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(kept(path), [])
        buffer.add(self.OWNER, self.counts(now), now)   # Readable again: added to what it holds.
        self.assertEqual(buffer.pending(self.OWNER, now)[0][next(iter(self.counts(now)))], [6, 1000])

    def test_a_damaged_or_other_version_file_is_kept_then_replaced(self):
        now = 1_791_633_600.0
        buffer = team_usage_buffer.UsageBuffer(self.directory)
        buffer.add(self.OWNER, self.counts(now), now)
        path = Path(buffer.path)
        before = path.read_bytes()
        for case in ("damaged", "later_version", "too_large"):
            with self.subTest(case=case):
                for copy in kept(path):
                    copy.unlink()
                path.write_bytes({"damaged": before[:len(before) // 2], "too_large": b" " * (2 << 20),
                                  "later_version": json.dumps({"version": 2, "owners": []}).encode()}[case])
                saved = path.read_bytes()
                with mock.patch("builtins.print"):
                    buffer.add(self.OWNER, self.counts(now), now)
                self.assertEqual([copy.read_bytes() for copy in kept(path)], [saved])
        with mock.patch.object(session_kept, "preserve", side_effect=OSError(errno.EIO, "I/O error")):
            path.write_bytes(b"{ damaged")
            with self.assertRaises(OSError):   # It can't be kept: never written over.
                buffer.add(self.OWNER, self.counts(now), now)
        self.assertEqual(path.read_bytes(), b"{ damaged")


class LocalApps(Folder):
    FIRST, SECOND = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"

    def test_local_apps_that_cant_be_read_now_are_never_replaced(self):
        registry = ClientRegistry(self.directory)
        registry.save({"codex": self.FIRST, "claude": self.SECOND})
        before = registry.path.read_bytes()
        with unreadable(registry.path, errno.EIO):
            self.assertEqual(registry.load(), {})
            with self.assertRaises(OSError):
                registry.save({"cursor": self.FIRST})
        self.assertEqual(registry.path.read_bytes(), before)
        self.assertEqual(kept(registry.path), [])

    def test_a_save_after_a_load_that_failed_keeps_every_other_app(self):
        registry = ClientRegistry(self.directory)
        registry.save({"codex": self.FIRST, "claude": self.SECOND})
        with unreadable(registry.path, errno.EIO):
            mapping = registry.load()   # The host starts with {} ...
        self.assertEqual(mapping, {})
        registry.save({"cursor": self.FIRST})   # ... and adds one app once the file reads again.
        self.assertEqual(registry.load(), {"codex": self.FIRST, "claude": self.SECOND, "cursor": self.FIRST})

    def test_another_instances_change_is_never_undone(self):
        first, second = ClientRegistry(self.directory), ClientRegistry(self.directory)
        first.save({"codex": self.FIRST})
        second.save({"codex": self.SECOND})   # Another Angelo rotated codex's conversation.
        first.save({"cursor": self.FIRST})   # This one adds an app: codex keeps the other's.
        self.assertEqual(first.load(), {"codex": self.SECOND, "cursor": self.FIRST})

    def test_a_list_of_the_wrong_shape_is_kept_then_replaced(self):
        registry = ClientRegistry(self.directory)
        registry.path.parent.mkdir(parents=True)
        registry.path.write_text("[]")
        with mock.patch("builtins.print"):
            registry.save({"cursor": self.FIRST})
        self.assertEqual([copy.read_bytes() for copy in kept(registry.path)], [b"[]"])

    def test_a_damaged_list_is_kept_then_replaced(self):
        registry = ClientRegistry(self.directory)
        registry.path.parent.mkdir(parents=True)
        registry.path.write_text("{ damaged")
        with mock.patch("builtins.print"):
            registry.save({"cursor": self.FIRST})
        self.assertEqual([copy.read_bytes() for copy in kept(registry.path)], [b"{ damaged"])
        self.assertEqual(registry.load(), {"cursor": self.FIRST})


class FileJournal(Folder):
    def test_a_file_that_cant_be_stated_is_never_written_without_its_before_image(self):
        """A regression pin that passes on 2c42a2c8a44 too (no source change): only the followed ``stat`` that decides
        "existed" fails (the path checks before it use ``lstat``), and Python 3.13's ``Path.is_file`` raises."""
        from angelo_agent.controller import Controller
        from angelo_agent.files import FileWorkspace
        for number in NUMBERS:
            with self.subTest(errno=errno.errorcode[number]):
                self.setUp()
                controller = Controller(self.directory)
                workspace = self.directory / "project"
                workspace.mkdir()
                target = workspace / "notes.md"
                target.write_text("original")
                files = FileWorkspace(workspace)
                checkpoint, _ = controller.checkpoints.reserve()
                real = os.stat

                def stat(path, *args, **kwargs):
                    if (kwargs.get("follow_symlinks", True) and isinstance(path, (str, os.PathLike))
                            and os.fspath(path) == os.fspath(target)):
                        raise OSError(number, os.strerror(number))
                    return real(path, *args, **kwargs)
                with mock.patch("os.stat", stat), self.assertRaises(OSError):
                    files.execute("angelo_write_file", {"path": "notes.md", "content": "changed"},
                                  controller.checkpoints, checkpoint["id"])
                self.assertEqual(target.read_text(), "original")
                files.execute("angelo_write_file", {"path": "notes.md", "content": "changed"},
                              controller.checkpoints, checkpoint["id"])
                self.assertEqual(controller.checkpoints.restore_since(checkpoint["id"]), 1)
                self.assertEqual(target.read_text(), "original", "restored from its before-image")


class Helpers(unittest.TestCase):
    def test_present_never_answers_no_for_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "a.json"
            self.assertIs(kept_state.present(path), False)
            path.write_text("{}")
            self.assertIs(kept_state.present(path), True)
            for number in NUMBERS:
                failing = mock.patch("angelo_agent.kept_state.os.stat",
                                     side_effect=OSError(number, os.strerror(number)))
                with self.subTest(errno=errno.errorcode[number]), failing:
                    self.assertIsNone(kept_state.present(path))
            self.assertEqual(kept_state.read(path), ({}, kept_state.OK))
            self.assertEqual(kept_state.read(path.with_name("missing")), (None, kept_state.NONE))
            self.assertEqual(kept_state.read(path.parent), (None, kept_state.DAMAGED))
            with unreadable(path, errno.EIO):
                self.assertEqual(kept_state.read(path), (None, kept_state.UNKNOWN))
            path.write_text("{ broken")
            self.assertEqual(kept_state.read(path), (None, kept_state.DAMAGED))


if __name__ == "__main__":
    unittest.main()
