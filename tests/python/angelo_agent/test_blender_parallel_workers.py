# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Worker Blenders end to end: chats in this Angelo work on their own worker's part file through ``RemoteExecutor``.

This (background) Angelo is the main one. It starts four real workers (``Angelo --background`` children over their
private connections) and binds one chat to each; every chat builds an unrelated part with typed programs only (a
chess piece, a furnished room, a vehicle with repeated wheels, a character with clothing parts), Auto or Ask with
Angelo's own approve operator. Checked, from the real results and the real files:

* each edit is staged, committed and checked in its worker, with its Undo step there, its request snapshot written
  window-free by the worker into this chat's store, its rows and results in this chat, and its program evidence
  minted into a completion here; this Angelo's own scene never changes;
* turn observations come from the worker's document; a script asks in Auto (worker Python itself is proven in
  ``test_blender_worker_python.py``); a script that loads a file, jobs and PREVIEW are refused with nothing run; a
  binding that changed after approval is refused; this Angelo's Undo leaves worker chats alone; task memory runs in
  this chat;
* every part file is saved (a fresh worker reads it back), and a worker that dies makes its chat's calls fail
  cleanly, never hang.

Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``. No provider or paid call.
"""

import json
import os
import signal
import sys
import tempfile
import time
import traceback
from pathlib import Path
from unittest.mock import patch

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-parallel-workers-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import program_completion, ui, worker_routing  # noqa: E402
from angelo_agent.headless_main import _register  # noqa: E402
from parallel_workers_qa import CASES, WorkerChat, op, percentile, program, primitive, worker_chat  # noqa: E402
from hosted_worker_chat import hosted  # noqa: E402

# Design A′ (``ANGELO_WORKER_CHAT_HOST`` unset or 1, the "hosted" variant): each chat's Controller runs in its worker
# and this Angelo keeps a mirror; the "lanes" variant (0) keeps the chat here. Checks that exist only on one path say
# why where they branch: a lane's internals (its RemoteExecutor round trips, captures, lead-side minting, two chats
# bound to one worker) have no counterpart when the worker runs the chat, which hosts one chat per worker by design.
HOSTED = hosted()

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string,
          "platform": sys.platform, "timings": {}, "variant": "hosted" if HOSTED else "lanes"}
STATE = OUT / "state"
PARTS = OUT / "parts"


def check(name, condition, detail=None, soft=False):
    """Record ``name``; a failed check stops the suite unless ``soft`` (recorded, its run goes on: the report fails)."""
    report["checks"][name] = bool(condition)
    if not condition and detail is not None:
        report.setdefault("details", {})[name] = detail
    assert condition or soft, name


def committed(result):
    return (isinstance(result, dict) and result.get("status") == "committed" and result.get("success") is True
            and all(row.get("passed") for row in result.get("checks") or [{}]) and not result.get("error"))


def main_scene():
    return sorted(obj.name for obj in bpy.data.objects)


def build_in_parallel(chats):
    """Every chat's programs, interleaved: each round sends one program to every worker, then waits for all."""
    plans = {name: CASES[name]()[0] for name in chats}
    for index in range(max(len(plan) for plan in plans.values())):
        waiting = {}
        for name, chat in chats.items():
            if index < len(plans[name]):
                if chat.controller.state["options"]["approval"] == "ASK":
                    key, answers = chat.request("angelo_scene_program", plans[name][index])
                    call = chat.wait_for_approval(key, answers)
                    check(name + "_%d_review_is_built_from_the_worker_binding" % index,
                          bool(getattr(call, "approval_digest", "")) if HOSTED   # Its card: the worker's summary.
                          else getattr(call, "_approval_review", None) is not None)
                    with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                        bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=chat.session)
                    waiting[name] = answers
                else:
                    waiting[name] = chat.request("angelo_scene_program", plans[name][index])[1]
        for name, answers in waiting.items():
            result = chats[name].wait(answers)
            check(name + "_%d_committed_in_its_worker" % index, committed(result), result)
            check(name + "_%d_has_its_worker_undo_step" % index, result.get("undoable") is True)


