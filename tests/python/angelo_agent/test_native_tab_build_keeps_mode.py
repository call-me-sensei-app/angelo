# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No step of Manual's tab build changes the window's tab or the active object's mode as a side effect
(``studio_workspaces``, ``timer_jobs``).

Evidence (an-blender53 on Mac 1, 2026-11-16, 758b07e8b77: a 655k-vertex sculpt file reopened with Load UI off while
the build ran): the half-made Media Studio tab outlived the interrupted build, and about 2 s later the next build
deleted it while the window showed it. Blender's Delete Workspace moves such a window to the tab's neighbour in the
strip, with the neighbour's object mode (``ED_workspace_delete``): the sculpted mesh went into Edit Mode (+1.3 GB,
seconds). Two faults let it through:

* ``timer_jobs.layout_kept`` compared the workspaces' ``session_uid``, which Blender renews on every load, the workspaces
  Load UI off keeps included (``swap_old_bmain_data_for_blendfile``): no load ever read as keeping the layout, so a
  build interrupted by one was never rolled back and its tab stayed half-made;
* removing a half-made tab deleted it under whatever window showed it;
* Blender's Duplicate Workspace copies a workspace's properties: Director's workspace, copied from the window's
  half-made Media Studio tab, carried its markers, and the next build deleted Director (a tab Angelo's tab build never
  made) under the window, which went to the stock Modeling tab in Edit Mode; the tabs built next took Edit Mode from
  there (an-blender53, run2/reopen-new).

Covered on the stand-in ``bpy`` of ``test_native_timer_jobs``, whose Delete Workspace moves a window still showing the
deleted tab to the neighbour, with the neighbour's mode, as Blender's does. The running app's proof:
``test_blender_tab_build_keeps_mode``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from test_native_timer_jobs import ADDON, Stage, Workspace, studio_tab_build, studio_workspaces, timer_jobs

UNFINISHED = studio_workspaces.UNFINISHED
REMOVE = studio_workspaces._remove   # The real one: Stage stands it in.


class Blender(Stage):
    """Stage, with Blender's own order: a switch (``window.workspace = ...``) and Delete Workspace are queued and
    applied on the next pass, in order (``flush``); Delete Workspace deletes the window's own workspace (a
    ``temp_override`` workspace is not the context's), and a window still showing it goes to its neighbour in the
    strip, with the neighbour's object mode; a switch gives the object the new workspace's mode when it differs."""

    def setUp(self):
        super().setUp()
        self.workspaces.insert(0, Workspace("Animation", 5, []))   # The strip's neighbour, in Edit Mode.
        self.workspaces["Animation"].object_mode = "EDIT"
        self.workspaces["Layout"].object_mode = "SCULPT"            # Where the person sculpts.
        self.events, self.modes, self.queue = [], [], []

        def delete():
            window = self.override["window"]
            self.queue.append(("delete", window.workspace, window))   # The window's own workspace.
            return {'FINISHED'}
        self.bpy.ops.workspace.delete = delete
        temp_override = self.bpy.context.temp_override

        def override(**kwargs):
            window = kwargs.get("window")
            self.bpy.context.workspace = window.workspace if window else None
            return temp_override(**kwargs)
        self.bpy.context.temp_override = override
        duplicate = self.bpy.ops.workspace.duplicate

        def duplicate_with_mode():   # Blender's copy keeps its source's mode, and queues its own switch.
            result = duplicate()
            self.workspaces[-1].object_mode = self.window.workspace.object_mode
            self.queue.append(("set", self.window, self.workspaces[-1]))
            return result
        self.bpy.ops.workspace.duplicate = duplicate_with_mode
        for patch in (mock.patch.object(studio_workspaces, "_remove", REMOVE),
                      mock.patch.object(type(self.window), "_show",
                                        lambda window, ws: self.queue.append(("set", window, ws)))):
            patch.start()
            self.addCleanup(patch.stop)
        self.mode = "SCULPT"   # The active object's mode.

    def show(self, window, workspace):
        """A switch as ED_workspace_change makes it: the object takes the new workspace's mode when it differs."""
        if workspace.object_mode != window.workspace.object_mode:
            self.mode = workspace.object_mode
        self.modes.append(self.mode)
        Workspace_show(window, workspace)

    def flush(self):
        """The next pass: the queued switches and deletes, in order."""
        while self.queue:
            kind, first, second = self.queue.pop(0)
            if kind == "set":   # ("set", window, workspace)
                if second in self.workspaces:
                    self.show(first, second)
                continue
            doomed, window = first, second   # ("delete", workspace, window)
            if doomed not in self.workspaces:
                continue
            self.events.append(("delete", doomed.name, window.workspace.name))
            index = self.workspaces.index(doomed)
            neighbour = self.workspaces[index - 1 if index else 1]
            for each in self.bpy.context.window_manager.windows:
                if each.workspace is doomed:   # ED_workspace_delete -> ED_workspace_change.
                    self.show(each, neighbour)
            self.removed.append(doomed.name)
            self.workspaces.remove(doomed)

    def start(self, passes):
        studio_tab_build.run(self.window)
        step = self.timers.pop()
        for _ in range(passes):
            self.assertIsNotNone(step())
            self.flush()
        return step

    def renew_session_uids(self):
        """What any load does, the workspaces and window manager Load UI off keeps included (blendfile.cc)."""
        for workspace in self.workspaces:
            workspace.session_uid += 1000
        self.bpy.context.window_manager.session_uid += 1


