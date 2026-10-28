# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""In-app driver of one live parallel-agent trial: a lead chat and the two workers it starts (BILLED).

Runs inside Angelo (GUI, started by ``parallel_live_trial.py``) with ``ANGELO_ENABLE_PARALLEL_AGENTS=1`` and one
isolated subscription connection. It sends the case's message (``parallel_live_cases.prompt``) to a new lead chat,
answers every card of the lead and its workers by the one-shot benchmark's rule (``answer_approvals``; Auto-bounded
typed edits run in each chat's own Auto mode, as for a person), waits until every chat is idle, sends the follow-up
(``parallel_live_cases.FOLLOW_UP``), then
measures each worker's part from its live scene, grades it (``parallel_live_cases.grade``), takes screenshots of
the rail, each live view and, when the lead merged, its scene framed on what it gained, saves the document and checks
it has no library. Writes ``qa/parallel-live.json``.
Caps: ``ANGELO_EVAL_TASK_TIMEOUT`` for the whole trial; on any error or timeout every chat stops, every worker
stops and Angelo quits.
"""

import json
import os
import sys
import time
import traceback
import types
from pathlib import Path

import bpy

assert not bpy.app.background and os.environ.get("ANGELO_EVAL_AUTHORIZED") == "1"
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
sys.path.insert(0, str(HERE))
OUT = Path(os.environ["ANGELO_QA_DIR"])
import addon_utils  # noqa: E402
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.filepaths.temporary_directory = str(OUT)
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
import qa_boot  # noqa: E402
import parallel_live_cases as cases  # noqa: E402
import eval_ask_why  # noqa: E402  (--ask-why-on-miss)
import eval_diagnostics as diagnostics  # noqa: E402  (--debug-thinking: a diagnostic run, never a proof)
from eval_abnormal_end import turn_ended  # noqa: E402
from eval_receipts import receipts_since, summarize_receipt  # noqa: E402
from angelo_agent import (live_view, live_view_draw, parallel_sessions, rail_pictures, session_rail_input,  # noqa
                          ui, worker_routing, worker_sessions)
from angelo_agent.chat_settings import clean_options  # noqa: E402
from angelo_agent.tool_specs import BOUNDED_MUTATIONS  # noqa: E402

CASE = os.environ["ANGELO_PARALLEL_CASE"]
CONNECTION_ID = os.environ["ANGELO_EVAL_CONNECTION_ID"]
TIMEOUT = float(os.environ.get("ANGELO_EVAL_TASK_TIMEOUT", "900"))
SETTLE = 3.0      # Seconds every chat must stay idle before the trial calls the work done.
SHOT_SETTLE = 1.5  # Seconds after a switch before its capture: the rail's 1 s rule, and a frame.
LEAD_CARDS = frozenset({"angelo_workers_start", "angelo_workers_merge"})   # The lead's own cards in an Ask chat.
report = {"case": CASE, "connection_id": CONNECTION_ID, "model": os.environ.get("ANGELO_EVAL_MODEL", ""),
          "binary": bpy.app.binary_path, "approved": [], "declined": [], "shots": [], "timings": {}}
state = {"phase": "boot", "started": time.monotonic(), "idle_since": None, "decided": set(), "done": False}
lead = None


def controllers():
    return [c for c in ui._controllers.values() if c is lead or worker_routing.worker_snapshots(c)]


def answer_approvals():
    """Every card of the lead and of its workers, by the one-shot benchmark's rule (``oneshot_session``: scene edits and
    reviewed Python approved; files, Studio, delegation and the rest declined), plus the lead's own worker cards (start,
    merge). Since workers run Python (e2e ed0290ff332) a worker's script asks too: the first rule, approving only the
    lead's start, declined every worker script, and each worker's turn failed (Opus, 2026-10-25 08:56)."""
    from oneshot_session import approval_decision
    for chat in controllers():
        for call in list(chat.pending.values()):
            key = (chat.state["id"], call.id)
            if not call.approval or call.executing or call.authorized or key in state["decided"]:
                continue
            auto = clean_options(chat.state.get("options"))["approval"] == "AUTO_BOUNDED"   # As the controller decides:
            if not call.recovery_review and (chat._automatic_script(call) or (auto and call.name in BOUNDED_MUTATIONS)):
                continue   # The chat's own Auto approves it on its next step; deciding first would race it.
            state["decided"].add(key)
            who = "lead" if chat is lead else "worker"
            lead_card = chat is lead and call.name in LEAD_CARDS
            if lead_card or approval_decision(call.name) == "approve":
                with bpy.context.temp_override(window=ui.main_window()):
                    result = bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=chat.state["id"])
                    if 'CANCELLED' in result and call.id in getattr(chat, "reviewed_calls", ()):
                        # Reviewed Python takes two clicks: the first opens the script, the second runs it.
                        bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=chat.state["id"])
                report["approved"].append([who, call.name])
            else:
                chat.decline_tool(call.id)
                report["declined"].append([who, call.name])


def all_idle():
    """Every chat's turn is over as the rail and the status read a worker: none pending, its lane idle (P1-20 L6)."""
    from angelo_agent import session_rail_draw
    return not worker_sessions._launching and all(turn_ended(c) and not session_rail_draw.working(c)
                                                  for c in controllers())


