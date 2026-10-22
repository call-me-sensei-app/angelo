# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The top bar's show/hide chat toggle and Ctrl+Option+N in the running app (``chat_toggle``).

Real default startup and real keys (``Window.event_simulate``). No CLI, account, network or inference.

Covered, each with a screenshot: in Manual mode the toggle is drawn pressed beside the mode menu while the chat is
docked; Ctrl+Option+N typed while the chat has keyboard focus hides it (its area closes, the 3D view takes the room,
the draft is kept) and the toggle is drawn unpressed; pressed again the chat docks on the same side with the same
draft; a popped-out chat hides (its window closes, the draft is kept) and shows docked again; in Director (since
1.0.4) the shortcut hides the chat too, the 3D view taking the window, and shows it again in Director's own split.

Run through ``run_native_suites.py --gui --include test_blender_chat_toggle``; check ``chat-toggle.json`` for
``success`` and ``phase == "complete"``.
"""

import json
import os
import tempfile
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-chat-toggle-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "chat-toggle.json"
DRAFT = "Keep this draft while the chat is hidden"
bpy.context.preferences.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": []}
started = time.monotonic()


def record_draws():
    """Keep what the toggle draws each time the top bar draws it: the pressed state the person sees."""
    from angelo_agent import chat_toggle
    original = chat_toggle.draw

    def draw(layout, context):
        state["drawn"] = chat_toggle.chat_state(context.window, context.window_manager.windows)[0]
        state["drawn_window"] = context.window.as_pointer() if context.window else None
        return original(layout, context)
    chat_toggle.draw = draw


def window():
    return bpy.context.window_manager.windows[0]


def chat_state():
    from angelo_agent import chat_toggle
    return chat_toggle.chat_state(window(), bpy.context.window_manager.windows)


def press_shortcut():
    """Ctrl+Option+N, typed with the pointer over the chat (keyboard focus there) or the 3D view."""
    view = next(a for a in window().screen.areas if a.type == 'VIEW_3D')
    x, y = view.x + view.width // 2, view.y + view.height // 2
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    window().event_simulate(type='N', value='PRESS', x=x, y=y, ctrl=True, alt=True)
    window().event_simulate(type='N', value='RELEASE', x=x, y=y, ctrl=True, alt=True)


def draft():
    found = chat_state()
    return found[2].spaces.active.history[-1].body if found[2] else None


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_CHAT_TOGGLE", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
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


def tick():
    try:
        assert time.monotonic() - started < 180, "Fixture timed out in " + state["phase"]
        checks, phase = state["checks"], state["phase"]
        from angelo_agent import director, ui
        settled = director._timer is None and ui._layout_timer is None
        if phase == "boot":
            if not settled or not chat_state()[2]:
                return .2
            record_draws()
            director.request_mode(window(), "MANUAL")
            state["phase"] = "manual"
            return .5
        if phase == "manual":
            if not wait(phase, lambda: settled and not director.is_director(window()) and chat_state()[0] == "docked"):
                return .2
            found = chat_state()
            found[2].spaces.active.history[-1].body = DRAFT
            state["side"] = "RIGHT" if found[2].x > window().width / 2 else "LEFT"
            capture("toggle-1-manual-shown.png")
            checks["the_toggle_draws_shown_while_docked"] = state.get("drawn") == "docked"
            state["drawn"] = None
            press_shortcut()
            state["phase"] = "hidden"
            return .5
        if phase == "hidden":
            # The top bar rebuilds by itself (the message bus): wait for its own draw, never force one.
            if not wait(phase, lambda: chat_state()[0] == "hidden" and state.get("drawn") == "hidden"):
                return .2
            capture("toggle-2-hidden.png")
            checks["the_toggle_draws_unpressed_while_hidden"] = state.get("drawn") == "hidden"
            checks["the_shortcut_hides_the_chat"] = chat_state()[0] == "hidden"
            checks["the_draft_is_kept"] = ui._drafts.get(ui.get_controller().state["id"], ("",))[0] == DRAFT
            press_shortcut()
            state["phase"] = "shown"
            return .5
        if phase == "shown":
            if not wait(phase, lambda: settled and chat_state()[0] == "docked" and draft() == DRAFT
                        and state.get("drawn") == "docked"):
                return .2
            capture("toggle-3-shown-again.png")
            checks["the_toggle_draws_pressed_again"] = state.get("drawn") == "docked"
            found = chat_state()
            checks["shown_again_with_its_draft"] = draft() == DRAFT
            checks["on_the_same_side"] = ("RIGHT" if found[2].x > window().width / 2 else "LEFT") == state["side"]
            area = found[2]
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(window=window(), area=area, region=region):
                bpy.ops.angelo.pop_out()
            state["phase"] = "popped"
            return .8
        if phase == "popped":
            if not wait(phase, lambda: chat_state()[0] == "popped"):
                return .2
            checks["popped_out_counts_as_shown"] = chat_state()[0] == "popped"
            press_shortcut()
            state["phase"] = "pop_hidden"
            return .8
        if phase == "pop_hidden":
            if not wait(phase, lambda: chat_state()[0] == "hidden" and len(bpy.context.window_manager.windows) == 1):
                return .2
            checks["hiding_a_pop_out_closes_its_window"] = len(bpy.context.window_manager.windows) == 1
            press_shortcut()
            state["phase"] = "pop_shown"
            return .5
        if phase == "pop_shown":
            if not wait(phase, lambda: settled and chat_state()[0] == "docked" and draft() == DRAFT):
                return .2
            checks["after_a_pop_out_it_docks_with_its_draft"] = draft() == DRAFT
            director.request_mode(window(), "DIRECTOR")
            state["phase"] = "director"
            return .8
        if phase == "director":
            if not wait(phase, lambda: settled and director.is_director(window()) and chat_state()[2] is not None):
                return .2
            press_shortcut()
            state["phase"] = "director_hidden"
            return .8
        if phase == "director_hidden":
            if not wait(phase, lambda: settled and chat_state()[0] == "hidden"):
                return .2
            capture("toggle-4-director-hidden.png")
            checks["director_hides_its_chat"] = chat_state()[0] == "hidden" and director.is_director(window())
            press_shortcut()
            state["phase"] = "director_shown"
            return .8
        if phase == "director_shown":
            if not wait(phase, lambda: settled and chat_state()[0] == "docked" and draft() == DRAFT):
                return .2
            capture("toggle-5-director-shown.png")
            checks["director_shows_it_again_with_its_draft"] = draft() == DRAFT and director.is_director(window())
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
