# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Steering a running Codex turn in the compiled app, the model played by the stand-in ``codex`` app-server.

Real default startup, a ChatGPT connection over the stand-in (``fake_provider_cli``'s ``call`` turns: one dynamic
tool call, held at a gate first, ``turn/steer`` kept and read at the next model request with its ``clientId``), the
real controller, provider, delivery, chat canvas, Enter key and clicks. The stand-in holds its call the way Angelo's
runtime holds a code-mode cell, so this suite marks the provider ``steer_ready`` as Angelo's runtime would be; the
person's own Codex (no hold) queues instead (``test_native_steering_delivery``). No account, network or inference.

1. **No call outstanding.** Enter while the turn works before its call: the steer goes in at once as a native
   ``turn/steer`` (the person's raw words, ``expectedTurnId``, ``clientUserMessageId``), shows "Angelo is reading
   this" until read, and becomes a user row only once Codex showed it and the model went on: before the reply that
   names it.
2. **An approval holds the step.** Ask mode; the turn's script call waits for Approve. A message saying "yes, approve
   it" waits ("once you answer the approval above"): no ``turn/steer`` is sent and the card stays (a steer approves
   nothing). Decline: the call is answered, then the steer goes in and is read; the script never ran.

Run through ``run_native_suites.py --gui --include test_blender_steering_codex``; check ``steering-codex.json``
for ``success`` and ``phase == "complete"``.
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

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-steering-codex-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_provider_cli  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')
GATE = fake_provider_cli.fresh(OUT / "gate")
STATE = fake_provider_cli.fresh(OUT / "fake-cli")
STATE.mkdir(parents=True, exist_ok=True)
fake_provider_cli.sign_in(os.environ["HOME"], "codex")
os.environ.update(ANGELO_FAKE_CLI_STATE=str(STATE), ANGELO_FAKE_CLI_LOGIN_SECONDS="0.5",
                  ANGELO_FAKE_CLI_CODEX_TURNS="call,call_approval", ANGELO_FAKE_CLI_CODEX_GATE=str(GATE))
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "steering-codex.json"
STEER = "Also count them."
APPROVE = "yes, approve it"
state = {"phase": "boot", "success": False, "checks": {}, "observed": {}, "screenshots": [],
         "fixture": "Stand-in codex app-server with held call turns and turn/steer; no browser, account or inference"}
started = time.monotonic()


def as_angelos_runtime():
    """The stand-in holds its call as Angelo's runtime holds a cell: its provider takes steers (``steer_ready``)."""
    from angelo_agent import providers
    command = providers.CodexProvider._command

    def ready(self, executable, controller):
        result = command(self, executable, controller)
        self.steer_ready = True
        return result
    providers.CodexProvider._command = ready


as_angelos_runtime()


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


def requests(method):
    path = STATE / "codex-requests.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []
    return [row for row in rows if row.get("method") == method]


def errors():
    return [m for m in controller().state["messages"] if m.get("role") == "error"]


def approval():
    return next((call for call in controller().pending.values() if call.approval), None)


