# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat follows the team account by itself: sign-in, access reasons, keys arriving, ready on the team.

Real default startup and real clicks (``Window.event_simulate``). The in-process
fake console (``fake_team_console.py``: fixture tokens, nothing leaves
127.0.0.1, the key store is off) plays the console, and a background thread
plays the browser: it completes the sign-in a moment after the click, as a
person's browser does, while the test sends no input and never pumps the UI
itself. Stand-in ``claude``/``codex`` CLIs are on PATH; no account, network or
inference is used.

Run through ``run_native_suites.py --gui --include test_blender_team_chat``;
check ``team-chat.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import threading
import time
import traceback
import urllib.request
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-team-chat-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_provider_cli  # noqa: E402
from fake_team_console import FakeConsole  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')  # This suite needs the default Director startup and its chat.

FAKE = OUT / "fake-cli"
FAKE.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_FAKE_CLI_STATE=str(FAKE), ANGELO_FAKE_CLI_LOGIN_SECONDS="60")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.use_preferences_save = False
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))
REPORT = OUT / "team-chat.json"
console = FakeConsole(seat=False)
state = {"phase": "early", "success": False, "checks": {}, "screenshots": [], "timings": {},
         "fixture": "In-process fake console and a background 'browser'; stand-in CLIs; no input while waiting"}
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def chat():
    from angelo_agent import console as chat_console
    area = next(a for a in window().screen.areas if chat_console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def view():
    from angelo_agent import chat_view
    return chat_view._views.get(chat()[0].as_pointer(), {})


def drawn():
    return {item[0]: item for item in view().get("drawn_options", [])}


def click(action, value=None):
    area, region = chat()
    rect = next(r for r, a, v in view().get("hits", []) if a == action and (value is None or v == value))
    x, y = int(region.x + rect[0] + rect[2] / 2), int(region.y + rect[1] + rect[3] / 2)
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x - 4, y=y)
    for kind, event_value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        window().event_simulate(type=kind, value=event_value, x=x, y=y)


def browser(url):
    """The person's browser: Allow on the console a moment later, then the loopback redirect; any other page
    (a console page an admin is sent to) is only recorded."""
    if "/oauth/authorize" not in url:
        state.setdefault("opened", []).append(url)
        return True
    state["sign_in_url"] = url

    def allow():
        time.sleep(0.6)
        DIRECT.open(console.callback(url), timeout=10).close()
    threading.Thread(target=allow, daemon=True).start()
    return True


def row():
    """(label, note) of the team row as drawn, or None."""
    item = drawn().get("team")
    return (str(item[1]), str(item[2])) if item else None


def refresh_token():
    """The console changed something; the next token answer carries it (as a periodic refresh would)."""
    from angelo_agent import team_account_ui
    account = team_account_ui.account()
    account.access_token(stale=account.access_token())


def focus_returns():
    """The window loses focus to the console and gets it back: the next event re-checks the team."""
    x, y = window().width // 4, window().height // 2
    window().event_simulate(type='WINDOW_DEACTIVATE', value='NOTHING', x=x, y=y)
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x + 1, y=y)
    state["focus_at"] = time.monotonic()


