# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A script being written in the real chat: its newest lines drawn under the live row, never stored, never a key.

Real default startup and real turns through the stand-in ``claude`` CLI, which
streams each StructuredOutput call as Claude Code does with fine-grained tool
streaming (``fake_provider_cli.plan_events``; rounds in
``script_stream_fixtures``). The provider, ``claude_stream.RoundStream``, the
controller's event pump, ``script_overlay``, ``chat_rows`` and ``chat_view``
are the app's own. What the chat drew is read from ``chat_view.text`` during a
redraw, next to a screenshot. No account, network or inference, and no script
runs.

Covered, in one chat:

1. A script plan's code shows while it is written: two frames, the later one
   drawing a later line and a larger line count under "Writing the script",
   the row as tall in both; the script's step stays above it; nothing of the
   code is stored.
2. The runtime refuses the plan: the well is gone from the screen, and the
   code is in no row but the plan call's own record (the runtime's, which keeps
   a call's operations, ``claude_stream``), in the transcript and in the save.
3. Stop while the code streams: the well is gone, nothing is stored.
4. A script that carries the connection's key, fragment by fragment behind a
   gate (``fake_provider_cli.wait_for_gate``): no frame draws five characters
   of it.
5. The main thread stays responsive while code streams: the gaps between
   event-loop ticks during phase 1 against an idle second before it, and the
   time of a full window redraw with and without the well (recorded; checked
   only against a loose ceiling here, compared between builds by the plan in
   ``doc/angelo/performance.rst``, "Streamed progress").

Run through ``run_native_suites.py --gui --include test_blender_script_stream``;
check ``script-stream.json`` for ``success`` and ``phase == "complete"``.

``ANGELO_SCRIPT_STREAM_TIMING=1`` runs phase 1's stream only and records check 5's numbers without the feature's
checks, so the same paced stream times a build without the script well (the base of the A/B in
``doc/angelo/performance.rst``, "Streamed progress") and one with it.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-script-stream-qa"))
OUT.mkdir(parents=True, exist_ok=True)
# Before the startup timer enables the add-on: addon_utils imports it through sys.path.
sys.path[:0] = [str(ROOT / "scripts/addons_core"), str(Path(__file__).resolve().parent)]
import fake_provider_cli  # noqa: E402
import reply_stream_fixtures  # noqa: E402
import script_stream_fixtures as rounds  # noqa: E402
from angelo_agent import product_env  # noqa: E402

product_env.clear('DISABLE_DEFAULT_CHAT')  # This suite needs the default Director startup.
from angelo_agent import script_overlay  # noqa: E402
script_overlay.SHOWN = True  # The well ships off in 1.0.4; this suite is of the well itself.

ROUNDS = OUT / "rounds"
ROUNDS.mkdir(exist_ok=True)
fake_provider_cli.sign_in(os.environ["HOME"], "claude")
os.environ.update(ANGELO_FAKE_CLI_STATE=str(OUT / "fake-cli"), ANGELO_FAKE_CLI_LOGIN_SECONDS="0.5",
                  ANGELO_FAKE_CLI_ROUNDS=str(ROUNDS))
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "script-stream.json"
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "observed": {}, "frames": {},
         "fixture": "Stand-in claude CLI streaming scripted plans; no browser, account, network or inference"}
started = time.monotonic()
moments = {}
KEY_PIECES = {rounds.KEY[start:start + 5] for start in range(len(rounds.KEY) - 4)}
GATE = OUT / "key-gate"
CAPTION = "Writing the script"
TIMING_ONLY = os.environ.get("ANGELO_SCRIPT_STREAM_TIMING") == "1"
ticks = {"last": None, "gaps": {}, "phase": None}  # Event-loop tick gaps by measured phase (check 5).


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
    """Redraw the window once; what the chat drew (its text runs), the live block's height and the redraw's time."""
    from angelo_agent import chat_rows, chat_view, ui
    runs, original = [], chat_view.text

    def recording(value, *args, **kwargs):
        runs.append(str(value))
        return original(value, *args, **kwargs)
    chat_view.text = recording
    begun = time.perf_counter()
    try:
        with bpy.context.temp_override(window=window()):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    finally:
        chat_view.text = original
    controller = ui.get_controller()
    live = chat_rows.live_block(controller)[3] if controller.busy else 0
    return {"text": flat(" ".join(runs)), "runs": runs, "live_height": live,
            "redraw_ms": round((time.perf_counter() - begun) * 1000, 2)}


def shown():
    from angelo_agent import ui
    try:
        from angelo_agent import script_overlay
    except ImportError:  # A build without the script well (TIMING_ONLY's base).
        return None
    return script_overlay.shown(ui.get_controller())


def writing(controller):
    """Whether the live row says a plan is being written: the stream's measured window, on every build."""
    from angelo_agent.claude_stream import STATUS_PLAN
    from angelo_agent.plan_progress import split
    return controller.busy and split(controller.status)[0] == STATUS_PLAN


