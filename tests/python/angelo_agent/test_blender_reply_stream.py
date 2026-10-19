# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Claude plan's reply in the real chat while it streams: drawn as written, never stored, never a key.

Real default startup and real turns through the stand-in ``claude`` CLI, which
streams each StructuredOutput call as Claude Code 2.1.293 does with
fine-grained tool streaming (``fake_provider_cli.plan_events``; rounds in
``reply_stream_fixtures``). The provider, ``claude_stream.RoundStream``, the
controller's event pump, ``reply_overlay`` and ``chat_view`` are the app's own.
What the chat drew is read from ``chat_view.text`` during a redraw, next to a
screenshot. No account, network or inference.

The add-on is imported from this checkout's ``scripts/addons_core`` before the
default startup enables it, as ``test_blender_program_names`` does, so the
suite proves the checkout's Python on a build whose native code matches; the
report records the add-on path.

Covered, in one chat:

1. An answer grows on screen while its plan is written: two frames, the later
   one drawing more of the reply in a taller row, neither storing it; the
   finished plan stores the whole reply.
2. A plan whose reply streamed and which the runtime's schema check then
   refused leaves no reply row on screen, in the transcript or in its save.
3. Stop while a reply streams leaves no reply row on screen or in the save,
   and its notice says only that the request stopped (no script was running).
4. A later round's reply replaces the row while it streams; when that round's
   plan is withdrawn (two plan calls, the last dropping the first's edit), the
   row shows the earlier round's reply again, which ran, and the withdrawn
   text is stored nowhere. The withdrawn call's provisional step ("Teal Cube",
   ``plan_steps``) shows under the live row while it is written and is gone
   once the later call starts, as is every row after the withdrawal.
5. With a connection key bound, a lookup whose operation carries the key, then
   a reply that repeats it across five character fragments: no frame draws
   five characters of it, nor do the opened rows of the plan call whose
   operations carry it and of that operation's own call. The stand-in sends
   each fragment of that reply only once the suite has drawn and checked the
   frame of the one before (``fake_provider_cli.wait_for_gate``), so every
   state of the split key is drawn, on any machine load; the stored reply and
   both rows hold it redacted, and the save never holds it. The plan call's
   Details list its operations, never its reply; the operation's row folds
   into its round's closed work group (``chat_rows.group_blocks``), which is
   opened first, as a person would.

A plan call's own row (the runtime's record, with its schema verdict) keeps
the call's operations but never its reply (``claude_stream``), so 2 and 4 also
hold for the save.

Run through ``run_native_suites.py --gui --include test_blender_reply_stream``;
check ``reply-stream.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-reply-stream-qa"))
OUT.mkdir(parents=True, exist_ok=True)
# Before the startup timer enables the add-on: addon_utils imports it through sys.path.
sys.path[:0] = [str(ROOT / "scripts/addons_core"), str(Path(__file__).resolve().parent)]
import fake_provider_cli  # noqa: E402
import reply_stream_fixtures as rounds  # noqa: E402
from angelo_agent import product_env  # noqa: E402

product_env.clear('DISABLE_DEFAULT_CHAT')  # This suite needs the default Director startup.

ROUNDS = OUT / "rounds"
ROUNDS.mkdir(exist_ok=True)
fake_provider_cli.sign_in(os.environ["HOME"], "claude")  # An everyday sign-in that already exists.
os.environ.update(ANGELO_FAKE_CLI_STATE=str(OUT / "fake-cli"), ANGELO_FAKE_CLI_LOGIN_SECONDS="0.5",
                  ANGELO_FAKE_CLI_ROUNDS=str(ROUNDS))
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "reply-stream.json"
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "observed": {}, "frames": {},
         "fixture": "Stand-in claude CLI streaming scripted plans; no browser, account, network or inference"}
started = time.monotonic()
moments = {}  # The frames the checks compare, by name, with every text run drawn.
# Every run of five characters of the key: none may ever be drawn.
KEY_PIECES = {rounds.KEY[start:start + 5] for start in range(len(rounds.KEY) - 4)}
GATE = OUT / "key-gate"  # The stand-in holds each fragment of the keyed reply until the last one's frame is drawn.
gated = {"streams": [], "acknowledged": -1}


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


def flat(text):
    return " ".join(str(text).split())


def drawn():
    """Redraw the window once; what the chat drew: its text runs, in order, and its content height."""
    from angelo_agent import chat_view
    runs, original = [], chat_view.text

    def recording(value, *args, **kwargs):
        runs.append(str(value))
        return original(value, *args, **kwargs)
    chat_view.text = recording
    try:
        with bpy.context.temp_override(window=window()):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    finally:
        chat_view.text = original
    return {"text": flat(" ".join(runs)), "runs": runs, "content_height": view_state().get("content_height", 0)}


