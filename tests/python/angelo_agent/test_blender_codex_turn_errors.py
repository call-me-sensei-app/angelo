# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Codex turn a provider throttle or usage limit ends: the chat shows why at once and is idle again.

Regression: a Codex turn ended after one request with HTTP 429
(``responseTooManyFailedAttempts``) and the chat stayed "working" for 300 s.
Real default startup, a real ChatGPT connection over the stand-in ``codex``
app-server (``fake_provider_cli.py``, the turn notifications in codex 0.162's
order and shape), the real controller, provider, chat canvas and Enter key. No
browser, account, network or inference.

Covered, in one chat:

1. A throttle and Stop: within 2 s the live row counts the one automatic
   resend's wait down ("Provider busy — retrying in N s", about a minute:
   ``codex_retry.RATE_SECONDS``) with Stop available; Stop ends the request at
   once and nothing more is sent.
2. A throttle twice (the wait shortened to 2 s from here on): the resend carries
   the host note, not the message again; then a "Too many requests" notice card
   within 2 s of the second failure; the chat is idle, the scene unchanged, no
   preview.
3. A usage limit: never resent; a "Usage limit reached" card within 2 s.
4. A throttle, then an answer: the resend completes the request; its reply and
   no new error row; the receipt counts one throttle resend.

Run through ``run_native_suites.py --gui --include test_blender_codex_turn_errors``;
check ``codex-turn-errors.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-codex-turn-errors-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_provider_cli  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')  # The default chat startup with its connect row.
fake_provider_cli.sign_in(os.environ["HOME"], "codex")  # An everyday ChatGPT sign-in that already exists.
os.environ.update(ANGELO_FAKE_CLI_STATE=str(OUT / "fake-cli"), ANGELO_FAKE_CLI_LOGIN_SECONDS="0.5",
                  ANGELO_FAKE_CLI_CODEX_TURNS="rate,rate,rate,usage,rate,ok")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "codex-turn-errors.json"
NOTICE_SECONDS = 2.0
MESSAGES = ("Stage a PREVIEW only, do not apply it, that moves 'Eval Cube' to location (3, 0, 0).",
            "Move 'Eval Cube' to (3, 0, 0).", "Make the cube red.", "Add a bevel to the cube.")
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "timings": {},
         "fixture": "Stand-in codex app-server, synthetic Enter; no browser, account, network or inference"}
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


def send(text):
    area, region = chat()
    area.spaces.active.history[-1].body = text
    x, y = region.x + region.width // 2, region.y + 60
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    window().event_simulate(type='RET', value='PRESS', x=x, y=y)
    window().event_simulate(type='RET', value='RELEASE', x=x, y=y)
    state["sent_at"], state["rows_before"] = time.time(), len(controller().state["messages"])


def controller():
    from angelo_agent import ui
    return ui.get_controller()


def requests():
    path = OUT / "fake-cli" / "codex-requests.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


def idle():
    c = controller()
    return not c.busy and not (c._worker and c._worker.is_alive())


