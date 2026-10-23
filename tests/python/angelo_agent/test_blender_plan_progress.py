# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A plan's provisional steps in the real chat while it is written: early, labelled Planned, never kept.

Real default startup and real turns through the stand-in ``claude`` CLI, which streams each StructuredOutput
call as Claude Code 2.1.293 does with fine-grained tool streaming, after a thinking block, at the pace Opus 5.5
wrote a recorded arched-footbridge plan (``plan_progress_fixtures``). The provider,
``claude_stream``, ``plan_progress``, ``plan_steps``, the controller's event pump and ``chat_rows`` are the
app's own; every live-row draw is recorded where it is drawn (``chat_rows._note``), with a screenshot of the
moments checked. No account, network or inference.

The add-on is imported from this checkout's ``scripts/addons_core``, as ``test_blender_reply_stream`` does.

Covered, in one chat:

1. Timing: the first provisional row ("Deck") is drawn within the stand-in's own time to write that name plus a
   small allowance, seconds before the plan is complete; the rows grow to the whole plan ("Deck", "Post ×4",
   "Rail ×2", "Grey Stone"), each with a Planned chip, none stored; when the plan has run they are gone and the
   real rows are the transcript's.
2. A plan the runtime's schema check refuses after it streamed: during the pause before the model's next call
   the live row shows none of its rows, and nothing of them is stored or saved.
3. Stop while the rows show: they go with the request, and nothing of the plan ran.
4. A step named with the connection's key, in five-character fragments: no draw of the whole phase ever shows
   five characters of the key; the row shows it redacted.
5. Arabic, with the bridge as Claude writes it once ``batch`` is exact (a post and a rail, then a batch each):
   the unit's row counts its batch once that is complete ("Ar Post ×4"), under the mirrored live row
   (screenshot), and the batch's copies are built.

Run through ``run_native_suites.py --gui --include test_blender_plan_progress``; check ``plan-progress.json``
for ``success`` and ``phase == "complete"``.
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-plan-progress-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path[:0] = [str(ROOT / "scripts/addons_core"), str(Path(__file__).resolve().parent)]
import fake_provider_cli  # noqa: E402
import plan_progress_fixtures as rounds  # noqa: E402
import reply_stream_fixtures as base  # noqa: E402
from angelo_agent import product_env  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')  # This suite needs the default Director startup.

ROUNDS = OUT / "rounds"
ROUNDS.mkdir(exist_ok=True)
fake_provider_cli.sign_in(os.environ["HOME"], "claude")
os.environ.update(ANGELO_FAKE_CLI_STATE=str(OUT / "fake-cli"), ANGELO_FAKE_CLI_LOGIN_SECONDS="0.5",
                  ANGELO_FAKE_CLI_ROUNDS=str(ROUNDS))
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "plan-progress.json"
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "observed": {}, "frames": {},
         "fixture": "Stand-in claude CLI streaming scripted plans; no browser, account, network or inference"}
started = time.monotonic()
moments = {}
KEY_PIECES = {base.KEY[start:start + 5] for start in range(len(base.KEY) - 4)}
draws = []  # Every live-row draw of the current phase: (seconds since its message was sent, label, rows drawn).


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


def install_recorder():
    """Record every draw of the live row where it is drawn (chat_rows._note), and every text run of the chat."""
    from angelo_agent import chat_rows, chat_view
    original_note, original_text = chat_rows._note, chat_view.text

    def note(drawn_state, key, role, title, status):
        original_note(drawn_state, key, role, title, status)
        if key == "live" and "sent" in state and len(draws) < 20000:
            draws.append((time.monotonic() - state["sent"], str(title), tuple(str(item) for item in status)))

    def text(value, *args, **kwargs):
        pieces = [piece for piece in KEY_PIECES if piece in str(value)]
        if pieces:
            state["observed"].setdefault("key_leaks", []).extend(pieces)
        return original_text(value, *args, **kwargs)
    chat_rows._note, chat_view.text = note, text


def flat(text):
    return " ".join(str(text).split())


