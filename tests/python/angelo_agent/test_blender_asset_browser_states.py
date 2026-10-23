# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An Asset Browser says when it is empty or a search finds nothing, from its real counts (1.0.5).

The 3D view becomes an Asset Browser on the Current File library. For English and German at UI scale 1 and 2:

* **empty** (no asset in the file): ``SpaceFileBrowser.angelo_listed_count`` and ``angelo_total_count`` are 0 and
  ``angelo_list_ready`` is true; the list draws "Nothing here yet" and a teal Generate in Media Studio, in pixels;
* **assets listed** (one material marked as an asset): the counts are 1 and the list draws no state (no teal pixels);
* **no results** (a search that matches nothing): listed 0 of 1; "No matches for ..." with Clear search and the teal
  button, in pixels;
* **Media Studio's own library, empty** (English and German): "Your generated images and models appear here" with
  the teal button.

Every drawn line and button label is measured again here and fits the block's width. Then real clicks, in English at
UI scale 1: Clear search empties only the search (the asset is listed again, no state), and Generate in Media Studio
runs the shared opener. Captures: ``asset-states-<state>-<scale>-<language>.png``.

Run through ``run_native_suites.py --gui --include test_blender_asset_browser_states``; check
``asset-browser-states.json`` for ``success`` and ``phase == "complete"``.
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
OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-asset-browser-states-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
prefs = bpy.context.preferences
prefs.view.show_splash = False
prefs.use_preferences_save = False
from angelo_agent import asset_browser_entry as entry, asset_browser_states as states  # noqa: E402

REPORT = OUT / "asset-browser-states.json"
TEAL = (43, 196, 191)          # card_kit.TEAL as drawn.
TEAL_LEAST = 800               # A 36-unit teal button is thousands of pixels at UI scale 1.
TEAL_MOST = 40                 # A list with assets: none (antialiasing aside).
QUERY = "zzqx-no-such-asset"
state = {"phase": "boot", "success": False, "checks": {}, "observed": {}, "screenshots": []}
checks, ctx = state["checks"], {}
started = time.monotonic()
acted = []
_act = entry.act


def _recorded_act(window, area, action):
    acted.append(action)
    return _act(window, area, action)


entry.act = _recorded_act


def window():
    return bpy.context.window_manager.windows[0]


def area():
    return next(a for a in window().screen.areas if a.as_pointer() == ctx["area"])


def region(kind='WINDOW'):
    return next(r for r in area().regions if r.type == kind)


def space():
    return area().spaces.active


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def pixels(path):
    import numpy
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        values = numpy.empty(width * height * 4, dtype=numpy.float32)
        image.pixels.foreach_get(values)
    finally:
        bpy.data.images.remove(image)
    return (values.reshape(height, width, 4)[..., :3] * 255).round().astype(numpy.int32)


def teal_in_list(path):
    import numpy
    r = region()
    block = pixels(path)[r.y:r.y + r.height, r.x:r.x + r.width]
    return int((numpy.abs(block - numpy.array(TEAL)).max(axis=2) <= 24).sum())


def settled():
    """No layout change under way: Director's start-up, a dock and Manual's tab build (which switches the window
    through the tabs it makes) have all finished. An area chosen before that can be gone a moment later (rerun on
    9d7186ae723: the 3D view picked at 2 s was replaced as the start-up built the tabs)."""
    import qa_boot   # The one boot gate: Angelo's start-up script, Director, a dock and the tab build.
    return qa_boot.started()


def counts():
    s = space()
    return {"listed": s.angelo_listed_count, "total": s.angelo_total_count, "ready": s.angelo_list_ready}


def refresh():
    with bpy.context.temp_override(window=window(), area=area(), region=region()):
        bpy.ops.asset.library_refresh()
    area().tag_redraw()


def press(x, y):
    w = window()
    for kind, value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        w.event_simulate(type=kind, value=value, x=int(x), y=int(y))


def park_pointer():
    """The pointer over the header, so no button is lit (hover) when the list is captured."""
    r = region('HEADER')
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=r.x + 4, y=r.y + r.height // 2)


def fits():
    """Every drawn line and button label, measured here, fits the block (and each label its button)."""
    from angelo_agent.flow_window import chat_view_view
    from angelo_agent import chat_view
    record = states.drawn.get(area().as_pointer())
    if not record:
        return False
    view, scale = chat_view_view(), chat_view.ui_scale()
    saved, chat_view._scale = chat_view._scale, 1.0
    try:
        width = min(states.MAX_WIDTH * scale, region().width - 2 * states.SIDE * scale)
        heading, body = 16 * scale, 12 * scale
        lines_ok = all(view.measure(line, heading, view._font) <= width + 1 for line in record["lines"])
        buttons_ok = all(view.measure(row, body, view._font) <= rect[2] + 1
                         for rows, rect in zip(record["buttons"], record["rects"]) for row in rows)
    finally:
        chat_view._scale = saved
    return lines_ok and buttons_ok


