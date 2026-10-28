# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A File or Asset Browser that has not been refreshed yet survives every entry point that can reach it (1.0.6).

A browser's file list (``SpaceFile.files``) is made at its first refresh and its runtime data at its first init: a
browser just made, read from a file or split off another one has neither for a moment. Each of these crashed Blender
5.2 (SIGSEGV, a null pointer) and each runs here in the same timer tick that makes the browser:

* **language**: an area becomes an Asset Browser and the interface language changes (``ND_UI_LANG`` listener);
* **activate on a new browser**: another area, never a browser before, becomes one and gets
  ``SpaceFileBrowser.activate_asset_by_id`` before its list exists;
* **deferred activate on a split browser**: that browser is split and the new half gets
  ``activate_asset_by_id(deferred=True)`` for a second asset before its init (a split copies the active file, so
  the half would otherwise show the first asset active without any activation).

Each must leave a working browser: the list reads (``angelo_list_ready``) and the activation lands once it has.
Captures: ``before-refresh-<case>.png``. The fourth entry point, a job-finished notifier reaching a split browser
before its init, has no deterministic trigger from Python; ``test_native_file_browser_uninitialized`` pins it.

Run through ``run_native_suites.py --gui --include test_blender_file_browser_before_refresh``; check
``file-browser-before-refresh.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path

import addon_utils
import bpy

assert not bpy.app.background and "--factory-startup" in sys.argv, "Use an isolated factory-startup GUI instance"
OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-file-browser-before-refresh-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
REPORT = OUT / "file-browser-before-refresh.json"
prefs = bpy.context.preferences
prefs.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "observed": {}, "screenshots": []}
checks, ctx = state["checks"], {}
started = time.monotonic()
LIMIT = 20.0   # Seconds a list may take to read after its browser first draws.


def window():
    return bpy.context.window_manager.windows[0]


def area(key="area"):
    return next((a for a in window().screen.areas if a.as_pointer() == ctx.get(key)), None)


def make_editor(target, ui_type):
    """As ``editor_types.set_editor``: the window in the context, so Blender sets up the editor's regions."""
    with bpy.context.temp_override(window=window(), screen=window().screen, area=target):
        target.ui_type = ui_type


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def active_name(target):
    """The browser's active file, as its own context reads it (None before the list has it)."""
    region = next(r for r in target.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window(), area=target, region=region):
        entry = getattr(bpy.context, "active_file", None)
    return getattr(entry, "name", None)


def ready(target):
    space = target.spaces.active
    return space.type == 'FILE_BROWSER' and bool(space.angelo_list_ready)   # Angelo's 1.0.5 RNA.


def wait(key, condition):
    """True once ``condition`` holds; raises after LIMIT seconds (the case's own deadline, set at its first wait)."""
    since = ctx.setdefault("since_" + key, time.monotonic())
    if condition():
        return True
    assert time.monotonic() - since < LIMIT, "%s: not reached in %.0f s" % (key, LIMIT)
    return False


def finish(error=None):
    if error is not None:
        state["error"] = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    else:
        state["phase"] = "complete"
    state["success"] = error is None and bool(checks) and all(checks.values())
    state["seconds"] = round(time.monotonic() - started, 2)
    REPORT.write_text(json.dumps(state, indent=2, default=str) + "\n", encoding="utf-8")
    print("file_browser_before_refresh", json.dumps({"success": state["success"], "checks": checks}))
    bpy.ops.wm.quit_blender()


def tick():
    import qa_boot
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 150, "Suite timed out"
        if phase == "boot":
            if not qa_boot.started():
                state["observed"]["boot_waiting_on"] = qa_boot.waiting_on()
                return .3
            target = max(window().screen.areas, key=lambda a: a.width * a.height)
            # "fresh" needs an area that never held a browser, so its new browser has no file list when the
            # activation arrives (a kept SpaceFile would keep its list): half of the 3D view, split off first.
            before = {a.as_pointer() for a in window().screen.areas}
            with bpy.context.temp_override(window=window(), area=target):
                bpy.ops.screen.area_split(direction='VERTICAL', factor=0.5)
            ctx["area"], ctx["was"] = target.as_pointer(), target.ui_type
            ctx["other"] = next(a.as_pointer() for a in window().screen.areas if a.as_pointer() not in before)
            ctx["material"] = bpy.data.materials.new("Before Refresh Probe")
            ctx["second"] = bpy.data.materials.new("Before Refresh Second")
            for material in (ctx["material"], ctx["second"]):
                material.asset_mark()
            ctx["language"] = prefs.view.language
            make_editor(target, 'ASSETS')
            prefs.view.language = "de_DE" if ctx["language"] != "de_DE" else "en_US"   # The same tick.
            state["phase"] = "language"
            return .3
        if phase == "language":
            if not wait("language", lambda: ready(area())):
                area().tag_redraw()
                return .2
            capture("before-refresh-language.png")
            checks["a_language_change_before_the_first_refresh_leaves_a_working_browser"] = ready(area())
            prefs.view.language = ctx["language"]
            make_editor(area(), ctx["was"])
            state["phase"] = "fresh"
            return .5
        if phase == "fresh":
            make_editor(area("other"), 'ASSETS')
            area("other").spaces.active.activate_asset_by_id(id_to_activate=ctx["material"], deferred=False)
            state["phase"] = "fresh_active"
            return .3
        if phase == "fresh_active":
            if not wait("fresh", lambda: ready(area("other")) and active_name(area("other")) == ctx["material"].name):
                area("other").tag_redraw()
                return .2
            capture("before-refresh-activate.png")
            state["observed"]["fresh_active"] = active_name(area("other"))
            checks["activating_on_a_new_browser_lands_once_its_list_reads"] = True
            state["phase"] = "split"
            return .3
        if phase == "split":
            before = {a.as_pointer() for a in window().screen.areas}
            with bpy.context.temp_override(window=window(), area=area("other")):
                bpy.ops.screen.area_split(direction='VERTICAL', factor=0.5)
            ctx["half"] = next(a.as_pointer() for a in window().screen.areas if a.as_pointer() not in before)
            half = area("half")
            half.spaces.active.activate_asset_by_id(id_to_activate=ctx["second"], deferred=True)  # Before init.
            state["phase"] = "split_active"
            return .3
        if phase == "split_active":
            half = area("half")
            if not wait("split", lambda: ready(half) and active_name(half) == ctx["second"].name):
                half.tag_redraw()
                return .2
            capture("before-refresh-split.png")
            state["observed"]["split_active"] = active_name(half)
            checks["a_deferred_activation_on_a_split_browser_lands_once_its_list_reads"] = True
            with bpy.context.temp_override(window=window(), area=half):
                bpy.ops.screen.area_close()
            with bpy.context.temp_override(window=window(), area=area("other")):
                bpy.ops.screen.area_close()
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=1.0)