def send(chat, text):
    ui.prepare_controller(chat)
    turn = worker_routing.prepare_turn(chat, ui.main_window(), text)
    chat.send(text, observation=turn.observation, recovery_evidence=turn.recovery_evidence)
    turn.commit()


def measure(session_id):
    """A worker's part as Angelo sees it live (its private Main): meshes, materials, overall size (metres)."""
    summary = live_view.summary(session_id) or {}
    size = summary.get("size") or (0.0, 0.0, 0.0)
    return {"meshes": summary.get("meshes", 0), "materials": summary.get("materials", 0),
            "size": round(max(size), 3), "objects": sorted(summary.get("objects") or [])[:60]}


def screenshot(name):
    """The window as the product last drew it: no draw is forced (one forced right after a switch captured a stale
    frame; a capture now waits SHOT_SETTLE after the switch, by when the product has drawn it by itself)."""
    from angelo_agent import view_editor
    width, height, pixels = live_view.window_pixels(ui.main_window())   # Drawn now: a window behind others is not
    (OUT / name).write_bytes(view_editor.encode_png(pixels, width, height, bottom_up=True))   # presented on macOS.
    report["shots"].append(name)


def tick():
    global lead
    try:
        now = time.monotonic()
        if state["done"]:
            return None
        if now - state["started"] > TIMEOUT:
            raise TimeoutError("trial cap of %g s reached in %s" % (TIMEOUT, state["phase"]))
        phase = state["phase"]
        if phase == "boot":
            if not qa_boot.started() or ui.main_window() is None:
                return .2
            if list(ui.connections().records) != [CONNECTION_ID]:
                raise RuntimeError("The isolated store must hold exactly the selected connection")
            bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "main.blend"), check_existing=False)
            report["document_before"] = sorted(o.name for o in ui.main_window().scene.objects)
            lead = ui.create_session(connection_id=CONNECTION_ID)
            options = {"effort": os.environ.get("ANGELO_EVAL_EFFORT", "low"), "fast": False}
            if os.environ.get("ANGELO_EVAL_MODEL"):
                options["model"] = os.environ["ANGELO_EVAL_MODEL"]
            lead.set_options(**options)
            lead.single_model_runs = True
            report.update(diagnostics.configure(lead))   # Debug thinking on the lead only when the run asks for it.
            report["lead_options"] = dict(lead.state.get("options") or {})
            state["marker"] = None
            send(lead, cases.prompt(CASE))
            report["sent_at"] = time.time()
            state["phase"], state["sent"] = "lead", now
            return .1
        answer_approvals()
        if phase in ("lead", "workers", "summary"):
            if not all_idle():
                state["idle_since"] = None
                return .2
            state["idle_since"] = state["idle_since"] or now
            if now - state["idle_since"] < SETTLE:
                return .2
            if phase == "lead":
                report["timings"]["lead_first_turn_s"] = round(now - state["sent"], 1)
                if not worker_sessions.workers_of(lead.state["id"]):
                    raise RuntimeError("The lead started no worker")
                state["phase"], state["idle_since"] = "workers", None
                return .5
            if phase == "workers":
                report["timings"]["all_workers_done_s"] = round(now - state["sent"], 1)
                screenshot("rail-main.png")
                send(lead, cases.FOLLOW_UP)
                state["phase"], state["idle_since"] = "summary", None
                return .2
            report["timings"]["summary_done_s"] = round(now - state["sent"], 1)
            state["phase"], state["shots"], state["shot_at"] = "shots", list(enumerate(
                worker_sessions.workers_of(lead.state["id"]), 1)), None
            return .1
        if phase == "shots":   # Each worker's live view as the person sees it after a click on its tile.
            if state["shot_at"] is not None and now - state["shot_at"] < SHOT_SETTLE:
                return .1
            if state["shot_at"] is not None:
                screenshot("live-%d.png" % state["shots"].pop(0)[0])
            if state["shots"]:
                session_rail_input.switch(state["shots"][0][1], ui.main_window())
                state["shot_at"] = now
                return .1
            session_rail_input.switch(lead.state["id"], ui.main_window())
            if not cases.lead_edits(lead.state["messages"])["merges"]:
                return grade()
            frame_merged()   # The lead merged: its scene, framed on what the merge brought, read by eye.
            state["phase"], state["shot_at"] = "merged-shot", now
            return .1
        if phase == "why":   # --ask-why-on-miss: the lead answers in Read only, then the trial ends.
            return state["why"].tick()
        if phase == "merged-shot":
            if now - state["shot_at"] < SHOT_SETTLE:
                return .1
            screenshot("main-merged.png")
            return grade()
        return .2
    except Exception:
        return finish(traceback.format_exc())


