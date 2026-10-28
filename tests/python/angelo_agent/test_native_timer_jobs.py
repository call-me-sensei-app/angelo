# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No timer job is left for Python's finalization, and none reads a file that is going away (``timer_jobs``).

Evidence: the 1.0.6 RC (7ea7499b79b) crashed at quit in two GUI suites, compiled_gui and first_launch_frames'
opted-out phase: SIGSEGV in ``WM_exit_ex`` -> ``BPY_python_end`` -> ``_Py_Finalize`` -> ``gen_close``, the tab build's
``finally`` (``studio_workspaces._build``) reading the window manager ``BKE_blender_free`` had freed. The build was
suspended between timer passes and held by a module (``studio_tab_build._job``); nothing closed it at quit. Covered on
a stand-in ``bpy``: a quit before the build's first step, mid-switch and in its area-close loop ends it with no
rollback and no read of Blender's data, and releases the layout; Python's finalization closing it reads nothing; the
add-on's unregister (the file stays) rolls it back as any stopped build; a load closes the build + Add Workspace starts
without touching the new file. Failed loads, holds, unregister and quit.blend: ``test_native_timer_jobs_load``. The
source scans: ``test_native_timer_jobs_scan``.
"""

import contextlib
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))


def fake_bpy():   # As test_native_studio_tab_build's: enough to import the modules.
    bpy = types.ModuleType("bpy")
    bpy.types = SimpleNamespace(Operator=object, Menu=object)
    bpy.props = types.ModuleType("bpy.props")
    bpy.props.__getattr__ = lambda name: (lambda *args, **kwargs: None)
    bpy.app = SimpleNamespace(background=False, handlers=SimpleNamespace(persistent=lambda function: function))
    return {"bpy": bpy, "bpy.types": bpy.types, "bpy.props": bpy.props}


with mock.patch.dict(sys.modules, fake_bpy()):
    import angelo_agent  # noqa: E402
    from angelo_agent import chat_hide, layout_hold, studio_tab_build, studio_workspaces, timer_jobs  # noqa: E402


class Freed(AssertionError):
    pass


class Poisoned:
    """``bpy`` once Blender has freed its data: every read is recorded (and fails the read)."""

    def __init__(self):
        self.reads = []

    def __getattr__(self, name):
        self.reads.append(name)
        raise Freed("bpy.%s read after Blender freed its data" % name)


class Workspace(dict):
    def __init__(self, name, address, areas):
        super().__init__()
        self.name, self.object_mode, self._address, self.session_uid = name, "OBJECT", address, address
        self.screen = SimpleNamespace(as_pointer=lambda: address + 1000, name=name, areas=list(areas))

    def as_pointer(self):
        return self._address


class Workspaces(list):
    def get(self, name, default=None):
        return next((w for w in self if w.name == name), default)

    def __getitem__(self, key):
        if not isinstance(key, str):
            return list.__getitem__(self, key)
        found = self.get(key)
        if found is None:
            raise KeyError(key)
        return found

    def __contains__(self, key):
        return self.get(key) is not None if isinstance(key, str) else list.__contains__(self, key)


def area(address, size):
    return SimpleNamespace(as_pointer=lambda: address, width=size, height=size, type="VIEW_3D")


Window = type("Window", (), {"__module__": "bpy.types",
                             # As Blender's: showing another workspace shows its screen.
                             "workspace": property(lambda self: self._workspace,
                                                   lambda self, value: self._show(value))})


def _show(self, workspace):
    self._workspace, self.screen = workspace, workspace.screen


Window._show = _show


class Stage(unittest.TestCase):
    """A window on Layout; the build duplicates it into Media Studio (three areas) and closes all but one."""

    def setUp(self):
        self.workspaces = Workspaces([Workspace("Layout", 10, [area(1, 50)])])
        self.window = Window()
        self.window.as_pointer = lambda: 0x9100
        self.window.workspace = self.workspaces[0]
        self.timers, self.removed, self.override = [], [], {}
        manager = SimpleNamespace(windows=[self.window], session_uid=77)

        def duplicate():   # Blender queues the switch to the copy: it shows on a later pass.
            self.workspaces.append(Workspace("Layout.001", 20, [area(2, 90), area(3, 30), area(4, 20)]))
            return {'FINISHED'}

        def area_close():
            self.window.screen.areas.remove(self.override["area"])
            return {'FINISHED'}
        close = mock.Mock(side_effect=area_close)
        close.poll = lambda: True

        def temp_override(**kwargs):
            self.override = kwargs
            return contextlib.nullcontext()
        self.bpy = SimpleNamespace(
            context=SimpleNamespace(window_manager=manager, temp_override=temp_override),
            data=SimpleNamespace(workspaces=self.workspaces),
            ops=SimpleNamespace(workspace=SimpleNamespace(duplicate=duplicate),
                                screen=SimpleNamespace(area_close=close)),
            app=SimpleNamespace(timers=SimpleNamespace(register=lambda f, **kwargs: self.timers.append(f)),
                                background=False,
                                handlers=SimpleNamespace(load_pre=[], load_post=[], exit_pre=[],
                                                         persistent=lambda f: f)))
        self.ui = SimpleNamespace(_layout_timer=None)
        stand_ins = {"ui": self.ui, "director": SimpleNamespace(_timer=None),
                     "director_layout": SimpleNamespace(is_director_workspace=lambda workspace: False)}
        modules = {"bpy": self.bpy, **{"angelo_agent." + name: module for name, module in stand_ins.items()}}
        patches = [mock.patch.dict(sys.modules, modules),
                   mock.patch.object(studio_workspaces, "bpy", self.bpy),
                   mock.patch.object(studio_tab_build, "bpy", self.bpy),
                   mock.patch.object(chat_hide, "bpy", self.bpy),
                   mock.patch.object(studio_workspaces, "_building", False),
                   mock.patch.object(studio_workspaces, "_holding", None),
                   mock.patch.object(studio_workspaces, "_kept_modes", {}),
                   mock.patch.object(studio_workspaces, "_startup", {"pending": True, "until": None}),
                   mock.patch.object(studio_workspaces, "place", lambda: True),
                   mock.patch.object(studio_workspaces, "fill", lambda window, kind: None),
                   mock.patch.object(studio_workspaces, "missing", lambda: ["studio"]),
                   mock.patch.object(studio_workspaces, "_remove", lambda window, workspace: self.removed.append(
                       workspace.name) or self.workspaces.remove(workspace)),
                   mock.patch.dict(studio_workspaces.last_build, {}, clear=True),
                   mock.patch.dict(studio_tab_build._job, {"generator": None}),
                   mock.patch.object(timer_jobs, "_jobs", {}),
                   mock.patch.dict(timer_jobs._state, {"leaving": False, "quitting": False, "layout": None})]
        patches += [mock.patch.object(angelo_agent, name, module, create=True) for name, module in stand_ins.items()]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def start(self, passes):
        """``studio_tab_build.run``, then its timer step ``passes`` times: the build is suspended there."""
        studio_tab_build.run(self.window)
        step = self.timers.pop()
        for shown in range(passes):
            if shown == 1:   # The queued switch to the copy has applied.
                self.window.workspace = self.workspaces["Media Studio"]
            self.assertIsNotNone(step())
        return step

    def freed(self):
        """From now on any read of Blender's data fails, as after ``BKE_blender_free``."""
        poisoned = Poisoned()
        for patch in (mock.patch.dict(sys.modules, {"bpy": poisoned}),
                      mock.patch.object(studio_workspaces, "bpy", poisoned),
                      mock.patch.object(studio_tab_build, "bpy", poisoned)):
            patch.start()
            self.addCleanup(patch.stop)
        return poisoned

    def released(self):
        self.assertIsNone(self.ui._layout_timer, "the layout settles again")
        self.assertIsNone(studio_workspaces._holding)
        self.assertFalse(studio_workspaces._building)
        self.assertIsNone(studio_tab_build._job["generator"])


