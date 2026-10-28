# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual's Media Studio and Generation Flows tabs are built only once the start-up is quiet (``studio_workspaces``).

1.0.5 first launch: the build switched the window's workspace under the setup card, which closed unanswered; the
welcome never opened and the window was left in Manual on another tab without the chat. Covered on a stand-in
``bpy``: each thing that holds the layout (Director's and the default layout's start-up, the pending splash, a card,
the welcome and its scheduling, a modal tool) holds the build; a build that stops part-way removes the tab it left
half-made and puts the window back where it was; a stopped build is tried again; and the build never goes through
the person-facing "finish or cancel the active tool" refusal. The running app's proof is
``test_blender_first_launch_tabs``.
"""

import ast
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))


def fake_bpy():
    bpy = types.ModuleType("bpy")
    bpy.types = SimpleNamespace(Operator=object, Menu=object)
    bpy.props = types.ModuleType("bpy.props")
    bpy.props.__getattr__ = lambda name: (lambda *args, **kwargs: None)
    bpy.utils = SimpleNamespace(register_class=lambda cls: None, unregister_class=lambda cls: None)
    bpy.app = SimpleNamespace(translations=SimpleNamespace(pgettext_iface=lambda text, context=None: text), background=False,
                              handlers=SimpleNamespace(persistent=lambda function: function))
    return {"bpy": bpy, "bpy.types": bpy.types, "bpy.props": bpy.props}


with mock.patch.dict(sys.modules, fake_bpy()):
    import angelo_agent  # noqa: E402
    from angelo_agent import studio_workspaces, studio_tab_build, timer_jobs  # noqa: E402

QUIET = {"director": SimpleNamespace(_timer=None), "ui": SimpleNamespace(_layout_timer=None),
         "splash_setup": SimpleNamespace(pending=lambda: False),
         "intro": SimpleNamespace(_timer=None, is_open=lambda: False),
         "card_host": SimpleNamespace(is_active=lambda kind=None: False),
         "input_policy": SimpleNamespace(interaction_active=lambda window: False)}


def quiet(**changes):
    """``startup_quiet`` with each start-up module stood in, ``changes`` replacing some of them."""
    modules = dict(QUIET, **changes)
    patches = []
    for name, module in modules.items():
        patches += [mock.patch.dict(sys.modules, {"angelo_agent." + name: module}),
                    mock.patch.object(angelo_agent, name, module, create=True)]
    for patch in patches:
        patch.start()
    try:
        return studio_workspaces.startup_quiet(object())
    finally:
        for patch in reversed(patches):
            patch.stop()


def function(name, module="studio_workspaces.py"):
    source = (ADDON / module).read_text()
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.get_source_segment(source, node)


class StartupQuietTests(unittest.TestCase):
    def test_quiet_only_when_nothing_holds_the_layout(self):
        self.assertTrue(quiet())
        holders = {
            "Director's start-up": {"director": SimpleNamespace(_timer=object())},
            "the default layout": {"ui": SimpleNamespace(_layout_timer=object())},
            "Blender's splash before Angelo's card": {"splash_setup": SimpleNamespace(pending=lambda: True)},
            "the welcome, scheduled": {"intro": SimpleNamespace(_timer=object(), is_open=lambda: False)},
            "the welcome, open": {"intro": SimpleNamespace(_timer=None, is_open=lambda: True)},
            "a card (setup, start panel, notice)": {"card_host": SimpleNamespace(is_active=lambda kind=None: True)},
            "a modal Blender tool": {"input_policy": SimpleNamespace(interaction_active=lambda window: True)},
        }
        for holder, changes in holders.items():
            with self.subTest(holder=holder):
                self.assertFalse(quiet(**changes))


class StartupBuildTests(unittest.TestCase):
    """The session's first build is part of the start-up: under its cover, before its cards, which wait for it."""

    def pending(self, missing, until=None, now=100.0):
        state = {"pending": True, "until": until}
        with mock.patch.object(studio_workspaces, "_startup", state), \
                mock.patch.object(studio_workspaces, "missing", lambda: missing), \
                mock.patch("time.monotonic", lambda: now):
            return studio_workspaces.startup_build_pending(), state

    def test_the_cards_wait_only_while_tabs_are_missing_and_never_forever(self):
        self.assertTrue(self.pending(["studio", "flows"])[0])
        self.assertFalse(self.pending([])[0], "nothing to build: the cards go on")
        result, state = self.pending(["flows"], until=50.0)
        self.assertFalse(result, "past STARTUP_LIMIT the cards go on whatever the build did")
        self.assertFalse(state["pending"])
        _, state = self.pending(["flows"])
        self.assertAlmostEqual(state["until"], 100.0 + studio_workspaces.STARTUP_LIMIT)

    def test_the_start_up_build_runs_once_director_has_settled(self):
        modules = {"director": SimpleNamespace(_timer=None), "ui": SimpleNamespace(_layout_timer=None),
                   "input_policy": SimpleNamespace(interaction_active=lambda window: False)}

        def ready(**changes):
            stand_ins = dict(modules, **changes)
            patches = []
            for name, module in stand_ins.items():
                patches += [mock.patch.dict(sys.modules, {"angelo_agent." + name: module}),
                            mock.patch.object(angelo_agent, name, module, create=True)]
            for patch in patches:
                patch.start()
            try:
                return studio_workspaces.startup_ready(object())
            finally:
                for patch in reversed(patches):
                    patch.stop()
        self.assertTrue(ready())
        self.assertFalse(ready(director=SimpleNamespace(_timer=object())))
        self.assertFalse(ready(ui=SimpleNamespace(_layout_timer=object())))
        self.assertFalse(ready(input_policy=SimpleNamespace(interaction_active=lambda window: True)))

    def test_the_cards_the_cover_and_the_window_suites_wait_for_it(self):
        intro = (ADDON / "intro.py").read_text()
        when_ready = intro.split("def open_when_ready():", 1)[1].split("\n    _timer, _timer_startup", 1)[0]
        self.assertIn("if studio_workspaces.startup_build_pending():\n            return .2", when_ready)
        self.assertLess(when_ready.index("startup_build_pending"), when_ready.index("open_setup"))
        layout = (ROOT / "scripts/startup/angelo_default_layout.py").read_text()
        covering = layout.split("def covering():", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("tabs.startup_build_pending()", covering, "the start-up cover stays up while it builds")
        pump = (ADDON / "card_host.py").read_text().split("def pump(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("or _tabs_pending()", pump, "no notice card shows over the 3D view before the start-up build")
        ensure = function("ensure")
        self.assertIn("if startup_build_pending():", ensure)
        self.assertIn('tabs._startup["pending"] = False', function("_release", "studio_tab_build.py"), "done or rolled back")
        # The window suites wait through the one boot gate, which waits for the start-up build and any build running
        # (qa_boot; test_native_qa_boot fails a suite that waits on the layout's timers itself).
        gate = (ROOT / "tests/python/angelo_agent/qa_boot.py").read_text()
        for waits in ("startup_build_pending", "_building", "_holding"):
            self.assertIn(waits, gate.split("def started(", 1)[1])
        for suite in ("test_blender_studio_discoverability.py", "test_blender_flow_menu.py",
                      "test_blender_studio_tabs.py", "test_blender_top_bar_permanence.py",
                      "test_blender_welcome_slides.py", "test_blender_flow_start.py",
                      "test_blender_director_shortcut.py"):
            with self.subTest(suite=suite):
                self.assertIn("qa_boot.started()", (ROOT / "tests/python/angelo_agent" / suite).read_text())


class BeforeDirectorTests(unittest.TestCase):
    """The start-up build runs before Director's layout makes the chat: once the chat is on the window, nothing
    switches it (1.0.5 RC: built after Director settled, it switched the window ~2.5 s in; seven unchanged suites lost
    the chat they had just found, and so would a person or an add-on)."""

    def test_the_default_layout_builds_the_tabs_before_director(self):
        layout = (ROOT / "scripts/startup/angelo_default_layout.py").read_text()
        opening = layout.split("def open_default():", 1)[1].split("\n    _timer = open_default", 1)[0]
        self.assertIn("if studio_tab_build.startup_build(window):", opening)
        built = opening.index("startup_build(window)")
        for later in ('director.request_mode(window, "DIRECTOR"', 'director.request_mode(window, "MANUAL"',
                      "ui.request_chat_dock(window", "intro.schedule_first_launch", "startup, _startup = _startup"):
            with self.subTest(later=later):
                self.assertLess(built, opening.index(later))

    def test_startup_build_starts_once_and_holds_until_done(self):
        started = []
        window = SimpleNamespace(screen=SimpleNamespace(is_temporary=False))
        run = lambda w, retry=None: started.append(w)
        with mock.patch.object(studio_tab_build, "run", run), \
                mock.patch.object(studio_workspaces, "startup_ready", lambda w: True), \
                mock.patch.object(studio_workspaces, "startup_build_pending", lambda: True):
            self.assertTrue(studio_tab_build.startup_build(window))
            self.assertEqual(started, [window])
            with mock.patch.object(studio_workspaces, "_holding", object()):
                self.assertTrue(studio_tab_build.startup_build(window), "still running: Director waits")
            self.assertEqual(len(started), 1, "never a second build")
        with mock.patch.object(studio_tab_build, "run", run), \
                mock.patch.object(studio_workspaces, "startup_build_pending", lambda: False):
            self.assertFalse(studio_tab_build.startup_build(window), "nothing owed: Director goes on at once")
        self.assertEqual(len(started), 1)

    def test_the_start_up_build_starts_at_the_first_pass(self):
        """Its switches free any popup open by then: started a second after the add-on, it closed a Help menu
        opened in that second (1.0.5 RC, app_identity, with the default chat off)."""
        self.assertIn("first_interval=0.0 if startup_build_pending() else 0.5", function("ensure"))
        self.assertIn("bpy.app.timers.register(_first_check, first_interval=0.0)", function("register"))

    def test_director_keeps_the_strip_order_without_a_switch(self):
        source = (ADDON / "director_layout.py").read_text()
        made = source.split('write_property(target, WORKSPACE_DIRECTOR, True)', 1)[1].split("\n    if not", 1)[0]
        self.assertIn("place()", made, "Director made after the tabs still reads first")


class ObjectModeTests(unittest.TestCase):
    """Blender sets the active object to a workspace's own mode on every switch: a build passing through another
    workspace left a sculpting object in Object Mode (1.0.5 final round, dispatch_fixes: "Prepare this object for
    sculpting first" after the start-up build). Each workspace the build switches to gets the window's mode for the
    build, and its own back once the window is home."""

    def test_every_switch_keeps_the_mode_and_each_workspace_gets_its_own_back(self):
        home = SimpleNamespace(name="Layout", object_mode="SCULPT")
        animation = SimpleNamespace(name="Animation", object_mode="POSE")
        same = SimpleNamespace(name="Copy", object_mode="SCULPT")
        workspaces = {w.name: w for w in (home, animation, same)}
        with mock.patch.object(studio_workspaces, "_kept_modes", {}), \
                mock.patch.object(studio_workspaces, "bpy", SimpleNamespace(data=SimpleNamespace(workspaces=workspaces))):
            studio_workspaces._same_mode(animation, home)
            studio_workspaces._same_mode(same, home)
            studio_workspaces._same_mode(home, home)
            self.assertEqual(animation.object_mode, "SCULPT", "switching to it never changes the object's mode")
            self.assertEqual(studio_workspaces._kept_modes, {"Animation": "POSE"}, "only what was changed")
            studio_workspaces.restore_modes()
            self.assertEqual(animation.object_mode, "POSE")
            self.assertEqual(studio_workspaces._kept_modes, {})

    def test_every_switch_in_the_build_is_preceded_by_it(self):
        build = function("_build")   # The generator; ``build`` binds its window by identity (live_target).
        for switch in ("_same_mode(source, bpy.data.workspaces.get(home_name))\n            yield from _switch(held, source.name)",
                       "bpy.data.workspaces.get(home_name))   # By name: held across\n"
                       "                yield from _switch(held, name)"):
            self.assertIn(switch, build)
        self.assertEqual(build.count("yield from _switch("), 2, "no switch without its mode")
        self.assertIn("tabs.restore_modes()", function("run", "studio_tab_build.py"))
        self.assertIn("tabs.restore_modes()", function("stop", "studio_tab_build.py"), "while the data exists")
        self.assertIn("tabs._kept_modes.clear()", function("load_post", "studio_tab_build.py"), "never the new file's")


class LoadDuringBuildTests(unittest.TestCase):
    """A file load discards the build's timer mid-build (1.0.5 RC, fresh_chat_document: the window stayed on a
    half-made Generation Flows tab and the layout never settled again). Ended at ``load_post``, once the load has
    replaced the file: a failed load keeps both (``test_native_timer_jobs_load``)."""

    def test_a_load_ends_the_build_without_rolling_back_and_releases_the_layout(self):
        ui = SimpleNamespace(_layout_timer=None)
        seen = {}

        def generator():
            try:
                yield 0.0
            finally:
                seen["leaving"] = timer_jobs.leaving()

        job = timer_jobs.track(generator())
        next(job)
        step = object()
        ui._layout_timer = step
        with mock.patch.dict(sys.modules, {"angelo_agent.ui": ui}), mock.patch.object(angelo_agent, "ui", ui, create=True), \
                mock.patch.object(studio_workspaces, "_holding", step), \
                mock.patch.object(studio_workspaces, "_building", True), \
                mock.patch.object(studio_workspaces, "_startup", {"pending": True, "until": None}), \
                mock.patch.dict(studio_tab_build._job, {"generator": job}):
            studio_tab_build.load_post(None)
            self.assertTrue(seen["leaving"], "the build's finally saw the load: nothing rolled back into it")
            self.assertFalse(timer_jobs.leaving())
            self.assertIsNone(ui._layout_timer, "the layout settles again")
            self.assertIsNone(studio_workspaces._holding)
            self.assertIsNone(studio_tab_build._job["generator"])
            self.assertTrue(timer_jobs.aborted(job))
        build = function("_build").split("    finally:", 1)[1]
        self.assertLess(build.index("if timer_jobs.leaving():"), build.index("_remove("))
        self.assertIn("studio_tab_build.register()", function("register"))
        self.assertIn('("load_post", load_post)', (ADDON / "studio_tab_build.py").read_text())


class WindowGoneBeforeTheFirstStepTests(unittest.TestCase):
    """Break-it SW3-M-1: a build whose window closed before its timer's first step claimed ``_building`` outside its
    cleanup, so it stayed set for the process and every later build was refused. Each entry point (the start-up and
    load ``studio_tab_build.run``, + Add Workspace's ``add_tab``) now leaves nothing claimed, and a build in another
    window then runs."""

    class Workspace(dict):
        def __init__(self, name, address):
            super().__init__()
            self.name, self.object_mode, self._address = name, "OBJECT", address

        def as_pointer(self):
            return self._address

    class Workspaces(dict):
        def __iter__(self):
            return iter(list(self.values()))

    def setUp(self):
        Window = type("Window", (), {"__module__": "bpy.types"})   # Blender's own: held by identity (live_target).
        self.workspaces = self.Workspaces({name: self.Workspace(name, 10 + i)
                                           for i, name in enumerate(("Layout", "Media Studio"))})
        self.windows = []

        def window(address):
            made = Window()
            made.as_pointer = lambda: address
            made.workspace = self.workspaces["Layout"]
            made.screen = SimpleNamespace(as_pointer=lambda: address + 1, name="Layout")
            self.windows.append(made)
            return made
        self.window = window
        self.timers = []
        bpy = SimpleNamespace(
            context=SimpleNamespace(window_manager=SimpleNamespace(windows=self.windows, session_uid=1)),
            data=SimpleNamespace(workspaces=self.workspaces),
            app=SimpleNamespace(timers=SimpleNamespace(register=lambda function, **kwargs: self.timers.append(function)),
                                background=False))
        self.ui = SimpleNamespace(_layout_timer=None)
        for patch in (mock.patch.dict(sys.modules, {"bpy": bpy, "angelo_agent.ui": self.ui}),
                      mock.patch.object(angelo_agent, "ui", self.ui, create=True),
                      mock.patch.object(studio_workspaces, "bpy", bpy), mock.patch.object(studio_tab_build, "bpy", bpy),
                      mock.patch.object(studio_workspaces, "_building", False),
                      mock.patch.object(studio_workspaces, "_holding", None),
                      mock.patch.object(studio_workspaces, "place", lambda: True),
                      mock.patch.dict(studio_workspaces.last_build, {})):
            patch.start()
            self.addCleanup(patch.stop)

    def released(self):
        self.assertFalse(studio_workspaces._building, "nothing claimed for good")
        self.assertIsNone(studio_workspaces._holding)
        self.assertIsNone(self.ui._layout_timer, "the layout settles again")

    def then_a_build_in_another_window_runs(self):
        other = self.window(0x200)
        self.assertEqual(list(studio_workspaces.build(other, ["studio"])), [])   # Adopts "Media Studio" at once.
        self.assertTrue(studio_workspaces.last_build["done"])
        self.assertEqual(studio_workspaces.tab_kind(self.workspaces["Media Studio"]), "studio")
        self.released()

    def test_the_start_up_build(self):
        window = self.window(0x100)
        with mock.patch.object(studio_workspaces, "missing", lambda: ["studio"]):
            studio_tab_build.run(window)
            self.windows.remove(window)   # Closed before the timer's first step.
            step = self.timers.pop()
            with mock.patch("builtins.print"):
                self.assertIsNone(step())
        self.released()
        self.then_a_build_in_another_window_runs()

    def test_add_workspace(self):
        window = self.window(0x100)
        studio_workspaces.add_tab(window, "studio")
        self.windows.remove(window)
        step = self.timers.pop()
        with mock.patch("builtins.print"):
            self.assertIsNone(step())
        self.released()
        self.then_a_build_in_another_window_runs()


class ToolsWaitForTheBuildTests(unittest.TestCase):
    """Agent turns, connected apps' calls and approved script files reach Blender through the GUI pump's one
    ``pump_controller_tools`` call; it holds them while the tab build runs, whose area closes freed an area a script
    had just changed (1.0.5 RC, asset_browser_states: a crash in wm_event_do_notifiers after ED_area_do_listen).
    Held, never dropped or reordered, and at most ``TOOLS_WAIT`` seconds. The running app's proof is
    ``test_blender_tools_wait_for_tab_build``."""

    def test_the_tool_pump_checks_the_build_first(self):
        source = (ADDON / "ui.py").read_text()
        self.assertEqual(source.count("pump_controller_tools("), 1, "one choke point")
        guard = "if window and controllers and not held and not tools_wait():"
        self.assertIn(guard, source)
        self.assertIn("from .studio_tab_build import tools_wait", source)
        self.assertLess(source.index(guard), source.index("pump_controller_tools("))
        between = source[source.index(guard):source.index("pump_controller_tools(")]
        self.assertNotIn("\n        if ", between, "nothing reaches the pump outside the guard")
        wait = function("tools_wait", "studio_tab_build.py")
        self.assertIn("tabs._building or tabs._holding is not None", wait)
        self.assertNotIn("_layout_timer", wait, "only the build: docks and Director's switch never hold the agent")

    def wait(self, now, building=False, holding=None):
        with mock.patch.object(studio_workspaces, "_building", building), \
                mock.patch.object(studio_workspaces, "_holding", holding), mock.patch("time.monotonic", lambda: now):
            return studio_tab_build.tools_wait()

    def test_held_while_the_build_runs_at_most_the_cap_from_the_first_held_tick(self):
        cap = studio_tab_build.TOOLS_WAIT
        self.assertLessEqual(cap, 2.0)
        with mock.patch.dict(studio_tab_build._tools, {"since": None}):
            self.assertFalse(self.wait(10.0), "no build: never held")
            self.assertTrue(self.wait(20.0, building=True), "the first held tick starts the cap")
            self.assertTrue(self.wait(20.0 + cap - .01, holding=object()), "the build's settle step still holds")
            self.assertFalse(self.wait(20.0 + cap, building=True), "past the cap they go, whatever the build does")
            self.assertFalse(self.wait(25.0, building=True))
            self.assertFalse(self.wait(26.0), "the build ended: the cap resets")
            self.assertTrue(self.wait(30.0, building=True), "the next build gets its own cap")


class OldFileTests(unittest.TestCase):
    def test_a_file_without_the_tabs_gets_them_on_load_behind_the_quiet_gate(self):
        """A file saved by 1.0.4 has neither tab nor record: its load builds them once, at a quiet moment (the
        session's start-up build is over by then). The running app's proof: test_blender_first_launch_tabs."""
        self.assertIn("ensure(window)", function("_loaded"))
        self.assertIn("persistent", (ADDON / "studio_workspaces.py").read_text().split("def _loaded", 1)[0][-200:])
        ensure = function("ensure")
        self.assertLess(ensure.index("if startup_build_pending():"), ensure.index("elif not startup_quiet(target):"))
        self.assertIn('_startup["pending"] = False', function("startup_build_pending"))
        with mock.patch.object(studio_workspaces, "_startup", {"pending": False, "until": None}), \
                mock.patch.object(studio_workspaces, "missing", lambda: ["studio", "flows"]):
            self.assertFalse(studio_workspaces.startup_build_pending(), "after the start-up: the quiet gate")


class BuildTests(unittest.TestCase):
    def test_the_build_waits_for_a_quiet_start_up_then_tries_again_if_it_stopped(self):
        ensure = function("ensure")
        self.assertIn("elif not startup_quiet(target):", ensure, "a later build waits for a quiet moment")
        self.assertIn("now - quiet_since[0] < QUIET", ensure, "quiet for a while, not for one tick")
        self.assertIn("if attempts[0] < ATTEMPTS:", ensure, "a stopped build is tried again")
        self.assertIn("if tabs.missing() and retry is not None:", function("run", "studio_tab_build.py"))
        self.assertGreaterEqual(studio_workspaces.QUIET, 0.5)

    def test_a_stopped_build_rolls_back_its_half_made_tab_and_the_window(self):
        build = function("_build")
        self.assertIn("made.append(name := created.name)", build)
        self.assertIn("made.remove(name)   # Finished: it stays, the tab from now on.", build)
        self.assertIn("created[UNFINISHED] = TABS[kind], kind, True", build, "half-made until then, in any saved file")
        finally_block = build.split("    finally:", 1)[1]
        self.assertIn("_remove(rollback, bpy.data.workspaces[name])", finally_block)
        self.assertIn("window.workspace = home", finally_block)
        self.assertLess(finally_block.index("window.workspace = home"), finally_block.index("_remove("),
                        "home first: deleting the shown tab would switch to a neighbour, with its object mode")
        self.assertIn("except ReferenceError:", finally_block, "a quit or load during a build logs no traceback")
        self.assertIn("fill(_alive(held), kind)\n"
                      "            made.remove(name)   # Finished: it stays, the tab from now on.\n"
                      "            del bpy.data.workspaces[name][UNFINISHED]\n"
                      "            _record(added_once() | {kind})", build, "recorded only once the tab is finished")

    def test_never_through_the_person_facing_refusal(self):
        fill = function("fill", "studio_tab_fill.py")
        self.assertNotIn("setup_area", fill, "studio.setup_area refuses while a tool is active")
        self.assertIn("select_console(window, area, generative=True)", fill)


if __name__ == "__main__":
    unittest.main()
