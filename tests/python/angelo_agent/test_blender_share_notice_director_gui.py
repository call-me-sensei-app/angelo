# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The network share an agent script was refused, named to the person on a card in the chat, in Director (Windows).

Confinement refuses an agent script a network share the person hasn't connected, by the name's text, before anything
connects; the agent reads only the plain network refusal. The C module records which share it was
(``_angelo_guard.network_shares_refused``, Windows only: only Windows refuses by a share), and the chat names it to the
person on an amber card, never to a model (``share_refusal_notice``, doc/angelo/agent_chat.rst).

Default Director startup, Angelo enabled as it starts (the real confinement), the default chat, no provider. An agent
script (``script_confinement.run_confined`` with the chat's call key, admitted as Angelo's approval would) opens a file
on ``\\\\angelo-share-proof.invalid\\castle`` (``.invalid`` never resolves, RFC 6761, so nothing could be reached even if
the refusal failed). The executor's call row then records the run's outcome (``activity_rows.store``, as the executor
does), which names what the chat is owed:

* before the run, no card names a share, and none is recorded;
* the open is refused, and the text the agent reads names no share;
* C records the share; the chat gets one card for it, person-only, kept from every model (``row_audience``);
* the card is drawn in English, in Arabic (right to left, its sentence translated, the share quoted whole) and in a
  narrow chat (every hit rect inside the region); a screenshot of each.

Run through ``run_native_suites.py --gui --include test_blender_share_notice_director_gui``; check
``share-notice-director-gui.json`` for ``success`` and ``phase == "complete"``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import gc
import json
import tempfile
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-share-notice-director-qa"))
OUT.mkdir(parents=True, exist_ok=True)
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_input  # noqa: E402
from angelo_agent import product_env  # noqa: E402

product_env.clear('DISABLE_DEFAULT_CHAT')   # The default Director startup, as people get it.
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "share-notice-director-gui.json"
HOST = "angelo-share-proof.invalid"
SHARE = "\\\\" + HOST + "\\castle"
SCRIPT = '''
try:
    open(PATH, "rb").close()
    result = "opened"
except OSError as error:
    result = "refused: " + str(error)[:300]
'''
CALL = "proof-share-1"
state = {"phase": "boot", "success": False, "checks": {}, "details": {}, "screenshots": []}
checks, started = state["checks"], time.monotonic()


def check(name, ok, detail=None):
    checks[name] = bool(ok)
    if detail is not None:
        state["details"][name] = detail


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


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


def cards():
    from angelo_agent import share_refusal_notice
    c = controller()
    with c.lock:
        return [dict(m) for m in c.state["messages"] if m.get("person_only") is True
                and (m.get("i18n") or [None])[0] == share_refusal_notice.ONE]


def capture(name):
    chat()[0].tag_redraw()
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))
    state["screenshots"].append(name)


def refusals():
    from angelo_agent import share_refusal_notice
    return share_refusal_notice.refusals()


def run_script():
    """The agent script, through the executor's confinement, with this chat's call key."""
    from angelo_agent import script_confinement, script_errors
    from confinement_attacks_admit import admit
    key = "%s/%s" % (controller().state["id"], CALL)
    admit(key, SCRIPT)
    namespace = {"__name__": "__angelo_script__", "bpy": bpy, "PATH": SHARE + "\\wall.png"}
    outcome = {"refused": None}
    try:
        _, result = script_confinement.run_confined(
            SCRIPT, namespace, None, key, finish=lambda ns: script_errors.read_back(ns.get("result")))
        outcome["result"] = json.dumps(result, default=str)[:400]
    except script_confinement.ScriptConfined as error:
        outcome["refused"] = str(script_confinement.reason_of(error))[:400]
    except Exception as error:   # noqa: BLE001 - reported as the check's detail.
        outcome["error"] = repr(error)[:400]
    gc.collect()
    return outcome


