# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A goal held across turns in the running app: the + menu row, the chip, the card and the rounds Angelo sends.

doc/angelo/goal_mode.rst. Real default startup (Director Mode with its chat), a real ChatGPT connection over the
stand-in ``codex`` app-server (``fake_provider_cli.py``: each turn answers "Done." and changes nothing), the real
controller, goal loop, chat canvas and synthetic input on the canvas's own hit geometry. No browser, account, network
or inference.

Covered, each with a screenshot:

1. The + menu's Goal row puts a pending chip in the composer; a click on the chip opens the card, whose segments set
   the check (Agent judges) and the rounds without progress (2).
2. Send starts the goal with that message (the objective). The turn ends; Angelo sends the next round itself: its
   request is the host's continuation ("From Angelo, not the person", the objective quoted), drawn as Angelo's row,
   never as the person's words. Two rounds that change nothing end the goal ("stalled"); no third request is sent.
   The ended card lists the rounds; Dismiss removes the chip.
3. A draft holds a due round: the chip says it waits for the message being written; the chip's Stop goal ends it and
   nothing more is sent, even once the draft is gone.
4. The pending chip and its card in Japanese, German, Arabic, Thai and Simplified Chinese: translated, never the
   English text (a build with WITH_INTERNATIONAL and the compiled catalogues).

Run through ``run_native_suites.py --gui --include test_blender_goal_gui``; check ``goal-gui.json`` for ``success``
and ``phase == "complete"``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-goal-gui-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_provider_cli  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_plus_menu  # noqa: E402  (the + menu's pages, read from the chat's canvas state)

product_env.clear('DISABLE_DEFAULT_CHAT')  # The default Director startup and its chat.
fake_provider_cli.sign_in(os.environ["HOME"], "codex")
os.environ.update(ANGELO_FAKE_CLI_STATE=str(OUT / "fake-cli"), ANGELO_FAKE_CLI_LOGIN_SECONDS="0.5",
                  ANGELO_FAKE_CLI_CODEX_TURNS=",".join(["ok"] * 12))
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "goal-gui.json"
OBJECTIVE = "Keep refining the cube until it looks finished."
SECOND = "Keep adding detail to the cube."
DRAFT = "one more thing"
LANGUAGES = ("ja_JP", "de_DE", "ar_EG", "th_TH", "zh_HANS")
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "timings": {}, "languages": {},
         "fixture": "Stand-in codex app-server, synthetic input; no browser, account, network or inference"}
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


def controller():
    from angelo_agent import ui
    return ui.get_controller()


def actions():
    return [(action, value) for _, action, value in view_state().get("hits", [])]


def _press(rect):
    area, region = chat()
    x, y = int(region.x + rect[0] + rect[2] / 2), int(region.y + rect[1] + rect[3] / 2)
    for kind, event_value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        window().event_simulate(type=kind, value=event_value, x=x, y=y)


def click(action, value=None, popup=False):
    hits = view_state().get("popup_hits" if popup else "hits", [])
    rect = next(r for r, a, v in hits if a == action and (value is None or v == value))
    _press(rect)


def key(kind):
    area, region = chat()
    x, y = region.x + region.width // 2, region.y + 60
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    window().event_simulate(type=kind, value='PRESS', x=x, y=y)
    window().event_simulate(type=kind, value='RELEASE', x=x, y=y)


def popup_key(kind):
    """Press and release ``kind`` with the pointer over the open popup, as a person's key press with it open."""
    area, region = chat()
    x, y, w, h = view_state().get("popup_rect") or (0, 0, region.width, region.height)
    px, py = int(region.x + x + w / 2), int(region.y + y + h / 2)
    for value in ('PRESS', 'RELEASE'):
        window().event_simulate(type=kind, value=value, x=px, y=py)


def draft(text):
    chat()[0].spaces.active.history[-1].body = text


def send(text):
    draft(text)
    key('RET')
    state["sent_at"] = time.time()


def requests():
    path = OUT / "fake-cli" / "codex-requests.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


def turn_starts():
    return [r for r in requests() if r["method"] == "turn/start"]