class QuitDuringTheBuildTests(Stage):
    def quit(self, step, quit_handlers, step_after=True, quitting=False):
        made = "Media Studio" in self.workspaces
        shown = self.window.workspace
        poisoned = self.freed()   # Stricter than Blender's order: exit_pre runs while the data still exists.
        with mock.patch("builtins.print") as printed:
            for handler in quit_handlers:
                handler(None)
        self.assertEqual(poisoned.reads, [], "nothing of Blender's read while quitting")
        self.assertFalse(printed.called, "no cleanup failed")
        self.assertEqual(self.removed, [], "nothing rolled back into the file that is going away")
        self.assertIs(self.window.workspace, shown, "the window is left as it is")
        self.assertEqual("Media Studio" in self.workspaces, made)
        self.assertEqual(studio_workspaces.last_build.get("rolled_back", []), [])
        if step_after:   # A quit or a load drops the timer; were the step to run, it would end at once.
            self.assertIsNone(step())
            self.assertEqual(poisoned.reads, [])
        self.assertEqual(timer_jobs.leaving(), quitting, "only timer_jobs' own quit handler sets the quit for good")

    def test_a_quit_before_the_first_step(self):
        step = self.start(0)
        self.quit(step, [studio_tab_build.exit_pre])
        self.released()

    def test_a_quit_mid_switch(self):
        step = self.start(1)   # The copy is made and named; the switch to it has not shown yet.
        self.assertEqual(studio_workspaces.last_build, {})
        self.quit(step, [studio_tab_build.exit_pre])
        self.released()
        self.assertFalse(studio_workspaces.last_build["done"])

    def test_a_quit_in_the_area_close_loop(self):
        step = self.start(2)
        self.assertEqual(len(self.window.screen.areas), 2, "one area closed, one still to close")
        self.quit(step, [studio_tab_build.exit_pre])
        self.released()

    def test_the_shared_quit_handler_alone_closes_it_and_unregister_then_releases_the_layout(self):
        step = self.start(2)
        self.quit(step, [timer_jobs._exit_pre], step_after=False, quitting=True)   # Whichever exit_pre runs first.
        self.assertFalse(studio_workspaces._building)
        studio_tab_build.stop()   # The add-on's unregister, later in Blender's exit.
        self.released()

    def test_python_finalization_closing_it_reads_nothing(self):
        """A build no handler closed (an add-on that failed to unregister): its ``finally`` still reads nothing."""
        self.start(2)
        generator = studio_tab_build._job["generator"]
        poisoned = self.freed()
        with mock.patch.object(timer_jobs, "sys", SimpleNamespace(is_finalizing=lambda: True)):
            generator.close()   # What _PyGen_Finalize does in Python's last collection.
        self.assertEqual(poisoned.reads, [])
        self.assertEqual(self.removed, [])