def hosted_rest(chats, room, vehicle, before):
    """The hosted variant's second half: what a lane checks through its own internals is checked here through the
    chat's worker and its mirror. Left out by design: two chats bound to one worker (A′ hosts one chat per worker),
    a lane's RemoteExecutor timeouts and captures, and references named across processes."""
    from angelo_agent.worker_mirror import MirrorRefused
    vehicle.controller.set_options(approval="ASK")
    deadline = time.monotonic() + 15
    while vehicle.controller.state["options"].get("approval") != "ASK" and time.monotonic() < deadline:
        vehicle.pump()
    key, answers = vehicle.request("angelo_set_transform", {"object": "Car Body", "location": [0, 0, 0.7]})
    call = vehicle.wait_for_approval(key, answers)
    ui.invalidate_workspace_state("undo")
    check("main_undo_leaves_worker_chats_alone", vehicle.card(key) is not None)
    vehicle.approve(call)
    check("approved_after_main_undo", vehicle.wait(answers).get("success") is True)
    vehicle.wait_idle()
    # A worker that stops answering: a Send to it returns at once and shows as sending, with nothing assumed done and
    # no error (an unanswered Send is never shown as failed, since the worker may still take it: worker_mirror_ops);
    # once the worker runs again it takes the message, which runs once.
    if hasattr(signal, "SIGSTOP"):
        pid = room.worker.process.pid
        os.kill(pid, signal.SIGSTOP)
        try:
            _call_id, answers = room.request("angelo_set_transform", {"object": "Floor", "location": [0, 0, 0.01]})
            room.pumps.clear()
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                room.pump()
            frozen = {"status": room.controller.status, "busy": room.controller.busy, "answered": bool(answers),
                      "errors": [m.get("text") for m in room.controller.state.get("messages") or ()
                                 if m.get("role") == "error"],
                      "max_pump_ms": max((seconds for seconds, _ in room.pumps), default=0) * 1000}
        finally:
            os.kill(pid, signal.SIGCONT)
        report["frozen_worker_send"] = frozen
        check("a_send_to_a_frozen_worker_is_sending_and_not_done", frozen["busy"] and frozen["status"] == "Sending"
              and not frozen["answered"] and not frozen["errors"], frozen)
        check("a_pump_never_waits_for_a_frozen_worker", frozen["max_pump_ms"] < 10, frozen)
        held = room.wait(answers)
        room.wait_idle()
        sent = [m for m in room.controller.state.get("messages") or ()
                if m.get("role") == "user" and str(m.get("text")).endswith(" #%d" % room._sends)]
        check("the_frozen_send_runs_once_after_thaw", held.get("success") is True and len(sent) == 1
              and room.objects()["Floor"]["location"] == [0.0, 0.0, 0.01], {"result": held, "sent": len(sent)})
        again = room.call("angelo_set_transform", {"object": "Floor", "location": [0, 0, 0]})
        check("a_frozen_worker_is_used_again_once_it_answers", again.get("success") is True, again)
    # A long edit in a worker: Angelo's main thread pumps the mirror meanwhile and never waits for the worker.
    room.pumps.clear()
    started = time.perf_counter()
    long_edit = room.call("angelo_scene_program", program([
        primitive("CUBE", "Tile", "tile", size=0.05, location=[0, 0, 0]),
        op("batch", sources=["$tile"], array=[{"step": [0.06, 0, 0], "count": 30}, {"step": [0, 0.06, 0], "count": 30}]),
    ]))
    took = time.perf_counter() - started
    waited = [seconds for seconds, busy in room.pumps if busy]
    report["timings"]["long_edit"] = {"seconds": took, "pumps": len(room.pumps), "pumps_while_busy": len(waited),
                                      "max_pump_ms_while_busy": max(waited, default=0) * 1000,
                                      "result": {k: long_edit.get(k) for k in ("success", "error", "error_code")}}
    check("a_long_worker_edit_commits", committed(long_edit), long_edit)
    check("the_main_thread_pumps_on_while_a_worker_edit_runs", took > 0.2 and len(waited) >= 10,
          report["timings"]["long_edit"])
    check("a_pump_never_waits_for_the_worker", max(waited, default=0) < 0.01, report["timings"]["long_edit"])
    memory = room.call("angelo_memory_read", {})
    check("task_memory_runs_in_this_chat", not memory.get("error") or memory.get("error_code") != "UNSUPPORTED",
          memory)
    reads = [seconds for chat in chats.values() for name, seconds in chat.timings if name == "angelo_inspect_scene"]
    edits = [seconds for chat in chats.values() for name, seconds in chat.timings if name == "angelo_set_transform"]
    report["timings"].update(read_p50=percentile(reads, .5), read_p75=percentile(reads, .75), reads=len(reads),
                             transform_p50=percentile(edits, .5), transforms=len(edits))
    for chat in chats.values():
        chat.stop()
    for name in CASES:   # Every part file was saved: a fresh worker reads it back.
        again = worker_chat(STATE, PARTS / (name + ".blend"), ROOT)
        check(name + "_part_file_was_saved", CASES[name]()[1] <= set(again.objects()))
        again.stop()
    # A worker that dies: its chat's next message is refused at once and says so; nothing runs here.
    lost = worker_chat(STATE, PARTS / "lost.blend", ROOT)
    os.kill(lost.worker.process.pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
    lost.worker.process.wait(10)
    time.sleep(0.2)
    started = time.perf_counter()
    try:
        result = lost.call("angelo_inspect_scene", {}, timeout=30)
    except MirrorRefused as error:
        result = {"error": str(error)}
    check("dead_worker_fails_fast", result.get("error") and time.perf_counter() - started < 5, result)
    lost.stop()
    check("main_scene_still_unchanged", main_scene() == before, main_scene())
    report["success"] = all(report["checks"].values())


def run_checks():
    PARTS.mkdir(parents=True, exist_ok=True)
    bpy.context.preferences.edit.use_global_undo = True
    _register(bpy, ui)   # The approve operator, as the window has it.
    before = main_scene()
    chats, minted = {}, []
    real_mint = program_completion.mint_completion

    def mint(host, call, captured, result):
        minted.append((call.name, captured is not None))
        return real_mint(host, call, captured, result)
    for name in CASES:
        chats[name] = worker_chat(STATE, PARTS / (name + ".blend"), ROOT,
                                 approval="ASK" if name == "chess" else "AUTO_BOUNDED")
    report["timings"]["start_seconds"] = {name: chat.start_seconds for name, chat in chats.items()}
    check("four_workers_run_at_once", all(chat.worker.alive() for chat in chats.values())
          and len({chat.worker.process.pid for chat in chats.values()}) == 4)
    window_undo_applies = []
    real_apply = ui.ANGELO_OT_apply_approved_tool.execute

    def apply_here(operator, context):   # The window's Undo operator: never for a worker's edit.
        window_undo_applies.append(operator.call_id)
        return real_apply(operator, context)
    with patch.object(program_completion, "mint_completion", side_effect=mint), \
            patch.object(ui.ANGELO_OT_apply_approved_tool, "execute", apply_here):
        build_in_parallel(chats)
        moved = chats["room"].call("angelo_set_transform", {"object": "Floor", "location": [0, 0, 0]})
    check("a_direct_edit_also_runs_in_its_worker", moved.get("success") is True, moved)
    check("no_worker_edit_took_an_undo_step_in_angelo", not window_undo_applies, window_undo_applies)
    if HOSTED:   # The worker's own Controller mints a program's completion; nothing is minted here.
        check("no_program_is_minted_in_angelo_for_a_hosted_chat", not minted, minted)
    else:
        check("every_program_is_minted_from_its_worker_evidence", minted and all(
            found for name, found in minted if name == "angelo_scene_program"), minted)
    for name, chat in chats.items():
        found = chat.objects()
        check(name + "_part_has_its_objects", CASES[name]()[1] <= set(found), sorted(found))
        others = set().union(*(CASES[other]()[1] for other in CASES if other != name)) - CASES[name]()[1]
        check(name + "_part_has_no_other_part", not (others & set(found)), sorted(found))
        controller = chat.controller
        results = controller.state.get("results") or {}
        if HOSTED:   # The worker's Controller writes the chat's file; its mirror reads the same records.
            check(name + "_results_are_in_this_chats_file", any(
                record.get("phase") == "completed" for record in results.values()), sorted(results))
        else:
            check(name + "_snapshot_written_by_the_worker", bool(controller.request_checkpoint)
                  and controller.checkpoints.availability(controller.request_checkpoint)["available"])
            check(name + "_results_are_this_chats", any(key.startswith(chat.session + "/") for key in results))
            check(name + "_no_capture_left_behind", not controller.remote_captures)
    check("main_scene_never_changed", main_scene() == before, main_scene())
    vehicle = chats["vehicle"]
    wheels = [n for n in vehicle.objects() if n.startswith("Wheel")]
    check("vehicle_has_four_wheels_from_one_design", len(wheels) == 4, wheels)

    # Turn observations come from the worker's document and its change feed.
    if HOSTED:   # The worker observes its own document (no lead-side turn exists): the scene the model read is it.
        seen = " ".join(str(row.get("text")) for row in vehicle.model_log("tool_result")
                        if row.get("tool") == "angelo_inspect_scene")
        check("turn_observation_is_the_workers", "Car Body" in seen and "Floor" not in seen, seen[-600:])
    else:
        turn = worker_routing.prepare_turn(vehicle.controller, None, "make the wheels wider")
        names = {row.get("name") for row in (turn.observation or {}).get("objects", [])}
        check("turn_observation_is_the_workers", "Car Body" in names and "Floor" not in names, sorted(names))
        turn.commit()

    # Python is offered under the lead's rules (the route itself: test_blender_worker_python.py): in Auto a script
    # asks unless trusted, and Decline runs nothing.
    room = chats["room"]
    room_before = room.objects()
    key, answers = room.request("angelo_execute_python", {"description": "x", "code": "import bpy\n"
                                                          "bpy.data.objects.new('Never', None)\nresult = 1"})
    room.controller.decline_tool(room.wait_for_approval(key, answers).id)
    check("a_worker_chats_script_asks_in_auto", room.wait(answers).get("error_code") == "DECLINED", answers)
    room.new_request()
    # Refused before anything runs: a script that would replace the part's file (named before its card), a job, a
    # PREVIEW.
    refusals = {}
    from angelo_agent.remote_executor import local_tools, worker_tools
    offered = set().union(local_tools(), *worker_tools())   # A worker chat's tools at most (parallel_access.restrict).
    for label, name, arguments in (
            ("refused_a_script_that_loads_a_file", "angelo_execute_python",
             {"description": "x", "code": "import bpy\nbpy.ops.wm.read_homefile(use_empty=True)"}),
            ("refused_angelo_job_start", "angelo_job_start", {"kind": "RENDER_STILL", "output": "still.png"}),
            ("refused_angelo_scene_program_preview", "angelo_scene_program",
             dict(program([primitive("CUBE", "Preview Box", "p")]), mode="PREVIEW"))):
        if HOSTED:   # A card may be drawn first in the worker: the person approves it, and it is still refused.
            # Each on a chat of its own: one that is not refused may end its chat (D6), never the room's.
            probe = worker_chat(STATE, PARTS / (label + ".blend"), ROOT)
            result, asked = probe.call_approving(name, arguments)
            probe.stop()
            report.setdefault("refusals_asked_first", {})[label] = asked
        else:
            result = room.call(name, arguments)
        refusals[label] = result
        report.setdefault("refusal_codes", {})[label] = result.get("error_code")
        # Hosted: soft, so every refusal is seen (FE defect D6: the worker's own executor lacks a lane's refusals).
        check(label, result.get("error") and result.get("scene_changed", False) is False, result, soft=HOSTED)
        # A tool its chat is offered is refused by the worker (UNSUPPORTED); one it is not offered (a job start), by
        # the request itself, before anything is sent (REFUSED: a hosted chat's request is its model's own).
        unsupported = "UNSUPPORTED" if not HOSTED or name in offered else "REFUSED"
        check(label + "_as_unsupported_here", result.get("error_code") == unsupported, result, soft=HOSTED)
    load = refusals["refused_a_script_that_loads_a_file"]
    check("a_load_refusal_names_the_operator", "read_homefile" in str(load.get("error")), load, soft=HOSTED)
    check("refusals_changed_nothing", room.objects().keys() == room_before.keys())

    # A view runs in the worker's Blender; its pictures come back as this chat's own references (the worker
    # capability contract, 2026-10-25): what the controller attaches for the model.
    view = room.call("angelo_view", {"views": ["ISO"], "resolution": 256})
    if HOSTED:   # The worker's own Controller attaches its pictures for the model; nothing comes back here.
        check("a_workers_view_runs_in_its_worker", not view.get("error"), view, soft=True)   # FE defect D7
    else:
        try:
            pictures = room.controller.references.tool_images(view)
        except (ValueError, OSError) as error:
            pictures = [repr(error)]
        check("a_workers_view_is_this_chats_pictures", not view.get("error") and len(pictures) == 1
              and all(isinstance(p, dict) and p.get("bytes", 0) > 0 for p in pictures), view)
    # Beside an image the person attached to this chat: it resolves in the worker's Blender too (break-it E2E-16 M-2).
    if HOSTED:   # A hosted chat's references travel with its message (chat.send's attachments), not by name here.
        hosted_rest(chats, room, vehicle, before)
        return
    image = bpy.data.images.new("QA reference", 64, 48)
    image.filepath_raw, image.file_format = str(OUT / "qa-reference.png"), "PNG"
    image.save()
    attached = room.controller.references.import_images([str(OUT / "qa-reference.png")])[0]
    pair = room.call("angelo_view", {"views": [{"location": [4, -4, 3], "look_at": [0, 0, 0.5],
                                                 "reference": attached["id"]}], "resolution": 256})
    check("a_workers_view_beside_the_persons_reference_resolves_it", not pair.get("error"), pair)

    # A binding that changed after approval: another chat on the same worker moves the body first.
    vehicle.controller.state["options"]["approval"] = "ASK"
    other = WorkerChat.__new__(WorkerChat)
    from angelo_agent.controller import Controller
    other.controller = Controller(str(STATE), session_id=str(__import__("uuid").uuid4()))
    other.session = other.controller.state["id"]
    worker_routing.bind(other.controller, vehicle.worker, vehicle.executor.worker_id + "-b")
    other.fence, other.timings, other.pumps, other._count = other.controller.open_external_read(), [], [], 0
    ui._controllers[other.session] = other.controller
    key, answers = vehicle.request("angelo_set_transform", {"object": "Car Body", "location": [0, 0, 2]})
    call = vehicle.wait_for_approval(key, answers)
    moved = other.call("angelo_set_transform", {"object": "Car Body", "location": [5, 0, 0.7]})
    check("other_chat_moved_the_body", moved.get("success") is True, moved)
    with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
        bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=vehicle.session)
    stale = vehicle.wait(answers)
    check("stale_approval_is_refused", stale.get("error_code") == "STALE_REVISION", stale)
    check("stale_approval_ran_nothing", vehicle.objects()["Car Body"]["location"] == [5.0, 0.0, 0.7])
    other.controller.remote_executor = None   # It leaves the worker without stopping it (vehicle's chat owns it).

    # This Angelo's Undo, Redo and file loads are about its own document: a worker chat's approval waits on.
    vehicle.new_request()
    key, answers = vehicle.request("angelo_set_transform", {"object": "Car Body", "location": [0, 0, 0.7]})
    call = vehicle.wait_for_approval(key, answers)
    ui.invalidate_workspace_state("undo")
    check("main_undo_leaves_worker_chats_alone", vehicle.controller.pending.get(key) is call)
    with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
        bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=vehicle.session)
    check("approved_after_main_undo", vehicle.wait(answers).get("success") is True)

    # A worker that stops answering for a while keeps its chat: refused meanwhile, back once it answers.
    if hasattr(signal, "SIGSTOP"):
        from angelo_agent import remote_executor
        pid = room.worker.process.pid
        os.kill(pid, signal.SIGSTOP)
        try:
            with patch.object(remote_executor, "READ_SECONDS", 1.0):
                slow = room.call("angelo_inspect_scene", {}, timeout=30)
                held = room.call("angelo_set_transform", {"object": "Floor", "location": [0, 0, 0.01]}, timeout=30)
        finally:
            os.kill(pid, signal.SIGCONT)
        check("a_slow_worker_is_an_error_not_an_end", slow.get("error") and room.executor.document_key[2] != "ended",
              slow)
        check("an_edit_to_a_slow_worker_ran_nothing", held.get("error_code") == "WRITER_BUSY"
              and held.get("scene_changed") is False and not held.get("partial_changes_possible"), held)
        time.sleep(remote_executor.PROBE_INTERVAL + 0.5)
        check("a_slow_worker_is_used_again_once_it_answers", "Floor" in room.objects())
        again = room.call("angelo_set_transform", {"object": "Floor", "location": [0, 0, 0]})
        check("edits_resume_in_the_same_request", again.get("success") is True, again)

    # A long edit in a worker: Blender's main thread keeps pumping meanwhile and never waits for the worker.
    from angelo_agent import worker_dispatch
    room.pumps.clear()
    started = time.perf_counter()
    long_edit = room.call("angelo_scene_program", program([
        primitive("CUBE", "Tile", "tile", size=0.05, location=[0, 0, 0]),
        op("batch", sources=["$tile"], array=[{"step": [0.06, 0, 0], "count": 30}, {"step": [0, 0.06, 0], "count": 30}]),
    ]))
    took = time.perf_counter() - started
    waited = [seconds for seconds, waiting in room.pumps if waiting]
    report["timings"]["long_edit"] = {"seconds": took, "pumps": len(room.pumps), "pumps_while_waiting": len(waited),
                                      "max_pump_ms_while_waiting": max(waited, default=0) * 1000,
                                      "max_pump_ms": max((s for s, _ in room.pumps), default=0) * 1000,
                                      "result": {k: long_edit.get(k) for k in ("success", "error", "error_code")},
                                      "lane_stats": dict(worker_dispatch.STATS)}
    check("a_long_worker_edit_commits", committed(long_edit), long_edit)
    check("the_main_thread_pumps_on_while_a_worker_edit_runs", took > 0.2 and len(waited) >= 10,
          report["timings"]["long_edit"])
    check("a_pump_never_waits_for_the_worker", max(waited, default=0) < 0.01, report["timings"]["long_edit"])

    # Task memory is this chat's own state.
    memory = room.call("angelo_memory_read", {})
    check("task_memory_runs_in_this_chat", not memory.get("error") or memory.get("error_code") != "UNSUPPORTED",
          memory)

    reads = [seconds for chat in chats.values() for name, seconds in chat.timings if name == "angelo_inspect_scene"]
    edits = [seconds for chat in chats.values() for name, seconds in chat.timings if name == "angelo_set_transform"]
    report["timings"].update(read_p50=percentile(reads, .5), read_p75=percentile(reads, .75), reads=len(reads),
                             transform_p50=percentile(edits, .5), transforms=len(edits))

    # Every part file was saved: a fresh worker reads it back.
    snapshot = chats["vehicle"].controller.request_checkpoint
    for chat in chats.values():
        chat.stop()
    from angelo_agent.checkpoints import CheckpointStore
    vehicle_chat = chats["vehicle"].controller
    check("a_workers_snapshots_never_reach_angelos_restore",
          worker_routing.worker_snapshots(vehicle_chat) and not CheckpointStore(STATE, vehicle_chat.state["id"])
          .availability(snapshot)["available"])
    # A restart: the binding is gone, but the chat stays a worker chat and never edits Angelo's own file.
    from angelo_agent.controller import Controller
    worker_routing._registries.clear()
    restarted = Controller(str(STATE), session_id=vehicle_chat.state["id"])
    restarted.set_document(ui.document_key())   # What the window does with every chat it opens.
    check("a_restarted_worker_chat_never_takes_angelos_document", restarted.document[2] == "ended", restarted.document)
    restarted.open_external_read()
    ui._controllers[restarted.state["id"]] = restarted
    answers = []
    restarted.request_tool("after-restart", "angelo_create_cube", {"name": "Stray", "size": 1, "location": [0, 0, 0]},
                           answers.append)
    for _ in range(50):
        if answers:
            break
        restarted.pump_events()
        from angelo_agent.controller import ToolDispatchBudget, pump_controller_tools
        pump_controller_tools([restarted], 0, None, None, lambda target, call: None,
                              budget=ToolDispatchBudget(seconds=60))
    check("a_restarted_worker_chat_refuses_and_never_edits_angelo", answers and answers[0].get("error_code") ==
          "CANCELLED" and "Stray" not in bpy.data.objects, answers)
    ui._controllers.pop(restarted.state["id"], None)
    for name in CASES:
        again = WorkerChat(STATE, PARTS / (name + ".blend"), ROOT)
        check(name + "_part_file_was_saved", CASES[name]()[1] <= set(again.objects()))
        again.stop()

    # A rail picture never kills a worker: on a machine that cannot draw offscreen (forced here: the test draw's
    # process is killed) the call fails with why, and the worker and its chat go on.
    from angelo_agent import remote_executor   # A machine that cannot draw: nothing learnt from the views above.
    remote_executor.OFFSCREEN_VERDICTS.clear()
    broken = WorkerChat(STATE, PARTS / "no-gpu.blend", ROOT, environ=dict(os.environ, ANGELO_QA_GPU_PROBE_CRASH="1"))
    from angelo_agent.refusal_codes import RefusalError
    broken.call("angelo_create_cube", {"name": "Subject", "size": 1, "location": [0, 0, 0]})   # Something to draw.
    try:
        broken.executor.thumbnail(256)
        refused = None
    except RefusalError as error:
        refused = error
    check("a_failed_offscreen_draw_fails_only_that_call", refused is not None and "cannot draw offscreen pictures"
          in str(refused) and broken.worker.alive(), str(refused))
    check("the_worker_works_on_after_a_failed_draw", broken.call("angelo_create_cube", {
        "name": "After", "size": 1, "location": [0, 0, 0]}).get("success") is True and "After" in broken.objects())
    broken.stop()
    from angelo_agent import remote_executor
    check("angelo_keeps_the_verdict_for_later_workers", remote_executor.OFFSCREEN_VERDICTS.get(
        str(broken.worker.binary), [None])[0] is False, remote_executor.OFFSCREEN_VERDICTS)
    remote_executor.OFFSCREEN_VERDICTS.clear()   # The forced failure was this test's alone: this machine probes anew.
    # Here (this machine's own GPU or none): a picture, or that same refusal; the worker lives either way.
    painter = WorkerChat(STATE, PARTS / "picture.blend", ROOT)
    empty = painter.executor.thumbnail(256)
    check("an_empty_part_has_no_picture_yet", empty.get("empty") is True and empty.get("pictures") == [], empty)
    painter.call("angelo_create_cube", {"name": "Subject", "size": 1, "location": [0, 0, 0]})
    try:
        picture = painter.executor.thumbnail(256)
        drawn, report["picture_here"] = bool(picture.get("pictures")), "drawn"
    except RefusalError as error:
        drawn, report["picture_here"] = "cannot draw offscreen pictures" in str(error), "refused: no usable GPU"
    check("a_picture_is_drawn_or_cleanly_refused_and_the_worker_lives", drawn and painter.worker.alive()
          and "Subject" in painter.objects())
    painter.stop()

    # A worker that dies: its chat's calls fail at once and say so.
    lost = WorkerChat(STATE, PARTS / "lost.blend", ROOT)
    os.kill(lost.worker.process.pid, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
    lost.worker.process.wait(10)
    time.sleep(0.2)
    started = time.perf_counter()
    result = lost.call("angelo_inspect_scene", {}, timeout=30)
    check("dead_worker_fails_fast", result.get("error") and time.perf_counter() - started < 5, result)
    edit = lost.call("angelo_create_cube", {"name": "Never", "size": 1, "location": [0, 0, 0]}, timeout=30)
    check("dead_worker_edit_fails_without_running", edit.get("error"), edit)
    lost.stop()
    check("main_scene_still_unchanged", main_scene() == before, main_scene())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    try:
        worker_routing.stop_all()
    except Exception:
        report.setdefault("cleanup_error", traceback.format_exc())
(OUT / "parallel-workers.json").write_text(json.dumps(report, indent=2, default=str))
print("PARALLEL-WORKERS-DONE", report.get("success"))
if not report.get("success"):
    sys.stdout.flush()   # os._exit skips it: the DONE line must reach a piped log too.
    os._exit(1)   # Report written and workers stopped above: a failed run never exits 0.
