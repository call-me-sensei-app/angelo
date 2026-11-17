# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A save made once, with no later try in its launch, waits out a moment's holder (``shared_replace.PATIENT_ATTEMPTS``).

Most saves that Windows refuses are owed and tried again from a timer, so their retries are short (100 ms): a file
held for good must never cost a UI tick more. Two saves have no later try. The per-user cache-key secret is stored
by the first keyed chat of a launch; refused, that launch keeps a secret of its own and shares no prompt-cache key
with later launches. A warm-up's base is saved when the warm-up ends; refused, the request already paid for is
forgotten and the next launch warms again. Both run on a provider's worker thread, never the main thread.

On VM CI (78d279707a1, win-1) the damaged-secret replace in
``test_native_agent_runtime_review.PerUserSecretTests`` was refused for all five tries: another program still held
the file the test had just written (a scan), past 100 ms on a loaded machine. Here a holder lets go after as many
refusals as a tick's save gets (``ntfs_sharing.hold(refusals=...)``: real handles on a Windows host, NTFS's rules
elsewhere), so each test fails on the short retries and passes on the patient ones. A file held for good is still
given up on, after a bounded wait, and the caller falls back as before.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import contextlib
import errno
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import cache_affinity_secret, codex_bases, shared_replace  # noqa: E402
import ntfs_sharing  # noqa: E402

# As many refusals as a tick's save gets: its last try is refused too, so only a longer wait lands.
A_MOMENT = shared_replace.ATTEMPTS