class UnregisterDuringTheBuildTests(Stage):
    def test_unregister_rolls_back_as_any_stopped_build_and_releases_the_layout(self):
        """The add-on goes but the file stays: the half-made tab goes (built again when the add-on comes back), the
        window goes home and each workspace keeps its own mode."""
        studio_tab_build.register()
        step = self.start(2)
        studio_tab_build.unregister()
        self.assertEqual(self.removed, ["Media Studio"], "the half-made tab is rolled back")
        self.assertEqual(self.window.workspace.name, "Layout", "the window ends where it was")
        self.assertEqual((self.bpy.app.handlers.load_post, self.bpy.app.handlers.exit_pre), ([], []))
        self.released()
        self.assertIsNone(step())

    def test_register_installs_both_handlers_once(self):
        studio_tab_build.register()
        studio_tab_build.register()   # A second register (a reload without unregister) never runs them twice.
        self.assertEqual(self.bpy.app.handlers.load_post, [studio_tab_build.load_post])
        self.assertEqual(self.bpy.app.handlers.exit_pre, [studio_tab_build.exit_pre])
        self.assertIn("studio_tab_build.register()", (ADDON / "studio_workspaces.py").read_text())
        self.assertIn("studio_tab_build.unregister()", (ADDON / "studio_workspaces.py").read_text())


class LoadDuringAddWorkspaceTests(Stage):
    def test_a_load_closes_add_workspace_s_build_without_touching_the_new_file(self):
        """+ Add Workspace's build had no load handler: its timer, discarded by the load, was dropped on Blender's
        next pass, and its ``finally`` then rolled back by name in the new file (its own Media Studio tab)."""
        studio_workspaces.add_tab(self.window, "studio")
        step = self.timers.pop()   # Held, as Blender's timer list holds it until after the load.
        self.assertIsNotNone(step())
        poisoned = self.freed()
        timer_jobs._load_post()
        self.assertEqual((poisoned.reads, self.removed), ([], []))
        self.assertFalse(studio_workspaces._building)
        del step   # Blender drops the discarded timer on its next pass: the closed job runs nothing more.
        self.assertEqual((poisoned.reads, self.removed), ([], []))


