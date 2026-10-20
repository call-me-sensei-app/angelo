# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chat that looks ready never has a dead click: the first launch, connected elsewhere, every no-model state.

Evidence: a 1.0.0/1.0.1 user's first launch showed the connected welcome ("What would you like to make?", the
examples, "Choose model") and nothing they clicked did anything; a restart fixed it
(``test_native_chat_start_states`` explains the two causes).

Real first launch (this suite runs with Angelo's startup on: ``first_launch``) and real input
(``Window.event_simulate``) on the chat's own hit geometry. The stand-in ``claude`` (``fake_provider_cli.py``) is
and ``codex`` are installed and already signed in on this computer; the ``models.json`` knob drives the model list. No real CLI,
browser, account, network or inference.

Covered, each with a screenshot: the setup card, then the welcome over the 3D view; with the welcome still open, a
connection added outside the chat (as the AI Providers form adds one that reuses this computer's sign-in) binds no
chat, and the chat shows "Choose a model to start" (its action adopts the connection), and an example prompt
clicked in the chat fills the composer;
the welcome closes; with a corner notice card open (as What's New shows one) a click in the chat works too; models that fail to load show "Couldn't load your models" with Retry and the provider's text
under Details; Retry with several models and no default shows no card and the model button says Default (the
connection's default runs, as the list's checked row says), and the button opens the list, where a click chooses a
model; a slow catalog shows "Loading your models…"; a connection
with exactly one model has it chosen; a tool holding the window's input (a modal operator that passes every event
on, as some add-ons run) makes a click on the chat show a notice naming it, and once it ends the same click works.

Against an app built before the fix (1.0.0/1.0.1) the clicks beside the welcome do nothing and the chat stays on
"Choose model": run with ``--allow-addon-mismatch`` to reproduce the report (the checks name what fails).

Run through ``run_native_suites.py --gui --include test_blender_chat_start_states``; check
``chat-start-states.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-chat-start-states-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_provider_cli  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')  # The default Director startup and its chat.
FAKE = OUT / "fake-cli"
FAKE.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_FAKE_CLI_STATE=str(FAKE), ANGELO_FAKE_CLI_LOGIN_SECONDS="1")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
for tool in ("claude", "codex"):
    fake_provider_cli.sign_in(os.environ["HOME"], tool)  # Signed in on this computer before Angelo first starts.
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "chat-start-states.json"
MODELS = FAKE / "models.json"
EFFORTS = [{"reasoningEffort": "low"}, {"reasoningEffort": "medium"}]
TWO = [{"model": "gpt-alpha", "displayName": "Alpha", "supportedReasoningEfforts": EFFORTS, "inputModalities": ["text"]},
       {"model": "gpt-beta", "displayName": "Beta", "supportedReasoningEfforts": EFFORTS, "inputModalities": ["text"]}]
ONE = [{"model": "gpt-solo", "displayName": "Solo", "supportedReasoningEfforts": EFFORTS, "inputModalities": ["text"]}]
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [],
         "fixture": "Stand-in claude CLI signed in on this computer and synthetic input; no browser, account, "
                    "network or inference"}
started = time.monotonic()


class ANGELO_OT_fixture_hold_input(bpy.types.Operator):
    """A modal operator that passes every event on and never ends by itself, as some add-ons run one."""
    bl_idname = "angelo.fixture_hold_input"
    bl_label = "Hold Input (fixture)"
    release = False

    def invoke(self, context, event):
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if type(self).release:
            return {'FINISHED', 'PASS_THROUGH'}
        return {'PASS_THROUGH'}


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
    return [(action, value) for _, action, value in view().get("hits", [])]


def press(x, y):
    for kind, value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        window().event_simulate(type=kind, value=value, x=x, y=y)


def at(rect, region=None):
    region = region or chat()[1]
    return int(region.x + rect[0] + rect[2] / 2), int(region.y + rect[1] + rect[3] / 2)


