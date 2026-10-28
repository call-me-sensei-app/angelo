# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""When a load, a quit or the add-on's unregister ends a timer job, and what it leaves (``timer_jobs``).

Evidence (break-it review of be8661b075d..5ca5f33c94f, 2026-10-27): jobs closed at ``load_pre`` were closed on a load
that then failed (a missing or corrupt file: ``load_post_fail``, the file stays and so do the timers), and each driver
read the closed job's StopIteration as success; no load released the dock's or Hide's layout hold; unregister closed
no job, and the tab build's ``stop`` cleared ``_building`` under + Add Workspace's build; a quit left a half-made tab
tagged as finished in quit.blend; ``leaving()`` read False again after the quit. Covered here on the stand-in ``bpy``
of ``test_native_timer_jobs``, through the handlers the add-on registers.
"""

import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from test_native_timer_jobs import (Stage, Workspace, chat_hide, layout_hold, studio_tab_build, studio_workspaces,
                                    timer_jobs)

UNFINISHED = studio_workspaces.UNFINISHED


class Handlers(Stage):
    def setUp(self):
        super().setUp()
        for name in ("load_post", "load_post_fail"):
            setattr(self.bpy.app.handlers, name, [])
        self.registered = set()
        self.bpy.app.timers.is_registered = lambda f: f in self.registered and f in self.timers
        timer_jobs.register()
        studio_tab_build.register()
        layout_hold.register()

    def fire(self, *events):
        for event in events:
            for handler in list(getattr(self.bpy.app.handlers, event)):
                handler(None)

    def finish(self, step, passes=10):
        for _ in range(passes):
            if step() is None:
                return
        self.fail("the build never finished")


class FailedLoadTests(Handlers):
    def test_a_failed_load_leaves_every_job_running_and_the_file_unchanged(self):
        """Open Recent on a deleted file mid-build: Blender runs load_pre, fails to read, runs load_post_fail. Its
        timers and its data stay, so the build goes on and finishes as if nothing happened."""
        step = self.start(2)   # In the area-close loop: the copy is made, one area still to close.
        released = []
        other = timer_jobs.track(iter_steps(), lambda: released.append(True))
        self.fire("load_pre", "load_post_fail")
        self.assertFalse(timer_jobs.aborted(studio_tab_build._job["generator"]))
        self.assertFalse(timer_jobs.aborted(other))
        self.assertEqual(released, [], "nothing released: the hold is still the job's")
        self.assertIs(self.ui._layout_timer, step, "the build still holds the layout")
        self.assertEqual(next(other), 0.1)
        self.finish(step)
        self.assertEqual(self.removed, [])
        made = self.workspaces["Media Studio"]
        self.assertEqual((made.get("angelo_tab"), UNFINISHED in made), ("studio", False), "a finished tab")
        self.assertTrue(studio_workspaces.last_build["done"])
        self.assertEqual(self.window.workspace.name, "Layout", "the window went home")
        self.released()

    def test_a_failed_load_keeps_the_dock_s_layout_hold(self):
        held = SimpleNamespace(timer=None)
        self.ui._layout_timer = held.timer = object()
        timer_jobs.track(iter_steps(), layout_hold.layout(held.timer))
        self.registered.add(held.timer)
        self.timers.append(held.timer)
        self.fire("load_pre", "load_post_fail")
        self.assertIs(self.ui._layout_timer, held.timer)

    def test_a_load_that_replaces_the_layout_closes_each_job_as_aborted_touching_nothing_of_the_new_file(self):
        step = self.start(2)
        job = studio_tab_build._job["generator"]
        seen, shown = [], self.window.workspace
        studio_workspaces._kept_modes["Layout"] = "SCULPT"
        self.bpy.app.handlers.load_post.append(lambda _: seen.append(self.ui._layout_timer))
        self.fire("load_pre")
        self.assertIs(self.ui._layout_timer, step, "load_pre ends nothing: the load may still fail")
        new_file = [Workspace(w.name, w.as_pointer() + 500, []) for w in self.workspaces]   # Its own, same names.
        self.workspaces[:] = new_file
        self.fire("load_post")   # The old layout is gone: nothing is rolled back into, or switched in, the new.
        self.assertEqual(self.removed, [])
        self.assertEqual(list(self.workspaces), new_file)
        self.assertEqual([w.object_mode for w in new_file], ["OBJECT", "OBJECT"], "no mode put into the new file")
        self.assertIs(self.window.workspace, shown)
        self.assertTrue(timer_jobs.aborted(job), "its step stops at once, never read as finished")
        self.assertEqual(seen, [None], "released before the other load_post handlers look")
        self.released()
        self.assertEqual(timer_jobs._jobs, {})


class LayoutKeptTests(Handlers):
    def test_a_load_that_keeps_the_layout_rolls_the_build_back_as_any_stop(self):
        """Load UI off (Angelo's Restore opens a checkpoint so) keeps the windows and the workspaces: the build's
        timer is gone, so it stops, and what it changed in that layout goes back (break-it review, d761ffcc212)."""
        step = self.start(2)
        job = studio_tab_build._job["generator"]
        studio_workspaces._kept_modes["Layout"] = "SCULPT"
        self.fire("load_pre", "load_post")
        self.assertEqual(self.removed, ["Media Studio"], "the half-made tab is rolled back")
        self.assertEqual(self.window.workspace.name, "Layout", "the window goes home")
        self.assertEqual(self.workspaces["Layout"].object_mode, "SCULPT", "its own mode back")
        self.assertTrue(timer_jobs.aborted(job))
        self.released()
        self.assertIsNone(step())

    def test_an_unknown_layout_counts_as_replaced(self):
        self.assertFalse(timer_jobs.layout_kept(), "no load_pre seen: nothing is put back")
        with mock.patch.object(timer_jobs, "_layout", lambda: None):
            self.fire("load_pre")
            self.assertFalse(timer_jobs.layout_kept(), "unreadable: nothing is put back")


class LeftoverTests(Handlers):
    def test_a_marked_tab_is_work_for_ensure_even_with_nothing_missing_and_never_the_pressed_tab(self):
        """A tab re-added by + Add Workspace after the person had deleted it (its kind recorded on the file) and saved
        mid-build: nothing is missing, so no build ran, and the half-made copy stayed (break-it review)."""
        leftover = Workspace("Media Studio", 30, [])
        leftover["angelo_tab"], leftover[UNFINISHED] = "studio", True
        self.workspaces.append(leftover)
        self.window.workspace = leftover
        self.assertFalse(studio_workspaces.active(self.window, "studio"))
        with mock.patch.object(studio_workspaces, "missing", lambda: []), \
                mock.patch.object(studio_workspaces, "in_order", lambda: True):
            studio_workspaces.ensure(self.window)
        self.assertEqual(len(self.timers), 1, "a build is owed: it removes the leftover")


class ReleaseTests(Handlers):
    def test_a_load_releases_hide_s_layout_hold_and_its_step_ends_without_touching_the_new_file(self):
        """Hide (and the dock, Director's mode switch) held a lock nothing released at a load: Director refused, the
        start-up build never ran, and every layout_after waited its 10 s."""
        steps = []
        collapse = mock.Mock(side_effect=lambda *args: iter_steps(steps))
        self.ui._dock_metadata = {0x77: True}
        area = SimpleNamespace(as_pointer=lambda: 0x77)
        with mock.patch.object(chat_hide.docking, "collapse_chat", collapse), \
                mock.patch.object(chat_hide.live_windows, "deferred",
                                  lambda manager, pointer, step: lambda: step(self.window)), \
                mock.patch.dict(sys.modules, {"angelo_agent.input_policy": SimpleNamespace(
                    layout_blocking=lambda window: False)}):
            chat_hide.request_hide(self.window, area)
            timer = self.timers[-1]
            self.registered.add(timer)   # Still registered: only the job's release clears the lock.
            self.assertIs(self.ui._layout_timer, timer)
            self.assertEqual(timer(), 0.1)
            self.fire("load_post")   # Blender discarded the timer; were it to run, it would stop at once.
            self.assertIsNone(self.ui._layout_timer)
            self.assertIsNone(timer())
        self.assertEqual(steps, [1], "the closed job was never resumed")

    def test_a_hold_whose_timer_a_load_discarded_is_released_one_still_registered_is_kept(self):
        """Holds that are not a job's (the dock's wait for the chat to draw, ``chat_paint``) go the same way."""
        kept, dropped = object(), object()
        for timer, registered in ((kept, True), (dropped, False)):
            self.ui._layout_timer = timer
            self.timers.append(timer)
            if registered:
                self.registered.add(timer)
            self.fire("load_post")
            self.assertIs(self.ui._layout_timer, kept if registered else None)

    def test_close_always_calls_the_release_even_when_the_cleanup_fails(self):
        released = []

        def failing():
            try:
                yield 0.1
            finally:
                raise RuntimeError("cleanup failed")
        job = timer_jobs.track(failing(), lambda: released.append("released"))
        next(job)
        with mock.patch("builtins.print"):
            timer_jobs.close(job)
        self.assertEqual(released, ["released"])
        self.assertTrue(timer_jobs.aborted(job))


class UnregisterTests(Handlers):
    def test_unregister_closes_every_job_as_aborted_while_the_file_stays(self):
        seen, released = [], []
        job = timer_jobs.track(watch(seen), lambda: released.append(True))
        next(job)
        timer_jobs.unregister()
        self.assertEqual((seen, released), ([False], [True]), "closed while the file stays, its hold released")
        self.assertTrue(timer_jobs.aborted(job))
        self.assertNotIn(timer_jobs._load_post, self.bpy.app.handlers.load_post)
        self.assertNotIn(timer_jobs._exit_pre, self.bpy.app.handlers.exit_pre)

    def test_add_workspace_s_step_stops_after_unregister_and_stop_leaves_its_build_marked_running(self):
        studio_workspaces.add_tab(self.window, "studio")
        step = self.timers.pop()
        self.assertIsNotNone(step())
        studio_tab_build.stop()   # Only the start-up build is the tab build's: + Add Workspace's still runs.
        self.assertTrue(studio_workspaces._building)
        timer_jobs.unregister()
        self.assertFalse(studio_workspaces._building)
        self.assertEqual(self.removed, ["Media Studio"], "rolled back: the file stays")
        self.window.workspace = self.workspaces["Layout"]
        self.assertIsNone(step())
        self.assertEqual(self.window.workspace.name, "Layout", "never switched as if it had finished")


class QuitFileTests(Handlers):
    def test_a_half_made_tab_is_marked_unfinished_at_a_quit_and_modes_go_back_before_quit_blend(self):
        """quit.blend is written after exit_pre: Recover Last Session gets what the data holds then."""
        self.start(2)
        studio_workspaces._kept_modes["Layout"] = "SCULPT"
        self.fire("exit_pre")
        made = self.workspaces["Media Studio"]
        self.assertEqual((made.get("angelo_tab"), UNFINISHED in made), ("studio", True), "marked half-made")
        self.assertEqual(self.workspaces["Layout"].object_mode, "SCULPT", "an ID write applies at once")
        self.assertIsNone(studio_workspaces.tab("studio"), "never taken for the finished tab")

    def test_the_next_build_removes_an_unfinished_tab_waits_for_the_delete_and_builds_it_again(self):
        unfinished = Workspace("Media Studio", 30, [])
        unfinished["angelo_tab"], unfinished[UNFINISHED] = "studio", True
        self.workspaces.append(unfinished)
        pending = []
        with mock.patch.object(studio_workspaces, "_remove", lambda window, workspace: pending.append(workspace)):
            studio_tab_build.run(self.window)
            step = self.timers.pop()
            self.assertEqual(step(), 0.0, "waits for Blender to apply the delete")
            self.assertEqual(pending, [unfinished], "never adopted")
            self.workspaces.remove(unfinished)   # Blender applied the delete on its next pass.
            switched = False
            for _ in range(12):
                made = self.workspaces.get("Media Studio")
                if made is not None and not switched:   # The duplicate's queued switch has applied.
                    self.window.workspace, switched = made, True
                if step() is None:
                    break
        made = self.workspaces["Media Studio"]
        self.assertIsNot(made, unfinished)
        self.assertEqual((made.get("angelo_tab"), UNFINISHED in made), ("studio", False))


class QuitFlagTests(Handlers):
    def test_a_job_closed_by_its_own_step_ends_and_is_released_once_that_step_returns(self):
        released, seen = [], []

        def closes_itself():
            try:
                timer_jobs.close(holder[0])   # The step ran a load or unregister.
                self.assertEqual(released, [], "never released mid-step")
                yield 0.1
                yield 0.1
            finally:
                seen.append(timer_jobs.leaving())
        holder = [timer_jobs.track(closes_itself(), lambda: released.append(True))]
        self.assertIsNone(next(holder[0]), "its timer stops")
        self.assertEqual((released, seen, timer_jobs._jobs), ([True], [True], {}))
        self.assertTrue(timer_jobs.aborted(holder[0]))

    def test_a_cleanup_s_own_value_error_never_keeps_a_closed_job(self):
        released = []

        def bad_cleanup():
            try:
                yield 0.1
            finally:
                raise ValueError("a bad JSON file")
        job = timer_jobs.track(bad_cleanup(), lambda: released.append(True))
        next(job)
        with mock.patch("builtins.print"):
            timer_jobs.close(job)
            timer_jobs.close_all()
        self.assertEqual((released, timer_jobs._jobs), ([True], {}))

    def test_the_quit_flag_stays_set(self):
        self.fire("exit_pre")
        self.assertTrue(timer_jobs.leaving(), "from exit_pre on, through unregister and Python's finalization")
        timer_jobs.close(timer_jobs.track(iter_steps()), leaving=False)
        self.assertTrue(timer_jobs.leaving())


def iter_steps(seen=None):
    while True:
        if seen is not None:
            seen.append(1)
        yield 0.1


def watch(seen):
    try:
        yield 0.1
    finally:
        seen.append(timer_jobs.leaving())


if __name__ == "__main__":
    unittest.main()