def frame_merged():
    """Select what the scene gained and frame it in the main window's largest 3D view. Only for the picture: a failure
    is recorded, never the trial's (an excluded merged collection's objects cannot be selected)."""
    try:
        window = ui.main_window()
        before = set(report["document_before"])
        for obj in window.scene.objects:
            try:
                obj.select_set(obj.name not in before, view_layer=window.view_layer)
            except RuntimeError:
                pass
        area = max((a for a in window.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
        region = next(r for r in area.regions if r.type == 'WINDOW')
        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.ops.view3d.view_selected('EXEC_DEFAULT')
    except Exception as error:   # noqa: BLE001
        report["frame_error"] = repr(error)


def grade():
    status = worker_sessions.status(lead)["workers"]
    numbers = {worker_sessions._parts[s][0]: s for s in worker_sessions.workers_of(lead.state["id"])
               if s in worker_sessions._parts}   # Each status row with its own worker's part (P1-20 L10).
    parts = [dict(row, **(measure(numbers[row["worker"]]) if row.get("worker") in numbers else {})) for row in status]
    after = sorted(o.name for o in ui.main_window().scene.objects)
    types = {o.name: "INSTANCER" if o.type == "EMPTY" and o.instance_type == "COLLECTION" and o.instance_collection
             else o.type for o in ui.main_window().scene.objects}   # What the lead's own additions are (the grade).
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "main.blend"), check_existing=False)
    report["libraries_in_memory_after_save"] = len(bpy.data.libraries)
    report["parts"] = parts
    edits = cases.lead_edits(lead.state["messages"])   # The lead's own additions and each merge it ran.
    report["lead_edits"] = {"added": edits["added"], "merges": [merge.get("merged") for merge in edits["merges"]],
                            "briefs": edits["briefs"]}
    report["document_after"], report["document_types"] = after, types
    from angelo_agent import worker_merge
    report["merge_checks_placement"] = checked = hasattr(worker_merge, "placement_check")   # e2e and later.
    in_scene = [c.name for c in ui.main_window().scene.collection.children_recursive]   # What the lead's scene holds.
    report["collections_in_scene"] = in_scene
    report["checks"] = cases.grade(CASE, parts, report["document_before"], after, edits, checked, in_scene, types)
    report["lead_reply"] = next((m.get("text") for m in reversed(lead.state["messages"])
                                 if m.get("role") == "assistant" and m.get("text")), "")[-2000:]
    report["receipts"] = {chat.state["id"]: [summarize_receipt(r) for r in receipts_since(chat, None)]
                          for chat in controllers()}
    failed = [name for name, passed in report["checks"].items() if not passed]
    if failed and diagnostics.in_app_flags().get("ask_why_on_miss"):   # One question, with what was measured.
        facts = {"worker_states": {str(p.get("worker")): p.get("state") for p in parts},
                 "merged_workers": sorted(str(e.get("worker")) for m in edits["merges"] for e in m.get("merged") or ()),
                 "collections_in_scene": in_scene}
        trial = types.SimpleNamespace(controller=lead, report=report, window=ui.main_window(), finish=finish,
                                      deferred_probe=False)
        state["why"] = eval_ask_why.InAppAskWhy(trial, bpy, [("lead", name, facts) for name in failed])
        state["phase"] = "why"
        return state["why"].send()
    return finish()


