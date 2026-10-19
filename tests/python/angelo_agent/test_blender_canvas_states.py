# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every clickable on the chat canvas and the welcome screen looks and feels clickable.

Real default startup and real input (``Window.event_simulate``) on the chat's
own hit geometry. Stand-in ``claude`` and ``codex`` executables
(``fake_provider_cli.py``) are installed and signed out; ``claude auth login``
waits long enough to see a sign-in in progress. No real CLI, browser, account,
network or inference is used.

Covered, each with a screenshot and a state check: a connect row under the
pointer gets the hover layer (brighter pixels) and the hand pointer; holding
the button darkens it (darker pixels) and releasing off it does nothing; the
Preferences link brightens as a link; there is no composer before a
connection; Tab walks the controls in reading order with a
visible focus ring, Shift+Tab walks back, Return activates the focused row and
starts Connect Claude, whose row turns into a running state with a spinner and
a Cancel button that Tab and Return reach and that cancels the sign-in; at a
narrow chat width the rows wrap and keep their states; connected, the composer
appears with focus and its empty Send button shows the not-allowed pointer and
why; the welcome screen shows the hand, a focus ring on Tab and a pressed state, and a release off its
button does nothing. The chat canvas has one, dark, palette: there is no light
theme to cover.

Run through ``run_native_suites.py --gui --include test_blender_canvas_states``;
check ``canvas-states.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-canvas-states-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_provider_cli  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')  # This suite needs the default Director startup and its chat.

FAKE = OUT / "fake-cli"
FAKE.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_FAKE_CLI_STATE=str(FAKE), ANGELO_FAKE_CLI_LOGIN_SECONDS="60")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "canvas-states.json"
CLAUDE = ("welcome_connect", "claude_subscription")
state = {"phase": "early", "success": False, "checks": {}, "screenshots": [], "brightness": {},
         "fixture": "Stand-in claude/codex CLIs and synthetic input; no browser, account, network or inference"}
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def chat():
    from angelo_agent import console
    area = next(a for a in window().screen.areas if console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def view():
    from angelo_agent import chat_view
    return chat_view._views.get(chat()[0].as_pointer(), {})


def rect_of(action, value=None):
    return next((r for r, a, v in view().get("hits", []) if a == action and (value is None or v == value)), None)


def to_window(rect):
    _, region = chat()
    return int(region.x + rect[0] + rect[2] / 2), int(region.y + rect[1] + rect[3] / 2)


def move_to(x, y):
    """Two moves, so the pointer really travels, as a person's does."""
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x - 4, y=y)
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)


def press_key(kind, shift=False):
    x, y = state.get("pointer", (10, 10))
    window().event_simulate(type=kind, value='PRESS', x=x, y=y, shift=shift)
    window().event_simulate(type=kind, value='RELEASE', x=x, y=y, shift=shift)


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def brightness(path, rect, region=None):
    """Mean brightness (0-255) of a rectangle given in region coordinates, read from a screenshot."""
    import numpy as np
    region = region or chat()[1]
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        pixels = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(image)
    x0, y0 = int(region.x + rect[0]) + 3, int(region.y + rect[1]) + 3  # Row 0 is the bottom, as in window coordinates.
    x1, y1 = int(region.x + rect[0] + rect[2]) - 3, int(region.y + rect[1] + rect[3]) - 3
    return float(pixels.reshape(height, width, 4)[y0:y1, x0:x1, :3].mean() * 255)


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_CANVAS_STATES", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=30):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + phase
    return False