def click(action, value=None):
    rect = next(r for r, a, v in view().get("hits", []) if a == action and (value is None or v == value))
    press(*at(rect))


def click_popup(action, matches=None):
    rect = next(r for r, a, v in view().get("popup_hits", []) if a == action and (matches is None or matches(v)))
    press(*at(rect))


def click_card(action):
    from angelo_agent import card_host
    active = card_host._active
    area = next(a for a in window().screen.areas if a.as_pointer() == active.area)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    rect = next(r for r, a, v in reversed(active.plan.hits) if a == action)
    press(*at(rect, region))


def click_welcome(action):
    from angelo_agent import intro
    area = next(a for a in window().screen.areas if a.as_pointer() == intro._open["area"])
    region = next(r for r in area.regions if r.type == 'WINDOW')
    rect = next(r for r, a, v in intro._open["plan"]["hits"] if a == action)
    press(*at(rect, region))


def draft():
    return chat()[0].spaces.active.history[-1].body


def clear_draft():
    chat()[0].spaces.active.history[-1].body = ""
    chat()[0].tag_redraw()


def card():
    return view().get("drawn_model_card")


def label():
    return view().get("model_label")


def set_models(**values):
    MODELS.write_text(json.dumps(values))


def refresh():
    """Load the chat's models again, as Retry does, with no model chosen."""
    from angelo_agent import ui
    controller = ui.get_controller()
    controller.set_options(model="")
    with bpy.context.temp_override(window=window()):
        bpy.ops.angelo.refresh_models()
    chat()[0].tag_redraw()


def forget_models():
    """As for a connection whose models never loaded yet: no catalog entry and no chat list."""
    from angelo_agent import ui
    controller = ui.get_controller()
    ui.catalogs().invalidate(controller.state["connection_id"])
    controller.models = []


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
    print("ANGELO_CHAT_START_STATES", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=30):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    if time.monotonic() - state["waiting"] > limit:
        state["checks"]["timed_out_in_" + phase] = False   # Recorded as a failed check; the suite goes on.
        state.pop("waiting", None)
        return True
    return False