def snap(name=None):
    from angelo_agent import ui
    frame = dict(drawn(), script=shown(), stored=json.dumps(ui.get_controller().state["messages"]))
    if name:
        path = OUT / name
        with bpy.context.temp_override(window=window()):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
            bpy.ops.screen.screenshot(filepath=str(path))
        state["screenshots"].append(str(path))
        frame["screenshot"] = str(path)
    frame["status"] = ui.get_controller().status
    return frame


def keep(name, frame):
    moments[name] = frame
    state["frames"][name] = {key: value for key, value in frame.items() if key not in ("runs", "stored")}
    return frame


def saved():
    from angelo_agent import ui
    from angelo_agent.sessions import read_transcript
    controller = ui.get_controller()
    controller.save()
    return json.dumps(read_transcript(controller.store.path), ensure_ascii=False)


def played():
    counter = ROUNDS / "next"
    return int(counter.read_text()) if counter.is_file() else 0


def begin(phase, scripted, message):
    base = played()
    for offset, events in enumerate(scripted):
        (ROUNDS / ("round-%d.json" % (base + offset))).write_text(json.dumps(events), encoding="utf-8")
    state.update(phase=phase, base=base, rounds=len(scripted))
    send(message)
    return .1


def request_done(controller):
    return not controller.busy and played() == state["base"] + state["rounds"]


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_SCRIPT_STREAM", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(condition, limit=60):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + state["phase"]
    return False


def probe():
    """An every-tick timer: the gap since the last tick, filed under the measured phase (check 5)."""
    now = time.perf_counter()
    if ticks["phase"] and ticks["last"] is not None:
        ticks["gaps"].setdefault(ticks["phase"], []).append((now - ticks["last"]) * 1000)
    ticks["last"] = now
    return 0.0 if state["phase"] != "complete" else None


def gap_summary(gaps):
    ordered = sorted(gaps)
    if not ordered:
        return {}
    pick = lambda q: round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 2)  # noqa: E731
    return {"ticks": len(ordered), "p50_ms": pick(.5), "p95_ms": pick(.95), "p99_ms": pick(.99),
            "max_ms": round(ordered[-1], 2)}


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
    state.update(phase="idle", idle_until=time.monotonic() + 1.0)
    keep("idle", snap())
    ticks["phase"] = "idle"
    return .05


def idle(controller, checks):
    if time.monotonic() < state["idle_until"]:
        return .05
    ticks["phase"] = None
    return begin("written", rounds.written_rounds(), "Add four lamp posts.")


def written(controller, checks):
    script = shown()
    if writing(controller) and ticks["phase"] is None and "written_after" not in moments:
        ticks["phase"] = "streaming"
    if TIMING_ONLY and writing(controller) and len(state.setdefault("timing_redraws", [])) < 40:
        state["timing_redraws"].append(drawn()["redraw_ms"])  # A full redraw every tick while it streams.
    if script and script[0] >= 8 and "written_early" not in moments:
        keep("written_early", snap("script-1-streaming-early.png"))
    elif script and "written_early" in moments and script[0] >= moments["written_early"]["script"][0] + 20 \
            and "written_later" not in moments:
        keep("written_later", snap("script-2-streaming-later.png"))
    if not wait(lambda: request_done(controller)):
        return .1
    ticks["phase"] = None
    if TIMING_ONLY:
        state["observed"]["main_thread"] = {"tick_gaps": {name: gap_summary(values)
                                                          for name, values in ticks["gaps"].items()},
                                            "redraw_ms": {"idle": moments["idle"]["redraw_ms"],
                                                          "streaming": state.get("timing_redraws", [])}}
        checks["5_the_stream_was_measured"] = state["observed"]["main_thread"]["tick_gaps"].get(
            "streaming", {}).get("ticks", 0) > 50
        finish()
        return None
    early, later = moments.get("written_early"), moments.get("written_later")
    checks["1_two_frames_were_drawn_while_the_code_streamed"] = bool(early and later)
    if early and later:
        checks["1_each_frame_draws_the_caption_and_the_newest_line"] = all(
            CAPTION in frame["text"] and flat(frame["script"][1][-1][:20]) in frame["text"] for frame in (early, later))
        checks["1_the_code_visibly_advances"] = (later["script"][0] > early["script"][0]
                                                 and flat(later["script"][1][-1]) != flat(early["script"][1][-1]))
        checks["1_the_row_does_not_grow_line_by_line"] = early["live_height"] == later["live_height"] > 0
        checks["1_its_step_stays_above_it"] = all("Four lamp posts" in frame["text"] for frame in (early, later))
        checks["1_code_being_written_is_never_stored"] = not any(
            "Painted Iron" in frame["stored"] for frame in (early, later))
    final = keep("written_after", snap("script-3-after-refusal.png"))
    checks["2_the_well_is_gone_after_the_refusal"] = CAPTION not in final["text"] and final["script"] is None
    checks["2_no_line_of_it_is_drawn"] = "primitive_cube_add" not in final["text"]
    holders = state["observed"]["code_saved_in"] = sorted({
        message["role"] + "/" + message.get("tool", "") for message in json.loads(saved())["messages"]
        if "Painted Iron" in json.dumps(message, ensure_ascii=False)})
    checks["2_only_the_plan_calls_own_record_holds_the_code"] = holders == ["activity/StructuredOutput"]
    gaps = {name: gap_summary(values) for name, values in ticks["gaps"].items()}
    redraws = {"idle": moments["idle"]["redraw_ms"], "streaming": [moments[name]["redraw_ms"] for name in
                                                                  ("written_early", "written_later") if name in moments]}
    state["observed"]["main_thread"] = {"tick_gaps": gaps, "redraw_ms": redraws}
    checks["5_the_main_thread_kept_ticking_while_code_streamed"] = (
        gaps.get("streaming", {}).get("ticks", 0) > 50 and gaps["streaming"]["max_ms"] < 250)
    return begin("stop", rounds.stopped_rounds(), "Add a row of lamps.")


