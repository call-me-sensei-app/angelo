# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A generation's record whose save Windows refuses is shown at once and owed to its file (``studio_commits``).

A worker's events are written to the library on the main thread (``StudioJobs.pump``): each takes the event from the
queue, then saves the asset's record. Another program that holds the record for longer than a tick's retries (100 ms:
a scan of the file the last event wrote, as on VM CI 78d279707a1 for the cache-key secret) refused that save, and the
event was gone: a finished, paid picture stayed "generating" in the window, with no Stop, until the library was read
again; a Generation Flow step waited on it for good; the agent's call failed although the picture was on disk.

The main thread never waits longer for a file (``shared_replace.PATIENT_ATTEMPTS`` is for worker threads), so the
window takes the change at once and the file is owed: written again no sooner than ``shared_replace.RETRY_SECONDS``
later, and once more when Studio closes. What another writer changed meanwhile is kept, and a reload that read the
old file loses nothing. ``ntfs_sharing``: real handles on a Windows host, NTFS's rules on the host's files elsewhere.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import errno
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import shared_replace, studio_state  # noqa: E402
from angelo_agent.studio_generation import StudioJobs  # noqa: E402
from angelo_agent.studio_state import StudioLibrary  # noqa: E402
import ntfs_sharing  # noqa: E402

# As many refusals as a tick's save gets: its last try is refused too, and the holder then lets go.
A_MOMENT = shared_replace.ATTEMPTS
TICK_PAUSES = [shared_replace.PAUSE * attempt for attempt in range(1, shared_replace.ATTEMPTS)]
READY = {"status": "ready", "progress": 100, "files": ["image.png"], "_finished": True}


class Case(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name) / "library"
        self.library = StudioLibrary(self.directory)
        self.asset = self.library.add("IMAGE", "a red kettle", "A model")
        self.identity = self.asset["id"]
        self.path = self.directory / "assets" / (self.identity + ".json")
        self.jobs = StudioJobs(self.library)
        self.jobs.active[self.identity], self.jobs.kinds[self.identity] = object(), "IMAGE"
        self.sharing = ntfs_sharing.Sharing(self)   # After the folder: let go of before it is removed.
        self.now = 1000.0   # The pump's clock: a refusal's time and a later pump's are read from the same one.
        commits = getattr(self.jobs, "commits", None)
        if commits is not None:
            commits.clock = lambda: self.now

    def finish(self):
        """The worker's last event reaches the pump; what the pump returned."""
        self.jobs.events.put((self.identity, dict(READY)))
        return self.jobs.pump()

    def later(self, seconds):
        """A pump ``seconds`` after the last one."""
        self.now += seconds
        return self.jobs.pump()

    def shown(self):
        return self.library.assets[self.identity]

    def on_disk(self):
        """The record as the next launch (or another window) reads it."""
        return StudioLibrary(self.directory).assets.get(self.identity)


class FinishedGenerationTests(Case):
    def test_a_record_held_for_a_moment_is_shown_ready_at_once_and_saved_at_a_later_pump(self):
        self.sharing.hold(self.path, refusals=A_MOMENT)
        self.assertTrue(self.finish(), "the window redraws")
        self.assertEqual((self.shown()["status"], self.shown()["files"]), ("ready", ["image.png"]))
        self.assertNotIn(self.identity, self.jobs.active, "the job left the active set")
        self.assertEqual(self.on_disk()["status"], "generating", "the save was refused")
        self.assertEqual(self.sharing.pauses, TICK_PAUSES, "a tick's retries, never a longer wait on the main thread")
        self.later(shared_replace.RETRY_SECONDS)
        self.assertEqual((self.on_disk()["status"], self.on_disk()["files"]), ("ready", ["image.png"]))
        self.assertEqual(sorted(entry.name for entry in self.path.parent.iterdir()), [self.path.name],
                         "no temporary left")

    def test_a_record_held_for_good_is_tried_after_each_pause_never_at_every_tick_and_once_at_close(self):
        self.sharing.hold(self.path)
        self.finish()
        for _tick in range(50):
            self.later(shared_replace.RETRY_SECONDS / 100)
        self.assertEqual(self.sharing.pauses, TICK_PAUSES, "no try before the pause is over")
        self.later(shared_replace.RETRY_SECONDS)
        self.assertEqual(self.sharing.pauses, TICK_PAUSES * 2, "one try after it")
        self.jobs.close()   # Never raises: the job's own saved state gives the next launch the result.
        self.assertEqual(self.sharing.pauses, TICK_PAUSES * 3, "and one when Studio closes")
        self.assertEqual((self.shown()["status"], self.on_disk()["status"]), ("ready", "generating"))
        self.sharing.let_go_all()
        self.later(shared_replace.RETRY_SECONDS)
        self.assertEqual(self.on_disk()["status"], "ready", "it lands once the file is free")

    def test_what_another_writer_changed_meanwhile_is_kept(self):
        self.sharing.hold(self.path, refusals=A_MOMENT)
        self.finish()
        folder = self.library.folder("Kettles")["id"]
        self.library.update(dict(self.shown(), folder=folder, status="stopped"))   # The person's own change, landed.
        self.later(shared_replace.RETRY_SECONDS)
        for record in (self.shown(), self.on_disk()):
            self.assertEqual((record["folder"], record["status"], record["files"]), (folder, "stopped", ["image.png"]))

    def test_a_reload_that_read_the_old_file_loses_nothing(self):
        self.sharing.hold(self.path)
        self.finish()
        self.library.reload()   # Another tool's refresh: the file still says "generating".
        self.assertEqual(self.shown()["status"], "generating")
        self.sharing.let_go_all()
        self.assertTrue(self.later(shared_replace.RETRY_SECONDS), "the window redraws")
        self.assertEqual((self.shown()["status"], self.on_disk()["status"]), ("ready", "ready"))

    def test_a_later_event_that_lands_ends_what_was_owed(self):
        self.sharing.hold(self.path, refusals=A_MOMENT)
        self.jobs.events.put((self.identity, {"status": "generating", "progress": 40}))
        self.jobs.pump()   # Refused: the progress is owed.
        self.finish()      # Lands, with the progress it follows.
        self.assertEqual((self.on_disk()["status"], self.on_disk()["progress"]), ("ready", 100))
        tries = len(self.sharing.pauses)
        self.later(shared_replace.RETRY_SECONDS)
        self.assertEqual(len(self.sharing.pauses), tries, "nothing is written again")

    def test_a_record_gone_from_the_library_is_never_written_back(self):
        holder = self.sharing.hold(self.path)
        self.finish()
        self.sharing.let_go(holder)
        self.path.unlink()       # Removed by another Angelo's window.
        self.library.reload()
        self.later(shared_replace.RETRY_SECONDS)
        self.assertFalse(self.path.exists())
        self.assertNotIn(self.identity, self.library.assets)

    def test_with_nothing_held_the_record_is_saved_at_once(self):
        self.assertTrue(self.finish())
        self.assertEqual((self.shown()["status"], self.on_disk()["status"], self.sharing.pauses),
                         ("ready", "ready", []))

    def test_an_event_that_changes_nothing_writes_nothing(self):
        self.jobs.events.put((self.identity, {"status": "generating"}))
        with patch.object(studio_state, "atomic_json", side_effect=AssertionError("written")):
            self.assertFalse(self.jobs.pump())

    def test_a_failure_that_is_not_a_holder_is_raised_as_before(self):
        with patch.object(studio_state, "atomic_json", side_effect=OSError(errno.ENOSPC, "No space left on device")):
            with self.assertRaises(OSError):
                self.finish()


if __name__ == "__main__":
    unittest.main()
