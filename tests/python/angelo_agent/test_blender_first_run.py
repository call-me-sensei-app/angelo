# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""First run: a welcome screen explains the two modes, the empty chat connects in one step and offers examples.

Real default startup and real clicks (``Window.event_simulate``) on the chat
canvas's own hit geometry. Stand-in ``claude`` and ``codex`` executables
(``fake_provider_cli.py``) are first on PATH: both are installed and signed
out, ``codex login`` is set to fail once and ``claude auth login`` succeeds
after a second, as if the person finished in the browser. No real CLI,
browser, account, network or inference is used.

Covered: the real first frame (the runner leaves Angelo's startup on for this
suite: ``first_launch``) is Blender's startup splash with its art and no Blender
form, drawn by Angelo's stand-ins (``angelo_default_layout``) before the add-on
loads; on first launch Angelo starts in Director; Blender's splash is never
reopened and Angelo's setup card follows it, the welcome waiting; real clicks
pick Arabic from the language list (the card switches language), the light
look and right-click select (each takes effect), back to English and dark; the
card fits a narrow view (twice the interface scale); the Blender-settings row
is disabled in a fresh profile; Continue saves the
preferences, and the welcome screen opens by itself once (splash, setup, welcome),
with banner art, the two modes, how to switch, demo tiles (a tile opens its
link and keeps the screen) and Start creating; the card offers
Connect Claude / Connect ChatGPT / an API key, with
no "Choose model" pill and no dead microphone; a failed sign-in stays in the
card with a retry and never opens Preferences; Connect Claude runs the
provider's own sign-in in a private profile, re-checks it, binds the chat and
loads its models, landing on a ready chat with a model chosen, example
prompts and under them the tip, a link to the docs; an example fills the composer; the API-key choice opens the
connection form, not Preferences; the welcome reopens from the menu and its
Try Manual mode button switches modes and saves the choice.

Run through ``run_native_suites.py --gui --include test_blender_first_run``;
check ``first-run.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-first-run-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_provider_cli  # noqa: E402
import splash_frames  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')  # This suite needs the default Director startup.
# --python runs before Blender builds its startup splash: count every layout of Blender's own splash menus from here.
splash_frames.instrument()
AT_PYTHON = splash_frames.menu_owners()