def snap(name=None):
    """A frame of the chat: what it drew, what was streaming and what the transcript held, with a screenshot."""
    from angelo_agent import ui
    frame = dict(drawn(), streaming=streaming(), stored=[m.get("text", "") for m in rows("assistant")])
    if name:
        path = OUT / name
        with bpy.context.temp_override(window=window()):
            # The screenshot reads the shown buffer, a frame behind; nothing runs between these draws.
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
            bpy.ops.screen.screenshot(filepath=str(path))
        state["screenshots"].append(str(path))
        frame["screenshot"] = str(path)
    frame["status"] = ui.get_controller().status
    frame["planned"] = planned_now()
    return frame


def planned_now():
    """The provisional steps the live row carries this moment (plan_steps), as their drawn labels."""
    from angelo_agent import chat_rows, plan_steps, ui
    rows_, more = plan_steps.rows(ui.get_controller().status)
    return [chat_rows.planned_label(kind, name, count) for _, kind, name, count in rows_] + ([more] if more else [])


def keep(name, frame):
    moments[name] = frame
    state["frames"][name] = {key: value for key, value in frame.items() if key != "runs"}
    return frame


def streaming():
    """The reply text streaming for the current request (reply_overlay), joined; empty when none is."""
    from angelo_agent import reply_overlay, ui
    controller = ui.get_controller()
    replies = controller.__dict__.get(reply_overlay.ATTRIBUTE) or {}
    return "".join(text for (generation, _), text in replies.items() if generation == controller._request_generation)


def rows(role=None):
    from angelo_agent import ui
    return [m for m in ui.get_controller().state["messages"] if role is None or m["role"] == role]


def saved():
    """The conversation as saved: the app's own save of the current transcript, read back from disk."""
    from angelo_agent import ui
    controller = ui.get_controller()
    controller.save()
    return controller.store.path.read_text(encoding="utf-8")


def saved_transcript():
    """The saved conversation as a load reads it: the base and every appended record (sessions.read_transcript)."""
    from angelo_agent.sessions import read_transcript
    saved()
    from angelo_agent import ui
    return read_transcript(ui.get_controller().store.path)


def saved_reply_rows(text):
    """Saved rows that hold ``text``, by role; an activity row is the runtime's record of a plan call."""
    return sorted({m["role"] + ("/" + m.get("tool", "") if m["role"] == "activity" else "")
                   for m in saved_transcript()["messages"] if text in json.dumps(m, ensure_ascii=False)})


def played():
    counter = ROUNDS / "next"
    return int(counter.read_text()) if counter.is_file() else 0


def begin(phase, scripted, message):
    """Script the rounds of the next request from the stand-in's next round on, then send ``message``."""
    base = played()
    for offset, events in enumerate(scripted):
        (ROUNDS / ("round-%d.json" % (base + offset))).write_text(json.dumps(events), encoding="utf-8")
    state.update(phase=phase, base=base, rounds=len(scripted))
    send(message)
    return .1


def request_done(controller):
    return not controller.busy and played() == state["base"] + state["rounds"]


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_REPLY_STREAM", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(condition, limit=60):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + state["phase"]
    return False


def boot(controller, checks):
    from angelo_agent import director, ui
    if not director.is_director(window()) or director._timer is not None or ui._layout_timer is not None:
        return .2
    import angelo_agent
    state["observed"]["add_on"] = str(Path(angelo_agent.__file__).resolve().parent)
    checks["the_add_on_is_this_checkouts"] = (state["observed"]["add_on"]
                                              == str((ROOT / "scripts/addons_core/angelo_agent").resolve()))
    assert checks["the_add_on_is_this_checkouts"], "The bundled add-on loaded instead of this checkout's"
    if not wait(lambda: not ui._authentication.running and ("welcome_connect", "claude_subscription") in actions()):
        return .2
    click("welcome_connect", "claude_subscription")
    state["phase"] = "connected"
    return .3


def connected(controller, checks):
    from angelo_agent import quick_connect, ui
    if not wait(lambda: quick_connect.flow("claude_subscription") is None and controller.models
                and ui.connection_available()):
        return .2
    controller.set_options(model="claude-opus-5-5")
    chat()[0].tag_redraw()
    return begin("grow", rounds.grow_rounds(), "What is in the default scene?")