def drawn():
    """Redraw the window once; what the chat drew: its text runs and the live row's rows."""
    from angelo_agent import chat_view
    runs, original = [], chat_view.text

    def recording(value, *args, **kwargs):
        runs.append(str(value))
        return original(value, *args, **kwargs)
    view_state().get("progress_drawn", {}).pop("live", None)  # Only this frame's live row, if it drew one.
    chat_view.text = recording
    try:
        with bpy.context.temp_override(window=window()):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
    finally:
        chat_view.text = original
    live = view_state().get("progress_drawn", {}).get("live")
    return {"text": flat(" ".join(runs)), "live": [str(item) for item in live[2]] if live else []}


def planned_now():
    """The rows the live row carries this moment (plan_steps), as their drawn labels."""
    from angelo_agent import chat_rows, plan_steps, ui
    rows, more = plan_steps.rows(ui.get_controller().status)
    return [chat_rows.planned_label(kind, name, count) for _, kind, name, count in rows] + ([more] if more else [])


def snap(name=None):
    from angelo_agent import ui
    frame = dict(drawn(), planned=planned_now(), stored=json.dumps(ui.get_controller().state["messages"]))
    if name:
        path = OUT / name
        with bpy.context.temp_override(window=window()):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
            bpy.ops.screen.screenshot(filepath=str(path))
        state["screenshots"].append(str(path))
        frame["screenshot"] = str(path)
    frame["status"] = ui.get_controller().status.replace("\x1f", " | ").replace("\x1e", " / ").replace("\x1d", ",")
    return frame


def keep(name, frame):
    moments[name] = frame
    state["frames"][name] = {key: value for key, value in frame.items() if key != "stored"}
    return frame


def rows(role=None):
    from angelo_agent import ui
    return [m for m in ui.get_controller().state["messages"] if role is None or m["role"] == role]


def saved():
    from angelo_agent import ui
    controller = ui.get_controller()
    controller.save()
    return controller.store.path.read_text(encoding="utf-8")


def played():
    counter = ROUNDS / "next"
    return int(counter.read_text()) if counter.is_file() else 0


def begin(phase, scripted, message):
    base_round = played()
    for offset, events in enumerate(scripted):
        (ROUNDS / ("round-%d.json" % (base_round + offset))).write_text(json.dumps(events), encoding="utf-8")
    draws.clear()
    state.update(phase=phase, base=base_round, rounds=len(scripted), sent=time.monotonic())
    send(message)
    return .05


def request_done(controller):
    return not controller.busy and played() == state["base"] + state["rounds"]


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_PLAN_PROGRESS", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.context.preferences.view.language = "en_US"
    bpy.ops.wm.quit_blender()


def wait(condition, limit=60):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + state["phase"]
    return False


def first_draw(test):
    return next((seconds for seconds, label, drawn_rows in draws if test(label, drawn_rows)), None)


def boot(controller, checks):
    from angelo_agent import director, ui
    if not director.is_director(window()) or not qa_boot.started():
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
    install_recorder()
    return begin("build", rounds.build_rounds(), "Add a slightly arched stone footbridge at the origin.")