def tick():
    try:
        assert time.monotonic() - started < 300, "Fixture timed out in " + state["phase"]
        checks, phase = state["checks"], state["phase"]
        from angelo_agent import director, ui, intro, card_host, input_policy
        from angelo_agent.chat_settings import selected_model
        controller = ui.get_controller()
        if phase == "boot":
            if not director.is_director(window()) or director._timer is not None or ui._layout_timer is not None:
                return .2
            if not wait(phase, lambda: card_host.is_active("setup") and card_host._active.plan is not None):
                return .2
            window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=window().width // 2, y=window().height // 2)
            capture("start-0-setup-card.png")
            click_card("continue")
            state["phase"] = "welcome"
            return .4
        if phase == "welcome":
            if not wait(phase, lambda: intro.is_open() and "plan" in intro._open and not ui._authentication.running):
                return .2
            capture("start-1-welcome-and-connect-card.png")
            checks["welcome_is_open_over_the_3d_view"] = intro.is_open()
            blocking = getattr(input_policy, "canvas_blocking", None)
            checks["the_welcome_never_holds_the_chats_input"] = blocking is not None and blocking(window()) == []
            checks["detected_sign_in_created_no_connection"] = ui.connections().records == {}
            # Connected outside the chat, as the AI Providers form's "this computer's sign-in" or a script does: the
            # chat's own mode (a new chat's is ChatGPT), which the chat adopts on its first message.
            from angelo_agent.provider_registry import CODING
            state["mode"] = controller.state["provider"]
            provider = "ANTHROPIC" if state["mode"].startswith("claude") else "OPENAI"
            state["record"] = ui.connections().add(provider, "SUBSCRIPTION", reuse_native=True, group=CODING)["id"]
            state["phase"] = "connected"
            return .3
        if phase == "connected":
            # A connection added elsewhere binds no chat (ai_providers.rst): the unbound chat says so, with the one
            # action that adopts it. 1.0.1: "Choose model" and nothing else.
            if not wait(phase, lambda: ui.connection_available() and card() == "Choose a model to start", limit=15):
                return .2
            capture("start-2-connected-beside-the-welcome.png")
            checks["a_connection_made_elsewhere_binds_no_chat"] = controller.state.get("connection_id") is None
            checks["the_unbound_chat_offers_its_models"] = card() == "Choose a model to start"
            checks["welcome_still_open"] = intro.is_open()
            # Click what the chat offers, beside the open welcome: an example, or (not connected) its connect row.
            state["beside"] = "example" if ("welcome_example", 1) in actions() else "use_claude"
            click(*(("welcome_example", 1) if state["beside"] == "example" else ("welcome_connect", state["mode"])))
            state["phase"] = "beside"
            return .3
        if phase == "beside":
            from angelo_agent import first_run, quick_connect
            def acted():
                if state["beside"] == "example":
                    return draft() == first_run.EXAMPLES[1]
                return quick_connect.flow(state["mode"]) is not None or bool(controller.state.get("connection_id"))
            if not wait(phase, acted, limit=5):
                return .2
            capture("start-3-click-beside-the-welcome.png")
            checks["a_click_in_the_chat_works_beside_the_welcome"] = acted()
            clear_draft()
            click_welcome("start")
            state["phase"] = "welcome_closed"
            return .4
        if phase == "welcome_closed":
            if not wait(phase, lambda: not intro.is_open()):
                return .2
            if ("welcome_connect", state["mode"]) in actions():
                click("welcome_connect", state["mode"])   # Not connected yet: the chat's own connect row.
            elif ("welcome_choose_model", None) in actions():
                click("welcome_choose_model")   # The card's action adopts the connection and opens the model list.
            state["phase"] = "bound"
            return .3
        if phase == "bound":
            if not wait(phase, lambda: ("welcome_example", 2) in actions(), limit=40):
                return .2
            if controller.state.get("connection_id") is not None:
                checks["choose_a_model_adopts_the_connection"] = controller.state["connection_id"] == state["record"]
            view().pop("popover", None)   # Close the model list the card opened.
            # A corner notice card, as What's New or an update notice opens over the 3D view.
            from angelo_agent.update_card import NoticeCard
            card_host.request(NoticeCard("check", "Fixture notice", "A corner card, as What's New shows one."))
            card_host.pump(lambda: True)
            state["phase"] = "notice"
            return .5
        if phase == "notice":
            if not wait(phase, lambda: card_host.is_active("notice") and card_host._active.plan is not None):
                return .2
            click("welcome_example", 2)
            state["phase"] = "notice_click"
            return .3
        if phase == "notice_click":
            from angelo_agent import first_run
            if not wait(phase, lambda: draft() == first_run.EXAMPLES[2], limit=5):
                return .2
            capture("start-4-click-beside-a-notice-card.png")
            checks["a_click_in_the_chat_works_beside_a_notice_card"] = draft() == first_run.EXAMPLES[2]
            clear_draft()
            card_host.close("done")
            if getattr(input_policy, "canvas_blocking", None) is None:
                state["reproduced_on"] = "an app built before the fix: the model states below are the fix's"
                finish()
                return None
            set_models(codex="fail", claude="fail")
            refresh()
            state["phase"] = "failed"
            return .3
        if phase == "failed":
            if not wait(phase, lambda: card() == "Couldn't load your models"
                        and ("welcome_retry_models", None) in actions(), limit=40):
                return .2
            checks["failed_models_show_a_card_with_retry"] = card() == "Couldn't load your models"
            click("expand", ("notice", "models"))
            state["phase"] = "failed_details"
            return .3
        if phase == "failed_details":
            capture("start-5-models-failed-with-details.png")
            checks["details_hold_the_providers_text"] = ("notice", "models") in view().get("expanded", set())
            set_models(codex=TWO)
            click("welcome_retry_models")
            state["phase"] = "choose"
            return .3
        if phase == "choose":
            # Several models and none flagged default: the connection's default runs (the list's checked Default row).
            if not wait(phase, lambda: card() is None and label() == "Default", limit=40):
                return .2
            capture("start-6-several-models-default.png")
            checks["several_models_without_a_default_say_default_and_ask_nothing"] = label() == "Default" and card() is None
            click("model")
            state["phase"] = "controls"
            return .4
        if phase == "controls":
            if not wait(phase, lambda: (view().get("popover") or {}).get("kind") == "controls"
                        and any(a == "popup_models" for _, a, _ in view().get("popup_hits", []))):
                return .2
            click_popup("popup_models")
            state["phase"] = "list"
            return .4
        if phase == "list":
            if not wait(phase, lambda: (view().get("popover") or {}).get("kind") == "models"
                        and any(a == "popup_select_model" and v and v[-1] == "gpt-alpha"
                                for _, a, v in view().get("popup_hits", []))):
                return .2
            capture("start-7-model-list.png")
            checks["the_model_button_opens_the_list_with_default_checked"] = True
            click_popup("popup_select_model", lambda v: v and v[-1] == "gpt-alpha")
            state["phase"] = "chosen"
            return .4
        if phase == "chosen":
            if not wait(phase, lambda: card() is None and (label() or "").startswith("Alpha")):
                return .2
            capture("start-8-model-chosen.png")
            checks["a_model_chosen_in_the_list_shows"] = (label() or "").startswith("Alpha")  # With its effort.
            set_models(codex=TWO, codex_delay=4)
            forget_models()
            refresh()
            state["phase"] = "loading"
            return .3
        if phase == "loading":
            if not wait(phase, lambda: card() == "Loading your models…", limit=10):
                return .2
            capture("start-9-loading.png")
            checks["a_slow_catalog_shows_loading"] = card() == "Loading your models…"
            set_models(codex=ONE)
            state["phase"] = "loading_done"
            return .3
        if phase == "loading_done":
            if not wait(phase, lambda: card() is None and label() == "Default", limit=20):
                return .2
            refresh()
            state["phase"] = "single"
            return .3
        if phase == "single":
            if not wait(phase, lambda: card() is None and (label() or "").startswith("Solo"), limit=40):
                return .2
            capture("start-10-single-model-chosen.png")
            checks["a_single_model_is_chosen_for_the_person"] = selected_model(controller)["id"] == "gpt-solo"
            with bpy.context.temp_override(window=window()):
                bpy.ops.angelo.fixture_hold_input('INVOKE_DEFAULT')
            state["phase"] = "held"
            return .3
        if phase == "held":
            click("welcome_example", 0)
            state["phase"] = "held_notice"
            return .3
        if phase == "held_notice":
            if not wait(phase, lambda: view().get("drawn_blocked"), limit=5):
                return .2
            capture("start-11-held-by-another-tool.png")
            checks["a_held_click_names_the_tool"] = list(view().get("drawn_blocked") or ()) == ["Hold Input (fixture)"]
            checks["a_held_click_changes_nothing"] = draft() == ""
            ANGELO_OT_fixture_hold_input.release = True
            window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=window().width // 2, y=window().height // 2)
            state["phase"] = "released"
            return .3
        if phase == "released":
            if not wait(phase, lambda: not input_policy.interaction_active(window()), limit=5):
                return .2
            click("welcome_example", 0)
            state["phase"] = "released_click"
            return .3
        if phase == "released_click":
            from angelo_agent import first_run
            if not wait(phase, lambda: draft() == first_run.EXAMPLES[0], limit=5):
                return .2
            capture("start-12-released.png")
            checks["once_the_tool_ends_the_click_works"] = draft() == first_run.EXAMPLES[0]
            checks["the_notice_goes_with_the_tool"] = not view().get("drawn_blocked")
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.utils.register_class(ANGELO_OT_fixture_hold_input)
bpy.app.timers.register(tick, first_interval=.5)
