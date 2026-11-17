# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A crash-report choice or record whose save Windows refuses is kept, and written again (``crash_state``).

``crash-reports.json`` holds the person's choice (ask, always, never) and which crash logs were dealt with. Every
save loads the file, changes it and writes it whole. A program holding the file past a tick's retries (100 ms)
refused the save, ``save`` returned False and nobody looked: the next ``load`` read the old file. A report already
sent was offered again at the next launch (and could be sent twice), and "Remember my choice" was forgotten.

A refused state is now owed: ``load`` gives it back, so every later change builds on it, and ``settle`` writes it
again no sooner than ``shared_replace.RETRY_SECONDS`` after each refusal, and once more as the add-on is
unregistered (``crash_ui``). ``ntfs_sharing``: real handles on a Windows host, NTFS's rules elsewhere.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import errno
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import crash_state, shared_replace, studio_state  # noqa: E402
import ntfs_sharing  # noqa: E402

A_MOMENT = shared_replace.ATTEMPTS   # As many refusals as a tick's save gets; the holder then lets go.
TICK_PAUSES = [shared_replace.PAUSE * attempt for attempt in range(1, shared_replace.ATTEMPTS)]
CRASH = crash_state.fingerprint("angelo.crash.txt", 1, 2)


class Case(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        self.path = self.directory / crash_state.STATE_FILE
        self.assertTrue(crash_state.save(self.directory, crash_state.CrashState()))   # The file exists.
        self.forget()
        self.addCleanup(self.forget)
        self.sharing = ntfs_sharing.Sharing(self)   # After the folder: let go of before it is removed.
        self.now = 1000.0   # The module's clock: a refusal's time and ``settle``'s read the same one.
        clock = patch.object(crash_state, "time", SimpleNamespace(time=time.time, monotonic=lambda: self.now))
        clock.start()
        self.addCleanup(clock.stop)

    @staticmethod
    def forget():
        getattr(crash_state, "_owed", {}).clear()

    def sent(self):
        """A report is sent and marked, as ``CrashFlow._mark`` does; what ``save`` returned."""
        state = crash_state.load(self.directory)
        state.mark(CRASH, crash_state.SENT, "report-1", now=5.0)
        return crash_state.save(self.directory, state)

    def later(self, seconds, final=False):
        self.now += seconds
        crash_state.settle(final=final)

    def next_launch(self):
        """The state as a launch that knows nothing of this one reads it."""
        self.forget()
        return crash_state.load(self.directory)


class OwedStateTests(Case):
    def test_a_sent_report_whose_save_was_held_for_a_moment_is_never_offered_again(self):
        self.sharing.hold(self.path, refusals=A_MOMENT)
        self.assertFalse(self.sent(), "the save was refused")
        self.assertEqual(self.sharing.pauses, TICK_PAUSES, "a tick's retries, never a longer wait")
        self.assertTrue(crash_state.load(self.directory).is_handled(CRASH), "this launch forgot the report was sent")
        self.later(shared_replace.RETRY_SECONDS)
        self.assertTrue(self.next_launch().is_handled(CRASH), "the next launch offers the sent report again")
        self.assertEqual([entry.name for entry in self.directory.iterdir()], [crash_state.STATE_FILE])

    def test_a_remembered_choice_made_while_the_file_was_held_builds_on_what_is_owed(self):
        self.sharing.hold(self.path, refusals=A_MOMENT)
        self.sent()
        state = crash_state.load(self.directory)   # "Remember my choice", a moment later.
        state.consent = crash_state.ALWAYS
        self.assertTrue(crash_state.save(self.directory, state), "the holder let go")
        again = self.next_launch()
        self.assertEqual((again.consent, again.is_handled(CRASH)), (crash_state.ALWAYS, True))
        tries = len(self.sharing.pauses)
        self.later(shared_replace.RETRY_SECONDS)
        self.assertEqual(len(self.sharing.pauses), tries, "nothing is owed any more")

    def test_a_file_held_for_good_is_tried_after_each_pause_never_sooner_and_once_at_the_end(self):
        self.sharing.hold(self.path)
        self.sent()
        for _tick in range(20):
            self.later(shared_replace.RETRY_SECONDS / 100)
        self.assertEqual(self.sharing.pauses, TICK_PAUSES, "no try before the pause is over")
        self.later(shared_replace.RETRY_SECONDS)
        self.assertEqual(self.sharing.pauses, TICK_PAUSES * 2, "one try after it")
        self.later(0.0, final=True)
        self.assertEqual(self.sharing.pauses, TICK_PAUSES * 3, "and one as the add-on is unregistered")
        self.assertTrue(crash_state.owed())
        self.sharing.let_go_all()
        self.later(shared_replace.RETRY_SECONDS)
        self.assertFalse(crash_state.owed())
        self.assertTrue(self.next_launch().is_handled(CRASH))

    def test_the_window_is_asked_to_write_it_again_and_does_so_until_it_lands(self):
        asked = []
        self.sharing.hold(self.path, refusals=A_MOMENT)
        with patch.object(crash_state, "on_owed", lambda: asked.append(1), create=True):
            self.sent()
        self.assertEqual(asked, [1])
        source = (ADDON / "crash_ui.py").read_text(encoding="utf-8")
        for needed in ("crash_state.on_owed = _settle_later", "crash_state.settle(final=True)",
                       "return shared_replace.RETRY_SECONDS if crash_state.owed() else None"):
            self.assertIn(needed, source)

    def test_with_nothing_held_a_save_lands_at_once_and_owes_nothing(self):
        self.assertTrue(self.sent())
        self.assertEqual((self.sharing.pauses, getattr(crash_state, "_owed", {})), ([], {}))
        self.assertTrue(self.next_launch().is_handled(CRASH))

    def test_a_folder_that_cannot_be_written_is_not_owed(self):
        with patch.object(studio_state.shared_replace, "replace", side_effect=OSError(errno.EROFS, "Read-only")):
            self.assertFalse(self.sent())
        self.assertEqual(getattr(crash_state, "_owed", {}), {})
        self.assertFalse(crash_state.load(self.directory).is_handled(CRASH), "as before: this session's object only")


if __name__ == "__main__":
    unittest.main()