def _telemetry():
    """What the rail and live view cost the main thread, kept by every trial, a failed or timed-out one too: taken in
    ``finish`` before anything is stopped. A measurement never fails a billed trial."""
    try:
        from angelo_agent import parallel_access, rail_redraw
        report["worker_limit"] = parallel_access.worker_limit()   # Workers at once in this trial (the machine's).
        # Every part read and picture this launch (count, total ms), and the newest 256 of each as samples.
        report["reloads"] = [parallel_sessions.refresh_total[0], round(parallel_sessions.refresh_total[1] * 1000, 2)]
        report["pictures"] = [rail_pictures.draw_total[0], round(rail_pictures.draw_total[1] * 1000, 2)]
        report["reload_ms_last256"] = [round(s * 1000, 2) for s in parallel_sessions.refresh_seconds]
        report["picture_ms_last256"] = [round(s * 1000, 2) for s in rail_pictures.draw_seconds]
        # Redraw REQUESTS the rail's pacing made (a tag of a window's main region), every one: not frames drawn.
        report["rail_redraw_requests"] = rail_redraw.requested[0]
        # Only the newest five costs per window when the trial ends: main-thread wall time from PRE_VIEW or the tag
        # to the rail's draw; neither whole-trial latency nor GPU time.
        report["rail_view_redraw_wall_ms_last5"] = {str(window): [round(c * 1000, 2) for c in costs]
                                                    for window, costs in rail_redraw.view_cost.items()}
    except Exception as error:   # noqa: BLE001
        report["telemetry_error"] = repr(error)


def finish(error=None):
    if state["done"]:
        return None
    state["done"] = True
    _telemetry()   # Before anything is stopped: a failed or timed-out trial keeps its diagnostics too.
    report.update(error=error, phase=state["phase"], success=error is None and all(
        (report.get("checks") or {"none": False}).values()))
    for chat in controllers():
        try:
            if chat.busy:
                chat.stop()
        except Exception:   # noqa: BLE001 - stopping is best effort; the workers stop below.
            pass
    try:
        worker_routing.stop_all()
    except Exception:
        report["cleanup_error"] = traceback.format_exc()
    (OUT / "parallel-live.json").write_text(json.dumps(report, indent=2, default=str))
    print("ANGELO_PARALLEL_LIVE", json.dumps({"case": CASE, "success": report["success"], "phase": state["phase"]}),
          flush=True)
    bpy.app.timers.register(lambda: bpy.ops.wm.quit_blender() and None, first_interval=0.5)
    return None


bpy.app.timers.register(tick, first_interval=1.0)
