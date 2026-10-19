# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A detected native sign-in is suggested, never set up.

Real app, real operators, Preferences and form dialog. The native status is a
stand-in delivered through the actual authentication event queue: the Codex
and Claude CLIs never run and no real profile, credential or Keychain is used.
Run through ``run_native_suites.py --gui`` (needs ``--enable-event-simulate``).
"""

import hashlib
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

import addon_utils
import bpy

assert "--factory-startup" in sys.argv and not bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-native-suggestions-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ["ANGELO_STATE_DIR"] = str(OUT / "state")
os.environ["ANGELO_CONNECTIONS_DIR"] = str(OUT / "connections")
os.environ["ANGELO_DISABLE_KEYCHAIN"] = "1"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ui, console, chat_view, preview_branding, native_suggestions
from angelo_agent.connections import ConnectionStore

STAND_IN = {"codex_subscription": "Connected to ChatGPT", "claude_subscription": "Connected to Claude",
            "codex_api": "Native API credential configured"}
cli_calls, refresh_calls = [], []
# Never launch a native CLI: record what the app would have checked instead.
ui._authentication.start = lambda *args, **kwargs: cli_calls.append(args[0] if args else kwargs.get("mode"))
ui.refresh_connected_models = lambda *args, **kwargs: refresh_calls.append("catalog")
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
preview_branding.install()
bpy.context.preferences.view.show_splash = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": {},
         "fixture": "Stand-in native status; no CLI, sign-in, credential or inference"}
before = [(o.name, tuple(o.location)) for o in bpy.context.scene.objects]
chat_id = None


class Layout:
    def __init__(self):
        self.labels, self.operators = [], []

    def row(self):
        return self

    def box(self):
        return self

    def column(self):
        return self

    def separator(self):
        pass

    def prop(self, data, name, **kwargs):
        self.labels.append(name)

    def label(self, text="", **kwargs):
        self.labels.append(text)

    def operator(self, name, **kwargs):
        properties = SimpleNamespace()
        self.operators.append((name, properties))
        return properties


def save():
    (OUT / "native-suggestions.json").write_text(json.dumps(state, indent=2, default=str))


def capture(name, window):
    with bpy.context.temp_override(window=window):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))
    state["screenshots"][name] = hashlib.sha256((OUT / name).read_bytes()).hexdigest()


def deliver_stand_in_status():
    """The same queue entries a finished non-billable status check produces."""
    for mode, status in STAND_IN.items():
        ui._authentication.modes[mode] = mode
        ui._authentication.running.add(mode)
        ui._authentication.events.put((mode, status, False))


def offered():
    return [s["mode"] for s in native_suggestions.suggestions(ui.connections(), ui._authentication.status)]


def saved_store():
    return ConnectionStore(ui.connections().directory)


def nothing_created_or_bound(controller):
    return (not ui.connections().records and not saved_store().records and controller.provider is None
            and controller.state.get("connection_id") is None)


def prefs_window():
    return next(w for w in bpy.context.window_manager.windows if any(a.type == 'PREFERENCES' for a in w.screen.areas))


def invoke_suggested_form(mode):
    window = prefs_window()
    area = next(a for a in window.screen.areas if a.type == 'PREFERENCES')
    with bpy.context.temp_override(window=window, area=area):
        return bpy.ops.angelo.new_connection('INVOKE_DEFAULT', **native_suggestions.form_properties(mode))


def close_dialog(confirm):
    window = prefs_window()
    window.event_simulate(type='RET' if confirm else 'ESC', value='PRESS')
    window.event_simulate(type='RET' if confirm else 'ESC', value='RELEASE')


def tick():
    global chat_id
    try:
        window = bpy.context.window_manager.windows[0]
        checks = state["checks"]
        controller = ui.get_controller()
        if state["phase"] == "boot":
            chat_id = controller.state["id"]
            controller.refresh_models = lambda: refresh_calls.append(controller.state["id"])
            checks["startup_only_checks_native_status"] = set(cli_calls) == {"codex_subscription", "claude_subscription", "codex_api"}
            checks["store_starts_empty"] = nothing_created_or_bound(controller)
            deliver_stand_in_status()
            state["phase"] = "detected"
            save()
            return .5
        if state["phase"] == "detected":
            if ui._authentication.running:
                return .1
            checks["stand_in_status_applied"] = all(ui._authentication.status.get(m) == s for m, s in STAND_IN.items())
            checks["detection_creates_and_binds_nothing"] = nothing_created_or_bound(controller)
            checks["detection_writes_no_connection"] = not saved_store().path.exists() or not json.loads(saved_store().path.read_text())["connections"]
            checks["both_subscriptions_suggested"] = offered() == ["codex_subscription", "claude_subscription"]
            layout = Layout()
            ui.draw_ai_providers(layout, bpy.context)
            forms = [vars(p) for name, p in layout.operators if name == "angelo.new_connection" and vars(p).get("reuse_existing")]
            checks["suggestion_rows_drawn"] = ("Found your ChatGPT sign-in (Codex)" in layout.labels
                                               and "Found your Claude Code sign-in" in layout.labels
                                               and native_suggestions.SUGGESTION_NOTE in layout.labels)
            checks["add_buttons_prefill_the_reuse_form"] = forms == [native_suggestions.form_properties("codex_subscription"),
                                                                    native_suggestions.form_properties("claude_subscription")]
            checks["dismiss_buttons_drawn"] = [p.mode for n, p in layout.operators if n == "angelo.dismiss_native_suggestion"] == ["codex_subscription", "claude_subscription"]
            checks["unbound_chat_not_available_on_detected_sign_in"] = not ui.connection_available()
            try:
                ui.prepare_controller()
                checks["send_path_refuses_without_a_connection"] = False
            except RuntimeError as error:
                checks["send_path_refuses_without_a_connection"] = "Connect Claude or ChatGPT" in str(error) and nothing_created_or_bound(controller)
            bpy.ops.angelo.open_chat(side="RIGHT")
            state["phase"] = "chat"
            save()
            return .5
        if ui._layout_timer is not None:
            return .1
        if state["phase"] == "chat":
            area = next(a for a in window.screen.areas if console.is_chat(a))
            capture("chat-setup-hint.png", window)
            view = chat_view._views[area.as_pointer()]
            # The chat offers each detected sign-in as a Connect button; drawing it creates nothing.
            offered_in_chat = [value for _, action, value in view["hits"] if action == "welcome_connect"]
            checks["chat_offers_detected_sign_ins_as_connect_buttons"] = (
                offered_in_chat == ["claude_subscription", "codex_subscription"]
                and any(action == "settings" for _, action, _ in view["hits"])
                and any(action == "welcome_api_key" for _, action, _ in view["hits"])
                and nothing_created_or_bound(controller))
            with bpy.context.temp_override(window=window):
                bpy.ops.angelo.settings()
            state["phase"] = "preferences"
            save()
            return .5
        if state["phase"] == "preferences":
            # The page is drawn on Angelo's canvas: capture once its first frame is on screen (a loaded machine can
            # take longer than the half second this phase waited to present a new window).
            from angelo_agent import providers_page_host as page
            region = next(r for a in prefs_window().screen.areas if a.type == 'PREFERENCES' for r in a.regions if r.type == 'WINDOW')
            state["page_waits"] = state.get("page_waits", 0) + 1
            from angelo_agent import providers_page_model
            # In the product the hero's banner is read at start-up, long before a person can open Preferences: open
            # the stock dialog only after that read has finished, as a person would.
            if (page.plan_for(region) is None or not providers_page_model.ART["done"]) and state["page_waits"] < 25:
                return .2
            if state["page_waits"] == 1:
                state["page_waits"] = 2
                return .3   # One more frame after the first draw, so the screenshot shows it.
            capture("ai-providers-suggestions.png", prefs_window())
            # Blender calls a dialog operator's cancel() when its popup goes away for any reason but confirming:
            # counting the calls says whether the form stayed open, independent of a screenshot's timing.
            original_cancel = ui.ANGELO_OT_new_connection.cancel

            def counted_cancel(self, context):
                state["form_cancels"] = state.get("form_cancels", 0) + 1
                return original_cancel(self, context)
            ui.ANGELO_OT_new_connection.cancel = counted_cancel
            checks["form_opens_from_suggestion"] = invoke_suggested_form("codex_subscription") == {'RUNNING_MODAL'}
            state["phase"], state["form_shots"] = "form_cancel", 0
            save()
            return .5
        if state["phase"] == "form_cancel":
            capture("suggested-chatgpt-form.png", prefs_window())
            same = state["screenshots"]["suggested-chatgpt-form.png"] == state["screenshots"]["ai-providers-suggestions.png"]
            state["form_shots"] += 1
            if same and not state.get("form_cancels") and state["form_shots"] < 6:
                return .4   # A Preferences screenshot can show an earlier frame while the form is open: shoot again.
            checks["form_stays_open_over_the_page"] = not state.get("form_cancels")
            checks["form_drawn_over_preferences"] = not same
            checks["open_form_adds_nothing"] = nothing_created_or_bound(controller)
            close_dialog(confirm=False)
            state["phase"] = "cancelled"
            save()
            return .5
        if state["phase"] == "cancelled":
            checks["form_close_is_detected"] = state.get("form_cancels") == 1
            checks["cancelled_form_adds_nothing"] = nothing_created_or_bound(controller) and offered() == ["codex_subscription", "claude_subscription"]
            invoke_suggested_form("codex_subscription")
            state["phase"] = "form_confirm"
            save()
            return .5
        if state["phase"] == "form_confirm":
            close_dialog(confirm=True)
            state["phase"] = "confirmed"
            save()
            return .5
        if state["phase"] == "confirmed":
            records = list(saved_store().records.values())
            checks["confirm_creates_exactly_one_reuse_connection"] = (
                len(records) == 1 and records[0]["mode"] == "codex_subscription" and records[0]["reuse_native"] is True
                and records[0]["name"] == "ChatGPT" and len(ui.connections().records) == 1)
            checks["confirm_binds_no_chat"] = controller.state.get("connection_id") is None and controller.provider is None
            checks["confirmed_suggestion_disappears"] = offered() == ["claude_subscription"]
            checks["explicit_connection_is_usable"] = ui.connection_available()
            capture("ai-providers-after-confirm.png", prefs_window())
            state["created"] = records[0]["id"] if records else None
            bpy.ops.angelo.dismiss_native_suggestion(mode="claude_subscription")
            checks["dismissed_suggestion_disappears"] = offered() == []
            checks["dismissal_persisted"] = "claude_subscription" in saved_store().ignored_native_modes
            bpy.ops.angelo.connection_remove('EXEC_DEFAULT', connection_id=state["created"] or "")
            checks["removal_resurrects_nothing"] = not saved_store().records and offered() == []
            deliver_stand_in_status()
            state["phase"] = "redetected"
            save()
            return .5
        if state["phase"] == "redetected":
            if ui._authentication.running:
                return .1
            reloaded = saved_store()
            checks["redetection_creates_and_binds_nothing"] = nothing_created_or_bound(controller) and offered() == []
            checks["dismissals_survive_reload"] = {"codex_subscription", "claude_subscription"} <= reloaded.ignored_native_modes
            checks["no_model_refresh_for_unbound_chat"] = chat_id not in refresh_calls
            checks["same_chat_throughout"] = controller.state["id"] == chat_id
            capture("ai-providers-after-dismiss-and-remove.png", prefs_window())
            checks["no_chat_draw_error"] = chat_view.last_error is None
            checks["scene_unchanged"] = before == [(o.name, tuple(o.location)) for o in bpy.context.scene.objects]
            state["success"] = all(checks.values())
            state["phase"] = "complete"
            save()
            print("ANGELO_NATIVE_SUGGESTIONS_GUI", json.dumps(state), flush=True)
            bpy.ops.wm.quit_blender()
            return None
    except Exception as error:
        state.update(success=False, phase="failed", error=str(error), traceback=traceback.format_exc())
        save()
        print(state["traceback"], flush=True)
        bpy.ops.wm.quit_blender()
        return None


bpy.app.timers.register(tick, first_interval=1)
