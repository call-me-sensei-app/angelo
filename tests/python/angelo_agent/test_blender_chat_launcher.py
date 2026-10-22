# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo in the 3D view's corner while the chat is hidden, in the running app (``chat_launcher``).

Real default startup and real input (``Window.event_simulate``). No CLI, account, network or inference.

Covered, each with a screenshot: no Angelo while the chat shows; hidden in Director, Angelo sits in the 3D view's
bottom-right corner (vision: the disc's card colour at its rim, the raven's colours at its centre, the scene
beyond); the pointer over it shows the tooltip; a click opens the chat in Director's split and Angelo is gone; a
click on the scene outside the disc still reaches the scene (it deselects); the same in Manual mode, on another
workspace tab, and at UI scale 2; with Reduce Motion it never blinks; and its draw cost.

Run through ``run_native_suites.py --gui --include test_blender_chat_launcher``; check ``chat-launcher.json`` for
``success`` and ``phase == "complete"``.
"""

import array
import json
import os
import tempfile
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

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
    state["screenshots"].append(str(path))
    return path


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
    if not wait(phase, lambda: chat_state() == "hidden" and place() is not None and chat_launcher.draws > state["mark"]):
        return False
    capture(shot)
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
        settled = director._timer is None and ui._layout_timer is None
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
            press(cx, cy)
            state["phase"] = "director_opened"
            return .5
        if phase == "director_opened":
            if not wait(phase, lambda: settled and chat_state() == "docked" and place() is None):
                return .2
            capture("launcher-3-director-click-opened-the-chat.png")
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
            press(cx, cy)
            state["phase"] = "manual_opened"
            return .5
        if phase == "manual_opened":
            if not wait(phase, lambda: settled and chat_state() == "docked" and place() is None):
                return .2
            capture("launcher-6-manual-click-opened-the-chat.png")
            checks["a_click_opens_the_chat_in_manual"] = chat_state() == "docked"
            checks["angelo_is_gone_in_manual"] = place() is None
            bpy.context.preferences.view.ui_scale = 2.0
            state["mark"] = chat_launcher.draws
            bpy.ops.angelo.toggle_chat()
            state["phase"] = "scale_two"
            return .8
        if phase == "scale_two":
            if not hide_and_find(phase, "launcher-7-manual-ui-scale-2.png", "done"):
                return .2
            _area, _cx, _cy, radius = place()
            checks["twice_the_size_at_ui_scale_2"] = abs(radius / (chat_launcher.DISC / 2)
                                                         - bpy.context.preferences.system.ui_scale) < 0.05
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
