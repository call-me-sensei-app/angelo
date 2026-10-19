# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The team account in the real app: status-bar item, its menu, AI Providers and the managed policy.

Signs in through the app's own loopback listener against the in-process fake
console (fake_team_console.py: tokens are fixture strings, nothing leaves
127.0.0.1, the key store is off), then checks and captures:

1. the status-bar item right of the version, signed in ("<team> · <name>");
2. its menu: account, team, seat, usage numbers, console pages, AI Providers,
   Sign Out;
2b. agent usage reports: a completed request on ``claude_team`` through the
   chat controller's own hook is reported to the console as one task;
3. AI Providers with the team section and its connections;
4. a managed policy that allows only team connections, with no seat: the
   item warns, AI Providers says "Contact your admin for access" and lists no
   personal connection, and the chat's setup line says the same;
5. sign-out: the item offers Sign in again.

Screenshots and ``team-account.json`` go to ANGELO_QA_DIR.
"""

import json
import os
import sys
import tempfile
import time
import traceback
import urllib.request
from pathlib import Path

import addon_utils
import bpy

assert "--factory-startup" in sys.argv and not bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-team-account-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_team_console import FakeConsole  # noqa: E402
import qa_waits  # noqa: E402

addon_utils.enable("angelo_agent", default_set=False, persistent=False)
from angelo_agent import chat_view, managed_policy, team_account_ui, team_status_bar, ui  # noqa: E402

bpy.context.preferences.view.show_splash = False
console = FakeConsole()
REVOKE_WAIT_S = 10   # x the runner's timeout scale
revoke_deadline = [0.0]
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
state = {"phase": "boot", "success": False, "checks": {}, "fixture": "In-process fake console; no network, no key store"}


def save():
    (OUT / "team-account.json").write_text(json.dumps(state, indent=2))


def capture(file, window):
    with bpy.context.temp_override(window=window):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / file))


def status_text():
    account = team_account_ui.account()
    return team_status_bar.status_item(account.view(), managed_policy.current())


def menu_lines():
    """Labels and operators the account menu draws now."""
    lines = []

    class Layout:
        def label(self, text="", **kwargs):
            lines.append(("label", text))

        def operator(self, idname, text="", **kwargs):
            lines.append(("operator", idname, text))
            return type("Props", (), {})()

        def separator(self):
            pass
    team_status_bar.ANGELO_MT_team_account.draw(type("Menu", (), {"layout": Layout()})(), bpy.context)
    return lines


def prefs_window():
    return next((w for w in bpy.context.window_manager.windows
                 if any(a.type == 'PREFERENCES' for a in w.screen.areas)), None)


def tick():
    try:
        window = bpy.context.window_manager.windows[0]
        ui.pump()
        account = team_account_ui.account()
        if state["phase"] == "boot":
            environ = dict(os.environ, ANGELO_CONSOLE_URL=console.url, ANGELO_GATEWAY_URL="http://127.0.0.1:9")
            assert team_account_ui.start(ui, background=False, environ=environ)
            account = team_account_ui.account()
            state["checks"]["signed_out_item_offers_sign_in"] = status_text() == ("Sign in", "USER")
            url = account.begin_sign_in(device="Angelo QA")
            state["checks"]["signing_in_item"] = status_text() == ("Signing in…", "TIME")
            DIRECT.open(console.callback(url), timeout=10).close()
            state["phase"] = "signing_in"
            return .2
        if state["phase"] == "signing_in":
            view = account.view()
            if not (view.signed_in and view.providers is not None and view.usage):
                account.tick(idle=True)
                return .2
            state["checks"]["signed_in_item_names_team_and_person"] = status_text() == ("Northwind Studio · mika",
                                                                                        'COMMUNITY')
            lines = menu_lines()
            labels = [line[1] for line in lines if line[0] == "label"]
            operators = [line[1] for line in lines if line[0] == "operator"]
            state["checks"]["menu_shows_account_team_seat_usage"] = (
                "mika@northwind.example" in labels and "Northwind Studio" in labels and "Seat: yes" in labels
                and any(label.startswith("This period: 1.2M tokens · 42 agent tasks") for label in labels))
            state["checks"]["menu_actions"] = operators == ["angelo.team_open_console"] * 3 + [
                "angelo.settings", "angelo.team_sign_out"]
            state["menu"] = labels
            # A completed team request through the chat controller's own receipt path, then a report.
            from angelo_agent.telemetry import RequestMetrics
            controller = ui.get_controller()
            controller._request_metrics = RequestMetrics("claude")
            controller._request_metrics.set_route(mode="claude_team", requested_model="claude-opus-5-5")
            controller._finish_metrics("completed")
            account._enqueue("usage_report")  # The 15-minute report, now.
            state["phase"], state["usage_sent_at"] = "usage_report", time.monotonic()
            return .2
        if state["phase"] == "usage_report":
            if not console.reports.reports():
                assert time.monotonic() - state["usage_sent_at"] < 30, "the usage report never reached the console"
                return .2
            body, = console.reports.reports()
            entry, = body["entries"]
            state["checks"]["completed_team_task_reported_once"] = (
                (entry["provider"], entry["model"], entry["agentTasks"]) == ("anthropic", "claude-opus-5-5", 1)
                and set(body) == {"batchId", "entries"})
            usage_file = Path(ui.connection_directory(), "team-usage.json")
            text = usage_file.read_text() if usage_file.exists() else ""
            state["checks"]["usage_buffer_keeps_no_batch_or_token"] = (
                '"batch":null' in text and account._access not in text and '"counts":[]' in text)
            team_account_ui.redraw_status_bars()
            state["phase"] = "status_capture"
            return .4
        if state["phase"] == "status_capture":
            capture("1-status-bar-signed-in.png", window)
            with bpy.context.temp_override(window=window):
                bpy.ops.wm.call_menu('INVOKE_DEFAULT', name="ANGELO_MT_team_account")
            state["phase"] = "menu"
            return .4
        if state["phase"] == "menu":
            capture("2-account-menu.png", window)
            with bpy.context.temp_override(window=window):
                bpy.ops.angelo.settings()
            state["phase"] = "providers"
            return .5
        if state["phase"] == "providers":
            prefs = prefs_window()
            capture("3-ai-providers-team.png", prefs)
            state["checks"]["team_connections_listed"] = {r["mode"] for r in team_account_ui.records().values()} >= {
                "claude_team", "codex_team", "openai_images_team"}
            # The organization's IT department: team connections only, a contact; this person has no seat.
            managed_policy.reset(managed_policy.combine([("QA profile", {
                "AllowPersonalConnections": False, "AdminContact": "it-help@northwind.example"})]))
            console.seat = False
            ui.connections().records.clear()
            account.access_token(stale=account.access_token())  # The next token says seat: false.
            state["phase"] = "policy"
            return .3
        if state["phase"] == "policy":
            view = account.view()
            if view.seat:
                return .2
            ui.pump()
            team_account_ui.redraw_status_bars()
            state["checks"]["no_seat_item_warns"] = status_text() == ("Northwind Studio · mika", 'ERROR')
            labels = [line[1] for line in menu_lines() if line[0] == "label"]
            state["checks"]["menu_says_contact_admin"] = "Contact your admin for access: it-help@northwind.example" in labels
            row = team_account_ui.welcome_row()
            # The reason and, for a member, the admin contact from the policy (team_access).
            state["checks"]["chat_card_team_row_says_no_seat_and_whom_to_ask"] = bool(row) and (
                row["label"] == "No seat in Northwind Studio" and row["note"] == "Contact your admin: it-help@northwind.example")
            from angelo_agent import chat_welcome
            state["checks"]["chat_card_offers_only_the_team"] = [o["id"] for o in chat_welcome._options(ui)] == ["team"]
            state["checks"]["personal_connections_cannot_be_added"] = not _add_personal()
            for area in prefs_window().screen.areas:
                area.tag_redraw()
            state["phase"] = "policy_capture"
            return .4
        if state["phase"] == "policy_capture":
            capture("4-ai-providers-managed-no-seat.png", prefs_window())
            capture("5-status-bar-no-seat.png", window)
            account.sign_out()
            revoke_deadline[:] = [time.monotonic() + REVOKE_WAIT_S * qa_waits.timeout_scale()]
            state["phase"] = "signed_out"
            return .3
        if state["phase"] == "signed_out":
            # The revocation is the account worker thread's request to the console: wait for it, bounded, rather
            # than a fixed .3 s (a loaded machine, or a window suite's busy main loop, can miss it).
            if not console.revoked and time.monotonic() < revoke_deadline[0]:
                return .1
            ui.pump()
            state["checks"]["signed_out_item_warns_in_a_team_only_policy"] = status_text() == ("Sign in", 'ERROR')
            state["checks"]["revoked_on_the_console"] = bool(console.revoked)
            state["phase"] = "signed_out_capture"
            return .4
        if state["phase"] == "signed_out_capture":
            capture("6-status-bar-signed-out.png", window)
            from angelo_agent import providers_page_host as page
            drawn = page.states_drawn[-1] if page.states_drawn else {}
            # The AI Providers page lists the organization's locks in its Attention banner, team only, no personal sections.
            state["checks"]["page_attention_lists_the_policy"] = (
                "Some settings are managed by your organization." in drawn.get("attention", ())
                and "Coding agents" not in drawn.get("sections", ()) and not drawn.get("add"))
            capture("4b-ai-providers-policy-signed-out.png", prefs_window())
            state["checks"]["no_chat_draw_error"] = chat_view.last_error is None
            state["success"] = all(state["checks"].values())
            state["phase"] = "complete"
            save()
            print("ANGELO_TEAM_ACCOUNT_GUI", json.dumps(state), flush=True)
            bpy.ops.wm.quit_blender()
            return None
        return .2
    except Exception as error:
        state.update(success=False, phase="failed", error=str(error), traceback=traceback.format_exc())
        save()
        print(state["traceback"], flush=True)
        bpy.ops.wm.quit_blender()
        return None


def _add_personal():
    try:
        ui.connections().add("ANTHROPIC", "API_KEY")
    except ValueError:
        return False
    return True


bpy.app.timers.register(tick, first_interval=1)
