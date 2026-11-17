# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Two Angelo processes on one chat: the open chat is never stopped by the other, and recovers by itself.

Evidence: the showcase's Godot session (build 06259fa5bb0, Linux, 2026-11-14). Every save through the engine
link made Godot start a background Angelo to import the ``.blend``; from then on the open Angelo's chat answered
"The invocation journal could not be saved; no native work was admitted" for every call until it was started
again, six times in a row. Any Angelo process loads the active chat, a file load binds it and saves, a quit
compacts it: the background one wrote the open chat's file, and the open chat's store, which never writes over
another writer's content (``session_guard``), refused for good.

Pinned with real second processes (this file's ``OTHER``: a plain Python that does to the chat what any Angelo
process does: load it, bind it, save, and compact it on the way out), through the controller's own admission:

* a background Angelo of this build never writes the open chat's file (its saves are refused: the chat's lease
  is the open one's, ``session_lease``), and the open chat's reads and edits go on, round after round;
* a chat another live Angelo opened first is a view here: its calls are refused with the cause's kind, nothing
  of it is written (its pending messages neither), and when that process ends the next call is admitted with no
  restart;
* a process that takes no lease (an earlier build) and writes over the open chat's file does not stop it either:
  its version is kept beside the chat, the open chat's state is written, each edit stays applied once, and the
  chat reads back verified; and again the next time;
* the chat's file removed under the open chat is written again;
* an older store of the same chat in the same process still takes nothing back from the newer one;
* a worker Blender's store of a hosted chat takes no lease, so the chat it hands back is Angelo's to write;
* where no lock can be had, a store writes as before;
* the lease's own file is opened by the lease itself, never through ``session_journal``'s Windows calls, which
  tests replace with stand-ins: under such a stand-in (a plain descriptor, which on Windows shares no delete) a
  store's lease kept its folder from being removed (VM CI of 5fc511e92b2, win-2: ``WinError 32`` on the
  ``.lease`` file when ``test_native_session_journal``'s temporary folder was cleaned up).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import errno
import json
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from agent_runtime_fixtures import AgentRuntimeFixture
from angelo_agent import chat_pending, session_kept
from angelo_agent.controller import Controller
from angelo_agent.sessions import HISTORY_WARNING, SessionStore

try:
    from angelo_agent import session_lease
except ImportError:   # Before the lease: the cases below still show what the second process did to the chat.
    session_lease = None

CUBE = {"name": "Cube", "size": 1, "location": [0, 0, 0]}
REFUSAL = "invocation journal could not be saved"
TESTS = str(Path(__file__).resolve().parent)
ADDONS = str(Path(__file__).resolve().parents[3] / "scripts/addons_core")
# The other Angelo process: OTHER directory chat-id mode. Modes: "background" (load, bind, save, quit), "stay" (the
# same, and it stays until a line arrives), "earlier-build" (a build from before the lease: it takes none),
# "worker" (a worker Blender's store of a hosted chat, which stays).
OTHER = r'''
import json, sys
sys.path[:0] = sys.argv[4:6]
import suite_isolation  # noqa: F401
directory, session, mode = sys.argv[1:4]
try:
    from angelo_agent import session_lease
except ImportError:
    session_lease = None
if mode == "earlier-build" and session_lease is not None:
    session_lease.held = lambda writer: True
outcome = {"errors": []}
if mode == "worker":
    from angelo_agent.worker_journal_owner import OwnedSessionStore
    store = OwnedSessionStore(directory, session)
    state = store.load()
    state["messages"].append({"role": "user", "text": "The worker's row"})
    store.save(state)
    print(json.dumps({"ready": True}), flush=True)
    sys.stdin.readline()
else:
    from angelo_agent.controller import Controller
    chat = Controller(directory, session_id=session)   # Any Angelo process loads the active chat,
    chat.set_document(("document", 1))                 # a file load binds it,
    try:
        chat.save()                                    # and saves it;
    except OSError as error:
        outcome["errors"].append(type(error).__name__)
    if mode == "stay":
        print(json.dumps({"ready": True}), flush=True)
        sys.stdin.readline()
    chat.close()                                       # a quit compacts it.
print(json.dumps(outcome), flush=True)
'''