def tick():
    try:
        assert time.monotonic() - started < 240, "Canvas-states fixture timed out in " + state["phase"]
        checks, phase = state["checks"], state["phase"]
        from angelo_agent import canvas_controls, director, quick_connect, ui
        if phase == "early":
            state["pointer"] = (window().width // 4, window().height // 2)  # Over the 3D view: nothing hovered.
            move_to(*state["pointer"])
            state["phase"] = "boot"
            return .2
        if phase == "boot":
            if director._timer is not None or ui._layout_timer is not None:
                return .2
            if not wait(phase, lambda: rect_of(*CLAUDE) and not ui._authentication.running):
                return .2
            state["rest"] = capture("canvas-1-rows-at-rest.png")
            state["claude_rect"] = rect_of(*CLAUDE)
            state["pointer"] = to_window(state["claude_rect"])
            move_to(*state["pointer"])
            state["phase"] = "hover"
            return .3
        if phase == "hover":
            path = capture("canvas-2-row-hover.png")
            rest, lit = brightness(state["rest"], state["claude_rect"]), brightness(path, state["claude_rect"])
            state["brightness"].update(rest=rest, hover=lit)
            checks["row_hover_shows_and_points"] = (view().get("hover") == CLAUDE and view().get("cursor") == 'HAND_POINT'
                                                    and lit > rest + 2)
            state["hover_path"] = path
            x, y = state["pointer"]
            window().event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
            state["phase"] = "pressed"
            return .3
        if phase == "pressed":
            path = capture("canvas-3-row-pressed.png")
            pressed = brightness(path, state["claude_rect"])
            state["brightness"]["pressed"] = pressed
            checks["press_darkens_on_mouse_down"] = view().get("pressed") == CLAUDE and pressed < state["brightness"]["hover"] - 2
            # Off the row before release: an ordinary button does nothing then.
            heading = (state["claude_rect"][0] + 40, state["claude_rect"][1] + state["claude_rect"][3] + 120, 10, 10)
            state["pointer"] = to_window(heading)
            move_to(*state["pointer"])
            window().event_simulate(type='LEFTMOUSE', value='RELEASE', x=state["pointer"][0], y=state["pointer"][1])
            state["phase"] = "released_off"
            return .3
        if phase == "released_off":
            checks["release_off_the_row_does_nothing"] = quick_connect.flow("claude_subscription") is None and not view().get("pressed")
            state["pointer"] = to_window(rect_of("settings"))
            move_to(*state["pointer"])
            state["phase"] = "link"
            return .3
        if phase == "link":
            capture("canvas-4-link-hover.png")
            checks["preferences_link_hovers_as_a_link"] = (view().get("hover") == ("settings", None) and view().get("cursor") == 'HAND_POINT'
                                                         and view().get("hit_style", {}).get(("settings", None)) == "link")
            # No composer before a connection: the connect options are the only call to action.
            checks["no_composer_before_a_connection"] = view().get("composer_hidden") is True and not rect_of("attach")
            # Keyboard over the chat: Tab walks the controls.
            area, region = chat()
            state["pointer"] = (int(region.x + region.width / 2), int(region.y + 40))
            move_to(*state["pointer"])
            state["phase"] = "focus"
            return .3
        if phase == "focus":
            order = canvas_controls.focus_order(view().get("hits", []))
            press_key('TAB')
            state.update(order=order, phase="focus_first")
            return .3
        if phase == "focus_first":
            capture("canvas-6-focus-ring.png")
            order = state["order"]
            first = view().get("focus") == order[0]
            press_key('TAB')
            press_key('TAB', shift=True)
            state.update(first=first, phase="focus_back")
            return .3
        if phase == "focus_back":
            order = state["order"]
            checks["tab_and_shift_tab_walk_in_reading_order"] = state["first"] and view().get("focus") == order[0]
            for _ in range(order.index(CLAUDE)):
                press_key('TAB')
            state["phase"] = "focus_claude"
            return .3
        if phase == "focus_claude":
            checks["tab_reaches_connect_claude"] = view().get("focus") == CLAUDE
            press_key('RET')
            state["phase"] = "running"
            return .3
        if phase == "running":
            flow = quick_connect.flow("claude_subscription")
            if not wait(phase, lambda: flow and flow["phase"] == "signing_in" and rect_of("welcome_cancel", "claude_subscription")):
                return .2
            capture("canvas-7-running-with-cancel.png")
            checks["return_starts_connect_and_the_row_shows_running"] = (rect_of(*CLAUDE) is None
                                                                        and rect_of("welcome_cancel", "claude_subscription") is not None)
            order = canvas_controls.focus_order(view().get("hits", []))
            state["focus_start"] = view().get("focus")
            target = ("welcome_cancel", "claude_subscription")
            view()["focus"] = None
            for _ in range(order.index(target) + 1):
                press_key('TAB')
            state["phase"] = "cancel"
            return .3
        if phase == "cancel":
            checks["tab_reaches_cancel"] = view().get("focus") == ("welcome_cancel", "claude_subscription")
            press_key('RET')
            state["phase"] = "cancelled"
            return .3
        if phase == "cancelled":
            if not wait(phase, lambda: quick_connect.flow("claude_subscription") is None and rect_of(*CLAUDE)):
                return .2
            checks["cancel_ends_the_sign_in"] = True
            area, _ = chat()
            # Narrow the chat to about 300 px by dragging the edge between the 3D view and the chat, as a person would.
            edge, middle = area.x - 1, area.y + area.height // 2
            target = area.x + area.width - 300
            move_to(edge, middle)
            window().event_simulate(type='LEFTMOUSE', value='PRESS', x=edge, y=middle)
            for x in (edge + 4, (edge + target) // 2, target):
                window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=middle)
            window().event_simulate(type='LEFTMOUSE', value='RELEASE', x=target, y=middle)
            state["phase"] = "narrow"
            return .5
        if phase == "narrow":
            area, region = chat()
            if not wait(phase, lambda: rect_of(*CLAUDE) and view().get("hit_region") == (region.width, region.height)):
                return .2
            state["pointer"] = to_window(rect_of(*CLAUDE))
            move_to(*state["pointer"])
            state["phase"] = "narrow_hover"
            return .3
        if phase == "narrow_hover":
            area, region = chat()
            capture("canvas-8-narrow-hover.png")
            inside = all(r[0] >= 0 and r[0] + r[2] <= region.width for r, a, v in view().get("hits", []))
            checks["narrow_chat_wraps_and_keeps_states"] = area.width <= 320 and inside and view().get("hover") == CLAUDE
            os.environ["ANGELO_FAKE_CLI_LOGIN_SECONDS"] = "1"  # Connect for real now, to reach the composer.
            state["pointer"] = to_window(rect_of(*CLAUDE))
            move_to(*state["pointer"])
            x, y = state["pointer"]
            window().event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
            window().event_simulate(type='LEFTMOUSE', value='RELEASE', x=x, y=y)
            state["phase"] = "connected"
            return .4
        if phase == "connected":
            if not wait(phase, lambda: ui.connection_available() and rect_of(canvas_controls.DISABLED), limit=40):
                return .2
            state["pointer"] = to_window(rect_of(canvas_controls.DISABLED))
            move_to(*state["pointer"])
            state["phase"] = "disabled"
            return .3
        if phase == "disabled":
            capture("canvas-5-disabled-send-reason.png")
            checks["empty_send_is_disabled_and_says_why"] = (view().get("hover") == (canvas_controls.DISABLED, canvas_controls.SEND_EMPTY)
                                                           and view().get("cursor") == 'STOP')
            _, region = chat()
            tip = canvas_controls.last_tip
            checks["disabled_send_reason_stays_inside_the_narrow_chat"] = bool(tip) and tip[0] >= 0 and (
                tip[0] + tip[2] <= region.width) and tip[1] + tip[3] <= region.height
            checks["the_composer_appears_with_focus_once_connected"] = not view().get("composer_hidden") and view().get("focused") is True
            from angelo_agent import intro
            intro.open_demo = lambda url: None  # Never open a real browser from a fixture.
            with bpy.context.temp_override(window=window()):
                bpy.ops.angelo.intro('INVOKE_DEFAULT')
            state["phase"] = "welcome"
            return .4
        if phase == "welcome":
            from angelo_agent import intro
            if not wait(phase, lambda: "plan" in intro._open):
                return .2
            area = next(a for a in window().screen.areas if a.as_pointer() == intro._open["area"])
            region = next(r for r in area.regions if r.type == 'WINDOW')
            start = next(r for r, a, v in intro._open["plan"]["hits"] if a == "start")
            state["welcome_region"] = (region.x, region.y)
            state["pointer"] = (int(region.x + start[0] + start[2] / 2), int(region.y + start[1] + start[3] / 2))
            move_to(*state["pointer"])
            state["phase"] = "welcome_hover"
            return .3
        if phase == "welcome_hover":
            from angelo_agent import intro
            checks["welcome_button_hovers_with_a_hand"] = intro._open.get("hover") == ("start", None) and intro._open.get("cursor") == 'HAND_POINT'
            checks["chat_hover_clears_when_the_pointer_leaves"] = view().get("hover") is None
            press_key('TAB')
            state["phase"] = "welcome_focus"
            return .3
        if phase == "welcome_focus":
            from angelo_agent import intro
            capture("canvas-9-welcome-focus.png")
            order = canvas_controls.focus_order(intro._open["plan"]["hits"])
            checks["welcome_tab_shows_a_focus_ring"] = intro._open.get("focus") == order[0]
            x, y = state["pointer"]
            window().event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
            state["phase"] = "welcome_pressed"
            return .3
        if phase == "welcome_pressed":
            from angelo_agent import intro
            capture("canvas-10-welcome-pressed.png")
            pressed = intro._open.get("pressed") == ("start", None)
            state["pointer"] = (state["pointer"][0], state["pointer"][1] + 200)
            move_to(*state["pointer"])
            window().event_simulate(type='LEFTMOUSE', value='RELEASE', x=state["pointer"][0], y=state["pointer"][1])
            state.update(welcome_pressed=pressed, phase="welcome_released")
            return .3
        if phase == "welcome_released":
            from angelo_agent import intro
            checks["welcome_press_shows_and_release_off_does_nothing"] = state["welcome_pressed"] and intro.is_open()
            press_key('ESC')
            state["phase"] = "done"
            return .3
        if phase == "done":
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.05)