def Workspace_show(window, workspace):
    window._workspace, window.screen = workspace, workspace.screen


class LayoutKeptTests(Blender):
    def test_load_ui_off_reads_as_keeping_the_layout_though_every_session_uid_is_renewed(self):
        self.fire_load_pre()
        self.renew_session_uids()
        self.assertTrue(timer_jobs.layout_kept(), "the same workspaces, kept: Load UI off")

    def test_a_new_file_s_own_workspaces_read_as_replacing_it(self):
        self.fire_load_pre()
        self.workspaces[:] = [Workspace(w.name, w.as_pointer() + 500, []) for w in self.workspaces]
        self.assertFalse(timer_jobs.layout_kept())

    def fire_load_pre(self):
        timer_jobs._load_pre()


class InterruptedBuildTests(Blender):
    def test_load_ui_off_mid_build_rolls_back_with_the_window_home_and_the_mode_kept(self):
        """The window shows the half-made tab when the load lands; the load keeps the layout (and renews the ids)."""
        step = self.start(2)   # The copy shows, an area still to close.
        self.assertEqual(self.window.workspace.name, "Media Studio")
        timer_jobs._load_pre()
        self.renew_session_uids()
        timer_jobs._load_post()
        studio_tab_build.load_post(None)
        self.flush()
        self.assertEqual(self.removed, ["Media Studio"], "the interrupted build's half-made tab is rolled back")
        self.assertEqual(self.window.workspace.name, "Layout", "the window ends where the build found it")
        self.assertEqual(self.mode, "SCULPT", "the object never left its mode")
        self.assertNotIn("EDIT", self.modes)
        self.assertIsNone(step())