FAKE = OUT / "fake-cli"
FAKE.mkdir(parents=True, exist_ok=True)
(FAKE / "login-fails").write_text("the first browser sign-in is abandoned\n")
os.environ.update(ANGELO_FAKE_CLI_STATE=str(FAKE), ANGELO_FAKE_CLI_LOGIN_SECONDS="1")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "first-run.json"
state = {"phase": "early", "success": False, "checks": {}, "screenshots": [],
         "fixture": "Stand-in claude/codex CLIs and synthetic clicks; no browser, account, network or inference"}
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def chat():
    from angelo_agent import console
    area = next(a for a in window().screen.areas if console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def hits():
    from angelo_agent import chat_view
    area, _ = chat()
    return chat_view._views.get(area.as_pointer(), {}).get("hits", [])


def actions():
    return [(action, value) for _, action, value in hits()]


def click(action, value=None):
    """Press and release on the drawn button, exactly where a person would click."""
    area, region = chat()
    rect = next(r for r, a, v in hits() if a == action and (value is None or v == value))
    x, y = int(region.x + rect[0] + rect[2] / 2), int(region.y + rect[1] + rect[3] / 2)
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    window().event_simulate(type='LEFTMOUSE', value='PRESS', x=x, y=y)
    window().event_simulate(type='LEFTMOUSE', value='RELEASE', x=x, y=y)


def click_welcome(action, value=None):
    """Click the welcome screen's own button, in window coordinates."""
    from angelo_agent import intro
    area = next(a for a in window().screen.areas if a.as_pointer() == intro._open["area"])
    region = next(r for r in area.regions if r.type == 'WINDOW')
    rect = next(r for r, a, v in intro._open["plan"]["hits"] if a == action and (value is None or v == value))
    x, y = int(region.x + rect[0] + rect[2] / 2), int(region.y + rect[1] + rect[3] / 2)
    for kind, event_value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        window().event_simulate(type=kind, value=event_value, x=x, y=y)


def card_actions():
    from angelo_agent import card_host
    return [(a, v) for _, a, v in card_host._active.plan.hits] if card_host._active and card_host._active.plan else []


def click_card(action, value=None):
    """Click the visible card's own control, in window coordinates."""
    from angelo_agent import card_host, canvas_rtl
    active = card_host._active
    area = next(a for a in window().screen.areas if a.as_pointer() == active.area)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    rect = next(r for r, a, v in reversed(active.plan.hits) if a == action and (value is None or v == value))
    x = rect[0] + rect[2] / 2
    x = canvas_rtl.pointer_x(x, region.width)  # Mirrored back in a right-to-left language.
    x, y = int(region.x + x), int(region.y + rect[1] + rect[3] / 2)
    for kind, event_value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        window().event_simulate(type=kind, value=event_value, x=x, y=y)


def preferences_open():
    return any(a.type == 'PREFERENCES' for w in bpy.context.window_manager.windows for a in w.screen.areas)


def cli_calls():
    path = FAKE / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


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
    print("ANGELO_FIRST_RUN", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
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
        assert time.monotonic() - started < 240, "First-run fixture timed out in " + state["phase"]
        checks, phase = state["checks"], state["phase"]
        if phase == "early":
            # The first main-loop turn: Blender's own startup splash, as a person's first launch shows it. Nothing is
            # reopened; the add-on has usually not loaded yet, so Angelo's startup stand-ins drew the splash menus.
            layout = sys.modules["angelo_default_layout"]
            state["first_frame"] = splash_frames.state()
            path = OUT / "first-run-0-startup.png"
            with bpy.context.temp_override(window=window()):
                bpy.ops.screen.screenshot(filepath=str(path))   # No forced redraw first: the frame as it is.
            state["screenshots"].append(str(path))
            state["first_frame"]["form_rows"] = splash_frames.form_rows(path)
            checks["stand_ins_own_the_splash_before_the_add_on"] = set(AT_PYTHON.values()) == {"angelo_default_layout"}
            checks["first_frame_is_the_splash_art"] = (
                (layout._splash_draws > 0 or sys.modules.get("angelo_agent.splash_setup") is not None)
                and state["first_frame"]["form_rows"] < splash_frames.FORM_ROWS)
            # Dialogs open under the pointer; simulated input starts it at the window corner.
            window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=window().width // 2, y=window().height // 2)
            state["phase"] = "boot"
            return .2
        from angelo_agent import director, ui, quick_connect, chat_view, intro
        from angelo_agent import splash_setup, card_host
        if phase == "boot":
            if not director.is_director(window()) or director._timer is not None or ui._layout_timer is not None:
                return .2
            if not wait(phase, lambda: card_host.is_active("setup") and card_host._active.plan is not None):
                return .2
            state["phase"] = "setup"
            return .4
        if phase == "setup":
            capture("first-run-0-setup-card.png")
            state["sequence_at_setup"] = list(splash_setup.sequence)
            checks["setup_card_shows_after_director_settles"] = (card_host.is_active("setup") and director.is_director(window())
                                                                 and not splash_setup.preferences_saved())
            checks["blenders_splash_is_never_reopened"] = splash_setup.reopened == 0 and splash_setup.sequence.count("splash") == 1
            checks["sequence_starts_with_the_splash"] = splash_setup.sequence[:1] == ["splash"] and splash_setup.drawn > 0
            checks["blenders_form_never_drawn"] = sum(splash_frames.form_draws.values()) == 0
            checks["setup_shown_once_before_the_welcome"] = (splash_setup.sequence == ["splash", "setup"]
                                                             and splash_setup.sequence.count("setup") == 1)
            checks["welcome_waits_for_the_setup_card"] = intro.opened == 0 and not intro.is_open()
            actions_shown = card_actions()
            checks["setup_offers_language_look_and_continue"] = (("language_menu", None) in actions_shown
                                                                 and ("look", "dark") in actions_shown
                                                                 and ("look", "light") in actions_shown
                                                                 and ("continue", None) in actions_shown)
            # A fresh profile has no earlier Blender settings: the row explains why its toggle is off.
            checks["blender_settings_row_disabled_without_earlier_settings"] = ("import", None) not in actions_shown
            click_card("language_menu")
            state["phase"] = "language_menu"
            return .4
        if phase == "language_menu":
            capture("first-run-0-setup-language-menu.png")
            codes = [v for a, v in card_actions() if a == "language"]
            state["languages_offered"] = codes
            checks["language_list_offers_angelos_languages"] = len(codes) >= 15 and "en_US" in codes
            arabic = next(code for code in codes if code.startswith("ar"))
            click_card("language", arabic)
            state["phase"] = "arabic"
            return .5
        if phase == "arabic":
            checks["language_choice_takes_effect"] = bpy.context.preferences.view.language.startswith("ar")
            capture("first-run-0-setup-ar.png")
            click_card("look", "light")
            state["phase"] = "light"
            return .5
        if phase == "light":
            theme = bpy.context.preferences.themes[0]
            checks["look_choice_takes_effect"] = (bpy.types.USERPREF_MT_interface_theme_presets.bl_label == "Angelo Light"
                                                  and theme.user_interface.wcol_regular.inner[0] > 0.5)
            capture("first-run-0-setup-light-ar.png")
            click_card("look", "dark")
            state["phase"] = "options"
            return .5
        if phase == "options":
            checks["dark_look_restores"] = bpy.context.preferences.themes[0].user_interface.wcol_regular.inner[0] < 0.5
            click_card("options")
            state["phase"] = "select_right"
            return .4
        if phase == "select_right":
            click_card("select", "RIGHT")
            state["phase"] = "english"
            return .4
        if phase == "english":
            kc_prefs = bpy.context.window_manager.keyconfigs.active.preferences
            checks["mouse_select_choice_takes_effect"] = getattr(kc_prefs, "select_mouse", "RIGHT") == "RIGHT"
            capture("first-run-0-setup-options-ar.png")
            click_card("select", "LEFT")
            click_card("language_menu")
            state["phase"] = "english_pick"
            return .4
        if phase == "english_pick":
            click_card("language", "en_US")
            state["phase"] = "narrow"
            return .5
        if phase == "narrow":
            checks["english_restored"] = bpy.context.preferences.view.language == "en_US"
            capture("first-run-0-setup-options.png")
            # Narrow: the same 3D view at twice the interface scale leaves the card half the room.
            bpy.context.preferences.view.ui_scale = 2.0
            state["phase"] = "narrow_capture"
            return .6
        if phase == "narrow_capture":
            capture("first-run-0-setup-narrow.png")
            plan = card_host._active.plan
            area = next(a for a in window().screen.areas if a.as_pointer() == card_host._active.area)
            region = next(r for r in area.regions if r.type == 'WINDOW')
            checks["narrow_card_fits_its_view"] = (plan.card[0] >= 0 and plan.card[0] + plan.card[2] <= region.width
                                                   and plan.card[1] >= 0 and plan.card[1] + plan.card[3] <= region.height)
            bpy.context.preferences.view.ui_scale = 1.0
            state["phase"] = "continue_click"
            return .6
        if phase == "continue_click":
            click_card("continue")
            state["phase"] = "setup_saved"
            return .4
        if phase == "setup_saved":
            if not wait(phase, splash_setup.preferences_saved):
                return .2
            state["phase"] = "welcome_after_setup"
            return .2
        if phase == "welcome_after_setup":
            if not wait(phase, lambda: intro.is_open() and "plan" in intro._open):
                return .2
            checks["continue_saves_preferences_and_opens_the_welcome"] = (splash_setup.preferences_saved() and intro.opened == 1
                                                                          and not card_host.is_active("setup"))
            state["sequence"] = list(splash_setup.sequence)
            checks["sequence_is_splash_setup_welcome_each_once"] = splash_setup.sequence == ["splash", "setup", "welcome"]
            state["phase"] = "welcome"
            return .4
        if phase == "welcome":
            from angelo_agent import welcome_screen
            capture("first-run-1-welcome.png")
            actions_shown = [(a, v) for _, a, v in intro._open["plan"]["hits"]]
            checks["welcome_opens_by_itself_on_first_launch"] = intro.opened == 1 and not intro.seen()
            checks["welcome_has_banner_art"] = welcome_screen._banner["pixels"] is not None
            checks["welcome_offers_start_switch_close_and_demos"] = (("start", None) in actions_shown and ("standard", None) in actions_shown
                                                                    and ("close", None) in actions_shown
                                                                    and [v for a, v in actions_shown if a == "demo"] == [0, 1, 2, 3])
            checks["starts_in_director"] = director.is_director(window())
            state["demo_urls"] = []
            intro.open_demo = state["demo_urls"].append  # Never open a real browser from a fixture.
            click_welcome("demo", 0)
            state["phase"] = "welcome_demo"
            return .4
        if phase == "welcome_demo":
            from angelo_agent import first_run
            if not wait(phase, lambda: state["demo_urls"]):
                return .2
            checks["demo_tile_opens_its_link_and_keeps_the_welcome"] = (state["demo_urls"] == [first_run.DEMOS[0][1]]
                                                                        and intro.is_open())
            click_welcome("start")
            state["phase"] = "intro_closed"
            return .4
        if phase == "intro_closed":
            if not wait(phase, lambda: not intro.is_open() and intro.seen()):
                return .2
            checks["start_creating_closes_and_stays_in_director"] = director.is_director(window())
            # Startup and load_post both reach the scheduler; a second welcome would cover the screen again.
            checks["intro_opened_exactly_once"] = intro.opened == 1 and json.loads(intro.state_path().read_text()) == {
                "seen": True, "choice": "DIRECTOR"}
            state["phase"] = "card"
            return .3
        if phase == "card":
            if not wait(phase, lambda: not ui._authentication.running and hits()):
                return .2
            capture("first-run-1-connect-card.png")
            checks["both_installed_clis_offered"] = [v for a, v in actions() if a == "welcome_connect"] == ["claude_subscription", "codex_subscription"]
            checks["api_key_and_preferences_offered"] = ("welcome_api_key", None) in actions() and ("settings", None) in actions()
            checks["no_choose_model_pill_without_connection"] = not any(a == "model" for a, _ in actions())
            checks["no_dead_microphone_without_connection"] = not any(a == "voice" for a, _ in actions())
            checks["discovery_created_nothing"] = ui.connections().records == {}
            click("welcome_connect", "codex_subscription")
            state["phase"] = "chatgpt_fails"
            return .3
        if phase == "chatgpt_fails":
            flow = quick_connect.flow("codex_subscription")
            if not wait(phase, lambda: flow and flow["phase"] == "failed"):
                return .2
            capture("first-run-2-chatgpt-sign-in-failed.png")
            checks["failed_sign_in_stays_in_the_card_with_a_retry"] = (
                "try again" in flow["reason"] and ("welcome_connect", "codex_subscription") in actions())
            checks["failed_sign_in_never_opens_preferences"] = not preferences_open()
            checks["codex_login_was_the_provider_command"] = any(c["tool"] == "codex" and c["arguments"][-1:] == ["login"] for c in cli_calls())
            checks["failed_sign_in_leaves_no_dead_connection"] = ui.connections().records == {}
            click("welcome_connect", "claude_subscription")
            state["phase"] = "claude_signing_in"
            return .2
        if phase == "claude_signing_in":
            flow = quick_connect.flow("claude_subscription")
            if not wait(phase, lambda: flow and flow["phase"] == "signing_in"):
                return .1
            capture("first-run-3-claude-signing-in.png")
            checks["sign_in_progress_shown_inline"] = ("welcome_cancel", "claude_subscription") in actions()
            state["phase"] = "claude_ready"
            return .2
        if phase == "claude_ready":
            controller = ui.get_controller()
            if not wait(phase, lambda: quick_connect.flow("claude_subscription") is None and controller.models and ui.connection_available()):
                return .2
            record = ui.connections().records[controller.state["connection_id"]]
            logins = [c for c in cli_calls() if c["tool"] == "claude" and c["arguments"][:2] == ["auth", "login"]]
            checks["claude_login_ran_in_a_private_profile"] = (len(logins) == 1 and logins[0]["arguments"] == ["auth", "login", "--claudeai"]
                                                              and record["id"] in logins[0]["profile"])
            checks["chat_bound_to_the_new_connection"] = record["mode"] == "claude_subscription" and not record["reuse_native"]
            from angelo_agent.chat_settings import selected_model
            model = selected_model(controller)
            checks["model_chosen_without_a_picker"] = bool(model) and model["id"] == "default"
            state["phase"] = "examples"
            return .3
        if phase == "examples":
            if not wait(phase, lambda: any(a == "welcome_example" for a, _ in actions())):
                return .2
            capture("first-run-4-connected-examples.png")
            checks["four_example_prompts_offered"] = [v for a, v in actions() if a == "welcome_example"] == [0, 1, 2, 3]
            from angelo_agent import first_run
            drawn, tip = actions(), ("url", first_run.TIP_URL)
            checks["tip_under_the_examples_links_to_the_docs"] = (
                tip in drawn and drawn.index(tip) > drawn.index(("welcome_example", 3)))
            checks["model_pill_back_once_connected"] = any(a == "model" for a, _ in actions())
            checks["no_microphone_on_a_claude_connection"] = not any(a == "voice" for a, _ in actions())
            checks["never_opened_preferences"] = not preferences_open()
            click("welcome_example", 2)
            state["phase"] = "example_filled"
            return .3
        if phase == "example_filled":
            from angelo_agent import first_run
            area, _ = chat()
            if not wait(phase, lambda: area.spaces.active.history[-1].body == first_run.EXAMPLES[2]):
                return .2
            capture("first-run-5-example-in-composer.png")
            checks["example_fills_the_composer_without_sending"] = not ui.get_controller().busy and not ui.get_controller().state["messages"]
            # The API-key choice opens the connection form over the chat, never Preferences. A new chat
            # on a key-only mode with no key is the unconnected state that shows the card again.
            area.spaces.active.history[-1].body = ""
            ui.create_session(mode="codex_api")
            area.tag_redraw()
            state["phase"] = "api_key"
            return .3
        if phase == "api_key":
            if not wait(phase, lambda: ("welcome_api_key", None) in actions()):
                return .2
            click("welcome_api_key")
            state["phase"] = "api_form"
            return .6
        if phase == "api_form":
            capture("first-run-6-api-key-form.png")
            checks["api_key_form_not_preferences"] = not preferences_open() and bool(quick_connect._key_form)
            window().event_simulate(type='ESC', value='PRESS', x=10, y=10)
            window().event_simulate(type='ESC', value='RELEASE', x=10, y=10)
            state["phase"] = "intro_again"
            return .4
        if phase == "intro_again":
            # Reachable later: Help menu and the Director / Manual menu both list it.
            checks["intro_in_help_and_mode_menus"] = (intro.draw_help in bpy.types.TOPBAR_MT_help.draw._draw_funcs
                                                      and "angelo.intro" in Path(director.__file__).read_text())
            with bpy.context.temp_override(window=window()):
                bpy.ops.angelo.intro('INVOKE_DEFAULT')
            state["phase"] = "intro_menu"
            return .5
        if phase == "intro_menu":
            if not wait(phase, lambda: "plan" in intro._open):
                return .2
            capture("first-run-7-welcome-again.png")
            checks["intro_reopens"] = intro.opened == 2
            click_welcome("standard")
            state["phase"] = "standard"
            return .4
        if phase == "standard":
            if not wait(phase, lambda: not director.is_director(window()) and director._timer is None and ui._layout_timer is None
                        and director.preferred_mode() == "MANUAL"):
                return .2
            capture("first-run-8-standard-editor.png")
            checks["try_the_standard_editor_switches_and_is_saved"] = ({"PROPERTIES", "OUTLINER"} <= {a.type for a in window().screen.areas}
                                                                      and not intro.is_open())
            state["phase"] = "done"
            return .4
        if phase == "done":
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.05)