def stop(controller, checks):
    if "stop_streaming" not in moments:
        if not wait(lambda: bool(shown()) and shown()[0] >= 10):
            return .1
        keep("stop_streaming", snap("script-4-before-stop.png"))
        click("stop")
        return .2
    if not wait(lambda: not controller.busy):
        return .1
    final = keep("stop_after", snap("script-5-after-stop.png"))
    checks["3_the_code_was_drawn_before_stop"] = CAPTION in moments["stop_streaming"]["text"]
    checks["3_stop_ends_the_turn_and_the_well"] = (controller.status == "Interrupted" and final["script"] is None
                                                   and CAPTION not in final["text"])
    # Stopped before the call was complete: not even the plan call's record exists.
    checks["3_nothing_of_it_is_stored"] = "Weathered Bronze" not in final["stored"] and "Weathered Bronze" not in saved()
    # The connection's key, as the app holds one in memory: sending binds it (ui -> Controller.select), and the
    # chat redacts with it. Setting controller.api_key directly is undone by that send, so nothing would be redacted
    # and the checks below would test an unbound key (test_blender_reply_stream binds it the same way).
    from angelo_agent import ui
    ui._memory_keys[controller.state["connection_id"]] = rounds.KEY
    state["observed"].update(key_leaks=[], key_unbound_frames=[], key_well_frames=0, key_drawn_redacted=0)
    state["key_streams"] = 0
    return begin("key", rounds.keyed_rounds(GATE), "How long is the token?")


def key(controller, checks):
    sent = reply_stream_fixtures.gate_sent(GATE)
    if sent >= state["key_streams"]:
        frame = drawn()
        if controller.api_key != rounds.KEY:
            state["observed"]["key_unbound_frames"].append(sent)
        if CAPTION in frame["text"]:
            state["observed"]["key_well_frames"] += 1
            script = shown()
            if script and any("[REDACTED]" in line for line in script[1]) and "[REDACTED]" in frame["text"]:
                state["observed"]["key_drawn_redacted"] += 1   # The key's line was drawn, as [REDACTED].
        leaks = [piece for piece in KEY_PIECES if piece in frame["text"]]
        if leaks:
            state["observed"]["key_leaks"].append({"fragment": sent, "pieces": leaks})
        if sent == 12:
            keep("key_streaming", snap("script-6-key-streaming.png"))
        reply_stream_fixtures.acknowledge(GATE, sent)
        state["key_streams"] = sent + 1
    if not wait(lambda: request_done(controller), limit=90):
        return .02
    from angelo_agent import ui
    checks["4_the_keyed_script_was_drawn"] = state["key_streams"] > 10 and state["observed"]["key_well_frames"] > 0
    checks["4_the_key_was_bound_in_every_checked_frame"] = not state["observed"]["key_unbound_frames"]
    checks["4_the_keys_line_was_drawn_redacted"] = state["observed"]["key_drawn_redacted"] > 0
    checks["4_no_frame_ever_drew_five_characters_of_the_key"] = not state["observed"]["key_leaks"]
    stored = saved()
    # The plan call's own record keeps its operations (claude_stream), so the key is there, redacted.
    checks["4_nothing_of_it_is_stored_unredacted"] = rounds.KEY not in stored and "[REDACTED]" in stored
    ui._memory_keys.pop(controller.state["connection_id"], None)
    finish()
    return None


PHASES = {"boot": boot, "connected": connected, "idle": idle, "written": written, "stop": stop, "key": key}


def tick():
    try:
        assert time.monotonic() - started < 300, "Script-stream fixture timed out in " + state["phase"]
        from angelo_agent import ui
        return PHASES[state["phase"]](ui.get_controller(), state["checks"])
    except Exception as error:
        finish(error)
        return None


bpy.app.timers.register(tick, first_interval=.5)
bpy.app.timers.register(probe, first_interval=.5)