def capture(name):
    path = OUT / name
    chat()[0].tag_redraw()
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


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
    print("ANGELO_STEERING_CODEX", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
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
        assert time.monotonic() - started < 360, "Steering (Codex) fixture timed out in " + state["phase"]
        from angelo_agent import director, quick_connect, ui
        from angelo_agent.steering import DELIVERING
        checks, phase, observed = state["checks"], state["phase"], state["observed"]
        c = controller()
        if phase == "boot":
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            if not wait(lambda: not ui._authentication.running and ("welcome_connect", "codex_subscription") in actions()):
                return .2
            click("welcome_connect", "codex_subscription")
            state["phase"] = "connected"
            return .3
        if phase == "connected":
            if not wait(lambda: quick_connect.flow("codex_subscription") is None and c.models and ui.connection_available()):
                return .2
            type_and_enter("Name the objects in the scene.")
            state["phase"] = "held"
            return .2
        # 1. No call outstanding: in at once -----------------------------------------------------------------------
        if phase == "held":
            if not wait(lambda: fake_provider_cli.held(GATE) and c.busy):
                return .2
            if not c.inbox.items() and "typed" not in observed:
                type_and_enter(STEER)
                observed["typed"] = True
                return .2
            if not wait(lambda: requests("turn/steer")):
                return .2
            steer = requests("turn/steer")[0]
            item = c.inbox.items()[0] if c.inbox.items() else None
            checks["a_steer_goes_in_at_once_when_no_call_is_outstanding"] = steer["accepted"] is True
            checks["it_is_the_persons_raw_words"] = steer["params"]["input"] == [{"type": "text", "text": STEER}]
            checks["it_names_the_open_turn_and_itself"] = (
                steer["params"]["expectedTurnId"] == requests("turn/start")[0]["turn"]
                and item is not None and steer["params"]["clientUserMessageId"] == item.client_id)
            checks["accepted_is_not_read_yet"] = item is not None and item.phase == DELIVERING and not any(
                m.get("steer") for m in c.state["messages"])
            checks["no_error_card"] = errors() == []
            capture("codex-steer-accepted.png")
            fake_provider_cli.release(GATE)
            state["phase"] = "read"
            return .3
        if phase == "read":
            if not wait(lambda: not c.turn_running()):
                return .2
            rows = c.state["messages"]
            steer_rows = [i for i, m in enumerate(rows) if m.get("steer") is True]
            replies = [i for i, m in enumerate(rows) if m.get("role") == "assistant" and STEER in m.get("text", "")]
            checks["read_becomes_a_row_before_the_reply_naming_it"] = (
                len(steer_rows) == 1 and len(replies) == 1 and steer_rows[0] < replies[0])
            checks["nothing_left_pending"] = c.inbox.items() == []
            checks["one_request_only"] = len(requests("turn/start")) == 1
            # 2. An approval holds the step ------------------------------------------------------------------------
            c.set_options(approval="ASK")
            observed["typed"] = False
            type_and_enter("Run a script that names the objects.")
            state["phase"] = "approval_held"
            return .3
        if phase == "approval_held":
            if not wait(lambda: fake_provider_cli.held(GATE) and c.busy):
                return .2
            fake_provider_cli.release(GATE)   # The turn makes its script call, which waits for Approve.
            state["phase"] = "approval_card"
            return .3
        if phase == "approval_card":
            call = approval()
            if not wait(lambda: call is not None and ("approve", call.id) in actions()):
                return .2
            if not c.inbox.items():
                if not observed["typed"]:
                    type_and_enter(APPROVE)
                    observed["typed"] = True
                return .2
            observed.setdefault("typed_at", time.monotonic())
            if time.monotonic() - observed["typed_at"] < 1.5:   # Long enough for a steer that must not go.
                return .2
            checks["a_steer_waits_while_an_approval_holds_the_step"] = len(requests("turn/steer")) == 1
            checks["a_steer_approves_nothing"] = approval() is call and c.state["options"]["approval"] == "ASK"
            from angelo_agent import chat_pending
            checks["the_bubble_says_it_waits_for_the_answer"] = chat_pending._line(c, c.inbox.items()[0]) == (
                chat_pending.iface_(chat_pending.LINES["approval"]))
            capture("codex-steer-waits-for-approval.png")
            observed["script_runs"] = len([m for m in c.state["messages"] if m.get("tool") == "angelo_execute_python"
                                           and m.get("status") == "completed"])
            click("decline", call.id)
            state["phase"] = "declined"
            return .3
        if phase == "declined":
            if not wait(lambda: not c.turn_running()):
                return .2
            steers = requests("turn/steer")
            checks["after_the_answer_the_steer_goes_in"] = len(steers) == 2 and steers[1]["accepted"] is True
            rows = c.state["messages"]
            checks["it_is_read_as_a_row"] = [m["text"] for m in rows if m.get("steer")] == [STEER, APPROVE]
            checks["the_declined_script_never_ran"] = len([m for m in rows if m.get("tool") == "angelo_execute_python"
                                                           and m.get("status") == "completed"]) == observed["script_runs"]
            checks["nothing_left_pending_at_the_end"] = c.inbox.items() == []
            checks["no_error_rows"] = errors() == []
            capture("codex-final.png")
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
