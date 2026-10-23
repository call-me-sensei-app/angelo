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
on, as some add-ons run) makes a click on the chat show a notice naming it, and once it ends the same click works;
the generation suggestions follow the examples, and one fills the composer the same way (1.0.5); the chat bar's
+ menu offers Media Studio, Generation Flows and the base meshes as closed groups: a click opens the base meshes
(remembered) and closes them, the Flows group's template opens a wired flow in its own window, and Media Studio
opens in its own window (1.0.5). The model list opens above the composer with room under Refresh models (1.0.5).

Against an app built before the fix (1.0.0/1.0.1) the clicks beside the welcome do nothing and the chat stays on
"Choose model": run with ``--allow-addon-mismatch`` to reproduce the report (the checks name what fails).

Run through ``run_native_suites.py --gui --include test_blender_chat_start_states``; check
``chat-start-states.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import re
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
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

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
    """The chat's canvas state; empty while the window shows no chat for a moment (a layout change such as
    Manual's tab build, ``studio_workspaces``, which holds the layout for its few frames), so a wait polls on."""
    from angelo_agent import chat_view, console
    area = next((a for a in window().screen.areas if console.is_chat(a)), None)
    return chat_view._views.get(area.as_pointer(), {}) if area is not None else {}


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


def capture(name, target=None):
    path = OUT / name
    with bpy.context.temp_override(window=target or window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def top_bar_buttons_visible(path):
    """With an account connected, Studio and Flows are still in the top bar (permanent, 1.0.5): drawn (their recorded
    widths) and visible by pixels inside each button, which follow Chat from its left edge."""
    import numpy
    from angelo_agent import chat_toggle, top_bar_fit
    win = window()
    fitted = top_bar_fit.widths.get(win.as_pointer(), {})
    if "studio" not in fitted or "flows" not in fitted or win.as_pointer() not in chat_toggle.anchors:
        return {}
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        values = numpy.empty(width * height * 4, dtype=numpy.float32)
        image.pixels.foreach_get(values)
    finally:
        bpy.data.images.remove(image)
    px = (values.reshape(height, width, 4)[..., :3] * 255).round().astype(numpy.int32)
    left, _, bottom = chat_toggle.anchors[win.as_pointer()]
    unit = fitted["chat"][2]
    row = px[int(bottom + .1 * unit):int(bottom + 1.1 * unit)]
    flat = row.reshape(-1, 3)
    keys = flat[:, 0] * 65536 + flat[:, 1] * 256 + flat[:, 2]
    values, counts = numpy.unique(keys, return_counts=True)
    main = int(values[counts.argmax()])
    background = numpy.array([main // 65536, main // 256 % 256, main % 256])
    # The buttons by pixels, not by the anchor: runs of columns that stand out from the bar, and among them three in a
    # row as wide as top_bar_fit measured Chat, Studio and Flows (the anchor is recorded beside, as a check of it).
    differs = numpy.abs(row - background).sum(axis=2) > 40
    columns = differs.mean(axis=0) > 0.3
    runs, start = [], None
    for x, on in enumerate(list(columns) + [False]):
        if on and start is None:
            start = x
        elif not on and start is not None:
            if x - start >= 4:
                runs.append((start, x - start))
            start = None
    names = ("chat", "studio", "flows")
    expected = [fitted[name][1] * unit for name in names]
    found = next((i for i in range(len(runs) - 2) if all(
        abs(runs[i + k][1] - expected[k]) <= 0.2 * expected[k] + 6 for k in range(3))), None)
    state["top_bar_probe"] = {"anchor_left": round(left), "runs": runs[-12:], "expected": [round(e) for e in expected],
                              "found_left": runs[found][0] if found is not None else None}
    shares = {}
    for k, name in enumerate(names):
        if found is None:
            shares[name] = 0.0
            continue
        x0, w = runs[found + k]
        block = differs[int(.25 * row.shape[0]):int(.75 * row.shape[0]), int(x0 + .2 * w):int(x0 + .8 * w)]
        shares[name] = round(float(block.mean()) if block.size else 0.0, 3)
    return shares


# What a person must never read (maintainer rule, 1.0.5): object or data-block names ("GEO-head_stylized", snake_case
# ids), file names, data-block type prefixes, hex ids.
INTERNAL = re.compile(r"\bGEO-|\w_\w|\.blend\b|\b(?:OB|ME|MA|NT|GR|IM)[A-Z]?:|\b[0-9a-f]{12,}\b")


def note_menu_names(tag):
    """Redraw once with the + menu open and keep every drawn run that reads as an internal name."""
    from angelo_agent import chat_view
    runs, original = [], chat_view.text

    def recording(value, *args, **kwargs):
        runs.append(str(value))
        return original(value, *args, **kwargs)
    chat_view.text = recording
    try:
        with bpy.context.temp_override(window=window()):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    finally:
        chat_view.text = original
    state.setdefault("plus_menu_internal", {})[tag] = {"runs": len(runs),
                                                       "internal": [run for run in runs if INTERNAL.search(run)]}


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
            if not director.is_director(window()) or not qa_boot.started():
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
            # 1.0.5 review row 19: the list's lower edge overlapped the composer by ~45 px, and Refresh models sat on
            # the list's bottom edge. The popover as drawn against the composer's top (the + menu's floor).
            popup, composer_top = view().get("popup_rect"), view().get("add_bottom", 0) - 8
            refresh_row = next((r for r, a, _ in view().get("popup_hits", []) if a == "popup_refresh"), None)
            state["model_list_geometry"] = {"popup": popup, "composer_top": composer_top, "refresh": refresh_row}
            checks["the_model_list_opens_above_the_composer"] = bool(popup) and popup[1] >= composer_top
            checks["refresh_models_has_room_under_it"] = bool(popup and refresh_row) and refresh_row[1] - popup[1] >= 6
            click_popup("popup_select_model", lambda v: v and v[-1] == "gpt-alpha")
            state["phase"] = "chosen"
            return .4
        if phase == "chosen":
            if not wait(phase, lambda: card() is None and (label() or "").startswith("Alpha")):
                return .2
            capture("start-8-model-chosen.png")
            checks["a_model_chosen_in_the_list_shows"] = (label() or "").startswith("Alpha")  # With its effort.
            state["top_bar_connected"] = top_bar_buttons_visible(OUT / "start-8-model-chosen.png")
            checks["with_an_account_studio_and_flows_stay_in_the_top_bar"] = (
                state["top_bar_connected"].get("studio", 0) > 0.05 and state["top_bar_connected"].get("flows", 0) > 0.05)
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
            # 1.0.5: the generation suggestions follow the scene examples; a click fills the box like the others.
            checks["the_generation_suggestions_follow_the_examples"] = [a for a in actions() if a[0] == "welcome_example"] == [
                ("welcome_example", index) for index in range(len(first_run.suggestions()))]
            click("welcome_example", len(first_run.EXAMPLES) + 1)
            state["phase"] = "generation_click"
            return .3
        if phase == "generation_click":
            from angelo_agent import first_run
            if not wait(phase, lambda: draft() == first_run.GENERATION_EXAMPLES[1], limit=5):
                return .2
            capture("start-13-generation-suggestion.png")
            checks["a_generation_suggestion_fills_the_box"] = draft() == first_run.GENERATION_EXAMPLES[1]
            clear_draft()
            click("attach")   # The chat bar's + button: Angelo's main navigation (chat_destinations, 1.0.5).
            state["phase"] = "plus_open"
            return .4
        if phase == "plus_open":
            if not wait(phase, lambda: (view().get("popover") or {}).get("kind") == "add"
                        and view().get("popup_hits"), limit=5):
                return .2
            rows = view()["popover"]["rows"]
            top = [r["value"] for r in rows if r["kind"] in {"row", "group"} and not r.get("depth")]
            state["plus_top"] = top
            capture("start-14-plus-menu.png")
            note_menu_names("top")
            checks["the_plus_menu_offers_media_studio_flows_and_base_meshes"] = top[:4] == [
                "attach", "media_studio", "flows", "base_mesh"]
            checks["its_groups_start_closed"] = not any(r.get("depth") for r in rows)
            click_popup("popup_add_group", lambda v: v == "base_mesh")
            state["phase"] = "plus_meshes"
            return .4
        if phase == "plus_meshes":
            from angelo_agent import chat_add_menu
            def meshes():
                return [r for r in (view().get("popover") or {}).get("rows", ()) if r.get("feature") == "base_mesh"
                        and r.get("depth")]
            if not wait(phase, lambda: meshes() and view().get("popup_hits"), limit=5):
                return .2
            capture("start-15-plus-menu-base-meshes-open.png")
            note_menu_names("base_meshes")
            checks["a_click_opens_the_base_meshes"] = len(meshes()) >= 8
            path = Path(chat_add_menu.memory().path or "")
            checks["the_open_group_is_remembered"] = path.is_file() and "base_mesh" in path.read_text()
            click_popup("popup_add_group", lambda v: v == "base_mesh")
            state["phase"] = "plus_flows"
            return .4
        if phase == "plus_flows":
            rows = (view().get("popover") or {}).get("rows", ())
            if not wait(phase, lambda: not any(r.get("depth") for r in (view().get("popover") or {}).get("rows", ()))
                        and view().get("popup_hits"), limit=5):
                return .2
            checks["a_second_click_closes_them"] = not any(r.get("depth") for r in rows)
            click_popup("popup_add_group", lambda v: v == "flows")
            state["phase"] = "flows_open"
            return .4
        if phase == "flows_open":
            def starters():
                return [r["value"] for r in (view().get("popover") or {}).get("rows", ())
                        if str(r.get("value", "")).startswith("flows:")]
            if not wait(phase, lambda: "flows:starter:image_to_3d" in starters() and view().get("popup_hits"), limit=5):
                return .2
            capture("start-16-plus-menu-flows.png")
            note_menu_names("flows")
            noted = state.get("plus_menu_internal", {})
            checks["no_plus_menu_row_shows_an_internal_name"] = set(noted) == {"top", "base_meshes", "flows"} and all(
                entry["runs"] and not entry["internal"] for entry in noted.values())
            checks["flows_offer_new_and_the_templates"] = starters()[:3] == [
                "flows:new", "flows:starter:image_to_3d", "flows:starter:image_reference_3d"]
            click_popup("popup_add_open", lambda v: v == "flows:starter:image_to_3d")
            state["phase"] = "flow_window"
            return .5
        if phase == "flow_window":
            from angelo_agent import flow_open
            if not wait(phase, lambda: flow_open.flow_window(bpy.context.window_manager.windows) is not None, limit=10):
                return .2
            target = flow_open.flow_window(bpy.context.window_manager.windows)
            tree = target.screen.areas[0].spaces.active.node_tree
            checks["a_template_opens_a_wired_flow_in_its_own_window"] = bool(
                tree and [n.name for n in tree.nodes if n.name in {"Image", "3D model", "Place in scene"}]
                and len(tree.links) == 2)
            # The template's whole graph is framed once its window and nodes are drawn (flow_open.frame_when_drawn),
            # on this path as on Director's Flows menu.
            import qa_flow_view
            since = state.setdefault("since", {}).setdefault("flow_framed", time.monotonic())
            framed = qa_flow_view.graph_framed(target.screen.areas[0], bpy.context.preferences.system.ui_scale,
                                               state.setdefault("flow_graph", {}))
            if not framed and time.monotonic() - since < 6:
                return .2
            checks["the_template_flow_window_frames_the_whole_graph"] = framed
            capture("start-17-flow-from-template.png", target)
            with bpy.context.temp_override(window=target, area=target.screen.areas[0]):
                bpy.ops.wm.window_close()
            click("attach")
            state["phase"] = "plus_studio"
            return .5
        if phase == "plus_studio":
            if not wait(phase, lambda: (view().get("popover") or {}).get("kind") == "add"
                        and view().get("popup_hits"), limit=5):
                return .2
            click_popup("popup_add_open", lambda v: v == "media_studio")
            state["phase"] = "studio_window"
            return .5
        if phase == "studio_window":
            from angelo_agent import studio_toggle
            if not wait(phase, lambda: studio_toggle.opened_window(bpy.context.window_manager.windows) is not None,
                        limit=10):
                return .2
            target = studio_toggle.opened_window(bpy.context.window_manager.windows)
            checks["media_studio_opens_from_the_plus_menu"] = studio_toggle.is_studio(target.screen.areas[0])
            capture("start-18-media-studio-from-plus.png", target)
            studio_toggle.close_studio(target)
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.utils.register_class(ANGELO_OT_fixture_hold_input)
bpy.app.timers.register(tick, first_interval=.5)