def wait(name, condition, limit=8.0, hold=0.3):
    """True once ``condition`` has held for ``hold`` seconds, with the list redrawn meanwhile: the counts change
    before the list draws, and a capture in the same pass showed the previous state (6ceaef365ff: the listed case
    captured the empty state while the drawn record already said none)."""
    since = state.setdefault("since", {}).setdefault(name, time.monotonic())
    held = state.setdefault("held", {})
    if condition():
        first = held.setdefault(name, time.monotonic())
        if time.monotonic() - first >= hold:
            state["since"].pop(name, None)
            held.pop(name, None)
            return True
        if "area" in ctx:
            area().tag_redraw()
        return False
    held.pop(name, None)
    if time.monotonic() - since > limit:
        if "area" not in ctx:
            raise AssertionError("timed out waiting for " + name)
        params = space().params
        raise AssertionError("timed out waiting for %s (counts %s, library %s, search %r)" % (
            name, counts(), params.asset_library_reference, params.filter_search))
    return False


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str, ensure_ascii=False))


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(checks) and all(checks.values()))
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    prefs.view.ui_scale, prefs.view.language = 1.0, "en_US"
    save()
    print("ANGELO_ASSET_BROWSER_STATES", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def look(key, expected):
    """Capture the list and check it: the counts, the drawn state, teal pixels and fit."""
    shot = capture("asset-states-%s.png" % key)
    teal = teal_in_list(shot)
    record = states.drawn.get(area().as_pointer()) or {}
    seen = {"counts": counts(), "state": record.get("state"), "teal": teal, "lines": record.get("lines"),
            "buttons": record.get("buttons")}
    state["observed"][key] = seen
    if expected is None:
        checks["assets_listed_draws_no_state_" + key] = seen["state"] is None and teal <= TEAL_MOST
    else:
        checks[f"{expected}_state_is_drawn_" + key] = seen["state"] == expected and teal >= TEAL_LEAST
        checks[f"{expected}_text_fits_" + key] = fits()
    return seen


def tick():
    try:
        assert time.monotonic() - started < 300, "Asset Browser states fixture timed out in " + state["phase"]
        phase = state["phase"]
        if phase == "boot":
            if not wait("settled", settled, limit=20, hold=0.5):
                return .2
            from angelo_agent.editor_types import set_editor
            target = max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
            ctx["area"] = target.as_pointer()
            set_editor(window(), target, ui_type='ASSETS')
            state["phase"] = "library"
            return .6
        if phase == "library":
            space().params.asset_library_reference = 'LOCAL'
            ctx["material"] = bpy.data.materials.new("Angelo QA material")
            ctx["todo"] = [(scale, language) for scale in (1.0, 2.0) for language in ("en_US", "de_DE")]
            prefs.view.use_translate_interface = True
            state["phase"] = "case"
            return .5
        if phase == "case":
            if not ctx["todo"]:
                prefs.view.ui_scale = 1.0
                ctx["studio_todo"] = ["en_US", "de_DE"]
                state["phase"] = "studio"
                return .6
            scale, language = ctx["todo"][0]
            if prefs.view.ui_scale != scale or prefs.view.language != language:
                prefs.view.ui_scale, prefs.view.language = scale, language
                return .5
            ctx["material"].asset_clear()
            space().params.filter_search = ""
            refresh()
            park_pointer()
            state["phase"] = "empty"
            return .5
        if phase in {"empty", "listed", "no_results"}:
            scale, language = ctx["todo"][0]
            key = "%s-%g-%s" % (phase, scale, language)
            state["counts_seen"] = counts()   # What the list says while it is awaited (a timeout says why).
            if phase == "empty":
                if not wait(key, lambda: counts()["ready"] and counts()["total"] == 0):
                    return .2
                seen = look(key, "empty")
                checks["the_counts_say_empty_" + key] = seen["counts"] == {"listed": 0, "total": 0, "ready": True}
                ctx["material"].asset_mark()
                refresh()
                state["phase"] = "listed"
                return .5
            if phase == "listed":
                if not wait(key, lambda: counts()["ready"] and counts()["listed"] == 1):
                    return .2
                seen = look(key, None)
                checks["the_counts_list_the_asset_" + key] = seen["counts"] == {"listed": 1, "total": 1, "ready": True}
                space().params.filter_search = QUERY
                area().tag_redraw()
                state["phase"] = "no_results"
                return .5
            if not wait(key, lambda: counts()["ready"] and counts()["listed"] == 0 and counts()["total"] == 1):
                return .2
            seen = look(key, "no_results")
            checks["the_counts_say_no_results_" + key] = seen["counts"] == {"listed": 0, "total": 1, "ready": True}
            checks["no_results_names_the_query_" + key] = QUERY in "".join("".join(seen["lines"] or []).split())
            ctx["todo"].pop(0)
            state["phase"] = "case"
            return .3
        if phase == "studio":   # Media Studio's own library, empty: it says what will appear there.
            if not ctx["studio_todo"]:
                space().params.asset_library_reference = 'LOCAL'
                prefs.view.language = "en_US"
                ctx["material"].asset_mark()
                space().params.filter_search = QUERY
                refresh()
                state["phase"] = "clicks"
                return .6
            language = ctx["studio_todo"][0]
            if prefs.view.language != language:
                prefs.view.language = language
                return .5
            from angelo_agent import media_shelf, studio_assets_host
            if not ctx.get("studio_library"):
                studio_assets_host.ensure_registered()
                ctx["studio_library"] = media_shelf.library_name()
            space().params.asset_library_reference = ctx["studio_library"]
            space().params.filter_search = ""
            refresh()
            state["phase"] = "studio_look"
            return .5
        if phase == "studio_look":
            language = ctx["studio_todo"][0]
            key = "studio-empty-1-" + language
            # Nothing listed is what makes it empty: the library's folder also holds its catalogue, registration and
            # lock files, which Blender's list counts in its total but never lists (d-merged on the stage: 0 of 3).
            if not wait(key, lambda: counts()["ready"] and counts()["listed"] == 0
                        and (states.drawn.get(area().as_pointer()) or {}).get("state") == "empty"):
                return .2
            seen = look(key, "empty")
            from angelo_agent.ui_text import iface_
            words = "".join("".join(seen["lines"] or []).split())
            checks["media_studio_library_says_what_appears_there_" + language] = (
                words == "".join(iface_(states.STUDIO_EMPTY).split()))
            ctx["studio_todo"].pop(0)
            state["phase"] = "studio"
            return .3
        if phase == "clicks":   # English, scale 1, the asset marked and the search typed: no results.
            if not wait("clicks", lambda: counts()["ready"] and counts()["listed"] == 0 and counts()["total"] == 1
                        and (states.drawn.get(area().as_pointer()) or {}).get("state") == "no_results"):
                area().tag_redraw()
                return .2
            r = region()
            hits = dict((action, rect) for rect, action in entry._hits.get((area().as_pointer(), 'WINDOW'), ()))
            x, y, w, h = hits["clear_search"]
            press(r.x + x + w / 2, r.y + y + h / 2)
            state["phase"] = "cleared"
            return .6
        if phase == "cleared":
            if not wait("cleared", lambda: counts()["ready"] and counts()["listed"] == 1):
                area().tag_redraw()
                return .2
            capture("asset-states-after-clear-search.png")
            checks["clear_search_clears_only_the_search"] = (space().params.filter_search == ""
                                                             and space().params.asset_library_reference == 'LOCAL'
                                                             and "clear_search" in acted)
            checks["after_clearing_no_state_is_drawn"] = (states.drawn.get(area().as_pointer()) or {}).get("state") is None
            ctx["material"].asset_clear()
            refresh()
            state["phase"] = "generate"
            return .6
        if phase == "generate":
            if not wait("generate", lambda: counts()["ready"] and counts()["total"] == 0
                        and (states.drawn.get(area().as_pointer()) or {}).get("state") == "empty"):
                area().tag_redraw()
                return .2
            r = region()
            hits = dict((action, rect) for rect, action in entry._hits.get((area().as_pointer(), 'WINDOW'), ()))
            x, y, w, h = hits["studio"]
            windows_before = len(bpy.context.window_manager.windows)
            ctx["workspace"] = window().workspace.name
            press(r.x + x + w / 2, r.y + y + h / 2)
            ctx["windows_before"] = windows_before
            state["phase"] = "generated"
            return .8
        if phase == "generated":
            capture("asset-states-after-generate.png")
            opened = (len(bpy.context.window_manager.windows) > ctx["windows_before"]
                      or window().workspace.name != ctx["workspace"])
            state["observed"]["generate"] = {"acted": acted, "windows": len(bpy.context.window_manager.windows),
                                             "workspace": window().workspace.name}
            checks["generate_runs_the_shared_opener"] = "studio" in acted and opened
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=1.0)