class LeftoverTests(Blender):
    def leftover(self, recorded):
        tab = Workspace("Media Studio", 30, [])
        tab["angelo_tab"], tab[UNFINISHED], tab.object_mode = "studio", recorded, "SCULPT"
        self.workspaces.insert(1, tab)   # Right after its neighbour in Edit Mode.
        Workspace_show(self.window, tab)   # A file saved mid-build (quit.blend, a save), opened showing it.
        return tab

    def build(self):
        with mock.patch.object(studio_workspaces, "missing", lambda: []):
            studio_tab_build.run(self.window)
            step = self.timers.pop()
            for _ in range(10):
                result = step()
                self.flush()
                if result is None:
                    return
        self.fail("the build never finished")

    def test_the_next_build_moves_the_window_off_a_shown_leftover_before_deleting_it(self):
        self.leftover("Layout")
        self.build()
        self.assertEqual(self.removed, ["Media Studio"])
        self.assertEqual(self.events, [("delete", "Media Studio", "Layout")],
                         "deleted once, from a window already showing the build's home")
        self.assertEqual(self.window.workspace.name, "Layout", "the home its build recorded")
        self.assertEqual((self.mode, self.modes), ("SCULPT", ["SCULPT"]), "the mode kept at every switch")

    def test_a_leftover_from_an_older_build_goes_to_an_ordinary_workspace_with_the_mode_kept(self):
        self.leftover(True)   # Saved before the home was recorded.
        self.build()
        self.assertEqual(self.removed, ["Media Studio"])
        self.assertIsNone(studio_workspaces.tab_kind(self.window.workspace), "an ordinary workspace")
        self.assertNotIn(UNFINISHED, self.window.workspace)
        self.assertNotIn("EDIT", self.modes)
        self.assertEqual(self.mode, "SCULPT")

    def test_a_shown_leftover_is_never_deleted_with_nowhere_to_go(self):
        tab = self.leftover("Layout")
        self.workspaces[:] = [tab]   # Only the leftover: Blender refuses to delete the last workspace anyway.
        with mock.patch("builtins.print"):
            REMOVE(self.window, tab)
        self.assertEqual(self.events, [])
        self.assertIs(self.window.workspace, tab)


class StrayRemoveTests(Blender):
    def test_every_window_showing_it_moves_first_and_the_delete_goes_on_the_last_of_them(self):
        """Blender applies the windows' notifiers window by window, each window's in order: queued on the last
        window that shows the tab, the delete applies after every one of their switches."""
        tab = Workspace("Media Studio", 30, [])
        tab["angelo_tab"], tab[UNFINISHED], tab.object_mode = "studio", "Layout", "SCULPT"
        self.workspaces.insert(1, tab)
        second = type(self.window)()
        second.as_pointer = lambda: 0x9200
        Workspace_show(second, tab)
        Workspace_show(self.window, tab)
        self.bpy.context.window_manager.windows.append(second)
        REMOVE(self.window, tab)
        self.assertEqual(self.queue[-1][0], "delete")
        self.assertIs(self.queue[-1][2], second, "queued on the last window that shows it")
        self.flush()
        self.assertEqual([w.workspace.name for w in (self.window, second)], ["Layout", "Layout"])
        self.assertEqual(self.removed, ["Media Studio"])
        self.assertEqual(self.workspaces["Layout"].object_mode, "SCULPT")


class ObjectModeTests(Blender):
    def test_the_window_s_destination_gets_the_object_s_own_mode_not_a_stale_one(self):
        """A load can leave the shown tab's mode apart from the object's: the home keeps the object's, never the
        stale one (switching to it changes nothing, and nor does a later switch back from another workspace)."""
        tab = Workspace("Media Studio", 30, [])
        tab["angelo_tab"], tab[UNFINISHED], tab.object_mode = "studio", "Layout", "OBJECT"   # Stale.
        self.workspaces.insert(1, tab)
        Workspace_show(self.window, tab)
        self.window.view_layer = SimpleNamespace(objects=SimpleNamespace(active=SimpleNamespace(mode="SCULPT")))
        self.assertTrue(REMOVE(self.window, tab))
        self.assertEqual(self.workspaces["Layout"].object_mode, "SCULPT")