def grow(controller, checks):
    text, reply = streaming(), rounds.GROW["reply"]
    if len(text) >= 80 and "grow_early" not in moments:
        keep("grow_early", snap("reply-1-streaming-early.png"))
    elif len(text) >= 280 and "grow_early" in moments and "grow_later" not in moments:
        keep("grow_later", snap("reply-2-streaming-later.png"))
    if not wait(lambda: request_done(controller)):
        return .1
    early, later = moments.get("grow_early"), moments.get("grow_later")
    checks["1_two_frames_were_drawn_while_the_answer_streamed"] = bool(early and later)
    if early and later:
        checks["1_each_frame_draws_what_had_streamed"] = (flat(early["streaming"]) in early["text"]
                                                          and flat(later["streaming"]) in later["text"])
        checks["1_the_answer_visibly_grows"] = (len(later["streaming"]) > len(early["streaming"])
                                                and flat(later["streaming"]) not in early["text"])
        checks["1_its_row_grows_taller"] = later["content_height"] > early["content_height"]
        checks["1_a_streaming_answer_is_never_stored"] = not any(reply[:30] in t for t in early["stored"] + later["stored"])
    final = keep("grow_final", snap("reply-3-complete.png"))
    checks["1_the_finished_plan_stores_its_whole_reply"] = (controller.status == "Completed"
                                                            and rows("assistant")[-1]["text"] == reply)
    checks["1_and_it_stays_drawn"] = flat(reply) in final["text"] and not final["streaming"]
    return begin("refused", rounds.refused_rounds(), "Delete the three cubes.")


def refused(controller, checks):
    claim = rounds.REFUSED["reply"]
    if streaming() == claim and "refused_streaming" not in moments:
        keep("refused_streaming", snap("refused-1-reply-streamed.png"))
    if not wait(lambda: request_done(controller)):
        return .1
    final = keep("refused_after", snap("refused-2-after-refusal.png"))
    state["observed"]["refused_status"] = controller.status
    streamed = moments.get("refused_streaming")
    checks["2_the_refused_plans_reply_was_drawn_first"] = bool(streamed) and claim in streamed["text"]
    checks["2_the_round_did_not_complete"] = controller.status != "Completed"
    checks["2_no_reply_row_is_drawn"] = claim not in final["text"] and not final["streaming"]
    checks["2_no_reply_row_is_in_the_transcript"] = not any(claim in m.get("text", "") for m in rows("assistant"))
    holders = state["observed"]["refused_saved_in"] = saved_reply_rows(claim)
    checks["2_nothing_of_it_is_in_the_saved_transcript"] = holders == []
    return begin("stop", rounds.stopped_rounds(), "Walk me through every light in the scene.")


def stop(controller, checks):
    if "stop_streaming" not in moments:
        if not wait(lambda: len(streaming()) >= 40):
            return .1
        keep("stop_streaming", snap("stop-1-reply-streaming.png"))
        click("stop")
        return .2
    if not wait(lambda: not controller.busy):
        return .1
    stopping = moments["stop_streaming"]
    partial = stopping["streaming"]
    final = keep("stop_after", snap("stop-2-after-stop.png"))
    state["observed"]["stopped_status"] = controller.status
    checks["3_the_partial_reply_was_drawn_before_stop"] = flat(partial) in stopping["text"]
    checks["3_stop_ends_the_turn"] = controller.status == "Interrupted"
    checks["3_no_reply_row_is_drawn"] = flat(partial[:30]) not in final["text"] and not final["streaming"]
    checks["3_no_reply_row_is_in_the_transcript"] = not any(partial[:30] in m.get("text", "") for m in rows("assistant"))
    checks["3_nothing_of_it_is_in_the_saved_transcript"] = saved_reply_rows(partial[:30]) == []
    notice = state["observed"]["stop_notice"] = rows("system")[-1]["text"] if rows("system") else ""
    checks["3_the_notice_says_only_that_it_stopped"] = notice == "Stopped" and "legacy script" not in final["text"]
    return begin("withdrawal", rounds.withdrawal_rounds(), "Check the cube is manifold.")


