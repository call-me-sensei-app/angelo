# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The in-app half of ``test_blender_prefs_and_quit.py``: one phase per window process (see that file).

Each phase is a generator of steps: a number waits that many seconds, an ``Until`` waits for a condition (or fails
the phase when it times out). Pointer moves, clicks and keys are real window events (``Window.event_simulate``).
Checks are recorded, never asserted one by one, so a receipt lists every one; anything unexpected ends the phase
with its traceback in ``<phase>.json``. A quit phase writes its receipt, with the wall-clock moment it asked to quit,
before it quits: the host measures how long the process then takes to end.
"""

import hashlib
import json
import sys
import time
import traceback
from pathlib import Path

import addon_utils
import bpy

ARGS = sys.argv[sys.argv.index("--") + 1:]
PHASE = ARGS[ARGS.index("--native-phase") + 1]
OUT = Path(ARGS[ARGS.index("--fixture-root") + 1])
ROUNDS = OUT / "rounds"
sys.path.insert(0, str(Path(__file__).resolve().parent))
import reply_stream_fixtures as fixtures  # noqa: E402

report = {"phase": PHASE, "checks": {}, "observed": {}, "screenshots": [], "success": False}
bpy.context.preferences.use_preferences_save = False


class Until:
    def __init__(self, condition, what, limit=60):
        self.condition, self.what, self.limit = condition, what, limit


def check(name, condition):
    report["checks"][name] = bool(condition)


def window():
    return bpy.context.window_manager.windows[0]


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    report["screenshots"].append(str(path))


def settled():
    """Director's layout is up, nothing is changing it and the chat's sign-in checks are done."""
    from angelo_agent import console, director, ui
    return (addon_utils.check("angelo_agent")[1] and director.is_director(window()) and director._timer is None
            and ui._layout_timer is None and not ui._authentication.running
            and any(console.is_chat(a) for a in window().screen.areas))


def chat():
    from angelo_agent import console
    area = next(a for a in window().screen.areas if console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def view_state():
    from angelo_agent import chat_view
    return chat_view._views.get(chat()[0].as_pointer(), {})


def press(x, y, kind='LEFTMOUSE'):
    for event, value in (('MOUSEMOVE', 'NOTHING'), (kind, 'PRESS'), (kind, 'RELEASE')):
        window().event_simulate(type=event, value=value, x=int(x), y=int(y))


def click_chat(action, value=None):
    area, region = chat()
    rect = next(r for r, a, v in view_state()["hits"] if a == action and (value is None or v == value))
    press(region.x + rect[0] + rect[2] / 2, region.y + rect[1] + rect[3] / 2)


def viewport():
    """The largest 3D view's main region (the one the welcome and the cards cover)."""
    area = max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    return next(r for r in area.regions if r.type == 'WINDOW')


def point_at_viewport():
    region = viewport()
    x, y = region.x + region.width // 2, region.y + region.height // 2
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    report["observed"]["pointer"] = [x, y]


def send(text):
    """Script one answering round for the stand-in claude CLI, then type ``text`` into the composer and send it."""
    played = int((ROUNDS / "next").read_text()) if (ROUNDS / "next").is_file() else 0
    ROUNDS.mkdir(parents=True, exist_ok=True)
    reply = "Reply %d: the chat answered after %s." % (played, PHASE)
    plan = {"operations": [], "reply": reply}
    (ROUNDS / ("round-%d.json" % played)).write_text(json.dumps(fixtures.answered("msg_%d" % played, [plan], 60, 0.0)))
    area, region = chat()
    area.spaces.active.history[-1].body = text
    x, y = region.x + region.width // 2, region.y + 60
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    window().event_simulate(type='RET', value='PRESS', x=x, y=y)
    window().event_simulate(type='RET', value='RELEASE', x=x, y=y)
    return reply


def replied(reply):
    from angelo_agent import ui
    controller = ui.get_controller()
    return not controller.busy and any(m["role"] == "assistant" and m.get("text") == reply
                                       for m in controller.state["messages"])


def connections_file():
    from angelo_agent import ui
    path = Path(ui.connection_directory()) / "connections.json"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def snapshot():
    """What must survive a preference read: the loaded add-on, its chats and the AI Providers connections."""
    from angelo_agent import ui
    return {"ui": sys.modules["angelo_agent.ui"], "controllers": dict(ui._controllers),
            "records": json.dumps(ui.connections().records, sort_keys=True), "file": connections_file()}