class UnshownTabTests(Blender):
    def unshown(self):
        tab = Workspace("Media Studio", 30, [])
        tab["angelo_tab"], tab[UNFINISHED], tab.object_mode = "studio", "Layout", "OBJECT"
        self.workspaces.insert(1, tab)
        return tab

    def test_remove_never_deletes_through_a_window_that_shows_another_workspace(self):
        """Delete Workspace would delete that window's own workspace (the stock Layout tab, in the evidence)."""
        tab = self.unshown()
        self.assertFalse(REMOVE(self.window, tab))
        self.assertEqual(self.queue, [])

    def test_the_build_shows_it_with_the_window_s_mode_then_deletes_it_and_goes_back(self):
        self.unshown()
        with mock.patch.object(studio_workspaces, "missing", lambda: []):
            studio_tab_build.run(self.window)
            step = self.timers.pop()
            for _ in range(10):
                result = step()
                self.flush()
                if result is None:
                    break
        self.assertEqual(self.removed, ["Media Studio"])
        self.assertEqual(self.events, [("delete", "Media Studio", "Layout")])
        self.assertEqual(self.window.workspace.name, "Layout")
        self.assertEqual(set(self.modes), {"SCULPT"}, "the mode kept at every switch")


class CopiedMarkerTests(Blender):
    def director_copied_from_the_half_made_tab(self):
        director = Workspace("Director", 7, [])
        director["angelo_director"], director["angelo_manual_workspace"] = True, "Media Studio"
        director["angelo_tab"], director[UNFINISHED] = "studio", "Layout"   # Copied with the tab's properties.
        director.object_mode = "SCULPT"
        self.workspaces.insert(1, director)   # Animation (Edit Mode) is its neighbour.
        tab = Workspace("Media Studio", 30, [])
        tab["angelo_tab"], tab[UNFINISHED], tab.object_mode = "studio", "Layout", "SCULPT"
        self.workspaces.append(tab)
        Workspace_show(self.window, director)
        return director

    def test_a_copy_carrying_a_tab_s_markers_is_kept_and_loses_them(self):
        director = self.director_copied_from_the_half_made_tab()
        with mock.patch.object(studio_workspaces, "missing", lambda: []):
            studio_tab_build.run(self.window)
            step = self.timers.pop()
            for _ in range(10):
                result = step()
                self.flush()
                if result is None:
                    break
        self.assertEqual(self.removed, ["Media Studio"], "only the tab the build made")
        self.assertIn(director, self.workspaces)
        self.assertEqual([k for k in ("angelo_tab", UNFINISHED) if k in director], [], "no tab's markers left")
        self.assertTrue(director["angelo_director"], "still Director")
        self.assertIs(self.window.workspace, director, "the window never left it")
        self.assertEqual(self.mode, "SCULPT")
        self.assertNotIn("EDIT", self.modes)

    def test_remove_refuses_anything_a_build_did_not_make_as_a_tab(self):
        director = self.director_copied_from_the_half_made_tab()
        with mock.patch("builtins.print"):
            self.assertFalse(REMOVE(self.window, director))
            self.assertFalse(REMOVE(self.window, self.workspaces["Layout"]))
        self.assertEqual(self.events, [])

    def test_every_copy_angelo_makes_drops_its_source_s_markers_and_only_tab_removal_deletes(self):
        """Every Duplicate Workspace in the add-on is followed, in its function, by ``drop_copied``; Delete
        Workspace is called by ``tab_removal`` alone, which deletes only a half-made tab no window shows."""
        copies, deletes = [], []
        for path in sorted(Path(ADDON).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for function in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
                text = ast.unparse(function)
                if "bpy.ops.workspace.duplicate(" in text and not any(
                        isinstance(n, ast.FunctionDef) and n is not function for n in ast.walk(function)):
                    copies.append((path.name, function.name, "drop_copied(" in text.split(
                        "bpy.ops.workspace.duplicate(", 1)[1]))
                if "bpy.ops.workspace.delete(" in text:
                    deletes.append(path.name)
        self.assertEqual(sorted({name for name, _f, _d in copies}), ["director_layout.py", "studio_workspaces.py"])
        for name, function, dropped in copies:
            with self.subTest(copy="%s:%s" % (name, function)):
                self.assertTrue(dropped, "a copy keeps its source's markers")
        self.assertEqual(sorted(set(deletes)), ["tab_removal.py"])


if __name__ == "__main__":
    unittest.main()
