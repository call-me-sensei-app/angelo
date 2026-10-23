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
Preferences link brightens as a link; before a connection the composer's field
waits while its + (Angelo's navigation) is there (1.0.5); Tab walks the controls in reading order with a
visible focus ring, Shift+Tab walks back, Return activates the focused row and
starts Connect Claude, whose row turns into a running state with a spinner and
a Cancel button that Tab and Return reach and that cancels the sign-in; at a
narrow chat width the rows wrap and keep their states; connected, the composer
appears with focus and its empty Send button shows the not-allowed pointer and
why; the welcome screen shows the hand, a focus ring on Tab and a pressed state, and a release off its
button does nothing. The chat canvas has one, dark, palette: there is no light
theme to cover. 1.0.5: the approval control's hover outline ends at its label (geometry and pixels), and Tab with
the pointer resting on a control shows the focus ring alone; in the narrow chat the composer draws no label cut
with an ellipsis and the disabled-send reason leaves the field's placeholder readable; the connect cards' column
is centred in the wide chat.

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
import qa_input  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

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


def drawn_texts():
    """Redraw once and return the chat's text runs as drawn: (text, x, y, size) in the chat's layout units."""
    from angelo_agent import chat_view
    runs, original = [], chat_view.text

    def recording(value, x, y, size=14, *args, **kwargs):
        runs.append((str(value), x, y, size))
        return original(value, x, y, size, *args, **kwargs)
    chat_view.text = recording
    try:
        with bpy.context.temp_override(window=window()):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    finally:
        chat_view.text = original
    return runs


def run_width(run):
    from angelo_agent import chat_view
    return chat_view.measure(run[0], run[3])


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
            if not qa_boot.started():
                return .2
            if not wait(phase, lambda: rect_of(*CLAUDE) and not ui._authentication.running):
                return .2
            state["rest"] = capture("canvas-1-rows-at-rest.png")
            state["claude_rect"] = rect_of(*CLAUDE)
            # 1.0.5 review row 24: ~370 px of cards sat at the left of a ~700 px chat, its right half empty.
            from angelo_agent import chat_view
            _, region = chat()
            width = region.width / getattr(chat_view, "_scale", 1.0)
            cards = [r for r, a, v in view().get("hits", []) if a in ("welcome_connect", "welcome_team", "welcome_api_key")]
            left, right = (min(r[0] for r in cards), max(r[0] + r[2] for r in cards)) if cards else (None, None)
            state["starter_column"] = {"chat_width": width, "left": left, "right": right}
            checks["the_starter_column_is_centred_in_the_chat"] = bool(cards) and width >= 480 and abs(
                left - (width - right)) <= 2
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
            # Pressed here, captured, then slid off the row before release (qa_input.drag_path, split at the press):
            # an ordinary button does nothing then.
            heading = (state["claude_rect"][0] + 40, state["claude_rect"][1] + state["claude_rect"][3] + 120, 10, 10)
            state["slide"] = qa_input.drag_path(state["pointer"], to_window(heading),
                                                bpy.context.preferences.system.ui_scale)
            qa_input.send(window(), state["slide"][:3])   # Arrive and press.
            state["pointer"] = to_window(heading)
            state["phase"] = "pressed"
            return .3
        if phase == "pressed":
            path = capture("canvas-3-row-pressed.png")
            pressed = brightness(path, state["claude_rect"])
            state["brightness"]["pressed"] = pressed
            checks["press_darkens_on_mouse_down"] = view().get("pressed") == CLAUDE and pressed < state["brightness"]["hover"] - 2
            qa_input.send(window(), state.pop("slide")[3:])   # Off the row, then the release there.
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
            # Before a connection the field waits (typing passes through) and the + menu, Angelo's navigation, is there.
            checks["the_field_waits_and_plus_navigates_before_a_connection"] = (
                view().get("composer_hidden") is True and bool(rect_of("attach")) and not rect_of("model"))
            # 1.0.5 review row 17: the approval control's hover outline spanned ~470 px around a ~100 px label.
            state["approval_rest"] = state["screenshots"][-1]   # canvas-4: the approval control is not hovered.
            state["pointer"] = to_window(rect_of("approval"))
            move_to(*state["pointer"])
            state["phase"] = "approval_hover"
            return .3
        if phase == "approval_hover":
            path = capture("canvas-6a-approval-hover.png")
            hit = rect_of("approval")
            runs = [run for run in drawn_texts() if hit and hit[0] <= run[1] <= hit[0] + 40
                    and hit[1] <= run[2] <= hit[1] + hit[3]]
            label_end = max((run[1] + run_width(run) for run in runs), default=None)
            state["approval"] = {"hit": hit, "label_runs": runs, "label_end": label_end}
            checks["approval_outline_fits_its_label"] = bool(hit and label_end) and (
                view().get("hover") == ("approval", None) and hit[0] + hit[2] <= label_end + 16)
            beyond = (label_end + 24, hit[1] + 4, 60, hit[3] - 8) if hit and label_end else None
            if beyond:   # The pixels past the label: unchanged by the hover (the outline ends at the label).
                lit, rest = brightness(path, beyond), brightness(state["approval_rest"], beyond)
                state["approval"]["beyond_brightness"] = {"hover": lit, "rest": rest}
            checks["nothing_lights_up_beyond_the_approval_label"] = bool(beyond) and abs(lit - rest) < 1.5
            press_key('TAB')   # Keyboard focus while the pointer rests on the approval control.
            state["phase"] = "approval_focus"
            return .3
        if phase == "approval_focus":
            capture("canvas-6b-focus-with-a-resting-pointer.png")
            drawn_texts()   # One more draw at rest.
            focus = view().get("focus")
            state["approval"]["focus_and_hover"] = [focus, view().get("hover")]
            checks["one_focus_indicator_at_a_time"] = focus is not None and view().get("hover") in (None, focus)
            press_key('ESC')
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
            qa_input.send(window(), qa_input.drag_path((edge, middle), (target, middle),
                                                       bpy.context.preferences.system.ui_scale))
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
            # 1.0.5 review row 18: at this width the footer drew "Approve …" and "Defau…", and the reason sat on the
            # field's placeholder. Read every run the composer draws, and where the tooltip went.
            from angelo_agent.ui_text import iface_
            runs = drawn_texts()
            tip = canvas_controls.last_tip
            composer_top = view().get("add_bottom", 0) - 8
            inside = [run for run in runs if run[2] < composer_top]
            state["narrow_composer"] = {"runs": inside, "tip": tip, "composer_top": composer_top,
                                        "model_label": view().get("model_label"), "chat_width": region.width}
            checks["the_composer_draws_every_label_whole_in_a_narrow_chat"] = bool(inside) and not any(
                run[0].rstrip().endswith(("\u2026", "...")) for run in inside)
            field = next((run for run in inside if run[0] == iface_("Do anything")), None)
            field_rect = (field[1], field[2] - 4, run_width(field), field[3] + 8) if field else None
            checks["the_disabled_send_reason_leaves_the_field_text_readable"] = bool(tip and field_rect) and not (
                tip[0] < field_rect[0] + field_rect[2] and field_rect[0] < tip[0] + tip[2]
                and tip[1] < field_rect[1] + field_rect[3] and field_rect[1] < tip[1] + tip[3])
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
            state["slide"] = qa_input.drag_path((x, y), (x, y + 200), bpy.context.preferences.system.ui_scale)
            qa_input.send(window(), state["slide"][:3])   # Arrive and press; slid off and released next.
            state["phase"] = "welcome_pressed"
            return .3
        if phase == "welcome_pressed":
            from angelo_agent import intro
            capture("canvas-10-welcome-pressed.png")
            pressed = intro._open.get("pressed") == ("start", None)
            state["pointer"] = (state["pointer"][0], state["pointer"][1] + 200)
            qa_input.send(window(), state.pop("slide")[3:])   # Off the button, then the release there.
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