def check_kept(prefix, before, same_chats=True):
    """Angelo stayed loaded and listed through the read, its chats are the same objects, connections intact."""
    from angelo_agent import ui
    prefs, guard = bpy.context.preferences, sys.modules.get("angelo_agent.prefs_reload")
    check(prefix + "angelo_enabled_and_listed", addon_utils.check("angelo_agent") == (True, True)
          and "angelo_agent" in prefs.addons and hasattr(bpy.types, "ANGELO_OT_open_chat"))
    check(prefix + "never_unloaded", sys.modules.get("angelo_agent.ui") is before["ui"] and guard is not None
          and guard.installed())
    if same_chats:
        check(prefix + "same_chats", ui._controllers == before["controllers"])
    check(prefix + "connections_kept", json.dumps(ui.connections().records, sort_keys=True) == before["records"]
          and connections_file() == before["file"] and before["file"] is not None)
    check(prefix + "a_connection_ready", bool(ui.ready_connections()))
    if same_chats:
        check(prefix + "chat_keeps_its_connection", ui.connection_available())


def angelo_settings():
    entry = bpy.context.preferences.addons.get("angelo_agent")
    return entry.preferences if entry else None


def phase_prefs():
    from angelo_agent import quick_connect, ui
    prefs = bpy.context.preferences
    yield Until(lambda: settled() and ("welcome_connect", "claude_subscription")
                in [(a, v) for _, a, v in view_state().get("hits", [])], "the chat's connect options", 90)
    click_chat("welcome_connect", "claude_subscription")
    yield Until(lambda: quick_connect.flow("claude_subscription") is None and ui.get_controller().models
                and ui.connection_available(), "the Claude connection")
    ui.get_controller().set_options(model="claude-opus-5-5")
    reply = send("What is in the default scene?")
    yield Until(lambda: replied(reply), "the first reply")
    report["observed"]["connections"] = sorted(ui.connections().records)
    settings = angelo_settings()
    settings.experimental_voice, settings.helper_standing_consent = True, False
    prefs.view.show_tooltips_python = True
    before = snapshot()

    # File > Defaults > Load Factory Preferences (and Preferences > Save & Load): the menu runs this operator.
    check("factory_read_ran", bpy.ops.wm.read_factory_userpref() == {'FINISHED'})
    yield 1.0
    check_kept("factory_", before)
    settings = angelo_settings()
    check("factory_angelo_settings_are_its_defaults", settings is not None and not settings.experimental_voice
          and settings.helper_standing_consent)
    check("factory_resets_everything_else", not prefs.view.show_tooltips_python)
    yield Until(settled, "the chat after factory preferences")
    capture("factory-preferences-chat.png")
    check("factory_chat_still_drawn", bool(view_state().get("hits")))
    reply = send("Is the chat still here?")
    yield Until(lambda: replied(reply), "a reply after factory preferences")
    check("factory_chat_answers_without_restart", replied(reply))

    # Preferences > Save & Load > Revert to Saved Preferences: the saved values, Angelo's own included.
    settings.experimental_voice, prefs.view.show_tooltips_python = True, True
    bpy.ops.wm.save_userpref()
    settings.experimental_voice, prefs.view.show_tooltips_python = False, False
    before = snapshot()
    check("revert_read_ran", bpy.ops.wm.read_userpref() == {'FINISHED'})
    yield 1.0
    check_kept("revert_", before)
    check("revert_brings_the_saved_values_back", angelo_settings().experimental_voice and prefs.view.show_tooltips_python)

    # An application template's start-up and preferences, then back (File > New > General).
    before = snapshot()
    check("template_read_ran", bpy.ops.wm.read_homefile(app_template="Video_Editing") == {'FINISHED'})
    yield 2.0
    check_kept("template_", before, same_chats=False)
    report["observed"]["template"] = prefs.app_template
    check("template_back_read_ran", bpy.ops.wm.read_homefile(app_template="") == {'FINISHED'})
    yield Until(settled, "the chat after leaving the template", 90)
    check_kept("template_back_", before, same_chats=False)

    # File > Defaults > Load Factory Settings: the factory start-up file and preferences.
    before = snapshot()
    check("factory_settings_read_ran", bpy.ops.wm.read_factory_settings() == {'FINISHED'})
    yield Until(settled, "the chat after factory settings", 90)
    check_kept("factory_settings_", before, same_chats=False)
    # A new document: its earlier chat says "Document changed. Start New Chat", as after File > New.
    check("factory_settings_new_chat", bpy.ops.angelo.new_chat() == {'FINISHED'})
    report["observed"]["new_chat"] = {"connection_id": ui.get_controller().selection_connection_id,
                                      "provider": ui.get_controller().selection_provider}
    yield Until(ui.connection_available, "the new chat's connection")
    capture("factory-settings-chat.png")
    reply = send("And after factory settings?")
    yield Until(lambda: replied(reply), "a reply after factory settings")
    check("factory_settings_chat_answers", replied(reply))

    # A person who turns Angelo off is never overridden by a later read.
    bpy.ops.preferences.addon_disable(module="angelo_agent")
    yield 0.5
    bpy.ops.wm.read_factory_userpref()
    yield 1.0
    check("disabled_by_the_person_stays_disabled", addon_utils.check("angelo_agent") == (False, False)
          and "angelo_agent" not in prefs.addons)
    check("disabled_restores_blenders_own_reset", addon_utils.disable_all.__module__ == "addon_utils")


