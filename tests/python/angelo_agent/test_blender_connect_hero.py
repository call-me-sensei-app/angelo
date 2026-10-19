# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Before a connection: no composer, and a showcase over the 3D view instead of the empty scene.

Real default startup and real input
(``Window.event_simulate``). Stand-in ``claude``/``codex`` CLIs are on PATH,
signed out; ``claude auth login`` finishes after a second. No real CLI,
browser, account, network or inference is used.

Covered, each with a screenshot: the empty chat without a connection shows
the connect options and no composer, and the showcase covers the 3D view
(render, caption, a line pointing to the chat, Explore the empty scene); the
first-launch welcome draws over it, with the showcase beneath its scrim; its
link hovers as a link; Explore fades it out and the empty scene is usable;
brought back, keys over it never reach the hidden scene, the chat's connect
options still hover beside it, Tab reaches Connect Claude and Return chooses
it; once the connection is ready the showcase fades out and releases its
handler, timer and texture, and the composer appears with keyboard focus, with
the example prompts.

Run through ``run_native_suites.py --gui --include test_blender_connect_hero``;
check ``connect-hero.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-connect-hero-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_provider_cli  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')  # The default Director startup and its chat.
product_env.clear('DISABLE_HERO')  # ...and the showcase the runner turns off elsewhere.

FAKE = OUT / "fake-cli"
FAKE.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_FAKE_CLI_STATE=str(FAKE), ANGELO_FAKE_CLI_LOGIN_SECONDS="1")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "connect-hero.json"
CLAUDE = ("welcome_connect", "claude_subscription")
state = {"phase": "early", "success": False, "checks": {}, "screenshots": [],
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


def actions():
    return [(a, v) for _, a, v in view().get("hits", [])]


def hero_region():
    from angelo_agent import connect_hero
    area = next(a for a in window().screen.areas if a.as_pointer() == connect_hero._state["area"])
    return next(r for r in area.regions if r.type == 'WINDOW')


def move_to(x, y):
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x - 4, y=y)
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    state["pointer"] = (x, y)


def press_key(kind, shift=False, unicode=None):
    x, y = state["pointer"]
    extra = {"unicode": unicode} if unicode else {}
    window().event_simulate(type=kind, value='PRESS', x=x, y=y, shift=shift, **extra)
    window().event_simulate(type=kind, value='RELEASE', x=x, y=y, shift=shift)