def first_text(request):
    parts = request.get("input") or []
    return next((p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"), "")


def idle():
    c = controller()
    return not c.busy and not (c._worker and c._worker.is_alive())


def goal():
    from angelo_agent import goal_loop
    return goal_loop.goal_of(controller())


def chip():
    from angelo_agent import chat_goal
    return chat_goal.chip_parts(controller())


def popover_kind():
    return (view_state().get("popover") or {}).get("kind")


def capture(name):
    path = OUT / name
    chat()[0].tag_redraw()
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
    print("ANGELO_GOAL_GUI", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(condition, limit=40):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + state["phase"]
    return False


def choose_goal():
    """The + button, then the Goal row, as a person clicks them. True once the row was clicked."""
    view = view_state()
    if not qa_plus_menu.is_open(view):
        click("attach")   # The chat bar's + button.
        return False
    kind, rect = qa_plus_menu.step(view, "popup_add_goal")
    assert kind != "missing", qa_plus_menu.miss(view, "popup_add_goal")
    if kind == "click":
        _press(rect)
        return True
    if kind in ("open", "back", "click"):
        _press(rect)
    return False


def tick():
    try:
        assert time.monotonic() - started < 300, "Goal GUI fixture timed out in " + state["phase"]
        from angelo_agent import director, goal_controls, goal_loop, goal_state as G, goal_text, quick_connect, ui
        checks, phase, c = state["checks"], state["phase"], controller()
        if phase == "boot":
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            if not wait(lambda: not ui._authentication.running and ("welcome_connect", "codex_subscription") in actions()):
                return .2
            click("welcome_connect", "codex_subscription")
            state["phase"] = "connected"
            return .3
        if phase == "connected":
            if not wait(lambda: quick_connect.flow("codex_subscription") is None and ui.connection_available()
                        and c.selection_provider == "codex_subscription" and c.selection_models):
                return .2
            state["objects"] = sorted(o.name for o in bpy.data.objects)
            state["phase"] = "menu"
            return .2
        if phase in ("menu", "menu_again"):
            if not wait(choose_goal, limit=20):
                return .3
            state["phase"] = "pending" if phase == "menu" else "pending_again"
            return .3
        if phase == "pending":
            if not wait(lambda: goal_loop.pending_goal(c) is not None and ("goal", None) in actions()):
                return .1
            parts = chip()
            state["pending_chip"] = parts
            checks["menu_row_puts_a_pending_chip_in_the_composer"] = goal() is None and parts is not None
            checks["pending_chip_offers_stop_or_remove"] = parts["close"] in ("discard", "stop")
            capture("goal-1-pending-chip.png")
            click("goal")
            state["phase"] = "card"
            return .3
        if phase == "card":
            if not wait(lambda: popover_kind() == "goal" and any(
                    a == "popup_goal_set" for _, a, _ in view_state().get("popup_hits", []))):
                return .1
            capture("goal-2-card-pending.png")
            click("popup_goal_set", ("check", G.JUDGED), popup=True)
            state["phase"] = "card_stall"
            return .3
        if phase == "card_stall":
            if not wait(lambda: goal_loop.pending_goal(c)["check"] == G.JUDGED):
                return .1
            click("popup_goal_set", ("stall_rounds", 2), popup=True)
            state["phase"] = "card_set"
            return .3
        if phase == "card_set":
            if not wait(lambda: goal_loop.pending_goal(c)["budget"]["stall_rounds"] == 2):
                return .1
            checks["card_segments_set_the_check_and_the_stall_limit"] = True
            capture("goal-3-card-set.png")
            popup_key('ESC')
            state["phase"] = "card_closed"
            return .3
        if phase == "card_closed":
            if not wait(lambda: popover_kind() != "goal", limit=5):
                return .1
            send(OBJECTIVE)
            state["phase"] = "rounds"
            return .2
        if phase == "rounds":
            held = goal()
            if held is not None and G.running(held) and len(turn_starts()) == 2 and "round_shot" not in state:
                state["round_shot"] = True
                capture("goal-4-automatic-round.png")
            if not wait(lambda: held is not None and G.ended(held) and idle(), limit=60):
                return .1
            state["ended_at"], state["goal"] = time.monotonic(), held
            state["phase"] = "settle"
            return 3.0   # Long enough for a stray round to be sent: none may be.
        if phase == "settle":
            held, starts = goal(), turn_starts()
            state["turn_inputs"] = [first_text(r)[:300] for r in starts]
            checks["send_starts_the_goal_with_its_message"] = held["objective"] == OBJECTIVE and \
                held["origin"] == G.CHAT_BAR
            checks["two_unchanged_rounds_end_it_stalled"] = held["status"] == G.STALLED and len(held["rounds"]) == 2
            checks["exactly_one_automatic_round_was_sent"] = len(starts) == 2
            checks["the_person_request_is_their_words"] = first_text(starts[0]).startswith(OBJECTIVE)
            checks["the_round_is_the_hosts_continuation"] = len(starts) > 1 and first_text(starts[1]).startswith(
                "From Angelo, not the person") and json.dumps(OBJECTIVE)[1:-1] in first_text(starts[1])
            rows = c.state["messages"]
            kinds = [m.get("request_kind") for m in rows if m.get("role") == "user"]
            checks["the_round_row_is_angelos_not_the_persons"] = kinds.count(goal_loop.REQUEST_KIND) == 1 and \
                kinds.count(None) + kinds.count("") == 1
            checks["scene_unchanged"] = sorted(o.name for o in bpy.data.objects) == state["objects"]
            parts = chip()
            state["ended_chip"] = parts
            checks["ended_chip_offers_dismiss"] = parts is not None and parts["close"] == "dismiss"
            capture("goal-5-ended-stalled.png")
            click("goal")
            state["phase"] = "ended_card"
            return .3
        if phase == "ended_card":
            if not wait(lambda: popover_kind() == "goal"):
                return .1
            checks["ended_card_lists_the_rounds"] = any(
                a == "popup_goal_act" and v == "dismiss" for _, a, v in view_state().get("popup_hits", []))
            capture("goal-6-ended-card.png")
            click("popup_goal_act", "dismiss", popup=True)
            state["phase"] = "dismissed"
            return .3
        if phase == "dismissed":
            if not wait(lambda: goal() is None and ("goal", None) not in actions()):
                return .1
            checks["dismiss_removes_the_chip"] = True
            state["phase"] = "menu_again"
            return .2
        if phase == "pending_again":
            if not wait(lambda: goal_loop.pending_goal(c) is not None):
                return .1
            goal_controls.set_option(c, "check", G.JUDGED)
            state["starts_before"] = len(turn_starts())
            # The goal's first turn runs until the draft is in: a stand-in turn ends in ~40 ms, before any draft.
            fake_provider_cli.script_codex_turns(["held"], OUT / "fake-cli")
            fake_provider_cli.fresh(fake_provider_cli.codex_turn_hold(OUT / "fake-cli"))
            send(SECOND)
            state["phase"] = "first_turn"
            return .1
        if phase == "first_turn":
            # The draft goes in while the turn runs (the stand-in holds it), so the round it ends is held by it.
            hold_at = fake_provider_cli.codex_turn_hold(OUT / "fake-cli")
            if not wait(lambda: fake_provider_cli.held(hold_at), limit=20):
                return .05
            draft(DRAFT)
            fake_provider_cli.release(hold_at)
            state["phase"] = "held"
            return .2
        if phase == "held":
            if not wait(lambda: idle() and goal_loop.waiting_reason(c) == goal_loop.WAITING_DRAFT, limit=20):
                return .1
            parts = chip()
            state["waiting_chip"] = parts
            checks["a_draft_holds_the_round"] = len(turn_starts()) == state["starts_before"] + 1
            from angelo_agent.ui_text import iface_
            checks["the_chip_says_it_waits_for_the_message"] = parts is not None and \
                parts["title"] == iface_("Waiting while you write a message")
            capture("goal-7-waiting-for-draft.png")
            click("goal_action", "stop")
            state["phase"] = "stopped"
            return .3
        if phase == "stopped":
            if not wait(lambda: G.ended(goal())):
                return .1
            draft("")
            state["phase"] = "stopped_settle"
            return 3.0
        if phase == "stopped_settle":
            held = goal()
            checks["stop_goal_ends_it_as_the_persons_stop"] = held["status"] == G.STOPPED and \
                held["reason"] == G.STOPPED_BY_PERSON
            checks["nothing_sent_after_stop_goal"] = len(turn_starts()) == state["starts_before"] + 1
            capture("goal-8-stopped.png")
            goal_controls.dismiss(c)
            state["phase"], state["language"] = "languages", 0
            return .2
        if phase == "languages":
            if state["language"] >= len(LANGUAGES):
                bpy.context.preferences.view.language = 'en_US'
                state["phase"] = "done"
                return .2
            code = LANGUAGES[state["language"]]
            prefs = bpy.context.preferences.view
            prefs.language = code
            prefs.use_translate_interface = True
            if goal_loop.pending_goal(c) is None:
                goal_controls.choose(c)
            state["phase"] = "language_card"
            return .5
        if phase == "language_card":
            from angelo_agent import ui_text
            code = LANGUAGES[state["language"]]
            if popover_kind() != "goal":
                if ("goal", None) in actions():
                    click("goal")
                return .3
            parts = chip()
            record = {"chip_title": parts["title"], "card_title": ui_text.iface_("Goal"),
                      "working_time": ui_text.iface_("Working time")}
            state["languages"][code] = record
            checks["translated_" + code] = record["working_time"] != "Working time" and \
                record["chip_title"] != state["pending_chip"]["title"]
            capture("goal-9-card-%s.png" % code)
            popup_key('ESC')
            state["language"] += 1
            state["phase"] = "languages"
            return .3
        if phase == "done":
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
