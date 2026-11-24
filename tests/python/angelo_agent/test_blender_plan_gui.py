# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Plan mode in the running app: the + menu row, the plan card, Go as the person's press only, the chips.

doc/angelo/plan_mode.rst. Real default startup (Director Mode with its chat), a real ChatGPT connection over the
stand-in ``codex`` app-server (``fake_provider_cli.py``: a scripted turn makes ``angelo_plan`` calls as the model
would), the real controller, chat canvas, grant guard in C and synthetic input on the canvas's own hit geometry. No
browser, account, network or inference.

Covered, each with a screenshot:

1. The + menu's Plan first row starts planning: the chat is Read only with ``planning`` set, the composer shows the
   Planning chip and the approval control reads Planning. A bake the chat's Auto admitted earlier, still queued in the
   app's real job manager, is cancelled as planning starts (never held for a later press to start), and the chat
   says so (break-it review of e327003b8c2, S1-S3).
2. A turn whose model calls ``angelo_plan`` PROPOSE draws the plan card under its row: both approaches, the
   recommendation, Go with this and Edit, and the Then control preselected Auto (the chat was in Auto).
3. Nothing but the person's press goes: ``bpy.ops.angelo.plan_go`` called by a script, ``planning_controls.go``
   called directly, and ``set_options(planning=False)`` inside agent code are all refused; the chat still plans and
   nothing is sent.
4. The person's press on Then: Ask, then on approach 2's Go with this: the chat leaves planning in Ask and the request
   the stand-in receives is the chosen approach as facts; the person's row reads as their choice; the card says which
   approach they went with; the scene is unchanged throughout planning.
5. The plan chip's Revise plan plans again; its close (Stop planning, the person's press) returns to Ask, sending
   nothing.
6. The card and the Planning chip in Japanese and Arabic (right to left): translated, never the English text.

Run through ``run_native_suites.py --gui --include test_blender_plan_gui``; check ``plan-gui.json`` for ``success``
and ``phase == "complete"``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-plan-gui-qa"))
OUT.mkdir(parents=True, exist_ok=True)
import fake_provider_cli  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_plus_menu  # noqa: E402  (the + menu's pages, read from the chat's canvas state)

product_env.clear('DISABLE_DEFAULT_CHAT')  # The default Director startup and its chat.
fake_provider_cli.sign_in(os.environ["HOME"], "codex")
os.environ.update(ANGELO_FAKE_CLI_STATE=str(OUT / "fake-cli"), ANGELO_FAKE_CLI_LOGIN_SECONDS="0.5",
                  ANGELO_FAKE_CLI_CODEX_TURNS="ok")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "plan-gui.json"
ASKED = "Let's plan the tower first: what are my options?"
PLAN = {"action": "PROPOSE", "summary": "Two ways to build the tower, by how often its floors will change.",
        "approaches": [{"title": "One mesh with Booleans", "steps": ["Block out the body", "Cut the windows"],
                        "risks": ["Booleans are slow to change later"], "estimate": "about 10 minutes"},
                       {"title": "A modular kit", "steps": ["Model one floor", "Array it upward", "Cap the roof"],
                        "risks": ["More objects to manage"], "estimate": "about 20 minutes"}],
        "recommended": 1, "questions": ["Will the tower go to a game engine?"]}
LANGUAGES = ("ja_JP", "ar_EG")
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "languages": {},
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


def hits():
    return view_state().get("hits", [])


def actions():
    return [(action, value) for _, action, value in hits()]


def press(rect, action):
    import qa_input
    from angelo_agent import chat_view
    if action in qa_input.AIMED:   # A shielded grant (Go): aimed below it while its shield passes, then into it.
        return qa_input.window_click(window(), chat()[1], rect, action, scale=chat_view.ui_scale())
    x, y = qa_input.chat_point(chat()[1], rect)   # The chat's points as window pixels (scale, right to left mirrored).
    if action in qa_input.GRANT_ACTIONS:   # It reaches a grant: only a person's click (move, then press).
        return qa_input.press(window(), x, y)
    for kind, event_value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        window().event_simulate(type=kind, value=event_value, x=x, y=y)


def click(action, value=None):
    rect = next(r for r, a, v in hits() if a == action and (value is None or v == value))
    press(rect, action)


def send(text):
    from angelo_agent import console
    area = chat()[0]
    area.spaces.active.history[-1].body = text
    x, y = chat()[1].x + chat()[1].width // 2, chat()[1].y + 60
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    for value in ('PRESS', 'RELEASE'):
        window().event_simulate(type='RET', value=value, x=x, y=y)
    assert console.is_chat(area)


def turn_starts():
    path = OUT / "fake-cli" / "codex-requests.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []
    return [row for row in rows if row["method"] == "turn/start"]