def withdrawal(controller, checks):
    first, later = rounds.LOOKUP["reply"], rounds.WITHDRAWN[0]["reply"]
    if "Teal Cube" in planned_now() and "withdrawal_rows" not in moments:
        keep("withdrawal_rows", snap("withdrawal-0-withdrawn-call-rows.png"))
    if streaming().startswith(rounds.WITHDRAWN[1]["reply"][:12]) and "withdrawal_second_call" not in moments:
        keep("withdrawal_second_call", snap())
    if streaming().startswith(later[:20]) and "withdrawal_later_streams" not in moments:
        keep("withdrawal_later_streams", snap("withdrawal-1-later-reply-streams.png"))
    notice = any("Nothing of the last plan ran" in m.get("text", "") for m in rows("system"))
    if notice and played() == state["base"] + 3 and controller.busy and "withdrawal_after" not in moments:
        keep("withdrawal_after", snap("withdrawal-2-after-withdrawal.png"))
    if not wait(lambda: request_done(controller)):
        return .1
    streamed, withdrawn = moments.get("withdrawal_later_streams"), moments.get("withdrawal_after")
    checks["4_both_moments_were_drawn"] = bool(streamed and withdrawn)
    if streamed and withdrawn:
        checks["4_the_later_reply_replaced_the_row_while_it_streamed"] = (
            later[:20] in streamed["text"] and first not in streamed["text"] and first in streamed["stored"])
        checks["4_after_the_withdrawal_the_earlier_reply_is_back"] = (
            first in withdrawn["text"] and "teal cube" not in withdrawn["text"] and not withdrawn["streaming"])
        checks["4_the_earlier_reply_stayed_stored"] = withdrawn["stored"].count(first) == 1 and not any(
            "teal cube" in t for t in withdrawn["stored"])
    rows_shown = moments.get("withdrawal_rows")
    checks["4_the_withdrawn_calls_step_showed_while_written"] = bool(rows_shown) and "Teal Cube" in rows_shown["text"]
    second = moments.get("withdrawal_second_call")
    checks["4_and_is_gone_once_the_later_call_starts_and_after"] = bool(second and withdrawn) and all(
        not frame["planned"] and "Teal Cube" not in frame["text"].replace(later, "") for frame in (second, withdrawn))
    final = keep("withdrawal_final", snap("withdrawal-3-corrected-answer.png"))
    checks["4_the_corrected_answer_completes_the_turn"] = (controller.status == "Completed"
                                                           and rows("assistant")[-1]["text"] == rounds.ANSWER["reply"])
    holders = state["observed"]["withdrawn_saved_in"] = saved_reply_rows("teal cube")
    checks["4_no_withdrawn_reply_row_is_saved"] = not any(not holder.startswith("activity/") for holder in holders)
    checks["4_nothing_withdrawn_is_in_the_saved_transcript"] = holders == []
    # The connection's key, as the app holds one in memory: sending binds it (ui -> Controller.select), and the
    # chat redacts it. A subscription connection never passes it to the CLI (providers.child_environment).
    from angelo_agent import ui
    ui._memory_keys[controller.state["connection_id"]] = rounds.KEY
    shutil.rmtree(GATE, ignore_errors=True)
    GATE.mkdir(parents=True)
    from angelo_agent.claude_stream import PlanReply
    gated.update(streams=rounds.keyed_streams(PlanReply()), acknowledged=-1)
    return begin("key", rounds.key_rounds(GATE), "Which key does this connection use?")


def leaked(frame):
    """Five characters of the key in what a frame drew, even split between two text runs."""
    pieces = [piece for piece in KEY_PIECES if piece in frame["text"] or piece in "".join(frame["runs"])]
    state["observed"].setdefault("key_leaks", []).extend(pieces)
    return pieces


def fragment_frame(observed):
    """The frame of the keyed reply's latest fragment, checked, with a screenshot of its first held and whole states."""
    leaked(drawn())
    raw = streaming()
    observed["key_fragments_drawn"] = observed.get("key_fragments_drawn", 0) + 1
    if rounds.held_back(raw):
        observed["held_back_frames"] = observed.get("held_back_frames", 0) + 1
        if "key_held_back" not in moments:
            leaked(keep("key_held_back", snap("key-1-split-key-held-back.png")))
    if rounds.KEY in raw and "key_whole_redacted" not in moments:
        leaked(keep("key_whole_redacted", snap("key-2-whole-key-redacted.png")))