def chat_mode():
    from angelo_agent import ui
    record = ui.connection_record()
    return record["mode"] if record else None


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    return path


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
        try:  # What the chat showed when it stopped, to read the failure without a rerun.
            from angelo_agent import team_account_ui, ui
            controller = ui.get_controller()
            state["at_failure"] = {"chat_mode": chat_mode(), "selection": controller.selection_connection_id,
                                   "ready": [r["mode"] for r in ui.ready_connections()], "drawn": view().get("drawn_options"),
                                   "line": view().get("team_ready_line"), "records": [r["mode"] for r in team_account_ui.records().values()],
                                   "view": repr(team_account_ui.account().view())[:600]}
        except Exception as problem:  # noqa: BLE001
            state["at_failure"] = repr(problem)
    else:
        state.update(phase="complete", success=all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_TEAM_CHAT", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
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
        assert time.monotonic() - started < 240, "Team-chat fixture timed out in " + state["phase"]
        checks, phase = state["checks"], state["phase"]
        from angelo_agent import chat_view, director, team_account_ui, ui
        if phase == "early":
            x, y = window().width // 4, window().height // 2
            window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
            state["phase"] = "boot"
            return .2
        if phase == "boot":
            if not qa_boot.started() or not view().get("hits"):
                return .2
            environ = dict(os.environ, ANGELO_CONSOLE_URL=console.url, ANGELO_GATEWAY_URL="http://127.0.0.1:9")
            assert team_account_ui.start(ui, background=False, environ=environ)
            team_account_ui.open_in_browser = browser
            state["phase"] = "team_row"
            return .2
        if phase == "team_row":
            if not wait(phase, lambda: "team" in drawn()):
                return .1
            capture("team-1-signed-out.png")
            click("welcome_team")
            state["phase"] = "signing_in"
            return .1
        if phase == "signing_in":
            account = team_account_ui.account()
            if account.view().signed_in:  # The background browser finished; from here nothing but timers runs.
                state["timings"]["signed_in"] = time.monotonic()
                state["draws_at_sign_in"] = chat_view.draw_count
                state["phase"] = "reflect"
                return .02
            if drawn().get("team", (0, 0, ""))[2] == "Finish signing in in your browser":
                state["saw_signing_in"] = True
            return .02
        if phase == "reflect":
            team_row = drawn().get("team")
            changed = team_row is None or team_row[2] != "Finish signing in in your browser"
            if not wait(phase, lambda: changed, limit=5):
                return .02
            state["timings"]["reflected"] = time.monotonic()
            delay = state["timings"]["reflected"] - state["timings"]["signed_in"]
            state["timings"]["delay_seconds"] = round(delay, 3)
            checks["chat_showed_signing_in"] = bool(state.get("saw_signing_in"))
            checks["chat_reflects_the_sign_in_within_a_tick"] = delay < 0.5 and chat_view.draw_count > state["draws_at_sign_in"]
            state["phase"] = "member_no_seat"
            return .2
        if phase == "member_no_seat":
            if not wait(phase, lambda: row() == ("No seat in Northwind Studio", "Contact your admin")):
                return .1
            capture("team-2-no-seat-member.png")
            checks["no_seat_names_the_reason_for_a_member"] = True
            console.role = "admin"
            refresh_token()
            state["phase"] = "admin_no_seat"
            return .2
        if phase == "admin_no_seat":
            if not wait(phase, lambda: row() == ("No seat in Northwind Studio", "Assign yourself a seat in People")):
                return .1
            capture("team-3-no-seat-admin.png")
            click("welcome_team")
            state["phase"] = "admin_people"
            return .3
        if phase == "admin_people":
            if not wait(phase, lambda: state.get("opened")):
                return .1
            checks["an_admin_is_sent_to_people"] = state["opened"][-1].endswith("/people") and "/orgs/" in state["opened"][-1]
            console.seat, console.providers = True, []
            refresh_token()
            state["phase"] = "no_keys"
            return .2
        if phase == "no_keys":
            if not wait(phase, lambda: row() == ("Northwind Studio has no AI provider keys yet", "Add keys in the console → Keys")):
                return .1
            capture("team-4-no-keys-admin.png")
            checks["no_keys_names_the_team_and_the_page"] = True
            from angelo_agent import team_chat
            team_chat.RECHECK_AFTER = 0.0  # A fixture can't wait the 30 s a person never notices.
            console.providers = ["google", "tripo"]
            focus_returns()
            state["phase"] = "no_coding"
            return .2
        if phase == "no_coding":
            if not wait(phase, lambda: row() and row()[0] == "No coding provider (Claude or OpenAI) is set up for this team"):
                return .1
            capture("team-5-no-coding-admin.png")
            checks["focus_rechecks_and_names_no_coding_provider"] = row()[1] == "Add a Claude or OpenAI key in the console → Keys"
            with bpy.context.temp_override(window=window()):
                bpy.ops.angelo.settings()  # AI Providers gives the same reason, with the console page.
            state["phase"] = "providers_page"
            return .6
        if phase == "providers_page":
            prefs = next((w for w in bpy.context.window_manager.windows
                          if any(a.type == 'PREFERENCES' for a in w.screen.areas)), None)
            if not wait(phase, lambda: prefs is not None):
                return .2
            path = OUT / "team-5b-ai-providers-no-coding.png"
            with bpy.context.temp_override(window=prefs):
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
                bpy.ops.screen.screenshot(filepath=str(path))
                bpy.ops.wm.window_close()
            state["screenshots"].append(str(path))
            from angelo_agent import team_chat, team_access
            checks["ai_providers_shares_the_reason"] = team_chat.access()["state"] == team_access.NO_CODING
            console.providers = ["anthropic", "openai"]  # The keys are added in the console while Angelo is open.
            focus_returns()
            state["phase"] = "ready_both"
            return .1
        if phase == "ready_both":
            if not wait(phase, lambda: chat_mode() == "claude_team" and view().get("team_ready_line")):
                return .1
            state["timings"]["keys_to_ready_seconds"] = round(time.monotonic() - state["focus_at"], 2)
            capture("team-6-ready-both.png")
            checks["keys_arriving_flip_the_chat_to_ready_within_a_recheck"] = (
                ui.connection_available() and "team" not in drawn() and state["timings"]["keys_to_ready_seconds"] < 5)
            checks["both_start_on_claude_code_and_the_picker_offers_both"] = (
                view()["team_ready_line"] == "Signed in to Northwind Studio. Your team's Claude and OpenAI keys are ready."
                and {"claude_team", "codex_team"} <= {r["mode"] for r in ui.ready_connections()})
            console.providers = ["openai"]
            focus_returns()
            state["phase"] = "ready_openai"
            return .1
        if phase == "ready_openai":
            if not wait(phase, lambda: chat_mode() == "codex_team" and (view().get("team_ready_line") or "").endswith("OpenAI key is ready.")):
                return .1
            capture("team-7-ready-openai.png")
            checks["openai_only_moves_the_chat_to_the_team_openai"] = ui.connection_available()
            console.providers = ["anthropic"]
            focus_returns()
            state["phase"] = "ready_claude"
            return .1
        if phase == "ready_claude":
            if not wait(phase, lambda: chat_mode() == "claude_team" and (view().get("team_ready_line") or "").endswith("Claude key is ready.")):
                return .1
            capture("team-8-ready-claude.png")
            checks["claude_only_moves_the_chat_to_the_team_claude"] = (
                ui.connection_available() and ("settings", None) in [(a, v) for _, a, v in view().get("hits", [])])
            with bpy.context.temp_override(window=window()):
                bpy.ops.angelo.new_chat()
            state["phase"] = "new_chat"
            return .3
        if phase == "new_chat":
            if not wait(phase, lambda: chat_mode() == "claude_team" and view().get("team_ready_line") and len(ui._controllers) > 1):
                return .1
            checks["a_new_chat_starts_ready_on_the_team"] = ui.connection_available()
            from angelo_agent import managed_policy
            managed_policy.reset(managed_policy.combine([("QA profile", {"AllowPersonalConnections": False})]))
            from angelo_agent import console as chat_console
            chat_console.redraw_chats(ui._controllers.values())
            state["phase"] = "policy_ready"
            return .3
        if phase == "policy_ready":
            capture("team-9-ready-personal-off.png")
            checks["personal_options_hidden_when_the_policy_forbids_them"] = (
                bool(view().get("team_ready_line")) and ("settings", None) not in [(a, v) for _, a, v in view().get("hits", [])])
            console.providers = []
            focus_returns()
            state["phase"] = "policy_blocked"
            return .2
        if phase == "policy_blocked":
            if not wait(phase, lambda: list(drawn()) == ["team"]):
                return .1
            capture("team-10-not-ready-personal-off.png")
            checks["team_only_policy_offers_only_the_team_row"] = row()[0] == "Northwind Studio has no AI provider keys yet"
            checks["no_chat_draw_error"] = chat_view.last_error is None
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