class TwoProcesses(AgentRuntimeFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.creates = 0
        self.executor.execute = Mock(side_effect=self.execute)

    def execute(self, name, arguments):
        if name == "angelo_create_cube":
            self.creates += 1
            return {"success": True, "verified": {"object_count": self.creates}, "changes": {
                "added": [{"name": arguments["name"], "type": "MESH"}], "removed": [], "modified": []}}
        return {"success": True, "objects": [], "object_count": self.creates}

    def invoke(self, identity, name="angelo_inspect_scene", arguments=None, c=None):
        c, replies = c or self.c, []
        c.request_tool(identity, name, {} if arguments is None else arguments, replies.append)
        self.pump(c)
        self.assertEqual(len(replies), 1, identity)
        return replies[0]

    def start(self, mode, session=None):
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", OTHER, self.directory.name, session or self.c.state["id"], mode, TESTS,
             ADDONS],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.end, process)
        return process

    def end(self, process):
        if process.poll() is None:
            process.kill()
        process.communicate()

    def finish(self, process):
        """The other process's outcome once it has quit by itself."""
        out, err = process.communicate("\n", timeout=180)
        self.assertEqual(process.returncode, 0, err[-3000:])
        return json.loads(out.strip().splitlines()[-1])

    def other_angelo(self, mode="background", session=None):
        return self.finish(self.start(mode, session))

    def staying(self, mode, session):
        """The other process, started and holding the chat open."""
        process = self.start(mode, session)
        while line := process.stdout.readline():
            if line.startswith("{") and json.loads(line).get("ready"):
                return process
        self.fail("The other process ended early: " + process.stderr.read()[-3000:])

    def goes_on(self, label, c=None):
        """A read and an edit are admitted and answered."""
        read = self.invoke("read-" + label, c=c)
        self.assertNotIn(REFUSAL, str(read.get("error", "")), read)
        self.assertTrue(read.get("success"), read)
        before = self.creates
        edit = self.invoke("edit-" + label, "angelo_create_cube", dict(CUBE, name="Cube " + label), c=c)
        self.assertTrue(edit.get("success"), edit)
        self.assertEqual(self.creates, before + 1)

    def reads_back_verified(self, identities, c=None):
        """The chat closed and read again, as a relaunch reads it: verified, its ledger open, every call's record."""
        c = c or self.c
        c.close()
        resumed = Controller(self.directory.name, session_id=c.state["id"])
        self.controllers.append(resumed)
        key = c.state["id"] + "/"
        self.assertFalse(resumed.state["invocations"]["closed"])
        texts = [str(row.get("text", "")) for row in resumed.state["messages"]]
        self.assertFalse(any(HISTORY_WARNING in text for text in texts), "the history was not verified")
        for identity in identities:
            self.assertIn(key + identity, resumed.state["results"], identity)
        return resumed

    def test_a_background_angelo_that_opens_the_chat_never_stops_the_open_one(self):
        self.assertTrue(self.invoke("edit-before", "angelo_create_cube", CUBE).get("success"))
        path = self.c.store.path
        for label in ("1", "2", "3"):   # Every save through the engine link started one (the showcase: six in a row).
            before = path.read_bytes()
            outcome = self.other_angelo()
            self.assertEqual(path.read_bytes(), before, "the background Angelo wrote the open chat's file")
            self.assertIn("JournalLeased", outcome["errors"])
            self.goes_on(label)
        self.assertEqual(self.creates, 4)
        self.assertEqual(session_kept.kept_copies(path), [])
        self.assertEqual(self.c.store._writer.taken_back, 0)
        again = self.invoke("edit-2", "angelo_create_cube", dict(CUBE, name="Cube 2"))
        self.assertTrue(again.get("replayed"), again)
        self.assertEqual(self.creates, 4)
        self.reads_back_verified(["edit-before", "read-1", "edit-1", "read-3", "edit-3"])

    def test_a_chat_another_live_angelo_opened_first_is_a_view_until_that_one_ends(self):
        session = str(uuid.uuid4())
        other = self.staying("stay", session)   # It made the chat, has it open and stays.
        view = Controller(self.directory.name, session_id=session)
        self.controllers.append(view)
        view.set_document(("document", 1))
        view.busy = True
        view.state["options"]["approval"] = "AUTO_BOUNDED"
        path = view.store.path
        before = path.read_bytes()
        pending = chat_pending._path(view)
        pending.parent.mkdir(mode=0o700, exist_ok=True)
        pending.write_text(json.dumps({"version": 1, "pending": []}), encoding="utf-8")
        view._pending_saved = None   # As when this process's own pending messages changed.
        chat_pending.save(view, final=True)
        self.assertTrue(pending.exists(), "a view wrote the other Angelo's pending messages")
        for identity, name, arguments in (("read-view", "angelo_inspect_scene", {}),
                                          ("edit-view", "angelo_create_cube", CUBE)):
            refused = self.invoke(identity, name, arguments, c=view)
            self.assertIn(REFUSAL, refused.get("error", ""), refused)
            self.assertEqual(refused.get("journal_error"), "JournalLeased")
            # And it says why and what to do (F9): the other process by its number, read from the lease it holds.
            self.assertEqual(refused.get("journal_cause"), "other_process")
            self.assertIn("Another Angelo process (number %d) has this chat open" % other.pid, refused["error"])
            self.assertIn("quit it and open the chat here again", refused["error"])
        self.assertEqual(session_lease.holder(view.store._writer), other.pid)
        self.assertEqual(self.creates, 0)
        self.assertEqual(path.read_bytes(), before, "a view wrote the other Angelo's chat")
        self.assertEqual(self.finish(other)["errors"], [])   # It quits; its lease goes with it.
        self.goes_on("after", c=view)                        # No restart: the next call takes the lease.
        self.assertEqual(session_lease.holder(view.store._writer), os.getpid())   # And the lease names this one.
        self.reads_back_verified(["read-after", "edit-after"], c=view)

    def test_a_file_an_earlier_build_wrote_over_is_kept_and_the_open_chat_goes_on(self):
        self.assertTrue(self.invoke("edit-before", "angelo_create_cube", CUBE).get("success"))
        path = self.c.store.path
        for count, label in enumerate(("1", "2"), 1):
            self.assertEqual(self.other_angelo("earlier-build")["errors"], [])
            theirs = path.read_bytes()
            self.goes_on(label)
            copies = session_kept.kept_copies(path)
            self.assertEqual(len(copies), count)
            self.assertIn(theirs, [copy.read_bytes() for copy in copies], "the other writer's version was not kept")
            self.assertEqual(self.c.store._writer.taken_back, count)
        for identity, arguments in (("edit-before", CUBE), ("edit-1", dict(CUBE, name="Cube 1"))):
            self.assertTrue(self.invoke(identity, "angelo_create_cube", arguments).get("replayed"), identity)
            changed = self.invoke(identity, "angelo_create_cube", dict(arguments, name="Another cube"))
            self.assertIn("different", changed.get("error", ""), identity)
        self.assertEqual(self.creates, 3)
        resumed = self.reads_back_verified(["edit-before", "read-1", "edit-1", "read-2", "edit-2"])
        resumed.document, resumed.busy = self.c.document, True
        again = self.invoke("edit-1", "angelo_create_cube", dict(CUBE, name="Cube 1"), c=resumed)
        self.assertTrue(again.get("replayed"), again)
        self.assertEqual(self.creates, 3)

    def test_a_chat_file_removed_under_the_open_chat_is_written_again(self):
        self.assertTrue(self.invoke("edit-before", "angelo_create_cube", CUBE).get("success"))
        self.c.store.path.unlink()
        self.goes_on("after")
        self.assertTrue(self.c.store.path.is_file())
        self.reads_back_verified(["edit-before", "read-after", "edit-after"])

    def test_an_older_store_of_the_same_process_takes_nothing_back_from_the_newer_one(self):
        self.assertTrue(self.invoke("edit-before", "angelo_create_cube", CUBE).get("success"))
        newer = Controller(self.directory.name, session_id=self.c.state["id"])
        self.controllers.append(newer)
        newer.document, newer.busy = self.c.document, True
        self.goes_on("newer", c=newer)
        refused = self.invoke("read-older")
        self.assertIn(REFUSAL, refused.get("error", ""), refused)
        self.assertEqual(refused.get("journal_error"), "JournalTaken")
        self.assertEqual(refused.get("journal_cause"), "other_writer")   # F9: the cause, and what to do.
        self.assertIn("Open the chat again from the chat list", refused["error"])
        self.assertEqual(session_kept.kept_copies(self.c.store.path), [])
        self.assertEqual(self.c.store._writer.taken_back, 0)
        self.assertTrue(self.invoke("read-newer-again", c=newer).get("success"))

    def test_a_workers_store_of_a_hosted_chat_takes_no_lease(self):
        session = str(uuid.uuid4())
        worker = self.staying("worker", session)   # It made the chat and stays, as a parked worker does.
        store = SessionStore(self.directory.name, session)
        state = store.load()
        self.assertEqual([row["text"] for row in state["messages"]], ["The worker's row"])
        state["messages"].append({"role": "user", "text": "Angelo's row, after the hand-back"})
        store.save(state)   # JournalLeased here would lock Angelo out of every chat a live worker ever hosted.
        self.finish(worker)

    def test_where_no_lock_can_be_had_a_store_writes_as_before(self):
        for error in (OSError(errno.ENOLCK, "No locks available"), OSError(errno.EOPNOTSUPP, "Not supported")):
            with self.subTest(error=error.errno), patch.object(session_lease, "_lock", side_effect=error):
                store = SessionStore(self.directory.name, str(uuid.uuid4()))
                state = store.new("claude_subscription")
                store.save(state)
                state["messages"].append({"role": "user", "text": "next"})
                store.save(state)
                self.assertEqual(store.last_write, "append")
                self.assertFalse(session_lease.view(store))

    def test_a_held_lease_is_a_small_file_anyone_can_read_and_copy(self):
        """Windows refuses a read of a locked byte through any other handle. The lease locked its first byte and,
        once the holder's process number was written there, the file could not be read or copied while held:
        Angelo's own carry-over from an earlier version's folder ended "failed ... because another Angelo is
        open" in the very process that held the lease (VM CI of dfdce191576, win-1 to win-3,
        ``test_native_version_carry_over``), and a backup or Blender's copy of the previous version's settings
        would have met the same. The lock is on a byte far past what the file holds."""
        import shutil
        self.assertTrue(self.invoke("edit", "angelo_create_cube", CUBE).get("success"))   # This chat's lease is held.
        lease = session_lease.lease_path(self.c.store.path)
        self.assertEqual(lease.read_bytes(), b"%d\n" % os.getpid())
        copied = Path(self.directory.name) / "copied.lease"
        shutil.copy2(lease, copied)
        self.assertEqual(copied.read_bytes(), lease.read_bytes())
        shutil.copytree(lease.parent, Path(self.directory.name) / "copied leases")
        self.assertEqual(session_lease.holder(self.c.store._writer), os.getpid())
        self.assertGreaterEqual(session_lease._LOCK_AT, 1 << 20)
        text = (Path(ADDONS) / "angelo_agent/session_lease.py").read_text(encoding="utf-8")
        self.assertIn("os.lseek(descriptor, _LOCK_AT, os.SEEK_SET)", text)
        self.assertNotIn("os.lseek(descriptor, 0, os.SEEK_SET)\n        try:\n            msvcrt.locking", text)
        self.goes_on("after-the-copies")   # And the lease is still this process's.

    def test_the_lease_opens_its_own_file_whatever_stands_in_for_the_journals_windows_calls(self):
        from angelo_agent import session_journal
        lease = session_lease.lease_path(Path(self.directory.name) / "sessions" / (str(uuid.uuid4()) + ".json"))

        def stand_in(*_arguments, **_named):
            raise AssertionError("the lease asked session_journal to open its file")
        with patch.object(session_journal, "_win32", stand_in), patch.object(session_journal, "open_shared", stand_in):
            descriptor = session_lease._open(lease)
        try:
            self.assertTrue(lease.is_file())
            lease.unlink()   # While it is held: on Windows only a handle that shares delete lets this through.
            self.assertFalse(lease.exists())
        finally:
            os.close(descriptor)
        text = (Path(ADDONS) / "angelo_agent/session_lease.py").read_text(encoding="utf-8")
        self.assertNotIn("session_journal.open_shared", text)
        self.assertNotIn("session_journal._win32", text)
        self.assertRegex(text, r"_SHARE_ALL\b[^\n]*0x7\b")
        self.assertIn("_OPEN_ALWAYS", text)


if __name__ == "__main__":
    unittest.main()
