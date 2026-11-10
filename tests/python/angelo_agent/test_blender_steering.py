# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Messages sent while a turn runs, in the compiled app, the model played by the stand-in ``claude`` CLI.

1.0.9 regression: during a long turn Enter showed "Something went wrong" ("The current request is still running;
queue the connection for the next message") and nothing could be steered or queued. Real default startup, the real
typed-plan loop, chat canvas, Enter key and clicks; ``fake_provider_cli.play_round`` plays scripted rounds and holds
each turn where the suite types (``hold``/``release``). No account, network or inference.

One chat, four requests:

1. **Steer.** While the first round's plan is held, Enter with a message: no error card, a pending bubble saying
   Steering in the tray above the composer. A model chosen meanwhile (``angelo.select_model``) says it takes over
   from the next message. Released, the plan's edit runs, and the next round's prompt ends with the person's words
   after the results; a user row "Sent while Angelo worked" sits where it was read, before the reply.
2. **Queue several.** Three messages during a held turn; Remove the second, Edit the third (back in the message
   box, changed, Enter again), switch the first to After this turn. The turn ends with no next round, and both
   remaining go as ONE request, oldest first, without the removed one.
3. **Stop pauses.** A message during a held turn, then Stop: its bubble says Not sent and nothing is sent by itself;
   Send sends it.
4. Every turn: no error row, the chat idle at the end, the tray empty.

Run through ``run_native_suites.py --gui --include test_blender_steering``; check ``steering.json`` for
``success`` and ``phase == "complete"``.
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

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-steering-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_provider_cli  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')  # The default Director startup with its chat.
ROUNDS = fake_provider_cli.fresh(OUT / "rounds")
ROUNDS.mkdir(parents=True, exist_ok=True)
GATES = [fake_provider_cli.fresh(OUT / ("gate-%d" % index)) for index in range(3)]
fake_provider_cli.sign_in(os.environ["HOME"], "claude")  # An everyday sign-in that already exists.
os.environ.update(ANGELO_FAKE_CLI_STATE=str(OUT / "fake-cli"), ANGELO_FAKE_CLI_LOGIN_SECONDS="0.5",
                  ANGELO_FAKE_CLI_ROUNDS=str(ROUNDS))
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "steering.json"
SESSION = "fixture-steering"
STEER = "Make it red as well."
QUEUED = ("First queued: add a lamp.", "Second queued: never mind.", "Third queued: and a rug.")
EDITED = "Third queued: and a round rug."
STOPPED = "After Stop: try a chair."
state = {"phase": "boot", "success": False, "checks": {}, "observed": {}, "screenshots": [],
         "fixture": "Stand-in claude CLI playing scripted, held rounds; no browser, account, network or inference"}
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


def type_and_enter(text):
    area, region = chat()
    area.spaces.active.history[-1].body = text
    x, y = region.x + region.width // 2, region.y + 60
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    window().event_simulate(type='RET', value='PRESS', x=x, y=y)
    window().event_simulate(type='RET', value='RELEASE', x=x, y=y)


def draft():
    return chat()[0].spaces.active.history[-1].body


def controller():
    from angelo_agent import ui
    return ui.get_controller()


def errors():
    return [m for m in controller().state["messages"] if m.get("role") == "error"]


def pending():
    return controller().inbox.items()


def played():
    counter = ROUNDS / "next"
    return int(counter.read_text()) if counter.is_file() else 0


def prompt(index):
    return (ROUNDS / ("prompt-%d.txt" % index)).read_text(encoding="utf-8")


def script(index, plan, gate=None):
    """The stand-in's ``index``-th round: held at ``gate`` first (the suite types there), then ``plan``."""
    events = ([fake_provider_cli.hold(gate)] if gate else []) + [{
        "type": "result", "subtype": "success", "is_error": False, "session_id": SESSION, "duration_ms": 900,
        "structured_output": plan}]
    (ROUNDS / ("round-%d.json" % index)).write_text(json.dumps(events), encoding="utf-8")


def move_cube(height):
    from angelo_agent import scene_queries
    cube = window().scene.objects["Cube"]
    return {"reply": "", "verify": True, "operations": [{"tool": "angelo_scene_program", "arguments": {
        "mode": "APPLY", "read_set": [],
        "write_set": [{"handle": scene_queries.object_handle(cube),
                       "revision": scene_queries.object_revision(window(), cube)}],
        "operations": [{"kind": "transform", "target": scene_queries.object_handle(cube),
                        "parameters": {"location": [0, 0, height]}}]}}]}


def reply(text):
    return {"reply": text, "operations": []}


def capture(name):
    path = OUT / name
    chat()[0].tag_redraw()
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def idle():
    c = controller()
    return not c.turn_running()


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def diagnose():
    """What the chat looked like when a phase failed: its rows, pending messages, turn state and draft."""
    try:
        c = controller()
        state["diagnosis"] = {
            "rows": [{k: str(m.get(k, ""))[:160] for k in ("role", "text", "status", "steer")}
                     for m in c.state["messages"][-12:]],
            "pending": [(i.text[:80], i.mode, i.phase, str(i.reason)[:120]) for i in c.inbox.items()],
            "busy": c.busy, "turn_running": c.turn_running(), "status": str(c.status), "draft": draft()[:120],
            "worker_alive": bool(c._worker and c._worker.is_alive()),
            "delivery_stops": repr(getattr(c, "_delivery_stops", None))}
        capture("failure.png")
    except Exception as failure:   # noqa: BLE001 - the diagnosis never hides the failure itself.
        state["diagnosis_error"] = repr(failure)


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
        diagnose()
    else:
        state.update(phase="complete", success=all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_STEERING", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(condition, limit=60):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + state["phase"]
    return False


def tick():
    try:
        assert time.monotonic() - started < 420, "Steering fixture timed out in " + state["phase"]
        from angelo_agent import director, quick_connect, ui
        from angelo_agent.steering import PAUSED, QUEUE, STEER as STEER_MODE
        checks, phase, observed = state["checks"], state["phase"], state["observed"]
        c = controller()
        if phase == "boot":
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            if not wait(lambda: not ui._authentication.running and ("welcome_connect", "claude_subscription") in actions()):
                return .2
            click("welcome_connect", "claude_subscription")
            state["phase"] = "connected"
            return .3
        if phase == "connected":
            if not wait(lambda: quick_connect.flow("claude_subscription") is None and c.models and ui.connection_available()):
                return .2
            script(0, move_cube(1.5), GATES[0])
            script(1, reply("Moved it and made it red."))
            type_and_enter("Move the cube up.")
            state["phase"] = "steer_held"
            return .2
        # 1. Steer ----------------------------------------------------------------------------------------------
        if phase == "steer_held":
            if not wait(lambda: fake_provider_cli.held(GATES[0]) and c.busy):
                return .2
            if not pending():
                if "steer_typed" not in observed:
                    type_and_enter(STEER)
                    observed["steer_typed"] = True
                return .2
            other = next((m["id"] for m in c.models if not m["default"]), "")
            bpy.ops.angelo.select_model(model=other, connection_id=c.state["connection_id"])
            observed["chosen_model"] = other
            state["phase"] = "steer_shown"
            return .3
        if phase == "steer_shown":
            if not wait(lambda: any(a == "pending_mode" for a, _ in actions())):
                return .2
            items = pending()
            checks["enter_during_a_turn_never_shows_an_error"] = errors() == []
            checks["the_message_waits_as_a_steer"] = [(i.text, i.mode) for i in items] == [(STEER, STEER_MODE)]
            checks["the_draft_was_taken"] = draft() == ""
            checks["a_model_chosen_during_the_turn_says_so"] = any(
                "takes over from your next message" in m.get("text", "") for m in c.state["messages"])
            checks["the_running_request_kept_its_model"] = (c.turn_options or {}).get("model", "") != observed["chosen_model"]
            capture("steer-pending.png")
            fake_provider_cli.release(GATES[0])
            state["phase"] = "steer_done"
            return .3
        if phase == "steer_done":
            if not wait(lambda: idle() and played() >= 2):
                return .2
            rows = c.state["messages"]
            text = prompt(1)
            from angelo_agent.steering_delivery import STEER_HEADING
            checks["the_next_round_reads_the_steer_after_the_results"] = (
                text.endswith(STEER_HEADING + "\n" + STEER) and '"result"' in text)
            checks["the_first_round_never_had_it"] = STEER not in prompt(0)
            steer_rows = [i for i, m in enumerate(rows) if m.get("steer") is True]
            reply_rows = [i for i, m in enumerate(rows) if m.get("role") == "assistant" and "red" in m.get("text", "")]
            checks["the_steer_row_sits_where_it_was_read"] = (
                len(steer_rows) == 1 and bool(reply_rows) and steer_rows[0] < reply_rows[-1]
                and rows[steer_rows[0]]["text"] == STEER)
            checks["the_cube_moved"] = abs(window().scene.objects["Cube"].location.z - 1.5) < 1e-4
            checks["nothing_left_pending_after_the_steer"] = pending() == []
            checks["no_error_rows_after_the_steer"] = errors() == []
            capture("steer-delivered.png")
            # 2. Queue several ------------------------------------------------------------------------------------
            observed["rounds_before_queue"] = played()
            script(2, reply("Looked at it."), GATES[1])
            script(3, reply("Added the lamp and the rug."))
            type_and_enter("Look at the room.")
            state["phase"] = "queue_held"
            return .3
        if phase == "queue_held":   # One input per tick: simulated events are handled on later passes.
            if not wait(lambda: fake_provider_cli.held(GATES[1]) and c.busy):
                return .2
            step, items = observed.setdefault("queue_step", 0), pending()
            if step < 3:
                if len(items) == step and draft() == "":
                    type_and_enter(QUEUED[step])
                    observed["queue_step"] = step + 1
                return .2
            if step == 3:
                if not wait(lambda: len(items) == 3 and all(("pending_remove", i.id) in actions() for i in items)):
                    return .2
                observed["queued_ids"] = [i.id for i in items]
                click("pending_remove", items[1].id)
                observed["queue_step"] = 4
                return .2
            first, _, third = observed["queued_ids"]
            if step == 4:
                if not wait(lambda: len(items) == 2 and ("pending_edit", third) in actions()):
                    return .2
                click("pending_edit", third)
                observed["queue_step"] = 5
                return .2
            if step == 5:
                if not wait(lambda: len(pending()) == 1 and draft() != ""):
                    return .2
                observed["edit_draft"] = draft()
                type_and_enter(EDITED)
                observed["queue_step"] = 6
                return .2
            if step == 6:
                if not wait(lambda: len(pending()) == 2 and ("pending_mode", (first, QUEUE)) in actions()):
                    return .2
                click("pending_mode", (first, QUEUE))
                state["phase"] = "queue_checked"
                return .3
        if phase == "queue_checked":
            if not wait(lambda: len(pending()) == 2):
                return .2
            items = pending()
            checks["edit_puts_the_text_back_in_the_message_box"] = observed["edit_draft"] == QUEUED[2]
            checks["removed_and_edited_messages_leave_and_come_back_in_order"] = (
                [i.text for i in items] == [QUEUED[0], EDITED])
            checks["after_this_turn_switches_the_mode"] = [i.mode for i in items] == [QUEUE, STEER_MODE]
            checks["no_error_rows_while_queueing"] = errors() == []
            capture("queue-pending.png")
            fake_provider_cli.release(GATES[1])
            state["phase"] = "queue_done"
            return .3
        if phase == "queue_done":
            if not wait(lambda: idle() and played() >= 4 and not pending()):
                return .2
            checks["the_queued_messages_went_as_one_request_oldest_first"] = (
                prompt(3).startswith(QUEUED[0] + "\n\n" + EDITED) and QUEUED[1] not in prompt(3))
            user_rows = [m for m in c.state["messages"] if m.get("role") == "user" and not m.get("steer")]
            checks["one_request_row_for_them"] = user_rows[-1]["text"] == QUEUED[0] + "\n\n" + EDITED
            checks["the_chosen_model_took_over_after_the_turn"] = (c.turn_options or {}).get("model", "") == observed["chosen_model"]
            # 3. Stop pauses ----------------------------------------------------------------------------------------
            script(4, reply("Thinking about chairs."), GATES[2])
            script(5, reply("Here is a chair."))
            type_and_enter("Now furnish it.")
            state["phase"] = "stop_held"
            return .3
        if phase == "stop_held":
            if not wait(lambda: fake_provider_cli.held(GATES[2]) and c.busy):
                return .2
            if not pending():
                if "stop_typed" not in observed:
                    type_and_enter(STOPPED)
                    observed["stop_typed"] = True
                return .2
            click("stop")
            fake_provider_cli.release(GATES[2])
            state["phase"] = "stopped"
            return .3
        if phase == "stopped":
            if not wait(lambda: idle() and pending() and pending()[0].phase == PAUSED):
                return .2
            observed.setdefault("stopped_at", time.monotonic())
            observed.setdefault("rounds_at_stop", played())
            if time.monotonic() - observed["stopped_at"] < 1.5:   # Long enough for a send that should not happen.
                return .2
            checks["stop_pauses_a_pending_message"] = pending()[0].reason == "stopped"
            checks["nothing_is_sent_by_itself_after_stop"] = played() == observed["rounds_at_stop"]
            checks["a_paused_bubble_offers_send"] = ("pending_send", pending()[0].id) in actions()
            capture("stop-paused.png")
            click("pending_send", pending()[0].id)
            state["phase"] = "resent"
            return .3
        if phase == "resent":
            if not wait(lambda: idle() and played() >= observed["rounds_at_stop"] + 1 and not pending()):
                return .2
            checks["send_sends_a_paused_message"] = STOPPED in prompt(played() - 1)
            checks["no_error_rows_at_the_end"] = errors() == []
            capture("final.png")
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