def build(controller, checks):
    from angelo_agent.ui_text import iface_
    shown = planned_now()
    if shown and "build_first" not in moments:
        keep("build_first", snap("progress-1-first-row.png"))
    elif len(shown) >= 3 and "build_more" not in moments:
        keep("build_more", snap("progress-2-rows-growing.png"))
    elif shown == rounds.BUILD_ROWS and "build_whole" not in moments:
        keep("build_whole", snap())
    else:
        drawn()
    if not wait(lambda: request_done(controller)):
        return .05
    final = keep("build_final", snap("progress-3-plan-ran.png"))
    plan = first_draw(lambda label, _: label == iface_("Preparing the next steps"))
    first = first_draw(lambda _, drawn_rows: bool(drawn_rows))
    last = max((seconds for seconds, _, drawn_rows in draws if drawn_rows), default=None)
    expected = rounds.THINKING + rounds.first_row_seconds(rounds.BUILD)
    state["observed"]["build_seconds"] = {"plan_label_drawn": plan, "first_row_drawn": first, "last_row_drawn": last,
                                          "stand_in_first_name_sent": expected, "plan_seconds": round(
                                              len(json.dumps(rounds.BUILD)) / rounds.CHARS * rounds.SECONDS, 2)}
    checks["1_the_first_row_is_drawn_as_soon_as_its_name_is_written"] = (
        plan is not None and first is not None and first - plan <= rounds.first_row_seconds(rounds.BUILD) + 0.5)
    checks["1_and_within_a_second_of_the_stand_ins_own_time_from_sending"] = first is not None and first <= expected + 1.0
    checks["1_seconds_before_the_plan_was_complete"] = first is not None and last is not None and last - first >= 3.0
    early, more, whole = moments.get("build_first"), moments.get("build_more"), moments.get("build_whole")
    checks["1_the_first_row_is_the_first_object"] = bool(early) and early["planned"][:1] == [rounds.FIRST_ROW]
    checks["1_the_rows_grow_to_the_whole_plan"] = bool(more and whole) and whole["planned"] == rounds.BUILD_ROWS
    checks["1_each_drawn_row_has_a_planned_chip"] = bool(more) and more["text"].count(iface_("Planned")) == len(
        more["planned"]) and all(label in more["text"] for label in more["planned"])
    checks["1_no_row_is_ever_stored"] = all(not any(label in frame["stored"] for label in ("Post ×4", "Rail ×2"))
                                            for frame in (early, more, whole) if frame) and "Post ×4" not in saved()
    checks["1_after_the_plan_ran_its_rows_are_gone"] = (controller.status == "Completed" and not final["planned"]
                                                        and iface_("Planned") not in final["text"])
    checks["1_the_real_rows_are_the_transcripts"] = any(m.get("tool") == "angelo_scene_program"
                                                        and m.get("status") == "completed" for m in rows("activity"))
    checks["1_the_footbridge_was_built"] = all(bpy.data.objects.get(name) for name in ("Deck", "Post_FL", "Rail_Back"))
    return begin("refused", rounds.refused_then_answered_rounds(), "Build it again, the old way.")


def refused(controller, checks):
    shown = planned_now()
    if shown and "refused_rows" not in moments:
        keep("refused_rows", snap("refused-1-rows-before-refusal.png"))
    # The runtime's verdict reaches the chat in one batch with the status that takes the rows away (claude_stream).
    failed = any(m.get("tool") == "StructuredOutput" and m.get("status") == "failed" for m in rows("activity"))
    if failed and "refused_at" not in state:
        state["refused_at"] = time.monotonic() - state["sent"]
    if failed and controller.busy and "refused_gap" not in moments:
        keep("refused_gap", snap("refused-2-after-refusal.png"))
    else:
        drawn()
    if not wait(lambda: request_done(controller)):
        return .05
    final = keep("refused_final", snap())
    gap, before = moments.get("refused_gap"), moments.get("refused_rows")
    after = [drawn_rows for seconds, _, drawn_rows in draws if seconds >= state.get("refused_at", 1e9)]
    state["observed"]["draws_after_refusal"] = len(after)
    checks["2_the_refused_plans_rows_were_drawn_first"] = bool(before) and "Old Deck" in before["planned"]
    checks["2_after_the_refusal_the_live_row_shows_none_of_them"] = (
        bool(gap) and gap["live"] == [] and not gap["planned"] and "Old Deck" not in gap["text"]
        and bool(after) and not any("Old " in label for drawn_rows in after for label in drawn_rows))
    checks["2_the_answer_completes_the_turn"] = (controller.status == "Completed"
                                                 and rows("assistant")[-1]["text"] == rounds.ANSWER["reply"])
    checks["2_nothing_of_them_is_stored_or_saved"] = "Old Post ×4" not in final["stored"] + saved()
    checks["2_and_nothing_of_the_refused_plan_ran"] = bpy.data.objects.get("Old Deck") is None
    return begin("stop", rounds.slow_rounds(), "Now a slower one.")


