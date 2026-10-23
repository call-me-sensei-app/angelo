# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Asset Shelf's Media Studio and Generation Flows pills in the running app (1.0.5, ``asset_shelf_add``).

Real default startup and real input (``Window.event_simulate``). No CLI, account, network or inference. In Manual
mode (Director hides the 3D view's shelf), for the
sculpt and the texture paint shelves, at UI scale 1 and 2: the shelf without the pill (*before*, the add-on's pill
off) and with it (*after*), each captured; the pill is drawn where it is recorded (its pixels changed, its teal plus
present: Media Studio's icon and label in light pixels), on the shelf's top edge and inside the 3D view; the pointer over it shows its tooltip; a real click opens
Media Studio in its own window with the 3D tab (sculpt) or the Image tab (texture paint) selected, read from the
Studio window's own pixels (the selected tab is filled in the card-kit teal, the other is not) and its state, and
from its first drawn frame: a draw handler beside Studio's records the tab of every frame drawn after the click, so a
frame of the previous visit's tab (what a person would see) fails.
German (de_DE) is captured too (doc/angelo/studio_entry_points.rst, "Checks every owner runs").

Run through ``run_native_suites.py --gui --include test_blender_asset_shelf_add``; check ``asset-shelf-add.json``
for ``success`` and ``phase == "complete"``.
"""

import json
import os
import tempfile
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-asset-shelf-add-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "asset-shelf-add.json"
prefs = bpy.context.preferences
prefs.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "pills": {}, "tabs": {}}
checks = state["checks"]
started = time.monotonic()
LIGHT = (237, 242, 240)   # The pill's icon and label (card_kit.TEXT, studio_toggle.frame_icon).
TEAL = (43, 196, 191)     # card_kit.TEAL in 0-255: a selected Studio tab (studio.PURPLE is that token).
# (case, object mode, Studio tab, UI scale, language): each case shows the shelf, captures it before and after,
# hovers, then clicks.
CASES = (("sculpt", 'SCULPT', "3D", 1.0, "en_US"), ("texture_paint", 'TEXTURE_PAINT', "IMAGE", 1.0, "en_US"),
         ("texture_paint_x2", 'TEXTURE_PAINT', "IMAGE", 2.0, "en_US"), ("sculpt_x2", 'SCULPT', "3D", 2.0, "en_US"),
         ("sculpt_de", 'SCULPT', "3D", 1.0, "de_DE"), ("texture_paint_de_x2", 'TEXTURE_PAINT', "IMAGE", 2.0, "de_DE"),
         ("sculpt_ar", 'SCULPT', "3D", 1.0, "ar_EG"))


def window():
    return bpy.context.window_manager.windows[0]


def windows():
    return bpy.context.window_manager.windows


def view3d():
    areas = [a for a in window().screen.areas if a.type == 'VIEW_3D']
    return max(areas, key=lambda a: a.width * a.height)


def capture(name, target=None):
    path = OUT / name
    with bpy.context.temp_override(window=target or window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def move(x, y, target=None):
    (target or window()).event_simulate(type='MOUSEMOVE', value='NOTHING', x=int(x), y=int(y))


def press(x, y, target=None):
    w = target or window()
    for kind, value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        w.event_simulate(type=kind, value=value, x=int(x), y=int(y))


def settled():
    """Director's layout and Manual's Media Studio / Generation Flows tabs are in place (studio_workspaces builds
    those tabs by switching the window through them for a moment after startup)."""
    return qa_boot.started()   # Director's layout and Manual's tabs (qa_boot).


def pixels(path):
    """A capture's pixels as an (height, width, 3) array of 0-255 integers, bottom row first (window pixels)."""
    import numpy
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        values = numpy.empty(width * height * 4, dtype=numpy.float32)
        image.pixels.foreach_get(values)
    finally:
        bpy.data.images.remove(image)
    return (values.reshape(height, width, 4)[..., :3] * 255).round().astype(numpy.int32)


def box(px, rect):
    x, y, w, h = (int(round(v)) for v in rect)
    return px[max(0, y):y + h, max(0, x):x + w]


def near(block, color, tolerance=40):
    import numpy
    return float((numpy.abs(block - numpy.array(color)).sum(axis=2) <= tolerance).mean()) if block.size else 0.0


def changed(before, after, rect):
    import numpy
    a, b = box(pixels(before), rect), box(pixels(after), rect)
    return float((numpy.abs(a - b).sum(axis=2) > 24).mean()) if a.size else 0.0


def pill():
    """``(rects by action, labelled, kind)`` of this view's pills as last drawn, or None."""
    from angelo_agent import asset_shelf_add
    place = asset_shelf_add.places.get(view3d().as_pointer())
    return (dict(place[1]), place[2], place[3]) if place else None


def studio_window():
    """The window showing Media Studio: its own (Director) or the main one on Manual's Media Studio tab."""
    from angelo_agent import studio
    return next((w for w in windows() if w.screen and any(studio.is_studio(a) for a in w.screen.areas)), None)


def studio_state(target):
    from angelo_agent import studio
    area = next((a for a in target.screen.areas if studio.is_studio(a)), None) if target and target.screen else None
    return studio.view(area) if area else {}, area


def tab_colours(path, target):
    """Mean distance from the teal of each tab button, from the Studio window's capture: the selected tab is
    the one filled teal. Hit rectangles are region pixels; the capture is window pixels."""
    import numpy
    view, area = studio_state(target)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    px = pixels(path)
    found = {}
    for rect, action, value in view.get("hits", []):
        if action == "tab":
            x, y, w, h = rect
            inner = (region.x + x + w * .1, region.y + y + h * .15, w * .25, h * .25)   # Fill, not the label.
            block = box(px, inner).reshape(-1, 3)
            found[value] = float(numpy.abs(block - numpy.array(TEAL)).sum(axis=1).mean()) if block.size else 999.0
    return found


FRAMES = []   # (seconds, tab) of every frame Media Studio draws, recorded beside Studio's own draw handler.


def _studio_frame():
    from angelo_agent import studio
    area, region = bpy.context.area, bpy.context.region
    if region is not None and region.type == 'WINDOW' and studio.is_studio(area):
        FRAMES.append((time.monotonic(), studio.view(area).get("kind")))


def set_mode(mode):
    area = view3d()
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window(), area=area, region=region):
        if bpy.context.object is None or bpy.context.object.type != 'MESH':
            if bpy.context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            bpy.ops.mesh.primitive_cube_add()
        bpy.ops.object.mode_set(mode=mode)
    area.tag_redraw()


def finish(error=None):
    prefs.view.ui_scale = 1.0
    prefs.view.language = "en_US"
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_ASSET_SHELF_ADD", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=20):
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in " + phase)
    return False


def tick():
    from angelo_agent import asset_shelf_add, canvas_controls
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 300, "Suite timed out"
        case_index = state.get("case", 0)
        name, mode, tab, scale, language = CASES[min(case_index, len(CASES) - 1)]
        if phase == "boot":   # Director hides the 3D view's shelf (director_layout.configure): Manual shows it.
            from angelo_agent import director
            if not settled():
                return .3
            if director.is_director(window()):
                director.request_mode(window(), "MANUAL")
            state["phase"] = "manual"
            return .5
        if phase == "manual":
            from angelo_agent import director
            state["manual_wait"] = {"director": director.is_director(window()), "waiting_on": qa_boot.waiting_on(),
                                    "workspace": window().workspace.name}   # In the report if this times out.
            if not wait(phase, lambda: settled() and not director.is_director(window())):
                return .2
            space = view3d().spaces.active
            state["shelf_region_shown_in_manual"] = space.show_region_asset_shelf
            if not space.is_property_readonly("show_region_asset_shelf"):   # Editable only where a shelf polls.
                space.show_region_asset_shelf = True   # The person's own layout decides; this suite needs it shown.
            state["phase"] = "mode"
            return .1
        if phase == "mode":   # Off for the before picture: the shelf as stock Blender draws it.
            state.setdefault("enabled", asset_shelf_add.enabled)   # The pills show in every run; off only here.
            asset_shelf_add.enabled = lambda: False
            prefs.view.ui_scale = scale
            prefs.view.language = language
            prefs.view.use_translate_interface = True
            set_mode(mode)
            state["phase"] = "shelf"
            return .5
        if phase == "shelf":
            if not wait(phase + name, lambda: asset_shelf_add.shelf_header(view3d()) is not None):
                return .2
            state["before"] = str(capture("shelf-{}-0-before.png".format(name)))
            asset_shelf_add.enabled = state["enabled"]
            view3d().tag_redraw()
            state["phase"] = "pill"
            return .4
        if phase == "pill":
            if not wait(phase + name, lambda: pill() is not None):
                return .2
            after = capture("shelf-{}-1-after.png".format(name))
            rects, labelled, kind = pill()
            rect = rects["studio"]
            header = asset_shelf_add.shelf_header(view3d())
            region = next(r for r in view3d().regions if r.type == 'WINDOW')
            px = pixels(after)
            record = {"rects": {a: [round(v) for v in r] for a, r in rects.items()}, "labelled": labelled,
                      "kind": kind, "scale": prefs.system.ui_scale}
            for action, r in rects.items():
                record[action] = {"changed": round(changed(state["before"], after, r), 3),
                                  "light": round(near(box(px, r), LIGHT), 3)}
            state["pills"][name] = record
            checks[name + "_both_pills_drawn_where_recorded"] = all(
                record[a]["changed"] > 0.3 and record[a]["light"] > 0.02 for a in ("studio", "flows"))
            checks[name + "_media_studio_then_generation_flows"] = rects["studio"][0] < rects["flows"][0]
            checks[name + "_labelled_at_this_width"] = labelled
            checks[name + "_pill_on_the_shelf_top_edge_inside_the_view"] = (
                header.y + header.height <= rect[1] <= header.y + header.height + 20 * prefs.system.ui_scale
                and rect[0] >= region.x and rect[0] + rect[2] <= region.x + region.width)
            checks[name + "_pill_scales_with_the_interface"] = abs(
                rect[3] - asset_shelf_add.HEIGHT * prefs.system.ui_scale) < 1.5
            checks[name + "_pill_asks_for_the_shelf_tab"] = kind == tab.lower()
            canvas_controls.last_tip = None
            move(rect[0] + rect[2] / 2, rect[1] + rect[3] / 2)
            state["phase"] = "hover"
            return .4
        if phase == "hover":
            if not wait(phase + name, lambda: (view3d().as_pointer(), "studio") in asset_shelf_add._hover
                        and canvas_controls.last_tip is not None, limit=8):
                return .2
            capture("shelf-{}-2-hover.png".format(name))
            checks[name + "_pointer_over_it_shows_the_tooltip"] = True
            rect = pill()[0]["studio"]
            state["workspace"] = window().workspace.name   # Manual's Media Studio tab replaces it; restored after.
            FRAMES.clear()
            state["clicked_at"] = time.monotonic()
            press(rect[0] + rect[2] / 2, rect[1] + rect[3] / 2)
            state["phase"] = "opened"
            return .6
        if phase == "opened":
            if not wait(phase + name, lambda: studio_window() is not None and studio_state(studio_window())[0]
                        .get("hits") and studio_state(studio_window())[0].get("kind") == tab and FRAMES):
                return .2
            target = studio_window()
            path = capture("shelf-{}-3-media-studio.png".format(name), target)
            view, _area = studio_state(target)
            colours = tab_colours(path, target)
            other = "IMAGE" if tab == "3D" else "3D"
            retries = state.setdefault("recaptures", {}).setdefault(name, 0)
            if abs(colours.get(tab, 0) - colours.get(other, 0)) < 60 and retries < 5:
                state["recaptures"][name] = retries + 1   # Not drawn yet (a new window's first frames): look again.
                return .4
            # What a person sees: every frame Studio drew since the click, the first one included.
            wrong = [kind for _at, kind in FRAMES].index(tab) if tab in [k for _a, k in FRAMES] else len(FRAMES)
            state.setdefault("frames", {})[name] = {"drawn": len(FRAMES), "before_the_tab": wrong,
                                                    "first_ms": round((FRAMES[0][0] - state["clicked_at"]) * 1000)}
            checks[name + "_its_first_frame_shows_its_tab"] = wrong == 0
            from angelo_agent import studio_toggle
            own = studio_toggle.opened_window(windows())
            state["tabs"][name] = {"state": view.get("kind"), "teal_distance": colours,
                                   "route": "its own window" if own is not None else "Manual's Media Studio tab"}
            checks[name + "_click_opens_media_studio_on_its_tab"] = (
                view.get("kind") == tab and colours.get(tab, 999) + 60 < colours.get(other, 0))
            if own is not None:
                studio_toggle.close_studio(own)
            else:
                window().workspace = bpy.data.workspaces[state["workspace"]]
            state["phase"] = "closed"
            return .4
        if phase == "closed":
            from angelo_agent import studio_toggle
            if not wait(phase + name, lambda: studio_toggle.opened_window(windows()) is None and len(windows()) == 1
                        and window().workspace.name == state["workspace"]):
                return .2
            state["case"] = case_index + 1
            state["phase"] = "mode" if state["case"] < len(CASES) else "complete"
            if state["phase"] == "complete":
                checks["every_open_was_through_the_one_opener"] = asset_shelf_add.opened == [
                    c[2].lower() for c in CASES]
                rect = pill()[0]["flows"]   # Generation Flows, last: its tab (Manual) or popup.
                press(rect[0] + rect[2] / 2, rect[1] + rect[3] / 2)
                state["phase"] = "flows"
                return .8
            return .2
        if phase == "flows":
            if not wait(phase, lambda: asset_shelf_add.opened[-1:] == ["flows"], limit=8):
                return .2
            capture("shelf-flows-opened.png")
            checks["generation_flows_pill_opens_flows"] = True
            state["phase"] = "complete"
            finish()
            return None
    except Exception as error:   # noqa: BLE001 (the suite reports any failure in its receipt)
        finish(error)
        return None
    return .2


_frame_handler = bpy.types.SpaceConsole.draw_handler_add(_studio_frame, (), 'WINDOW', 'POST_PIXEL')
bpy.app.timers.register(tick, first_interval=.5)
