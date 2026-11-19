# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Restart to Update never hands over a document edited after the person's decision (update_restart_handoff).

The maintainer's audit of 2.0.1 (P1): the clean path and Don't Save entered the hand-over's settle timer, which took
any document state that read the same twice. An undoable edit landing during that wait became the stamp the installer
kept, so the edit was treated as accepted and lost at the swap. The hand-over now keeps the decision: the document's
stamp, its asset-catalog generation and every edit event from the moment the person decided. Each way in separately:

* clean file: any change while it settles never hands over and asks again (the save card, as the edit is unsaved);
* Don't Save: the discarded state is the only one handed over; an edit after it asks again;
* Save and Restart: only the save marking the file and images saved may settle (its own catalog write included,
  which advances the catalogs' generation: after a save only catalogs unsaved again count); anything after it stops
  the restart and says so (as before, now at once instead of after the settle timeout);
* in every route, an edit event (an undo step, a depsgraph update, a load) whose stamp reads the same as before
  (an edit and its Undo) still counts, and a catalog edit counts too; the change handlers go once the wait ends.

On the stand-in bpy of test_native_update_ready (Blender's save timing: ``Data.notifier_pass``). The real app's timing
is test_blender_update_restart_settle.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import notices_fakes  # noqa: E402
import test_native_update_ready as ready  # noqa: E402


class EditsWhileSettling(ready.RestartCase):
    def setUp(self):
        super().setUp()
        self.bpy.data.filepath = "/work/castle.blend"

    def one_pass(self):
        """One event-loop pass: Blender's notifiers, then the settle timer."""
        notices_fakes.settle(self.bpy, passes=1)

    def edit(self, dirty=True):
        """An edit as the person makes one: an undo step and a depsgraph update, and the file marked modified."""
        for name in ("undo_pre", "depsgraph_update_post"):
            for handler in list(getattr(self.bpy.app.handlers, name)):
                handler(None, None)
        self.bpy.data.is_dirty = dirty

    def handlers_left(self):
        return sum(len(getattr(self.bpy.app.handlers, name)) for name in
                   ("depsgraph_update_post", "load_pre", "save_pre", "undo_pre", "redo_pre"))

    # The clean path -------------------------------------------------------------------------------------------------

    def test_a_clean_file_unchanged_hands_over_once(self):
        self.press()
        self.assertEqual(len(self.started), 1)

    def test_an_edit_while_a_clean_file_settles_never_hands_over_and_asks_again(self):   # The audit's P1.
        self.restart.begin(self.offer_, self.prepared, self.host)
        self.one_pass()
        self.edit()
        self.settle()
        self.assertEqual(self.started, [], "the edit is never treated as accepted")
        self.assertEqual(self.shown("update_save").name, "castle.blend", "the person is asked again")
        self.assertEqual(self.handlers_left(), 0)

    def test_an_edit_and_its_undo_while_settling_still_count(self):
        self.restart.begin(self.offer_, self.prepared, self.host)
        self.one_pass()
        self.edit(dirty=False)   # An edit Undone at once: the stamp reads as before, the events don't.
        self.settle()
        self.assertEqual(self.started, [])
        self.assertTrue(self.queued("update"), "nothing unsaved: the update card comes back")

    def test_a_catalog_edit_while_settling_never_hands_over(self):
        generations = iter([17, 18, 18, 18, 18])
        with mock.patch.object(self.restart, "_catalog_generation", lambda native: next(generations, 18)):
            self.restart.begin(self.offer_, self.prepared, self.host)
            self.settle()
        self.assertEqual(self.started, [])

    # Don't Save -----------------------------------------------------------------------------------------------------

    def test_dont_save_hands_over_the_discarded_state(self):
        self.bpy.data.is_dirty = True
        self.press()
        self.shown("update_save").act("dont_save", None, self.host)
        self.settle()
        self.assertEqual(len(self.started), 1)

    def test_an_edit_after_dont_save_never_hands_over_and_asks_again(self):
        self.bpy.data.is_dirty = True
        self.press()
        self.shown("update_save").act("dont_save", None, self.host)
        self.one_pass()
        self.edit()   # Still modified: the stamp reads the same, the edit event does not.
        self.settle()
        self.assertEqual(self.started, [], "only the state the person discarded is handed over")
        self.assertTrue(self.queued("update_save"), "the person is asked again")
        self.assertEqual(self.handlers_left(), 0)

    def test_an_image_painted_after_dont_save_never_hands_over(self):
        image = SimpleNamespace(name="paint", is_dirty=False)
        image.as_pointer = lambda: 7
        self.bpy.data.images, self.bpy.data.is_dirty = [image], True
        self.press()
        self.shown("update_save").act("dont_save", None, self.host)
        self.one_pass()
        image.is_dirty = True   # No event reached the handlers: the stamp alone says it.
        self.settle()
        self.assertEqual(self.started, [])

    # Save and Restart -----------------------------------------------------------------------------------------------

    def test_save_and_restart_settles_on_the_save_alone(self):
        self.bpy.data.is_dirty = True
        self.press()
        self.shown("update_save").act("save", None, self.host)
        self.settle()
        self.assertEqual(len(self.started), 1)

    def test_an_edit_after_the_save_stops_at_once_and_says_so(self):
        self.bpy.data.is_dirty = True
        self.press()
        self.shown("update_save").act("save", None, self.host)
        self.one_pass()   # The save's notifier: saved.
        self.edit()
        self.one_pass()   # No settle timeout: the edit stops it.
        self.assertEqual(self.started, [])
        self.assertIn("Angelo did not restart", [c.title for c in self.queued("notice")])
        self.assertEqual(self.handlers_left(), 0)

    def test_the_saves_own_catalog_write_settles(self):   # Seen in the app: the save advances the generation.
        self.bpy.data.is_dirty = True
        self.press()
        generations = iter([17, 40])
        with mock.patch.object(self.restart, "_catalog_generation", lambda native: next(generations, 40)):
            self.shown("update_save").act("save", None, self.host)
            self.settle()
        self.assertEqual(len(self.started), 1)

    def test_a_catalog_edited_after_the_save_stops_it(self):
        self.bpy.data.is_dirty = True
        self.press()
        generations = iter([17, 40])
        with mock.patch.object(self.restart, "_catalog_generation", lambda native: next(generations, 40)), \
                mock.patch.object(self.restart, "_catalogs_unsaved", lambda: True):
            self.restart._launch_settled(self.offer_, self.prepared, saved=True)
            self.bpy.data.is_dirty = False
            self.settle()
        self.assertEqual(self.started, [])
        self.assertIn("Angelo did not restart", [c.title for c in self.queued("notice")])

    def test_a_file_dirty_again_after_reading_saved_never_settles(self):
        self.bpy.data.is_dirty = True
        self.press()
        self.shown("update_save").act("save", None, self.host)
        self.one_pass()
        self.bpy.data.is_dirty = True   # No event reached the handlers: the stamp alone says it.
        self.settle()
        self.assertEqual(self.started, [])

    def test_another_file_loaded_while_settling_never_hands_over(self):
        self.restart.begin(self.offer_, self.prepared, self.host)
        self.one_pass()
        self.bpy.data.filepath = "/work/other.blend"
        self.settle()
        self.assertEqual(self.started, [])


class TheStandInsHandlers(unittest.TestCase):
    """The stand-in bpy carries every handler list Blender's ``bpy.app.handlers`` has, each empty and its own.

    A list missing from it failed only the tests that had not added it themselves (VM CI of f9f749b4af7:
    test_native_restored_document, after the hand-over began watching edits); Blender always has them all."""

    def test_every_list_of_blender_s_is_there(self):
        source = (HERE.parents[2] / "source/blender/python/intern/bpy_app_handlers.cc").read_text(encoding="utf-8")
        fields = source.split("app_cb_info_fields[] = {", 1)[1].split("{nullptr}", 1)[0]
        blender = [name for name in re.findall(r'^\s*\{"(\w+)"', fields, re.MULTILINE) if name != "persistent"]
        self.assertGreater(len(blender), 40)
        self.assertEqual(list(notices_fakes.APP_HANDLER_LISTS), blender)
        modules, _record = notices_fakes.build()
        handlers = modules["bpy.app.handlers"]
        lists = [getattr(handlers, name) for name in blender]
        self.assertTrue(all(value == [] for value in lists))
        self.assertEqual(len({id(value) for value in lists}), len(lists), "each list is its own")


if __name__ == "__main__":
    unittest.main()
