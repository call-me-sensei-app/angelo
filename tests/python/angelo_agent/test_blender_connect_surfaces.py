# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every surface that offers a one-click Connect offers every available subscription, with the same words.

The state a person reported: Claude Code installed but signed out, Codex's
ChatGPT sign-in present. Stand-in ``claude``/``codex`` CLIs answer the real
start-up status checks that way (``fake_provider_cli``: a marker file in the
suite's own HOME); no real CLI, browser, account, network or inference is used.

Walked and captured: the showcase beside the chat's welcome card, the
first-launch welcome screen, the AI Providers page, its Add-connection form
(OpenAI, then Anthropic: *Use existing sign-in* ticked only for the ChatGPT
sign-in that was found, the browser note for Claude) and a Connect Claude click on the page, which runs the
stand-in browser sign-in and ends with exactly one Claude connection while the
page still offers ChatGPT. The add-on is the app's own (nothing is imported
from this checkout), so the same suite shows an older app's behaviour.

Run through ``run_native_suites.py --gui --include test_blender_connect_surfaces``;
check ``connect-surfaces.json`` for ``success`` and ``phase == "complete"``.
"""

import hashlib
import json
import os
import pwd
import sys
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-connect-surfaces-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_provider_cli  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

assert Path(os.environ["HOME"]).resolve() != Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(), \
    "Run through run_native_suites.py: this suite marks a stand-in sign-in in HOME"
product_env.clear('DISABLE_DEFAULT_CHAT')  # The default Director startup and its chat.
product_env.clear('DISABLE_HERO')  # ...and the showcase beside the welcome card.
FAKE = OUT / "fake-cli"
FAKE.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_FAKE_CLI_STATE=str(FAKE), ANGELO_FAKE_CLI_LOGIN_SECONDS="1")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
fake_provider_cli.sign_in(os.environ["HOME"], "codex")  # ChatGPT signed in; Claude Code installed, signed out.
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "connect-surfaces.json"
CLAUDE, CHATGPT = "claude_subscription", "codex_subscription"
EXPECTED = {CLAUDE: "Not connected", CHATGPT: "Connected to ChatGPT"}
state = {"phase": "early", "success": False, "checks": {}, "observed": {}, "screenshots": [],
         "fixture": "Stand-in claude/codex CLIs (ChatGPT signed in, Claude signed out); no browser, account or inference"}
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def chat_hits():
    from angelo_agent import chat_view, console
    area = next(a for a in window().screen.areas if console.is_chat(a))
    return chat_view._views.get(area.as_pointer(), {}).get("hits", [])


def prefs():
    window_ = next((w for w in bpy.context.window_manager.windows if any(a.type == 'PREFERENCES' for a in w.screen.areas)), None)
    if window_ is None:
        return None, None, None
    area = next(a for a in window_.screen.areas if a.type == 'PREFERENCES')
    return window_, area, next(r for r in area.regions if r.type == 'WINDOW')


def page_plan():
    from angelo_agent import providers_page_host as page
    _, _, region = prefs()
    return page.plan_for(region) if region else None


def page_words(plan):
    """The page's drawn text and button labels, and its Connect buttons."""
    texts = [op[1] for op in plan.ops if op[0] == "text"]
    buttons = [op[2] for op in plan.ops if op[0] == "pbutton"]
    connect = [value for _, action, value in plan.hits if action == "quick_connect"]
    return texts, buttons, connect