def click_at(x, y):
    move_to(x, y)
    window().event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
    window().event_simulate(type='LEFTMOUSE', value='RELEASE', x=x, y=y)


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
        state.update(phase="complete", success=all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_CONNECT_HERO", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
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
        assert time.monotonic() - started < 240, "Connect-hero fixture timed out in " + state["phase"]
        checks, phase = state["checks"], state["phase"]
        from angelo_agent import chat_view, connect_hero, director, intro, ui
        if phase == "early":
            move_to(window().width // 4, window().height // 2)
            state["phase"] = "boot"
            return .2
        if phase == "boot":
            if director._timer is not None or ui._layout_timer is not None:
                return .2
            if not wait(phase, lambda: connect_hero.is_shown() and connect_hero._state.get("plan")
                        and CLAUDE in actions()):
                return .2
            capture("hero-1-not-connected.png")
            checks["no_composer_before_a_connection"] = (view().get("composer_hidden") is True and not any(
                a in {"send", "attach", "approval", "model", "disabled"} for a, _ in actions()))
            checks["showcase_covers_the_3d_view"] = (connect_hero.shown == 1 and connect_hero.last_error is None
                                                     and connect_hero._state["pixels"] is not None)
            with bpy.context.temp_override(window=window()):
                bpy.ops.angelo.intro('INVOKE_DEFAULT')
            state["phase"] = "welcome_over"
            return .4
        if phase == "welcome_over":
            if not wait(phase, lambda: "plan" in intro._open):
                return .2
            capture("hero-2-welcome-first.png")
            checks["the_welcome_draws_over_the_showcase"] = (intro.is_open() and connect_hero.is_shown()
                                                             and connect_hero.last_error is None)
            press_key('ESC')
            state["phase"] = "link"
            return .3
        if phase == "link":
            if not wait(phase, lambda: not intro.is_open()):
                return .1
            region = hero_region()
            rect = connect_hero._state["plan"]["link_rect"]
            move_to(int(region.x + rect[0] + rect[2] / 2), int(region.y + rect[1] + rect[3] / 2))
            state["phase"] = "link_hover"
            return .3
        if phase == "link_hover":
            capture("hero-3-explore-hover.png")
            checks["explore_hovers_as_a_link"] = connect_hero._state.get("hover") == ("explore", None)
            x, y = state["pointer"]
            window().event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
            window().event_simulate(type='LEFTMOUSE', value='RELEASE', x=x, y=y)
            state["phase"] = "explored"
            return .2
        if phase == "explored":
            if not wait(phase, lambda: not connect_hero.is_shown(), limit=5):
                return .1
            capture("hero-4-empty-scene-explored.png")
            checks["explore_fades_it_out_and_leaves_no_handler"] = (
                connect_hero._handler is None and connect_hero._timer is None and view().get("composer_hidden") is True)
            connect_hero._dismissed = False  # Bring it back, as a later launch without a connection would.
            connect_hero.watch()
            state["phase"] = "back"
            return .3
        if phase == "back":
            if not wait(phase, lambda: connect_hero.is_shown() and connect_hero._state.get("plan")):
                return .2
            region = hero_region()
            move_to(int(region.x + region.width / 2), int(region.y + region.height / 2))
            for obj in bpy.context.view_layer.objects:
                obj.select_set(False)
            press_key('A')  # Select All, were it to reach the hidden scene.
            state["phase"] = "keys_stopped"
            return .3
        if phase == "keys_stopped":
            checks["keys_over_the_showcase_never_reach_the_scene"] = (
                len(bpy.context.view_layer.objects) > 0
                and not any(obj.select_get() for obj in bpy.context.view_layer.objects))
            area, region = chat()
            rect = next(r for r, a, v in view()["hits"] if (a, v) == CLAUDE)
            move_to(int(region.x + rect[0] + rect[2] / 2), int(region.y + rect[1] + rect[3] / 2))
            state["phase"] = "chat_hover"
            return .3
        if phase == "chat_hover":
            capture("hero-5-chat-usable-beside-it.png")
            checks["the_connect_options_stay_usable_beside_it"] = view().get("hover") == CLAUDE
            from angelo_agent import canvas_controls
            order = canvas_controls.focus_order(view().get("hits", []))
            for _ in range(order.index(CLAUDE) + 1):
                press_key('TAB')
            state["phase"] = "focused_row"
            return .3
        if phase == "focused_row":
            checks["tab_reaches_connect_claude_without_a_composer"] = view().get("focus") == CLAUDE
            capture("hero-6-connect-claude-focused.png")
            press_key('RET')
            state["phase"] = "connecting"
            state["connect_started"] = time.monotonic()
            return .3
        if phase == "connecting":
            if not wait(phase, lambda: ui.connection_available() and not connect_hero.is_shown(), limit=40):
                return .2
            state["phase"] = "composer"
            return .3
        if phase == "composer":
            # Any event reaches the chat's input handler, which takes the focus the new composer asked for.
            x, y = window().width // 4, window().height // 2
            move_to(x, y)
            state["phase"] = "composer_focus"
            return .3
        if phase == "composer_focus":
            capture("hero-7-connected-composer-focused.png")
            checks["return_chose_claude_and_the_showcase_faded_out"] = (
                connect_hero._handler is None and connect_hero._timer is None and not connect_hero._state)
            checks["the_composer_appears_with_focus_and_examples"] = (
                not view().get("composer_hidden") and any(a == "attach" for a, _ in actions())
                and view().get("focused") is True and any(a == "welcome_example" for a, _ in actions()))
            press_key('H', unicode="h")  # Typed with the pointer over the 3D view: the focused composer takes it.
            state["phase"] = "typed"
            return .3
        if phase == "typed":
            area, _ = chat()
            checks["typing_goes_to_the_composer"] = area.spaces.active.history[-1].body == "h"
            checks["no_chat_draw_error"] = chat_view.last_error is None and connect_hero.last_error is None
            state["phase"] = "done"
            return .2
        if phase == "done":
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.05)