def stop(controller, checks):
    if "stop_rows" not in moments:
        if not wait(lambda: len(planned_now()) >= 2):
            drawn()
            return .05
        keep("stop_rows", snap("stop-1-rows-showing.png"))
        click("stop")
        return .1
    if not wait(lambda: not controller.busy):
        return .05
    final = keep("stop_after", snap("stop-2-after-stop.png"))
    checks["3_rows_were_drawn_before_stop"] = len(moments["stop_rows"]["planned"]) >= 2
    checks["3_stop_takes_them_with_the_request"] = (controller.status == "Interrupted" and not final["planned"]
                                                    and "Slow Deck" not in final["text"])
    checks["3_nothing_of_the_plan_ran"] = bpy.data.objects.get("Slow Deck") is None
    from angelo_agent import ui
    ui._memory_keys[controller.state["connection_id"]] = base.KEY  # As the existing key turn binds it.
    state["observed"]["key_leaks"] = []
    return begin("keyed", rounds.keyed_rounds(), "Put up a sign.")


def keyed(controller, checks):
    if any("Sign [REDACTED]" in label for label in planned_now()) and "keyed_row" not in moments:
        keep("keyed_row", snap("key-1-row-redacted.png"))
    else:
        drawn()
    if not wait(lambda: request_done(controller)):
        return .02
    final = keep("keyed_final", snap())
    from angelo_agent import ui
    ui._memory_keys.pop(controller.state["connection_id"], None)
    checks["4_the_keyed_row_was_drawn_redacted"] = bool(moments.get("keyed_row"))
    checks["4_no_draw_ever_showed_five_characters_of_the_key"] = not state["observed"]["key_leaks"]
    checks["4_nor_any_row_or_save"] = not any(piece in final["stored"] + saved() for piece in KEY_PIECES)
    view = bpy.context.preferences.view
    languages = {item.identifier for item in view.bl_rna.properties["language"].enum_items}
    state["observed"]["arabic_available"] = bpy.app.build_options.international and "ar_EG" in languages
    if state["observed"]["arabic_available"]:
        view.language, view.use_translate_interface = "ar_EG", True
    return begin("arabic", rounds.build_rounds(rounds.batched_footbridge("Ar "), "msg_arabic"), "جسر مشاة حجري مقوس")


def arabic(controller, checks):
    shown = planned_now()
    if "Ar Post" in shown and "arabic_unit" not in moments:
        keep("arabic_unit", snap())
    elif "Ar Post ×4" in shown and "arabic_rows" not in moments:
        keep("arabic_rows", snap("arabic-1-rows.png"))
    else:
        drawn()
    if not wait(lambda: request_done(controller)):
        return .05
    unit, counted = moments.get("arabic_unit"), moments.get("arabic_rows")
    checks["5_the_rows_are_drawn_in_arabic_too"] = bool(counted) and "Ar Deck" in counted["text"]
    checks["5_a_batch_counts_on_its_units_row_once_complete"] = (
        bool(unit) and bool(counted) and counted["planned"][:2] == ["Ar " + row for row in rounds.BATCHED_ROWS[:2]]
        and not any("Batch" in str(label) for label in counted["planned"]))
    checks["5_and_its_copies_are_built"] = controller.status == "Completed" and sum(
        obj.name.startswith("Ar Post") for obj in bpy.data.objects) == 4
    bpy.context.preferences.view.language = "en_US"
    finish()
    return None


PHASES = {"boot": boot, "connected": connected, "build": build, "refused": refused, "stop": stop, "keyed": keyed,
          "arabic": arabic}


def tick():
    try:
        assert time.monotonic() - started < 300, "Plan-progress fixture timed out in " + state["phase"]
        from angelo_agent import ui
        return PHASES[state["phase"]](ui.get_controller(), state["checks"])
    except Exception as error:
        finish(error)
        return None


bpy.app.timers.register(tick, first_interval=.5)