class TimerJobsTests(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.object(timer_jobs, "_jobs", {}),
                      mock.patch.dict(timer_jobs._state, {"leaving": False, "quitting": False, "layout": None})):
            patch.start()
            self.addCleanup(patch.stop)

    def job(self, seen, fail=False):
        try:
            yield 0.1
            yield 0.1
        finally:
            seen.append(timer_jobs.leaving())
            if fail:
                raise RuntimeError("cleanup failed")

    def test_a_load_or_a_quit_closes_every_running_job_while_leaving(self):
        seen = []
        running = [timer_jobs.track(self.job(seen, fail=True)), timer_jobs.track(self.job(seen))]
        for job in running:
            next(job)
        unstarted = timer_jobs.track(self.job(seen))
        with mock.patch("builtins.print") as printed:
            timer_jobs._load_post()
        self.assertEqual(seen, [True, True], "each cleanup saw the file going away; one failing stops no other")
        self.assertTrue(printed.called)
        self.assertFalse(timer_jobs.leaving())
        self.assertIsNone(unstarted.generator.gi_frame, "closed: nothing resumes it")
        self.assertTrue(all(map(timer_jobs.aborted, running + [unstarted])))
        self.assertEqual(len(timer_jobs._jobs), 0)

    def test_closing_while_the_file_stays_is_not_leaving(self):
        seen = []
        job = timer_jobs.track(self.job(seen))
        next(job)
        timer_jobs.close(job, leaving=False)
        self.assertEqual(seen, [False])

    def test_finished_and_failed_jobs_leave_the_registry_and_a_plain_iterator_is_a_job_too(self):
        seen = []
        job = timer_jobs.track(self.job(seen))
        self.assertEqual(list(job), [0.1, 0.1])
        with self.assertRaises(KeyError):
            next(timer_jobs.track(broken()))
        running = timer_jobs.track(self.job(seen))
        next(running)
        self.assertEqual(list(timer_jobs._jobs), [running], "running: it stays, no weak reference drops it")
        running.close()   # Its driver ends it (a step that gave up).
        self.assertEqual(timer_jobs._jobs, {})
        self.assertFalse(timer_jobs.aborted(running), "only a load, a quit or unregister aborts a job")
        plain = timer_jobs.track(iter([0.05]), lambda: seen.append("released"))
        self.assertEqual(next(plain), 0.05)
        timer_jobs.close(plain)   # Nothing to clean up: its release still runs.
        self.assertEqual((timer_jobs._jobs, seen[-1], timer_jobs.aborted(plain)), ({}, "released", True))

    def test_python_finalizing_counts_as_leaving(self):
        with mock.patch.object(timer_jobs, "sys", SimpleNamespace(is_finalizing=lambda: True)):
            self.assertTrue(timer_jobs.leaving())

    def test_the_add_on_installs_the_handlers_and_they_survive_a_load(self):
        source = (ADDON / "__init__.py").read_text()
        register, unregister = source.split("def unregister():", 1)
        self.assertIn("timer_jobs.register()", register)
        self.assertLess(register.index("timer_jobs.register()"), register.index("ui.register()"), "before any job")
        self.assertIn("timer_jobs.unregister()", unregister)
        for _, handler in timer_jobs.HANDLERS:
            self.assertTrue(handler._bpy_persistent)
        other = lambda _: None   # noqa: E731
        handlers = SimpleNamespace(load_pre=[other], load_post=[other], exit_pre=[other])
        with mock.patch.dict(sys.modules, {"bpy": SimpleNamespace(app=SimpleNamespace(handlers=handlers))}):
            timer_jobs.register()
            timer_jobs.register()
            self.assertEqual((handlers.load_pre, handlers.load_post, handlers.exit_pre),
                             ([timer_jobs._load_pre, other], [timer_jobs._load_post, other],
                              [timer_jobs._exit_pre, other]), "once, and first")
            timer_jobs.unregister()
            self.assertEqual((handlers.load_pre, handlers.load_post, handlers.exit_pre), ([other], [other], [other]))


def broken():
    raise KeyError("first step")
    yield


if __name__ == "__main__":
    unittest.main()