def first_text(request):
    parts = request.get("input") or []
    return next((p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"), "")


def idle():
    c = controller()
    return not c.busy and not (c._worker and c._worker.is_alive())


def planning():
    from angelo_agent import planning_controls
    return planning_controls.is_planning(controller())


def approval():
    from angelo_agent.chat_settings import clean_options
    return clean_options(controller().state.get("options"))["approval"]


def capture(name):
    path = OUT / name
    chat()[0].tag_redraw()
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def queue_waiting_bake(c):
    """A bake the chat's Auto admitted, still queued in the app's own job manager as planning starts: held behind the
    person's operation (``edit_tool_hold``), so the job timer starts nothing until planning has started."""
    from angelo_agent import edit_tool_hold, job_ui
    from angelo_agent.job_store import new_record
    state["tool_running"] = edit_tool_hold.running
    edit_tool_hold.running = lambda: True
    jobs = job_ui.manager()
    record = new_record(jobs.store.allocate(), c.state["id"], "call-bake", {"kind": "BAKE_TEXTURE"}, now=time.time())
    jobs.store.add(record)
    jobs.queue.append(record["id"])
    return record["id"]


def release_tool_hold():
    from angelo_agent import edit_tool_hold
    if "tool_running" in state:
        edit_tool_hold.running = state.pop("tool_running")


def finish(error=None):
    release_tool_hold()
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_PLAN_GUI", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(condition, limit=40):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + state["phase"]
    return False


def choose_plan():
    """The + button, then the Plan first row, as a person clicks them. True once the row was clicked."""
    view = view_state()
    if not qa_plus_menu.is_open(view):
        click("attach")   # The chat bar's + button.
        return False
    kind, rect = qa_plus_menu.step(view, "popup_add_plan")
    assert kind != "missing", qa_plus_menu.miss(view, "popup_add_plan")
    if kind == "click":
        press(rect, "popup_add_plan")
        return True
    if kind in ("open", "back"):
        press(rect, "popup_add_group")
    return False


def plan_turn(name="propose"):
    """The next turn's model calls angelo_plan PROPOSE (the stand-in's scripted call)."""
    fake_provider_cli.script_codex_call(name, "angelo_plan", PLAN, OUT / "fake-cli")
    fake_provider_cli.script_codex_turns(["call:" + name], OUT / "fake-cli")


def card_shown():
    return any(a == "plan_go" for a, _v in actions())


def scripted_presses_refused():
    """A script, Angelo's own code and agent code each try to press Go or end planning: all refused (plan_mode.rst,
    "Safety"). Returns what each did."""
    from angelo_agent import grant_guard, planning_controls, planning_state as S
    c = controller()
    call_id, _plan = S.live_plan(c.state["messages"], True)
    found = {}
    try:
        found["operator"] = sorted(bpy.ops.angelo.plan_go('EXEC_DEFAULT', session_id=c.state["id"], call_id=call_id,
                                                          approach=0, approval="AUTO_BOUNDED"))
    except RuntimeError as error:   # The guard's report (an ERROR report raises in a Python call).
        found["operator"] = "refused: " + str(error).strip().splitlines()[-1][:120]
    try:
        planning_controls.go(c, call_id, 0, "AUTO_BOUNDED")
        found["function"] = "ran"
    except PermissionError as error:
        found["function"] = "refused: " + str(error)[:120]
    with grant_guard.agent_script():
        try:
            c.set_options(planning=False)
            found["agent_code"] = "ran"
        except PermissionError as error:
            found["agent_code"] = "refused: " + str(error)[:120]
    return found


def tick():
    try:
        assert time.monotonic() - started < 300, "Plan GUI fixture timed out in " + state["phase"]
        from angelo_agent import director, planning_card, planning_state as S, planning_text as T, quick_connect, ui
        from angelo_agent.ui_text import iface_
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
            state["approval_before"] = approval()
            state["waiting_bake"] = queue_waiting_bake(c)
            state["phase"] = "menu"
            return .2
        if phase == "menu":
            if not wait(lambda: choose_plan(), limit=20):
                return .3
            state["phase"] = "planning"
            return .3
        if phase == "planning":
            if not wait(lambda: planning() and planning_card.chip_shown(c)
                        and any(m.get("job") == state["waiting_bake"] for m in c.state["messages"])):
                return .1
            from angelo_agent import job_chat, job_ui, planning_controls
            bake = job_ui.manager().store.get(state["waiting_bake"])
            checks["a_waiting_bake_is_cancelled_as_planning_starts"] = (
                bake["status"], bake["reason"]) == ("cancelled", planning_controls.JOBS_CANCELLED_BY)
            rows = [job_chat.notice_display(m) for m in c.state["messages"] if m.get("job") == state["waiting_bake"]]
            checks["its_row_says_planning_cancelled_it"] = len(rows) == 1 and \
                iface_("Cancelled: its chat started planning") in rows[0]
            release_tool_hold()
            checks["the_menu_row_starts_planning_read_only"] = approval() == "READ_ONLY" and planning()
            checks["the_planning_chip_is_shown"] = planning_card.chip_parts(c)[0] == iface_(T.CHIP_PLANNING)
            checks["the_build_setting_is_the_chats_own"] = c.state["options"]["plan_return"] == (
                "AUTO_BOUNDED" if state["approval_before"] == "AUTO_BOUNDED" else "ASK")
            capture("plan-1-planning.png")
            plan_turn()
            send(ASKED)
            state["phase"] = "card"
            return .3
        if phase == "card":
            if not wait(lambda: idle() and card_shown(), limit=40):
                return .1
            rows = [m for m in c.state["messages"] if S.plan_of(m) is not None]
            checks["propose_draws_the_card_under_its_row"] = len(rows) == 1 and planning()
            shown = actions()
            checks["the_card_offers_go_and_edit_per_approach"] = all(
                any(a == "plan_go" and v[1] == index for a, v in shown) and ("plan_edit", (rows[0]["call_id"], index))
                in shown for index in range(2))
            checks["then_is_preselected_from_the_chat"] = all(
                v[2] == c.state["options"]["plan_return"] for a, v in shown if a == "plan_go")
            capture("plan-2-card.png")
            state["refused"] = scripted_presses_refused()
            checks["a_scripted_go_is_refused"] = str(state["refused"]["operator"]).startswith("refused") or \
                state["refused"]["operator"] == ["CANCELLED"]
            checks["a_direct_go_is_refused"] = state["refused"]["function"].startswith("refused")
            checks["agent_code_cannot_end_planning"] = state["refused"]["agent_code"].startswith("refused")
            checks["still_planning_after_scripted_presses"] = planning() and len(turn_starts()) == 1
            state["call_id"] = rows[0]["call_id"]
            click("plan_then", (state["call_id"], "ASK"))
            state["phase"] = "then"
            return .3
        if phase == "then":
            if not wait(lambda: ("plan_go", (state["call_id"], 1, "ASK")) in actions()):
                return .1
            click("plan_go", (state["call_id"], 1, "ASK"))
            state["phase"] = "went"
            return .3
        if phase == "went":
            if not wait(lambda: not planning() and len(turn_starts()) == 2 and idle(), limit=40):
                return .1
            text = first_text(turn_starts()[1])
            state["go_request"] = text[:600]
            checks["go_leaves_planning_in_the_setting_pressed"] = approval() == "ASK"
            checks["go_sends_the_chosen_approach_as_facts"] = text.startswith("The person chose approach 2 of the plan") \
                and '"A modular kit"' in text and '"Array it upward"' in text
            rows = [m for m in c.state["messages"] if S.choice_of(m) is not None]
            checks["the_persons_row_is_their_choice"] = len(rows) == 1 and rows[0]["plan_choice"]["approach"] == 1
            checks["the_card_says_which_was_chosen"] = S.card_state(c.state["messages"], state["call_id"], False) == \
                (S.CHOSEN, 1) and not card_shown()
            checks["scene_unchanged"] = sorted(o.name for o in bpy.data.objects) == state["objects"]
            parts = planning_card.chip_parts(c)
            checks["the_plan_chip_holds_the_choice"] = parts is not None and "A modular kit" in parts[0]
            capture("plan-3-after-go.png")
            click("plan_chip_revise")
            state["phase"] = "revising"
            return .3
        if phase == "revising":
            if not wait(lambda: planning()):
                return .1
            checks["revise_plans_again"] = approval() == "READ_ONLY" and c.state["options"]["plan_return"] == "ASK"
            capture("plan-4-revising.png")
            state["starts_before_stop"] = len(turn_starts())
            click("plan_chip_stop")
            state["phase"] = "stopped"
            return .3
        if phase == "stopped":
            if not wait(lambda: not planning()):
                return .1
            checks["stop_planning_returns_to_the_build_setting"] = approval() == "ASK"
            checks["stop_planning_sends_nothing"] = len(turn_starts()) == state["starts_before_stop"]
            state["phase"], state["language"] = "languages", 0
            return 2.0
        if phase == "languages":
            if state["language"] >= len(LANGUAGES):
                bpy.context.preferences.view.language = 'en_US'
                state["phase"] = "done"
                return .2
            prefs = bpy.context.preferences.view
            prefs.language, prefs.use_translate_interface = LANGUAGES[state["language"]], True
            from angelo_agent import planning_controls
            planning_controls.start(c)   # Narrowing: any code may start planning.
            plan_turn("propose_%d" % state["language"])
            send(ASKED)
            state["phase"] = "language_card"
            return .5
        if phase == "language_card":
            if not wait(lambda: idle() and card_shown(), limit=40):
                return .1
            code = LANGUAGES[state["language"]]
            record = {"go": iface_(T.GO), "chip": planning_card.chip_parts(c)[0], "then": iface_(T.THEN)}
            state["languages"][code] = record
            checks["translated_" + code] = record["go"] != T.GO and record["chip"] != T.CHIP_PLANNING
            capture("plan-5-card-%s.png" % code)
            click("plan_chip_stop")
            state["language"] += 1
            state["phase"] = "languages"
            return 1.0
        if phase == "done":
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


import qa_input  # noqa: E402  (a click on its way holds the tick: qa_input.after_clicks)
bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=.5)