def open_welcome():
    from angelo_agent import intro
    with bpy.context.temp_override(window=window()):
        result = bpy.ops.angelo.intro('INVOKE_DEFAULT')
    return result == {'RUNNING_MODAL'} and intro.is_open()


def welcome_point(action, value=None):
    from angelo_agent import canvas_rtl, intro
    region = viewport()
    rect = next(r for r, a, v in intro._open["plan"]["hits"] if a == action and (value is None or v == value))
    return region.x + canvas_rtl.pointer_x(rect[0] + rect[2] / 2, region.width), region.y + rect[1] + rect[3] / 2


def request_quit(then=None):
    """Ask to quit as a script does; ``then`` opens an overlay right after, as a timer can (its handler goes first)."""
    report["quit_requested"] = time.time()
    bpy.ops.wm.quit_blender()
    if then is not None:
        then()
    finish(quitting=False)


def phase_quit_open():
    """The welcome open over the 3D view, the pointer resting on it: its own input works and a quit completes."""
    from angelo_agent import intro
    links = []
    intro.open_demo = links.append   # Never a real browser from a fixture.
    yield Until(settled, "the default layout", 90)
    check("welcome_opens", open_welcome())
    yield Until(lambda: "plan" in intro._open, "the welcome's layout")
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=int(welcome_point("start")[0]),
                            y=int(welcome_point("start")[1]))
    yield 0.3
    check("welcome_hovers_its_button", intro._open.get("hover") == ("start", None)
          and intro._open.get("cursor") == 'HAND_POINT')
    window().event_simulate(type='TAB', value='PRESS', x=10, y=10)
    window().event_simulate(type='TAB', value='RELEASE', x=10, y=10)
    yield 0.3
    check("welcome_tab_moves_its_focus", intro._open.get("focus") is not None)
    press(*welcome_point("demo", 0))
    yield 0.3
    check("welcome_demo_click_opens_its_link", len(links) == 1 and intro.is_open())
    capture("welcome-open.png")
    window().event_simulate(type='ESC', value='PRESS', x=10, y=10)
    window().event_simulate(type='ESC', value='RELEASE', x=10, y=10)
    yield 0.3
    check("welcome_escape_closes_it", not intro.is_open() and intro.seen())
    check("welcome_reopens", open_welcome())
    yield Until(lambda: "plan" in intro._open, "the welcome's layout again")
    point_at_viewport()
    yield 0.3
    check("welcome_open_under_the_pointer_at_quit", intro.is_open())
    request_quit()


def phase_quit_race(overlay):
    """Quit asked for, then an overlay opens at once over the pointer (in front of Blender's quit handler)."""
    from angelo_agent import card_host, intro
    yield Until(settled, "the default layout", 90)
    point_at_viewport()
    yield 0.5

    def welcome():
        check("welcome_opened_after_the_quit_request", open_welcome())
        report["observed"]["welcome_open"] = intro.is_open()

    def card():
        bpy.ops.angelo.show_about()
        check("modal_card_opened_after_the_quit_request", card_host.is_active("about") and card_host._active.card.modal)
    request_quit(welcome if overlay == "welcome" else card)


PHASES = {"prefs": phase_prefs, "quit-open": phase_quit_open, "quit-race": lambda: phase_quit_race("welcome"),
          "quit-card": lambda: phase_quit_race("card")}


def finish(error=None, quitting=True):
    """Write the receipt; ``quitting``: then quit, the moment recorded first (a quit phase asked already)."""
    if error:
        report["error"] = error.replace("Traceback (most recent", "Trace (most recent")   # The host scans for it.
    report["success"] = not error and bool(report["checks"]) and all(report["checks"].values())
    report["failed_checks"] = [name for name, ok in report["checks"].items() if not ok]
    if quitting:
        report["quit_requested"] = time.time()
    (OUT / (PHASE + ".json")).write_text(json.dumps(report, indent=2, default=str))
    print("ANGELO_PREFS_AND_QUIT_PHASE", json.dumps({"phase": PHASE, "success": report["success"]}), flush=True)
    if quitting:
        bpy.ops.wm.quit_blender()


def start():
    steps, state = PHASES[PHASE](), {"until": None, "since": 0.0, "done": False}
    started = time.monotonic()

    def tick():
        if state["done"]:
            return None
        try:
            assert time.monotonic() - started < 300, "Phase timed out"
            until = state["until"]
            if until is not None:
                if not until.condition():
                    assert time.monotonic() - state["since"] < until.limit, "Timed out waiting for " + until.what
                    return 0.2
                state["until"] = None
            step = next(steps)
            if isinstance(step, Until):
                state.update(until=step, since=time.monotonic())
                return 0.2
            return step
        except StopIteration:
            state["done"] = True
            if "quit_requested" not in report:
                finish()
        except Exception:
            state["done"] = True
            finish(traceback.format_exc())
        return None

    bpy.app.timers.register(tick, first_interval=1.0, persistent=True)   # Load Factory Settings reloads the file.