def capture(name, target=None):
    path = OUT / name
    with bpy.context.temp_override(window=target or window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def scroll_to_rows(target, area, region):
    """Bring the Get started rows into the small Preferences window (one wheel step is 40 pixels)."""
    with bpy.context.temp_override(window=target, area=area, region=region):
        bpy.ops.view2d.scroll_up(page=True)
        for _ in range(9):
            bpy.ops.view2d.scroll_down()


def fresh_shot(name, target):
    """Capture the Preferences window; False until it shows a new frame (a new or changed window presents late)."""
    capture(name, target)
    data = (OUT / name).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    tries = state.setdefault("tries", {})
    tries[name] = tries.get(name, 0) + 1
    if (len(data) < 30000 or digest == state.get("prefs_digest")) and tries[name] < 15:
        return False
    state["prefs_digest"] = digest
    return True


def stable_shot(name, target):
    """Capture until two shots in a row match: the page has settled and the screenshot is not an older frame."""
    capture(name, target)
    digest = hashlib.sha256((OUT / name).read_bytes()).hexdigest()
    shots = state.setdefault("stable", {})
    previous, count = shots.get(name, (None, 0))
    shots[name] = (digest, count + 1)
    return digest == previous or count + 1 >= 15


def records():
    from angelo_agent import ui
    return [(r["mode"], r["reuse_native"]) for r in ui.connections().records.values()]


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_CONNECT_SURFACES", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=40):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + phase
    return False


def tick():
    try:
        assert time.monotonic() - started < 280, "Connect-surfaces fixture timed out in " + state["phase"]
        checks, seen, phase = state["checks"], state["observed"], state["phase"]
        from angelo_agent import connect_hero, director, first_run, intro, ui
        from angelo_agent import providers_page_host as page
        if phase == "early":
            window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=window().width // 4, y=window().height // 2)
            state["phase"] = "boot"
            return .2
        if phase == "boot":
            if director._timer is not None or ui._layout_timer is not None:
                return .2
            statuses = ui._authentication.status
            if not wait(phase, lambda: all(statuses.get(m) == s for m, s in EXPECTED.items())
                        and not ui._authentication.running and connect_hero.is_shown()
                        and any(a == "welcome_connect" for _, a, _ in chat_hits())):
                return .2
            capture("a1-showcase-and-chat-card.png")
            chat = [v for _, a, v in chat_hits() if a == "welcome_connect"]
            seen["chat_connect"] = chat
            seen["chat_notes"] = [r["note"] for r in first_run.connect_options(statuses) if r["action"] == "welcome_connect"]
            checks["status_checks_report_this_macs_state"] = all(statuses.get(m) == s for m, s in EXPECTED.items())
            checks["chat_offers_claude_and_chatgpt"] = chat == [CLAUDE, CHATGPT]
            with bpy.context.temp_override(window=window()):
                bpy.ops.angelo.intro('INVOKE_DEFAULT')
            state["phase"] = "welcome"
            return .4
        if phase == "welcome":
            if not wait(phase, lambda: "plan" in intro._open):
                return .2
            capture("a2-welcome-screen.png")
            seen["welcome_actions"] = sorted({a for _, a, _ in intro._open["plan"]["hits"]})
            checks["welcome_screen_offers_no_one_sided_connect"] = not any("connect" in a for a in seen["welcome_actions"])
            x, y = window().width // 4, window().height // 2  # Over the 3D view, where the welcome takes its keys.
            window().event_simulate(type='ESC', value='PRESS', x=x, y=y)
            window().event_simulate(type='ESC', value='RELEASE', x=x, y=y)
            state["phase"] = "providers"
            return .4
        if phase == "providers":
            if not wait(phase, lambda: not intro.is_open()):
                return .1
            with bpy.context.temp_override(window=window()):
                bpy.ops.angelo.settings()
            state["phase"] = "page"
            return .5
        if phase == "page":
            from angelo_agent import providers_page_model
            target, _, _ = prefs()
            if not wait(phase, lambda: page_plan() is not None and providers_page_model.ART["done"]
                        and target.as_pointer() in page._modals):
                return .2
            if not fresh_shot("a3-ai-providers.png", target):
                return .4
            texts, buttons, connect = page_words(page_plan())
            seen.update(page_connect=connect, page_buttons=buttons)
            joined = " ".join(" ".join(texts).split())  # Wrapped lines, read as the sentences they are.
            checks["page_offers_what_the_chat_offers"] = connect == seen["chat_connect"]
            checks["page_uses_the_chats_connect_labels"] = {"Connect Claude", "Connect ChatGPT"} <= set(buttons)
            checks["page_says_browser_or_found_like_the_chat"] = all(note in joined for note in seen["chat_notes"])
            checks["drawing_the_page_creates_nothing"] = records() == []
            _, area, region = prefs()
            scroll_to_rows(target, area, region)
            state["phase"] = "page_rows"
            return .4
        if phase == "page_rows":
            target, area, region = prefs()
            if not fresh_shot("a3b-ai-providers-get-started.png", target):
                return .4
            with bpy.context.temp_override(window=target, area=area, region=region):
                bpy.ops.view2d.scroll_up(deltay=4000)
            page.act("add", "CODING", target, area, region)
            state["phase"] = "sheet"
            return .5
        if phase == "sheet":
            if not wait(phase, lambda: page._sheet is not None and page._sheet_plan is not None):
                return .2
            if not fresh_shot("a4-add-connection-openai.png", prefs()[0]):
                return .4
            sheet = page._sheet
            from angelo_agent.provider_registry import methods
            seen["sheet_providers"] = [p for p, _ in sheet._providers()]
            seen["sheet_methods"] = {p: list(methods("CODING", p)) for p in ("OPENAI", "ANTHROPIC")}
            checks["form_offers_a_subscription_for_both"] = all(
                "SUBSCRIPTION" in m for m in seen["sheet_methods"].values()) and {"OPENAI", "ANTHROPIC"} <= set(seen["sheet_providers"])
            texts = " ".join(" ".join(op[1] for op in page._sheet_plan.ops if op[0] == "text").split())
            seen["sheet_openai"] = {"reuse": sheet.reuse, "texts": texts}
            # ChatGPT's sign-in is found here: the form starts ticked and says it reuses it.
            checks["form_reuses_a_found_sign_in"] = (sheet.provider == "OPENAI" and sheet.reuse
                                                     and "Uses the ChatGPT sign-in already on this computer" in texts)
            sheet.act("provider", "ANTHROPIC", page._SheetHost(sheet))
            state["phase"] = "sheet_anthropic"
            return .5
        if phase == "sheet_anthropic":
            if not fresh_shot("a5-add-connection-anthropic.png", prefs()[0]):
                return .4
            labels = [op[2] for op in page._sheet_plan.ops if op[0] == "button"] if page._sheet_plan else []
            texts = [op[1] for op in page._sheet_plan.ops if op[0] == "text"] if page._sheet_plan else []
            seen["sheet_anthropic"] = {"method": page._sheet.method, "buttons": labels, "texts": texts,
                                       "reuse": page._sheet.reuse}
            checks["form_offers_claude_by_subscription"] = page._sheet.method == "SUBSCRIPTION"
            # Claude Code is signed out: nothing to reuse, so the box starts unticked and Connect opens the browser.
            checks["form_does_not_offer_a_missing_sign_in"] = (page._sheet.reuse is False and "Opens Claude sign-in in your browser"
                                                               in " ".join(" ".join(texts).split()))
            page.close_sheet("cancel")
            state["phase"] = "page_connect"
            return .4
        if phase == "page_connect":
            if CLAUDE not in seen.get("page_connect", ()):
                checks["page_connect_claude_connects"] = False  # Nothing to click: the page offers no Connect Claude.
                finish()
                return None
            target, area, region = prefs()
            scroll_to_rows(target, area, region)
            page.act("quick_connect", CLAUDE, target, area, region)
            state["phase"], state["connect_started"] = "connecting", time.monotonic()
            return .2
        if phase == "connecting":
            from angelo_agent import quick_connect
            item = quick_connect.flow(CLAUDE)
            if item and item["phase"] == "signing_in" and "signing_in_chips" not in seen:
                seen["signing_in_chips"] = list(page.states_drawn[-1].get("chips", ())) if page.states_drawn else []
                capture("a6-ai-providers-signing-in.png", prefs()[0])
            if not wait(phase, lambda: ui.connection_available() and quick_connect.flow(CLAUDE) is None and bool(
                    page.states_drawn) and "Available in chat" in page.states_drawn[-1].get("chips", ())):
                return .2
            state["phase"] = "connected"
            return .6
        if phase == "connected":
            if not stable_shot("a7-ai-providers-after-connect-claude.png", prefs()[0]):
                return .5
            _, _, connect = page_words(page_plan())
            seen.update(after_records=records(), after_connect=connect)
            seen["after_chips"] = list(page.states_drawn[-1].get("chips", ())) if page.states_drawn else []
            controller = ui.get_controller()
            record = ui.connections().records.get(controller.state.get("connection_id") or "")
            checks["page_connect_claude_connects"] = records() == [(CLAUDE, False)] and bool(record) and record["mode"] == CLAUDE
            checks["page_still_offers_chatgpt"] = connect == [CHATGPT]
            checks["claude_ready_in_chat_on_the_page"] = "Available in chat" in seen["after_chips"]
            default = str(Path(os.environ["HOME"]) / ".claude")
            logins = [c["profile"] for c in fake_calls() if c["tool"] == "claude" and c["arguments"][:2] == ["auth", "login"]]
            seen["claude_logins"] = logins
            checks["signed_out_claude_signs_in_a_private_profile"] = len(logins) == 1 and logins[0] != default
            finish()
            return None
        return .2
    except Exception as error:  # noqa: BLE001  (the receipt records it)
        finish(error)
        return None


def fake_calls():
    path = FAKE / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


bpy.app.timers.register(tick, first_interval=1.0, persistent=True)