class Case(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.sharing = ntfs_sharing.Sharing(self)   # After the folder: let go of before it is removed.

    def patient_pauses(self):
        return [shared_replace.PAUSE * attempt for attempt in range(1, shared_replace.PATIENT_ATTEMPTS)]


class PerUserSecretTests(Case):
    def setUp(self):
        super().setUp()
        self.data = self.folder / "Angelo"
        self.data.mkdir()
        self.path = self.data / cache_affinity_secret.FILE
        self.path.write_text("not a secret")

    def stored(self):
        """``(secret, what was said on standard error)`` of one launch's store."""
        said = io.StringIO()
        with patch.object(sys, "stderr", said):
            value = cache_affinity_secret._stored(self.data)
        return value, said.getvalue()

    def test_a_damaged_secret_held_for_a_moment_is_still_replaced(self):
        self.sharing.hold(self.path, refusals=A_MOMENT)
        value, said = self.stored()
        self.assertEqual(said, "", "the launch fell back to a secret of its own")
        self.assertEqual(shared_replace.read_text(self.path).strip(), value.hex())
        self.assertGreaterEqual(len(self.sharing.pauses), A_MOMENT, "it waited past a tick's retries")
        self.assertEqual([entry.name for entry in self.data.iterdir()], [cache_affinity_secret.FILE], "no temporary left")
        self.assertEqual(self.stored(), (value, ""), "the next launch reads it")

    def test_so_is_one_on_a_file_system_without_hard_links(self):
        self.sharing.hold(self.path, refusals=A_MOMENT)
        with patch.object(os, "link", side_effect=OSError(errno.EPERM, "Hard links are not supported")):
            value, said = self.stored()
        self.assertEqual((said, shared_replace.read_text(self.path).strip()), ("", value.hex()))

    def test_a_secret_held_for_good_is_given_up_on_after_a_bounded_wait_and_said(self):
        self.sharing.hold(self.path)
        value, said = self.stored()
        self.assertEqual(len(value), 32)
        self.assertIn("could not be stored", said)
        self.assertEqual(said.count("\n"), 1, "said once")
        self.assertEqual(shared_replace.read_text(self.path), "not a secret", "never half replaced")
        self.assertEqual(self.sharing.pauses, self.patient_pauses())
        self.assertEqual([entry.name for entry in self.data.iterdir()], [cache_affinity_secret.FILE], "no temporary left")

    def test_a_first_launch_with_nothing_held_never_waits(self):
        self.path.unlink()
        value, said = self.stored()
        self.assertEqual((said, self.sharing.pauses, shared_replace.read_text(self.path).strip()), ("", [], value.hex()))


class WarmUpBaseTests(Case):
    def setUp(self):
        super().setUp()
        self.store = codex_bases.BaseStore(self.folder)
        self.store.put("scope", "first", "thread-first", None)   # The file exists: a later save replaces it.

    def saved(self, save):
        """What ``save()`` printed (a save that is refused says so there)."""
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            save()
        return said.getvalue()

    def test_a_base_saved_while_its_file_is_held_for_a_moment_is_remembered(self):
        self.sharing.hold(self.store.path, refusals=A_MOMENT)
        said = self.saved(lambda: self.store.put("scope", "second", "thread-second", None))
        self.assertEqual(said, "", "the warm-up's base was not saved")
        again = codex_bases.BaseStore(self.folder)   # The next launch.
        self.assertEqual((again.get("scope", "second")["thread"], again.get("scope", "first")["thread"]),
                         ("thread-second", "thread-first"))
        self.assertEqual([entry.name for entry in self.folder.iterdir()], [codex_bases.FILE], "no temporary left")

    def test_so_is_a_failed_warm_up_s_backoff(self):
        self.sharing.hold(self.store.path, refusals=A_MOMENT)
        said = self.saved(lambda: self.store.put_failure("scope", "second"))
        self.assertEqual(said, "")
        self.assertTrue(codex_bases.BaseStore(self.folder).failed_recently("scope", "second"))

    def test_a_file_held_for_good_is_given_up_on_after_a_bounded_wait_and_said(self):
        self.sharing.hold(self.store.path)
        said = self.saved(lambda: self.store.put("scope", "second", "thread-second", None))
        self.assertIn("could not be saved", said)
        self.assertIsNone(self.store.get("scope", "second"))
        self.assertEqual(self.store.get("scope", "first")["thread"], "thread-first", "what was saved stays")
        self.assertEqual(self.sharing.pauses, self.patient_pauses())
        self.assertEqual([entry.name for entry in self.folder.iterdir()], [codex_bases.FILE], "no temporary left")


class TheBudgetTests(unittest.TestCase):
    # Module -> (replaces that use the patient retries, why it has no later try, the thread it runs on).
    ONCE = {
        "cache_affinity_secret.py": (2, "Stored by a launch's first keyed chat and cached for the process: refused, "
                                        "the launch's cache keys are its own.", "a provider's or the relay's worker"),
        "codex_bases.py": (1, "Saved when a warm-up ends: refused, its base is forgotten and warmed again.",
                           "a provider's worker"),
    }

    def test_the_patient_wait_is_longer_than_a_tick_s_and_bounded(self):
        pauses = [shared_replace.PAUSE * attempt for attempt in range(1, shared_replace.PATIENT_ATTEMPTS)]
        self.assertGreater(shared_replace.PATIENT_ATTEMPTS, shared_replace.ATTEMPTS)
        self.assertGreaterEqual(sum(pauses), 1.0, "a scan outlasted 100 ms on a loaded machine")
        self.assertLessEqual(sum(pauses), 2.0, "never a hang")
        self.assertLess(sum(pauses), shared_replace.RETRY_SECONDS)

    def test_only_the_saves_with_no_later_try_use_it(self):
        used = {}
        for path in sorted(ADDON.glob("*.py")):
            count = path.read_text(encoding="utf-8").count("shared_replace.PATIENT_ATTEMPTS")
            if count and path.name != "shared_replace.py":
                used[path.name] = count
        self.assertGreater(len(list(ADDON.glob("*.py"))), 100)
        self.assertEqual(used, {name: count for name, (count, _why, _thread) in self.ONCE.items()},
                         "a save tried again from a timer or a UI tick keeps the short retries: a file held for good "
                         "must not cost a tick two seconds")
        for name, (_count, why, thread) in self.ONCE.items():
            with self.subTest(module=name):
                self.assertTrue(why and "worker" in thread, "never the main thread")


if __name__ == "__main__":
    unittest.main()