def finish(error=None):
    if error is not None:
        state["error"] = "".join(traceback.format_exception(error))
        try:
            capture("failure.png")
        except Exception as failure:   # noqa: BLE001 - a diagnosis never hides the error
            state["diagnosis_error"] = repr(failure)
    else:
        state["phase"] = "complete"
        state["success"] = bool(checks) and all(checks.values())
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    bpy.context.preferences.view.language = 'en_US'
    save()
    print("ANGELO_SHARE_NOTICE_DIRECTOR_GUI", json.dumps({"success": state["success"], "phase": state["phase"]}),
          flush=True)
    bpy.ops.wm.quit_blender()


def wait(condition, limit=30):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + state["phase"]
    return False


def go(phase, delay=.3):
    state["phase"] = phase
    save()
    return delay


def tick():
    try:
        assert time.monotonic() - started < 240, "Share notice fixture timed out in " + state["phase"]
        from angelo_agent import activity_rows, director, row_audience, ui_text
        phase = state["phase"]
        if phase == "boot":
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            if not wait(lambda: controller() is not None and controller().state.get("id")):
                return .2
            check("windows", sys.platform == "win32", sys.platform)
            check("no_share_card_before_the_run", not cards())
            state["before"] = [serial for serial, _share in refusals()]
            return go("run")
        if phase == "run":
            outcome = run_script()
            state["details"]["outcome"] = outcome
            said = outcome.get("refused") or outcome.get("result") or outcome.get("error") or ""
            check("the_open_is_refused", outcome.get("refused") is not None or "refused: " in said, outcome)
            check("the_agent_reads_no_share", HOST not in said and "castle" not in said, said)
            recorded = [(serial, share) for serial, share in refusals() if serial not in state["before"]]
            check("c_records_the_share", any(share.lower() == SHARE.lower() for _serial, share in recorded), recorded)
            activity_rows.store(controller(), {"id": CALL, "tool": "angelo_execute_python", "status": "error",
                                               "result": {"error": said}})
            return go("card")
        if phase == "card":
            if not wait(lambda: cards()):
                return .2
            found = cards()
            check("one_card_names_the_share", len(found) == 1 and SHARE in found[0].get("text", ""), found)
            check("the_card_is_kept_from_every_model", all(row_audience.model_hidden(m) for m in found))
            check("the_activity_row_names_no_share", not any(
                HOST in json.dumps(m, default=str) for m in controller().state["messages"]
                if m.get("call_id") == CALL))
            capture("share-1-english.png")
            if not bpy.app.build_options.international:
                state["arabic"] = "WITH_INTERNATIONAL is off: Arabic not checked"
                return go("narrow_drag")
            bpy.context.preferences.view.use_translate_interface = True
            bpy.context.preferences.view.language = 'ar_EG'
            return go("arabic", .8)
        if phase == "arabic":
            card = cards()[0]
            shown = ui_text.display_notice(card)
            check("arabic_is_right_to_left", ui_text.is_rtl())
            check("the_card_is_translated_and_quotes_the_share_whole",
                  shown != card.get("text") and SHARE in shown, shown)
            capture("share-2-arabic.png")
            bpy.context.preferences.view.language = 'en_US'
            return go("narrow_drag")
        if phase == "narrow_drag":
            area, _ = chat()
            edge, middle = area.x - 1, area.y + area.height // 2
            target = area.x + area.width - 320
            qa_input.send(window(), qa_input.drag_path((edge, middle), (target, middle),
                                                       bpy.context.preferences.system.ui_scale))
            return go("narrow", .6)
        if phase == "narrow":
            area, region = chat()
            if not wait(lambda: view_state().get("hit_region") == (region.width, region.height)):
                return .2
            state["details"]["narrow_width"] = area.width
            check("the_card_fits_a_narrow_chat", area.width <= 360 and all(
                rect[0] >= 0 and rect[0] + rect[2] <= region.width + 1 for rect, _, _ in view_state().get("hits", [])),
                area.width)
            capture("share-3-narrow.png")
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=.5, persistent=True)
