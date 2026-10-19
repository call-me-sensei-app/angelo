# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Readable chat cards: approvals, provider limits, offline errors, voice and snapshot settings.

Real default startup, real clicks on the chat canvas and a real turn through
stand-in CLIs (``fake_provider_cli.py``): ``claude`` is already signed in on
this computer and answers every turn with a session-limit result naming its
reset time; ``codex`` is installed and signed out. No browser, account,
network or inference.

Covered: Connect Claude reuses the existing sign-in (no login runs); a limit
result shows "Usage limit reached · resets …" with Switch connection and
Connect ChatGPT; Switch connection opens the in-chat picker; Connect ChatGPT
signs in and binds the chat; the microphone stays hidden unless the
Experimental voice preference is on (off by default at 1.0), and then shows
only on a Codex connection with audio tools; an offline error gets its own headline; a script approval
has one Approve that never looks disabled (its first click reveals the code
and approves nothing) and, in a new chat's Auto mode, offers Always allow
scripts, which starts off and which an assignment outside the Preferences editor
can't turn on (the chat says so); a native program card (shown in Ask) names objects
instead of handles; Snapshot Settings explains the 2 GiB default.

Run through ``run_native_suites.py --gui --include test_blender_chat_cards``;
check ``chat-cards.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-chat-cards-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_provider_cli  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')  # This suite needs the default Director startup.

fake_provider_cli.sign_in(os.environ["HOME"], "claude")  # An everyday sign-in that already exists.
os.environ.update(ANGELO_FAKE_CLI_STATE=str(OUT / "fake-cli"), ANGELO_FAKE_CLI_LOGIN_SECONDS="0.5",
                  ANGELO_FAKE_CLI_TURN="limit")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
bpy.context.preferences.edit.use_global_undo = True
REPORT = OUT / "chat-cards.json"
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [],
         "fixture": "Stand-in claude/codex CLIs, synthetic clicks; no browser, account, network or inference"}
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def chat():
    from angelo_agent import console
    area = next(a for a in window().screen.areas if console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def view_state():
    from angelo_agent import chat_view
    return chat_view._views.get(chat()[0].as_pointer(), {})


def actions():
    return [(action, value) for _, action, value in view_state().get("hits", [])]


def click(action, value=None):
    area, region = chat()
    rect = next(r for r, a, v in view_state()["hits"] if a == action and (value is None or v == value))
    x, y = int(region.x + rect[0] + rect[2] / 2), int(region.y + rect[1] + rect[3] / 2)
    for kind, event_value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        window().event_simulate(type=kind, value=event_value, x=x, y=y)


def hover(action, value=None):
    """Move the pointer onto a drawn control, travelling as a person's pointer does."""
    area, region = chat()
    rect = next(r for r, a, v in view_state()["hits"] if a == action and (value is None or v == value))
    x, y = int(region.x + rect[0] + rect[2] / 2), int(region.y + rect[1] + rect[3] / 2)
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x - 4, y=y)
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)


def key_over_chat(kind):
    area, region = chat()
    x, y = region.x + region.width // 2, region.y + 60
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    window().event_simulate(type=kind, value='PRESS', x=x, y=y)
    window().event_simulate(type=kind, value='RELEASE', x=x, y=y)


def preferences_open():
    return any(a.type == 'PREFERENCES' for w in bpy.context.window_manager.windows for a in w.screen.areas)