def key(controller, checks):
    observed, streams = state["observed"], gated["streams"]
    if controller.busy:
        observed["key_bound"] = observed.get("key_bound", True) and controller.api_key == rounds.KEY
    sent = rounds.gate_sent(GATE)
    if sent > gated["acknowledged"]:  # Fragment ``sent`` is out; the next waits until its frame is drawn.
        if not wait(lambda: not streams[sent] or streaming() == streams[sent]):
            leaked(drawn())
            return .05
        fragment_frame(observed)
        rounds.acknowledge(GATE, sent)
        gated["acknowledged"] = sent
        return .05
    leaked(drawn())
    if not wait(lambda: request_done(controller)):
        return .1
    leaked(keep("key_final", snap("key-3-complete.png")))
    stored = rows("assistant")[-1]["text"]
    whole = moments.get("key_whole_redacted")
    checks["5_the_connection_key_was_bound_for_the_turn"] = state["observed"].get("key_bound") is True
    held = observed.get("held_back_frames", 0)  # Every state that ends in the start of the key, each drawn once.
    checks["5_frames_were_drawn_while_a_split_key_streamed"] = held >= 2 and held == sum(
        bool(rounds.held_back(text)) for text in streams)
    checks["5_every_fragment_of_it_was_drawn_and_checked"] = (gated["acknowledged"] == len(streams) - 1
                                                              and observed.get("key_fragments_drawn") == len(streams))
    checks["5_the_whole_key_is_drawn_redacted"] = bool(whole) and "[REDACTED]" in whole["text"]
    checks["5_the_stored_reply_holds_it_redacted"] = "[REDACTED]" in stored and rounds.KEY not in stored
    checks["5_the_saved_transcript_never_holds_it"] = not any(piece in saved() for piece in KEY_PIECES)
    checks["5_no_row_holds_it"] = not any(piece in json.dumps(rows(), ensure_ascii=False) for piece in KEY_PIECES)
    # The runtime's record of the plan call whose operations carry the key, opened: its details draw them.
    call = next(m for m in reversed(rows("activity")) if m.get("tool") == "StructuredOutput"
                and m.get("arguments", {}).get("operations"))
    click("expand", call["call_id"])
    state["phase"] = "key_details"
    return .3


def key_details(controller, checks):
    from angelo_agent.ui_text import iface_
    details = keep("key_plan_call_details", snap("key-4-plan-call-details.png"))
    leaked(details)
    # A plan call's Details: its operations under one heading, never the reply it carried (chat_rows._details).
    checks["5_the_plan_calls_details_were_drawn"] = (iface_("Planned operations:") in details["text"]
                                                     and "angelo_scene_search" in details["text"]
                                                     and rounds.KEY_LOOKUP["reply"] not in details["text"])
    checks["5_they_show_the_key_redacted"] = "[REDACTED]" in details["text"]
    # The operation's own row (the host's record of the call it ran) folds into its round's work group: open that.
    call = next(m for m in reversed(rows("activity")) if m.get("tool") == "angelo_scene_search")
    group = rounds.work_group(rows(), call["call_id"])
    state["observed"]["key_operation_group"] = view_state().get("progress_drawn", {}).get(group)
    checks["5_the_operation_folds_into_a_closed_work_group"] = (
        ("expand", group) in actions() and ("expand", call["call_id"]) not in actions()
        and group not in view_state().get("expanded", set()))
    click("expand", group)
    state.update(phase="key_operation_group", key_operation=call["call_id"])
    return .3


def key_operation_group(controller, checks):
    leaked(drawn())
    if not wait(lambda: ("expand", state["key_operation"]) in actions()):
        return .2
    leaked(keep("key_operation_group", snap("key-5-work-group-open.png")))
    click("expand", state["key_operation"])
    state["phase"] = "key_operation_details"
    return .3


def key_operation_details(controller, checks):
    details = keep("key_operation_details", snap("key-6-operation-details.png"))
    leaked(details)
    from angelo_agent import ui
    ui._memory_keys.pop(controller.state["connection_id"], None)
    checks["5_the_operations_details_were_drawn"] = "angelo_scene_search" in details["text"] and "[REDACTED]" in details["text"]
    # Its own Details, not only the plan call's still open above it: the row opened and the transcript grew.
    checks["5_the_operations_own_row_opened"] = (
        state["key_operation"] in view_state().get("expanded", set())
        and details["content_height"] > moments["key_operation_group"]["content_height"])
    checks["5_no_frame_ever_drew_five_characters_of_the_key"] = not state["observed"]["key_leaks"]
    finish()
    return None


PHASES = {"boot": boot, "connected": connected, "grow": grow, "refused": refused, "stop": stop,
          "withdrawal": withdrawal, "key": key, "key_details": key_details,
          "key_operation_group": key_operation_group, "key_operation_details": key_operation_details}


def tick():
    try:
        assert time.monotonic() - started < 300, "Reply-stream fixture timed out in " + state["phase"]
        from angelo_agent import ui
        return PHASES[state["phase"]](ui.get_controller(), state["checks"])
    except Exception as error:
        finish(error)
        return None


bpy.app.timers.register(tick, first_interval=.5)
