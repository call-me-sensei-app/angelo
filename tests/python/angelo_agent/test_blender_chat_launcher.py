# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo in the 3D view's corner while the chat is hidden, in the running app (``chat_launcher``).

Real default startup and real input (``Window.event_simulate``). No CLI, account, network or inference.

Covered, each with a screenshot: no Angelo while the chat shows; hidden in Director, Angelo sits in the 3D view's
bottom-right corner (vision: the disc's card colour at its rim, the raven's colours at its centre, the scene
beyond); the pointer over it shows the tooltip; a click opens the chat in Director's split and Angelo is gone; a
click on the scene outside the disc still reaches the scene (it deselects); the same in Manual mode, on another
workspace tab, and at UI scale 2 (where a click still opens the chat); with Reduce Motion it never blinks; its draw
cost; and, with the twice-a-second watch stopped, the chat's area turned into a 3D view (as the Editor Type menu
does) brings Angelo by Blender's signal alone.

Run through ``run_native_suites.py --gui --include test_blender_chat_launcher``; check ``chat-launcher.json`` for
``success`` and ``phase == "complete"``.
"""

import array
import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))   # qa_frames
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')
os.environ["ANGELO_ENABLE_CHAT_LAUNCHER"] = "1"
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-chat-launcher-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "chat-launcher.json"
bpy.context.preferences.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "pixels": {}}
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def chat_state():
    from angelo_agent import chat_toggle
    return chat_toggle.chat_state(window(), bpy.context.window_manager.windows)[0]


def place():
    from angelo_agent import chat_launcher
    return chat_launcher._places.get(window().as_pointer()) if window().as_pointer() in chat_launcher._shown else None


def drawn_since(count):
    from angelo_agent import chat_launcher
    return chat_launcher.draws > count


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    if str(path) not in state["screenshots"]:
        state["screenshots"].append(str(path))
    return path


GRID = tuple((fx, fy) for fx in (0.25, 0.5, 0.75) for fy in (0.45, 0.6, 0.75))   # Region fractions, any content.
TOLERANCE = 0.03   # Per channel, 0..1: the same frame read twice; a different frame or editor differs by more.
_drawn = {}   # chat area pointer -> what its region holds right after the chat's own draw (``_read_chat_region``).
_order = {"handle": None, "chat": None}   # This suite's draw handler, and the chat's handler it was added after.


def _watch_chat_draws():
    """(Re)adds the region read after the chat's own draw handler and forgets earlier reads. Blender calls a region
    type's draw handlers in the order they were added (``ED_region_draw_cb_activate`` appends), and the add-on can
    register the chat's handler again after this module's import, so a read added once at import can run before the
    chat draws and see only the region's clear colour (the 1.0.5 triage: the in-draw read passed at points where the
    picture showed the connect cards)."""
    from angelo_agent import chat_view
    if _order["handle"] is not None:
        bpy.types.SpaceConsole.draw_handler_remove(_order["handle"], 'WINDOW')
    _order.update(handle=bpy.types.SpaceConsole.draw_handler_add(_read_chat_region, (), 'WINDOW', 'POST_PIXEL'),
                  chat=chat_view._handler)
    _drawn.clear()


def _read_chat_region():
    """After the chat's own draw: its region's current pixels, the frame being made (what test_blender_chat_popovers
    and test_blender_chat_usage read to avoid macOS front-buffer lag), kept as RGB 0-255; judged in ``chat_in_pixels``,
    not during drawing."""
    import gpu
    import numpy
    from angelo_agent import chat_view, console
    area, region = bpy.context.area, bpy.context.region
    if area is None or region is None or not console.is_chat(area):
        return
    if chat_view._handler is None or chat_view._handler is not _order["chat"]:
        return   # The chat's handler was added after this one: this read would come before its draw.
    width, height = region.width, region.height
    pixels = gpu.state.active_framebuffer_get().read_color(0, 0, width, height, 4, 0, 'UBYTE')
    pixels.dimensions = width * height * 4
    _drawn[area.as_pointer()] = numpy.frombuffer(pixels, dtype=numpy.uint8).reshape(height, width, 4)[:, :, :3].copy()


def judge_drawn(rgb):
    """What the chat's region held after its own draw: the colours at ``GRID`` (0..1), the share of pixels away from
    the median colour (``qa_frames``: drawn content, not one flat fill) and the most common colour (0..1)."""
    import numpy
    import qa_frames
    height, width = rgb.shape[:2]
    grid = [tuple(round(int(v) / 255, 3) for v in rgb[int(height * fy), int(width * fx)]) for fx, fy in GRID]
    packed = rgb.astype(numpy.uint32)
    values, counts = numpy.unique((packed[..., 0] << 16) | (packed[..., 1] << 8) | packed[..., 2], return_counts=True)
    common = int(values[counts.argmax()])
    return {"grid": grid, "share": round(qa_frames.content_share(rgb), 3), "drawn": qa_frames.drawn(rgb),
            "common": tuple(round(((common >> shift) & 255) / 255, 3) for shift in (16, 8, 0))}


_watch_chat_draws()


def close(a, b):
    return max(abs(p - q) for p, q in zip(a, b)) < TOLERANCE


def chat_in_pixels(phase, shot):
    """The chat is really there, judged by what is drawn, never by the dock's own report: the area is the Angelo chat
    editor; its region, read right after the chat's own draw, holds drawn content (``qa_frames``) on the chat's
    background as its most common colour; and a picture shows that same frame: at 6 or more of the 9 ``GRID`` points the
    screenshot has the colour the chat's region held there. The points carry no assumption about where the chat draws
    its content (the welcome and connect cards, a transcript): the region read is the reference. ``capture`` forces two
    buffer swaps and then Blender's screenshot reads the front buffer, which on macOS returned the window from mid-dock
    right after a dock (release-105's screenshot review, 2026-10-23: a second Properties where the chat was; the app had
    drawn the chat 0.06 s after the dock, Mac 1 diagnostic 105h; candidate 4c): the picture is taken again, 0.2 s
    apart, until it shows the chat, at most 10 times, and the tries are recorded. False while a retry is due."""
    from angelo_agent import chat_view, console
    area = next((a for a in window().screen.areas if console.is_chat(a)), None)
    tries = state.setdefault("picture_tries", {})
    tries[phase] = tries.get(phase, 0) + 1
    if chat_view._handler is not _order["chat"] and tries[phase] < 10:
        _watch_chat_draws()   # The add-on added the chat's handler again: read after it from the next draw.
        return False
    capture(shot)
    region = next((r for r in area.regions if r.type == 'WINDOW'), None) if area else None
    held = _drawn.get(area.as_pointer()) if area else None
    drawn = judge_drawn(held) if held is not None else None
    shown = 0
    if region and drawn:
        picture = pixels(shot, [(region.x + int(region.width * fx), region.y + int(region.height * fy))
                                for fx, fy in GRID])
        shown = sum(close(p, d) for p, d in zip(picture, drawn["grid"]))
    if shown < 6 and tries[phase] < 10:
        return False
    state.setdefault("drawn_pixels", {})[phase] = dict(drawn or {}, shown=shown)
    checks = state["checks"]
    checks[phase + "_the_area_is_the_angelo_chat"] = bool(
        area and area.type == 'CONSOLE' and area.spaces.active.language == console.language_id)
    checks[phase + "_its_region_draws_the_chat"] = bool(drawn) and drawn["drawn"] and close(drawn["common"],
                                                                                          chat_view.BG[:3])
    checks[phase + "_a_picture_shows_the_chat"] = shown >= 6
    return True


def pixels(name, points):
    """RGB (0..1) of the screenshot ``name`` at window-pixel ``points`` (Blender images are bottom-up too)."""
    image = bpy.data.images.load(str(OUT / name), check_existing=False)
    try:
        width, height = image.size
        values = array.array('f', [0.0]) * (width * height * 4)
        image.pixels.foreach_get(values)
        scale = width / window().width
        found = []
        for x, y in points:
            index = (int(y * scale) * width + int(x * scale)) * 4
            found.append(tuple(round(v, 3) for v in values[index:index + 3]))
        state["pixels"][name] = found
        return found
    finally:
        bpy.data.images.remove(image)


def angelo_drawn(name):
    """Vision: the disc's card colour just inside its rim, the raven (not the card) at its centre, the scene well
    outside it."""
    from angelo_agent import card_kit
    _area, cx, cy, radius = place()
    rim, centre, outside = pixels(name, [(cx, cy - radius * 0.86), (cx, cy), (cx - radius * 2.2, cy)])
    card = tuple(round(v, 3) for v in card_kit.CARD[:3])
    close = lambda a, b: max(abs(p - q) for p, q in zip(a, b)) < 0.035   # noqa: E731
    return close(rim, card) and not close(centre, card) and not close(outside, card)


def press(x, y, kind='LEFTMOUSE'):
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=int(x), y=int(y))
    window().event_simulate(type=kind, value='PRESS', x=int(x), y=int(y))
    window().event_simulate(type=kind, value='RELEASE', x=int(x), y=int(y))


def finish(error=None):
    from angelo_agent import chat_launcher
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    steady = sorted(list(chat_launcher.samples)[1:]) or [0.0]   # The first draw builds the raven's batches once.
    state.update(elapsed_seconds=round(time.monotonic() - started, 1), draws=chat_launcher.draws,
                 draw_ms=round(1000 * chat_launcher.draw_seconds / max(1, chat_launcher.draws), 3),
                 draw_ms_median=round(1000 * steady[len(steady) // 2], 3), draw_ms_max=round(1000 * steady[-1], 3),
                 view_cost_ms={str(k): round(v * 1000, 2) for k, v in chat_launcher._cost.items()},
                 blinks=chat_launcher.blinks)
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_CHAT_LAUNCHER", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=20):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    if time.monotonic() - state["waiting"] > limit:
        state["checks"]["timed_out_in_" + phase] = False
        state.pop("waiting", None)
        return True
    return False


def hide_and_find(phase, shot, next_phase):
    """Wait for Angelo drawn in the corner, then capture and judge the picture."""
    from angelo_agent import chat_launcher

    def in_the_shown_view():   # Angelo drawn in this tab's own 3D view, not a place left from the tab before.
        view = max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
        return place() is not None and place()[0] == view.as_pointer()
    if not wait(phase, lambda: chat_state() == "hidden" and in_the_shown_view() and chat_launcher.draws > state["mark"]):
        return False
    tries = state.setdefault("picture_tries", {})
    tries[phase] = tries.get(phase, 0) + 1
    capture(shot)
    if not angelo_drawn(shot) and tries[phase] < 10:
        return False   # The front-buffer read can hand back a frame from before Angelo (chat_in_pixels): again.
    state["checks"][phase + "_angelo_in_the_corner"] = place() is not None and angelo_drawn(shot)
    _area, cx, cy, radius = place()
    view = max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    state["checks"][phase + "_bottom_right_of_the_view"] = (cx > view.x + view.width / 2 and cy < view.y + view.height / 2)
    state["phase"] = next_phase
    return True


def tick():
    try:
        assert time.monotonic() - started < 240, "Fixture timed out in " + state["phase"]
        checks, phase = state["checks"], state["phase"]
        from angelo_agent import chat_launcher, director, ui
        settled = qa_boot.started()
        if phase == "boot":
            if not settled or not director.is_director(window()) or chat_state() != "docked":
                return .2
            chat_launcher.BLINK_EVERY = 0.6   # The suite's own pace; the real one is every few seconds.
            if bpy.app.timers.is_registered(chat_launcher._idle):   # A timer started at the real pace restarts at it.
                bpy.app.timers.unregister(chat_launcher._idle)
            capture("launcher-0-director-chat-shown.png")
            checks["no_angelo_while_the_chat_shows"] = place() is None
            state["mark"] = chat_launcher.draws
            bpy.ops.angelo.toggle_chat()
            state["phase"] = "director"
            return .4
        if phase == "director":
            if not hide_and_find(phase, "launcher-1-director-corner.png", "hover"):
                return .2
            return .2
        if phase == "hover":
            _area, cx, cy, _radius = place()
            window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=int(cx), y=int(cy))
            state["phase"] = "hovered"
            return .5
        if phase == "hovered":
            from angelo_agent import canvas_controls
            canvas_controls.last_tip = None
            capture("launcher-2-hover-tooltip.png")
            checks["the_pointer_over_angelo_shows_the_tooltip"] = (window().as_pointer() in chat_launcher._hover
                                                                   and canvas_controls.last_tip is not None)
            state["blinks_before"] = chat_launcher.blinks
            state["phase"] = "blinking"
            return 1.6
        if phase == "blinking":
            cheap = any(v <= chat_launcher.MAX_VIEW_COST for v in chat_launcher._cost.values())
            checks["angelo_blinks_in_a_cheap_view"] = chat_launcher.blinks > state["blinks_before"] or not cheap
            bpy.context.preferences.view.use_reduce_motion = True
            state["blinks_before"] = chat_launcher.blinks
            state["phase"] = "reduced"
            return 1.6
        if phase == "reduced":
            checks["no_blink_with_reduce_motion"] = chat_launcher.blinks == state["blinks_before"]
            bpy.context.preferences.view.use_reduce_motion = False
            # A click on the scene beside Angelo is the scene's: it deselects the cube.
            _area, cx, cy, radius = place()
            bpy.data.objects["Cube"].select_set(True)
            press(cx - radius * 3.0, cy + radius * 0.2)
            state["phase"] = "scene_click"
            return .5
        if phase == "scene_click":
            checks["a_click_beside_angelo_reaches_the_scene"] = not bpy.data.objects["Cube"].select_get()
            bpy.data.objects["Cube"].select_set(True)
            _area, cx, cy, _radius = place()
            _watch_chat_draws()   # Only a draw after this click counts, read after the chat's own.
            press(cx, cy)
            state["phase"] = "director_opened"
            return .5
        if phase == "director_opened":
            if not wait(phase, lambda: settled and chat_state() == "docked" and place() is None):
                return .2
            if not chat_in_pixels(phase, "launcher-3-director-click-opened-the-chat.png"):
                return .2
            checks["a_click_opens_the_chat_in_director"] = chat_state() == "docked" and director.is_director(window())
            checks["angelo_is_gone"] = place() is None
            checks["the_click_selected_nothing_behind_it"] = bpy.data.objects["Cube"].select_get()
            director.request_mode(window(), "MANUAL")
            state["phase"] = "manual"
            return .8
        if phase == "manual":
            if not wait(phase, lambda: settled and not director.is_director(window()) and chat_state() == "docked"):
                return .2
            state["mark"] = chat_launcher.draws
            bpy.ops.angelo.toggle_chat()
            state["phase"] = "manual_hidden"
            return .4
        if phase == "manual_hidden":
            if not hide_and_find(phase, "launcher-4-manual-corner.png", "manual_tab"):
                return .2
            return .2
        if phase == "manual_tab":
            from angelo_agent import director_layout
            other = next(w for w in bpy.data.workspaces if w != window().workspace
                         and not director_layout.is_director_workspace(w)
                         and any(a.type == 'VIEW_3D' for s in w.screens for a in s.areas))
            state["tab"] = other.name
            state["mark"] = chat_launcher.draws
            window().workspace = other
            state["phase"] = "manual_tab_hidden"
            return .6
        if phase == "manual_tab_hidden":
            if not hide_and_find(phase, "launcher-5-manual-other-tab.png", "manual_click"):
                return .2
            return .2
        if phase == "manual_click":
            _area, cx, cy, _radius = place()
            _watch_chat_draws()   # Only a draw after this click counts, read after the chat's own.
            press(cx, cy)
            state["phase"] = "manual_opened"
            return .5
        if phase == "manual_opened":
            if not wait(phase, lambda: settled and chat_state() == "docked" and place() is None):
                return .2
            if not chat_in_pixels(phase, "launcher-6-manual-click-opened-the-chat.png"):
                return .2
            checks["a_click_opens_the_chat_in_manual"] = chat_state() == "docked"
            checks["angelo_is_gone_in_manual"] = place() is None
            bpy.context.preferences.view.ui_scale = 2.0
            state["mark"] = chat_launcher.draws
            bpy.ops.angelo.toggle_chat()
            state["phase"] = "scale_two"
            return .8
        if phase == "scale_two":
            if not hide_and_find(phase, "launcher-7-manual-ui-scale-2.png", "scale_two_click"):
                return .2
            _area, _cx, _cy, radius = place()
            checks["twice_the_size_at_ui_scale_2"] = abs(radius / (chat_launcher.DISC / 2)
                                                         - bpy.context.preferences.system.ui_scale) < 0.05
            return .2
        if phase == "scale_two_click":
            # Editors lie further apart at a larger scale: the dock's layout checks take Blender's own border then
            # (layout.border_gap; 1.0.4 refused here: "The outer editor column must span the window height").
            _area, cx, cy, _radius = place()
            _watch_chat_draws()   # Only a draw after this click counts, read after the chat's own.
            press(cx, cy)
            state["phase"] = "scale_two_opened"
            return .5
        if phase == "scale_two_opened":
            if not wait(phase, lambda: settled and chat_state() == "docked" and place() is None):
                return .2
            if not chat_in_pixels(phase, "launcher-8-ui-scale-2-click-opened-the-chat.png"):
                return .2
            checks["a_click_opens_the_chat_at_ui_scale_2"] = chat_state() == "docked"
            from angelo_agent import chat_toggle, console
            # Only Blender's message bus can move Angelo now: the watch is stopped until Angelo is found.
            if bpy.app.timers.is_registered(chat_toggle._watch):
                bpy.app.timers.unregister(chat_toggle._watch)
            state["mark"] = chat_launcher.draws
            next(a for a in window().screen.areas if console.is_chat(a)).ui_type = 'VIEW_3D'
            state["phase"] = "editor_type_hidden"
            return .2
        if phase == "editor_type_hidden":
            if not hide_and_find(phase, "launcher-9-editor-type-changed.png", "done"):
                return .2
            from angelo_agent import chat_toggle
            bpy.app.timers.register(chat_toggle._watch, first_interval=0.5, persistent=True)
            bpy.context.preferences.view.ui_scale = 1.0
            steady = sorted(list(chat_launcher.samples)[1:]) or [1.0]
            checks["its_draw_costs_under_half_a_millisecond"] = steady[len(steady) // 2] < 0.0005
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