def cli_calls():
    path = OUT / "fake-cli" / "calls.jsonl"
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
    print("ANGELO_CHAT_CARDS", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(condition, limit=40):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + state["phase"]
    return False


def approval_call(identity, name, arguments):
    from angelo_agent import ui
    from angelo_agent.controller import ToolCall
    from angelo_agent.tool_specs import validate_call
    controller = ui.get_controller()
    call = ToolCall(identity, name, validate_call(name, arguments), ui.document_key(), approval=True)
    if name == "angelo_scene_program":
        # This display-only fixture inserts a pending card without the tool
        # queue. Reproduce its normal admission/review preparation; drawing
        # deliberately never constructs missing program review snapshots.
        from angelo_agent.blender_tools import BlenderExecutor
        from angelo_agent.approval_review import prepare_review
        executor = BlenderExecutor(window())
        executor.studio_context = controller
        call.precondition = prepare_review(call, controller._precondition(call, executor))
    controller.pending[call.id] = call
    chat()[0].tag_redraw()
    return call


def tick():
    try:
        assert time.monotonic() - started < 240, "Chat-cards fixture timed out in " + state["phase"]
        from angelo_agent import director, ui, quick_connect, chat_view, voice
        checks, phase = state["checks"], state["phase"]
        controller = ui.get_controller()
        if phase == "boot":
            if not director.is_director(window()) or director._timer is not None or ui._layout_timer is not None:
                return .2
            if not wait(lambda: not ui._authentication.running and ("welcome_connect", "claude_subscription") in actions()):
                return .2
            click("welcome_connect", "claude_subscription")
            state["phase"] = "claude_ready"
            return .3
        if phase == "claude_ready":
            if not wait(lambda: quick_connect.flow("claude_subscription") is None and controller.models and ui.connection_available()):
                return .2
            record = ui.connections().records[controller.state["connection_id"]]
            checks["signed_in_cli_reused_without_a_login"] = record["reuse_native"] and not any(
                c["tool"] == "claude" and c["arguments"][:2] == ["auth", "login"] for c in cli_calls())
            checks["no_microphone_on_claude"] = not any(a == "voice" for a, _ in actions())
            area, _ = chat()
            area.spaces.active.history[-1].body = "Make the cube red"
            key_over_chat('RET')
            state["phase"] = "limit"
            return .3
        if phase == "limit":
            if not wait(lambda: not controller.busy and any(m["role"] == "error" for m in controller.state["messages"])
                        and ("welcome_switch", None) in actions()):
                return .2
            capture("cards-1-usage-limit.png")
            error = next(m for m in controller.state["messages"] if m["role"] == "error")
            from angelo_agent import first_run
            notice = first_run.notice(error["text"])
            checks["limit_notice_names_the_reset_time"] = notice and notice["kind"] == "limit" and notice["reset"] == "4:30pm (UTC)"
            checks["stored_error_text_unchanged"] = error["text"].startswith("You've hit your session limit")
            checks["limit_offers_switch_and_other_provider"] = ("welcome_connect", "codex_subscription") in actions()
            checks["limit_never_opens_preferences"] = not preferences_open()
            hover("welcome_switch")
            state["phase"] = "limit_hover"
            return .3
        if phase == "limit_hover":
            capture("cards-1b-notice-action-hover.png")
            checks["notice_action_hovers_with_a_hand"] = (view_state().get("hover") == ("welcome_switch", None)
                                                          and view_state().get("cursor") == 'HAND_POINT')
            click("welcome_switch")
            state["phase"] = "switch"
            return .4
        if phase == "switch":
            popover = view_state().get("popover")
            if not wait(lambda: view_state().get("popover")):
                return .2
            capture("cards-2-switch-connection-picker.png")
            checks["switch_connection_opens_the_in_chat_picker"] = popover["kind"] == "models" and not preferences_open()
            key_over_chat('ESC')
            state["phase"] = "connect_chatgpt"
            return .3
        if phase == "connect_chatgpt":
            if not wait(lambda: not view_state().get("popover") and ("welcome_connect", "codex_subscription") in actions()):
                return .2
            click("welcome_connect", "codex_subscription")
            state["phase"] = "chatgpt_ready"
            return .3
        if phase == "chatgpt_ready":
            if not wait(lambda: quick_connect.flow("codex_subscription") is None and controller.selection_provider == "codex_subscription"
                        and controller.selection_models):
                return .2
            checks["chatgpt_connected_from_the_notice"] = any(c["tool"] == "codex" and c["arguments"][-1:] == ["login"] for c in cli_calls())
            voice._audio_tools_ready = True  # As on a Mac with ffmpeg and ffplay installed.
            chat()[0].tag_redraw()
            state["phase"] = "voice_default_off"
            return .4
        if phase == "voice_default_off":
            # Hidden at 1.0 unless the person opts in, even where it could work.
            checks["microphone_hidden_by_default_even_where_voice_works"] = (
                not ui.preferences().experimental_voice and not any(a == "voice" for a, _ in actions()))
            ui.preferences().experimental_voice = True
            chat()[0].tag_redraw()
            state["phase"] = "voice_on"
            return .3
        if phase == "voice_on":
            if not wait(lambda: any(a == "voice" for a, _ in actions())):
                return .2
            capture("cards-3-codex-microphone.png")
            checks["microphone_on_codex_with_audio_tools"] = sys.platform != "darwin" or any(a == "voice" for a, _ in actions())
            voice._audio_tools_ready = False  # No ffmpeg: the control disappears instead of failing on click.
            chat()[0].tag_redraw()
            state["phase"] = "voice_off"
            return .3
        if phase == "voice_off":
            if not wait(lambda: not any(a == "voice" for a, _ in actions())):
                return .2
            checks["no_microphone_without_audio_tools"] = True
            ui.preferences().experimental_voice = False
            controller.state["messages"].append({"role": "error", "text": "API Error: Connection error."})
            controller.version += 1
            state["phase"] = "offline"
            return .4
        if phase == "offline":
            capture("cards-4-offline.png")
            from angelo_agent import first_run
            checks["offline_error_gets_a_plain_headline"] = first_run.notice("API Error: Connection error.")["kind"] == "offline"
            checks["offline_offers_switch_connection"] = ("welcome_switch", None) in actions()
            state["script"] = approval_call("fixture-script", "angelo_execute_python",
                {"description": "Bevel the cube", "code": "import bpy\nbpy.ops.object.modifier_add(type='BEVEL')"}).id
            state["phase"] = "script"
            return .4
        if phase == "script":
            if not wait(lambda: ("approve", state["script"]) in actions()):
                return .2
            capture("cards-5-script-approval.png")
            checks["one_approve_button"] = sum(a == "approve" for a, _ in actions()) == 1
            # A new chat is in Auto, so the script card also offers Always allow scripts, which starts off.
            checks["script_card_offers_always_allow_in_auto"] = ("script_always_allow", state["script"]) in actions()
            checks["always_allow_scripts_starts_off"] = ui.preferences().always_allow_scripts is False
            # Not the Preferences editor (as a script, an add-on or a keymap would): the real setter changes nothing.
            ui.preferences().always_allow_scripts = True
            checks["only_the_person_turns_always_allow_on"] = ui.preferences().always_allow_scripts is False
            click("approve", state["script"])
            state["phase"] = "script_reviewed"
            return .4
        if phase == "script_reviewed":
            call = controller.pending.get(state["script"])
            if not wait(lambda: state["script"] in controller.reviewed_calls):
                return .2
            capture("cards-6-script-code-shown.png")
            checks["first_approve_click_reveals_the_code"] = ("approval", state["script"]) in view_state().get("expanded", set())
            checks["first_approve_click_runs_nothing"] = call is not None and call.approval and not call.approved
            checks["the_chat_says_the_change_was_ignored"] = any(
                "only you can change it" in (m.get("text") or "") for m in controller.state["messages"])
            click("decline", state["script"])
            state["phase"] = "program"
            return .4
        if phase == "program":
            if not wait(lambda: state["script"] not in controller.pending):
                return .2
            program = {"mode": "APPLY", "read_set": [], "write_set": [], "expected_checks": [],
                       "operations": [{"kind": "create_primitive", "target": None,
                                       "parameters": {"primitive": "CUBE", "alias": "ui_cube", "name": "Cube.001", "size": 2}},
                                      {"kind": "material_principled", "target": "$ui_cube",
                                       "parameters": {"name": "Fixture Red", "inputs": {"Base Color": [0.8, 0.1, 0.1, 1]}}}]}
            # Auto would approve a bounded program at once (with no turn running, cancel it): show its card in Ask.
            controller.set_options(approval="ASK")
            call = approval_call("fixture-program", "angelo_scene_program", program)
            call.target_labels, call._target_labels_prepared = {"$ui_cube": "Cube.001"}, True
            state["program"] = call.id
            state["phase"] = "program_card"
            return .4
        if phase == "program_card":
            if not wait(lambda: ("approve", state["program"]) in actions()):
                return .2
            capture("cards-7-program-approval.png")
            from angelo_agent.approvals import approval_message, review_arguments
            call = controller.pending[state["program"]]
            checks["program_card_preparation_runs_nothing"] = (
                not call.approved and not call.authorized
                and "Cube.001" not in bpy.data.objects and "Fixture Red" not in bpy.data.materials)
            message = approval_message(call.name, review_arguments(call), target_labels=call.target_labels)
            shown = message["title"] + message["body"] + message["warning"]
            checks["program_card_has_no_jargon_or_handles"] = not any(word in shown for word in (
                "$ui_cube", "declared revision", "journaled", "native action", "Native action"))
            checks["program_card_names_the_object"] = "“Cube.001”" in shown
            hover("approve", state["program"])
            state["phase"] = "program_hover"
            return .3
        if phase == "program_hover":
            capture("cards-7b-approve-hover.png")
            checks["approve_hovers_with_a_hand"] = (view_state().get("hover") == ("approve", state["program"])
                                                    and view_state().get("cursor") == 'HAND_POINT')
            # The same card's Details, opened: the complete exact review in a framed mono block.
            view_state()["expanded"].add(("approval", state["program"]))
            chat()[0].tag_redraw()
            state["phase"], state["details_waits"] = "program_details", 0
            return .4
        if phase == "program_details":
            state["details_waits"] += 1
            if not view_state().get("last_detail_rows") and state["details_waits"] < 20:
                chat()[0].tag_redraw()
                return .2
            capture("cards-7c-program-details.png")
            rows = view_state().get("last_detail_rows", ())
            from angelo_agent import chat_view
            checks["program_details_drawn_in_mono"] = bool(rows) and view_state().get("last_detail_font") == chat_view._mono
            checks["program_details_keep_one_action_row"] = all(
                (a, state["program"]) in actions() for a in ("approve", "decline", "review"))
            view = bpy.context.preferences.view
            languages = {item.identifier for item in view.bl_rna.properties["language"].enum_items}
            if bpy.app.build_options.international and "ar_EG" in languages:
                view.language, view.use_translate_interface = "ar_EG", True
                chat()[0].tag_redraw()
                state["phase"], state["details_waits"] = "program_arabic", 0
                return .6
            click("decline", state["program"])
            state["phase"] = "snapshot_settings"
            return .4
        if phase == "program_arabic":
            state["details_waits"] += 1
            if not view_state().get("last_detail_rows") and state["details_waits"] < 20:
                chat()[0].tag_redraw()
                return .2
            capture("cards-7d-program-details-arabic.png")
            from angelo_agent import chat_view
            # Arabic detail text uses the shaping font (the mono font has no Arabic forms), never reversed mono.
            checks["arabic_details_use_the_shaping_font"] = view_state().get("last_detail_font") == chat_view._font
            checks["arabic_card_keeps_its_actions"] = all(
                (a, state["program"]) in actions() for a in ("approve", "decline", "review"))
            bpy.context.preferences.view.language = "en_US"
            bpy.context.preferences.view.ui_scale = 1.5  # The card at the canvas's one scale factor.
            chat()[0].tag_redraw()
            state["phase"] = "program_scaled"
            return .6
        if phase == "program_scaled":
            if not wait(lambda: ("decline", state["program"]) in actions()):
                return .2
            capture("cards-7e-program-details-scale-1.5.png")
            checks["scaled_card_keeps_its_actions"] = all(
                (a, state["program"]) in actions() for a in ("approve", "decline", "review"))
            bpy.context.preferences.view.ui_scale = 1.0
            chat()[0].tag_redraw()
            state["phase"] = "program_decline"
            return .6
        if phase == "program_decline":
            if not wait(lambda: ("decline", state["program"]) in actions()):
                return .2
            click("decline", state["program"])
            state["phase"] = "snapshot_settings"
            return .4
        if phase == "snapshot_settings":
            with bpy.context.temp_override(window=window()):
                bpy.ops.angelo.checkpoint_settings('INVOKE_DEFAULT')
            checks["restore_operator_named_plainly"] = bpy.types.ANGELO_OT_restore_checkpoint.bl_label == "Restore Snapshot"
            state["phase"] = "snapshot_dialog"
            return .6
        if phase == "snapshot_dialog":
            capture("cards-8-snapshot-settings.png")
            window().event_simulate(type='ESC', value='PRESS', x=20, y=20)
            window().event_simulate(type='ESC', value='RELEASE', x=20, y=20)
            checks["never_opened_preferences"] = not preferences_open()
            state["phase"] = "translation"
            return .4
        if phase == "translation":
            # The display-time path: a language switch, and a template translated by the shipped catalogue
            # (locale/angelo) with its value kept. Needs a build with WITH_INTERNATIONAL and the catalogues.
            from angelo_agent.ui_text import iface_
            view = bpy.context.preferences.view
            languages = {item.identifier for item in view.bl_rna.properties["language"].enum_items}
            if bpy.app.build_options.international and "fr_FR" in languages:
                view.language, view.use_translate_interface = "fr_FR", True
                template = "Usage limit reached · resets {time}"
                french = bpy.app.translations.pgettext_iface(template)
                shown = iface_(template, time="4:30pm (UTC)")
                state["translated_template"] = shown
                checks["templates_translate_at_display_time"] = (
                    french != template and shown == french.format(time="4:30pm (UTC)") and "4:30pm (UTC)" in shown)
                chat()[0].tag_redraw()
                state["phase"] = "translated"
                return .4
            state["translation_runtime"] = ("not available in this build (international=%s, fr_FR listed=%s); "
                                            "extraction readiness is pinned by test_native_first_run" %
                                            (bpy.app.build_options.international, "fr_FR" in languages))
            state["phase"] = "done"
            return .2
        if phase == "translated":
            capture("cards-9-translated-notice.png")
            bpy.context.preferences.view.language = "en_US"
            state["phase"] = "done"
            return .2
        if phase == "done":
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
