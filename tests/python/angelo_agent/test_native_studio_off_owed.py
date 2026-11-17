# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Turning the Asset Browser library off stays off when another program held its record at the click.

Turn off removes the Preferences entry and then records the removal (``registration.json``). The record is what keeps
the library off: at the next launch an entry that is missing is added again unless the record says it was removed.
On Windows a program holding the record past a tick's retries (100 ms) refused that save after the entry was gone,
and nothing wrote it again, so the next launch turned the library back on.

The refusal is still raised (the person is told), and the record stays owed: the tick writes it again no sooner than
``shared_replace.RETRY_SECONDS`` after each refusal, and once more as the add-on is unregistered. Turn on, or an
entry that is back, ends what is owed. The holder here is a stand-in that refuses the record's replace as
``shared_replace`` reports a holder (``Refused``); NTFS's own rules are ``test_native_shared_replace``'s.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import errno
import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_studio_assets_space import MODULES  # noqa: E402  (the stand-in bpy the add-on was imported with)

with mock.patch.dict(sys.modules, MODULES):
    from angelo_agent import studio_assets, studio_assets_host as host, studio_assets_space as space  # noqa: E402

# The copy of ``shared_replace`` the record is written through (``atomic_json``'s own).
WRITER = studio_assets.atomic_json.__globals__["shared_replace"]
# The copy the code under test catches from. A tree without the fix has no ``studio_assets_space.shared_replace``:
# the writer's copy stands in there, so each test fails as a test and the file never fails at import (on a Windows
# shard an import error ended the whole run before any test of that shard: VM CI, a542c361067, win-1).
REPLACES = getattr(space, "shared_replace", WRITER)
Refused = REPLACES.Refused
PAUSE = REPLACES.RETRY_SECONDS


class Holder:
    """Another program holding ``registration.json``: its next ``refusals`` replaces are refused (None: for good)."""

    def __init__(self, refusals):
        self.left, self.tries = refusals, 0

    def replace(self, source, target, *_arguments, **_keywords):
        if Path(target).name == studio_assets.REGISTRATION:
            self.tries += 1
            if self.left is None or self.left > 0:
                self.left = None if self.left is None else self.left - 1
                raise Refused(errno.EACCES, "Access is denied", str(target))
        return os.replace(source, target)


class Libraries(list):
    def new(self, name, directory):
        entry = SimpleNamespace(name=name, path=directory, import_method='PACK')
        self.append(entry)
        return entry


class TurnOffHeldTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.directory = Path(root.name) / "blender-assets"
        self.directory.mkdir()
        self.libraries = Libraries([SimpleNamespace(name="Angelo Media Studio", path=str(self.directory))])
        studio_assets.write_registration(self.directory, removed=False)
        self.now = 1000.0
        self.forget()
        self.addCleanup(self.forget)
        for patcher in (mock.patch.dict(host._state, registered=True, active=True),
                        mock.patch.object(host, "directories", return_value=(Path(root.name), self.directory)),
                        mock.patch.object(host, "_libraries", return_value=self.libraries),
                        mock.patch.object(space, "time", SimpleNamespace(monotonic=lambda: self.now)),
                        mock.patch.dict(sys.modules, MODULES)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def held(self, refusals):
        holder = Holder(refusals)
        patcher = mock.patch.object(WRITER, "replace", holder.replace)
        patcher.start()
        self.addCleanup(patcher.stop)
        return holder

    @staticmethod
    def forget():
        getattr(space, "_off_owed", {}).clear()

    @staticmethod
    def owed():
        """The folders whose Turn off is still owed."""
        return sorted(getattr(space, "_off_owed", {}))

    def removed(self):
        """What the record on disk says, as the next launch reads it."""
        return bool(studio_assets.read_registration(self.directory).get("removed"))

    def tick(self, seconds):
        self.now += seconds
        return host.observe()

    def test_a_record_held_at_the_click_is_written_at_a_later_tick_and_the_next_launch_keeps_it_off(self):
        self.held(1)
        with self.assertRaises(Refused):   # The person is told, as before.
            host.turn_off()
        self.assertEqual((host._state["registered"], list(self.libraries), self.removed()), (False, [], False))
        self.assertFalse(self.tick(PAUSE))
        self.assertTrue(self.removed(), "the record was never written again")
        self.assertEqual(self.owed(), [])
        self.assertFalse(host.ensure_registered(), "the next launch added the library back")
        self.assertEqual(list(self.libraries), [])

    def test_a_record_held_for_good_is_tried_after_each_pause_never_at_every_tick_and_once_at_unregister(self):
        holder = self.held(None)
        with self.assertRaises(Refused):
            host.turn_off()
        for _tick in range(20):
            self.tick(PAUSE / 100)
        self.assertEqual(holder.tries, 1, "no try before the pause is over")
        self.tick(PAUSE)
        self.assertEqual(holder.tries, 2, "one try after it")
        space.settle_all_off()
        self.assertEqual(holder.tries, 3, "and one as the add-on is unregistered")
        self.assertIn("studio_assets_space.settle_all_off()", inspect.getsource(host.unregister))
        holder.left = 0   # The other program lets go.
        self.tick(PAUSE)
        self.assertEqual((self.removed(), self.owed()), (True, []))

    def test_turn_on_after_a_refused_turn_off_stays_on(self):
        self.held(1)
        with self.assertRaises(Refused):
            host.turn_off()
        host.turn_on()
        self.assertEqual((len(self.libraries), self.removed(), self.owed()), (1, False, []))
        self.assertTrue(self.tick(PAUSE))
        self.assertFalse(self.removed(), "the off was written after Turn on")

    def test_an_entry_that_is_back_ends_what_was_owed(self):
        holder = self.held(None)
        with self.assertRaises(Refused):
            host.turn_off()
        self.libraries.new("Angelo Media Studio", str(self.directory))   # Added again in Preferences.
        holder.left = 0
        self.assertTrue(self.tick(PAUSE))
        self.assertEqual((self.removed(), self.owed()), (False, []))

    def test_with_nothing_held_turn_off_owes_nothing(self):
        host.turn_off()
        self.assertEqual((self.removed(), self.owed()), (True, []))

    def test_a_failure_that_is_not_a_holder_is_raised_and_never_owed(self):
        full = OSError(errno.ENOSPC, "No space left on device")
        with mock.patch.object(WRITER, "replace", side_effect=full), self.assertRaises(OSError):
            host.turn_off()
        self.assertEqual(self.owed(), [])


if __name__ == "__main__":
    unittest.main()