def new_rows():
    return controller().state["messages"][state["rows_before"]:]


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
        state.update(phase="complete", success=all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_CODEX_TURN_ERRORS", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(condition, limit=40):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + state["phase"]
    return False


def error_rows():
    return [m for m in new_rows() if m.get("role") == "error"]


def scene_unchanged():
    return sorted(o.name for o in bpy.data.objects) == state["objects"] and not controller().native_previews()


def tick():
    try:
        assert time.monotonic() - started < 240, "Codex turn-errors fixture timed out in " + state["phase"]
        from angelo_agent import chat_titles, codex_retry, director, notice_kinds, quick_connect, ui
        checks, phase, c = state["checks"], state["phase"], controller()
        if phase == "boot":
            if not director.is_director(window()) or director._timer is not None or ui._layout_timer is not None:
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
            send(MESSAGES[0])
            state["phase"] = "throttled_retrying"
            return .1
        if phase == "throttled_retrying":
            if not wait(lambda: c.status.startswith(codex_retry.WAITING) or error_rows(), limit=20):
                return .05
            state["timings"]["send_to_countdown_s"] = round(time.time() - state["sent_at"], 3)
            state["first_countdown"] = chat_titles.live_label(c.status)
            seconds = int(c.status.rpartition(codex_retry.SEPARATOR)[2])
            checks["countdown_within_2s_of_the_throttle"] = state["timings"]["send_to_countdown_s"] < NOTICE_SECONDS
            checks["countdown_waits_about_a_minute"] = 55 <= seconds <= 63
            checks["live_row_says_retrying_in_n_seconds"] = state["first_countdown"] == (
                "Provider busy — retrying in %d s" % seconds)
            checks["stop_available_while_waiting"] = c.busy and ("stop", None) in actions()
            capture("turn-errors-1-countdown.png")
            state["countdown_seconds"], state["phase"] = seconds, "countdown_moves"
            return 1.6
        if phase == "countdown_moves":
            seconds = int(c.status.rpartition(codex_retry.SEPARATOR)[2]) if c.status.startswith(codex_retry.WAITING) else None
            checks["countdown_counts_down"] = seconds is not None and seconds < state["countdown_seconds"]
            state["stopped_at"] = time.time()
            click("stop")
            state["phase"] = "stopped"
            return .05
        if phase == "stopped":
            if not wait(lambda: idle(), limit=5):
                return .05
            state["timings"]["stop_to_idle_s"] = round(time.time() - state["stopped_at"], 3)
            starts = [r for r in requests() if r["method"] == "turn/start"]
            checks["stop_ends_the_wait_at_once"] = state["timings"]["stop_to_idle_s"] < 1.5 and c.status == "Interrupted"
            checks["stop_sends_nothing_more"] = [r["outcome"] for r in starts] == ["rate"] and not error_rows()
            checks["nothing_applied_after_stop"] = scene_unchanged()
            capture("turn-errors-2-stopped.png")
            # The rest of the suite: the same resend after 2 s instead of a minute.
            codex_retry.RATE_SECONDS, codex_retry.JITTER_SECONDS = 2.0, 0.0
            send(MESSAGES[1])
            state["phase"] = "throttled_notice"
            return .05
        if phase == "throttled_notice":
            if not wait(lambda: error_rows() and idle(), limit=30):
                return .05
            seen, sent = time.time(), requests()
            starts = [r for r in sent if r["method"] == "turn/start"]
            state["timings"]["second_failure_to_notice_s"] = round(seen - starts[-1]["time"], 3)
            checks["two_turn_starts_for_one_message"] = [r["outcome"] for r in starts][1:] == ["rate", "rate"]
            checks["resend_carries_the_note_not_the_message"] = (
                starts[2]["input"] == [{"type": "text", "text": codex_retry.NOTE}]
                and starts[1]["input"][0]["text"].startswith(MESSAGES[1]))
            checks["notice_within_2s_of_the_failure"] = state["timings"]["second_failure_to_notice_s"] < NOTICE_SECONDS
            checks["one_too_many_requests_card"] = [notice_kinds.kind(m) for m in error_rows()] == ["rate"]
            checks["throttle_offers_switch_connection"] = ("welcome_switch", None) in actions()
            checks["chat_idle_after_the_throttle"] = idle() and c.status == "Failed"
            checks["nothing_applied_after_the_throttle"] = scene_unchanged()
            capture("turn-errors-3-too-many-requests.png")
            send(MESSAGES[2])
            state["phase"] = "usage_notice"
            return .05
        if phase == "usage_notice":
            if not wait(lambda: error_rows() and idle(), limit=20):
                return .05
            state["timings"]["send_to_usage_notice_s"] = round(time.time() - state["sent_at"], 3)
            starts = [r for r in requests() if r["method"] == "turn/start"]
            checks["usage_limit_never_resent"] = [r["outcome"] for r in starts] == ["rate", "rate", "rate", "usage"]
            checks["usage_notice_within_2s"] = state["timings"]["send_to_usage_notice_s"] < NOTICE_SECONDS
            checks["one_usage_limit_card"] = [notice_kinds.kind(m) for m in error_rows()] == ["usage"]
            checks["chat_idle_after_the_usage_limit"] = idle() and c.status == "Failed"
            capture("turn-errors-4-usage-limit.png")
            send(MESSAGES[3])
            state["phase"] = "resent_answer"
            return .05
        if phase == "resent_answer":
            if not wait(lambda: idle() and any(m.get("role") == "assistant" and m.get("text") for m in new_rows()),
                        limit=30):
                return .1
            starts = [r for r in requests() if r["method"] == "turn/start"]
            checks["throttle_then_answer_completes"] = (
                [r["outcome"] for r in starts][-2:] == ["rate", "ok"] and c.status == "Completed")
            checks["reply_shown_without_an_error_row"] = not error_rows() and any(
                m.get("text") == "Done." for m in new_rows() if m.get("role") == "assistant")
            receipts = [r for r in c.telemetry.receipts if r.get("session_id") == c.state["id"]]
            state["last_receipt_counts"] = receipts[-1].get("counts") if receipts else None
            checks["receipt_counts_one_throttle_resend"] = (state["last_receipt_counts"] or {}).get(
                "provider_retries_rate") == 1
            checks["live_row_back_after_the_resend"] = not c.status.startswith(codex_retry.WAITING)
            capture("turn-errors-5-resent-answer.png")
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
