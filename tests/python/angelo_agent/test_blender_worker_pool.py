# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Warm workers end to end (``worker_pool``, ``worker_switch``): real worker Blenders launched ahead, and one worker
process building two parts one after another, through Angelo's own executor.

This (background) Angelo is the lead. Checked, from real processes and files:

* **Spin-up**, start to brief: a start that takes a worker launched ahead against a cold start, alternating, n each
  (``timings``); the lead's lookup of its workers' tools launched them, up to the free places only.
* **One process, two parts.** A worker builds a part with typed programs and its chat is done; the queue's seam
  (``worker_pool.reuse``) gives its process a waiting part: a new chat with the part's own number binds to the same
  process, its brief its first message; the new part holds nothing of the earlier one (objects, materials, data);
  the earlier part's file is unchanged, its chat left the worker, and both parts merge into the lead's scene.
* **Release.** Untaken workers launched ahead stop after their idle time and at the end of a lead turn that started
  none: their processes exit and their scratch parts are removed.
* **Died after a save.** A worker builds and saves, then its Blender is killed: its status row says failed, and the
  merge's own rule makes it mergeable in its row, in status's list and in the lead's observation alike; the merge
  takes its last save, with a warning, and then nothing lists it.
* **Died before its first edit.** Its part file, made at its launch, holds no objects: mergeable nowhere, and the
  merge refuses it by name, saying why, with nothing added.

Parallel edit is forced on, and this background Angelo's capability is set to a capable machine's (a background
Angelo never passes the bar's GPU check), with the run's cap at 2 workers. Provider sends are recorded, never made:
no provider or paid call. Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``.
"""

import json
import os
import statistics
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-worker-pool-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_ENABLE_PARALLEL_AGENTS="1",
                  ANGELO_PARALLEL_WORKER_CAP="2")
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (parallel_access, parallel_capability, parallel_sessions, ui, worker_dispatch,  # noqa: E402
                          worker_pool, worker_routing, worker_sessions)
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.controller import ToolDispatchBudget, pump_controller_tools  # noqa: E402
from angelo_agent.headless_main import _register  # noqa: E402
from parallel_workers_qa import CASES, WorkerChat, no_worker_done_event  # noqa: E402
from hosted_worker_chat import HostedWorkerChat, connect, counted_at_once, hosted, install_stand_in, ui_tick  # noqa

HOSTED = hosted()   # Design A′: a worker chat's turns run in its worker (the "hosted" variant).
if HOSTED:
    install_stand_in(OUT / "stand-in")   # The model those turns run on: no provider or paid call.

report = {"variant": "hosted" if HOSTED else "lanes", "checks": {}, "paid_requests": 0,
          "binary_path": bpy.app.binary_path, "platform": sys.platform,
          "timings": {}}
START, STATUS, MERGE, LOOKUP = ("angelo_workers_start", "angelo_workers_status", "angelo_workers_merge",
                                "angelo_agent_capabilities")
TRIALS = int(os.environ.get("ANGELO_QA_POOL_TRIALS") or 5)
sent = {}   # chat session -> [(time.perf_counter(), text)]


def check(name, condition, detail=None):
    report["checks"][name] = bool(condition)
    if not condition and detail is not None:
        report.setdefault("details", {})[name] = detail
    assert condition, name


def recorded(make):
    def new_chat(lead):
        chat = make(lead)

        def send(text, observation=None, recovery_evidence=None, chat=chat):
            sent.setdefault(chat.state["id"], []).append((time.perf_counter(), text))
            if HOSTED:   # Really sent: its worker's turn runs (the stand-in keeps working) and the mirror shows it.
                HostedWorkerChat.send_brief(chat, text)
                return
            chat.state.setdefault("messages", []).append({"role": "user", "text": text})   # As a delivered send.
        chat.send = send
        return chat
    return new_chat


def drive(controller):
    """A worker chat made by the lead's start, driven as a provider turn drives it (``WorkerChat``; hosted: its own
    turns in its worker, ``HostedWorkerChat``)."""
    if HOSTED:   # Its brief's turn (held by the stand-in) ends first: what follows are the driven turns.
        chat = HostedWorkerChat.of(controller)
        chat.release("brief")
        chat.wait_idle()
        return chat
    chat = WorkerChat.__new__(WorkerChat)
    chat.controller, chat.session = controller, controller.state["id"]
    chat.executor = worker_routing.bound(controller)
    chat.worker = chat.executor.worker
    chat.fence, chat.timings, chat.pumps, chat._count = controller.open_external_read(), [], [], 0
    return chat


def turn_over(controller, chat):
    """The driven turn ends: a lane's external read closes; a hosted chat's own turn has ended with its reply."""
    if HOSTED:
        chat.wait_idle()
    else:
        controller.close_external_read(chat.fence)


def rss_mb(pid):   # Reported only; read on every platform (qa_machine.resident_mb: no ps on Windows).
    from qa_machine import resident_mb
    return resident_mb(pid)


def settled(lead):
    for entry in list(worker_pool._pool):
        check("a_worker_launched_ahead_became_ready", entry["done"].wait(120) and entry["error"] is None,
              repr(entry["error"]))
    return [entry["worker"] for entry in worker_pool._pool]


def end_trial(lead):
    for session in list(worker_sessions.workers_of(lead.state["id"])):
        chat = ui._controllers.get(session)
        if chat is not None and worker_routing.bound(chat) is not None:
            worker_routing.unbind(chat, worker_routing.END_CLOSING)
    worker_pool.release_all()
    for thread in list(worker_routing._stopping):
        thread.join(30)


def start_one(lead, executor, name):
    """Start one part through the lead's executor; seconds from the start to its brief, and its chat."""
    begun = time.perf_counter()
    result = executor.execute(START, {"parts": [{"name": name, "brief": "A small %s, about 1 m." % name.lower()}]})
    check("start_" + name + "_answers", result.get("success") is True, result)
    session = worker_sessions.workers_of(lead.state["id"])[-1]
    check("brief_" + name + "_sent_once", len(sent.get(session, ())) == 1, sent.get(session))
    return sent[session][0][0] - begun, ui._controllers[session]


def lookup(lead, executor):
    lead.busy = True
    begun = time.perf_counter()
    executor.execute(LOOKUP, {"family": "TOOLS", "names": [START, STATUS, MERGE]})
    report["timings"].setdefault("lookup_ms", []).append(round((time.perf_counter() - begun) * 1000, 1))


def spin_up(lead, executor):
    cold, ahead, launches, memory = [], [], [], []
    for trial in range(TRIALS):
        seconds, chat = start_one(lead, executor, "Cold %d" % trial)
        cold.append(seconds)
        from angelo_agent import worker_queue
        check("cold_%d_bound_after_its_start" % trial, worker_routing.bound(chat) is not None, {
            "parked": chat.state["id"] in worker_queue.parked, "died": worker_sessions.died.get(chat.state["id"]),
            "messages": [(m.get("role"), str(m.get("text"))[:200]) for m in chat.state.get("messages") or ()][-4:],
            "busy": chat.busy, "failures": dict(worker_sessions.failures)})
        launches.append(worker_routing.bound(chat).worker.ready_seconds)
        check("cold_%d_launched" % trial, worker_routing.bound(chat).worker.how == "launched")
        end_trial(lead)
        lookup(lead, executor)
        pool = settled(lead)
        check("ahead_%d_fills_the_free_places" % trial, len(pool) == parallel_access.worker_limit(), len(pool))
        memory.extend(rss_mb(worker.process.pid) for worker in pool)
        seconds, chat = start_one(lead, executor, "Ahead %d" % trial)
        ahead.append(seconds)
        worker = worker_routing.bound(chat).worker
        check("ahead_%d_took_a_ready_worker" % trial, worker.how == "switched" and worker in pool
              and worker not in worker_pool.pooled(), worker.how)
        check("ahead_%d_scratch_removed" % trial, worker.scratch is None)
        lead.busy = False
        end_trial(lead)
    report["timings"]["start_to_brief_s"] = {
        "cold": [round(v, 3) for v in cold], "ahead": [round(v, 3) for v in ahead],
        "cold_median": round(statistics.median(cold), 3), "ahead_median": round(statistics.median(ahead), 3),
        "saved_median": round(statistics.median(cold) - statistics.median(ahead), 3),
        "cold_launch_ready_s": [round(v, 3) for v in launches]}
    report["timings"]["idle_worker_rss_mb"] = memory
    check("a_start_from_a_worker_launched_ahead_is_faster", statistics.median(ahead) < statistics.median(cold))


def part_names(path):
    with bpy.data.libraries.load(str(path)) as (source, _target):
        return sorted(source.objects), sorted(source.materials), sorted(source.meshes)


def lead_merge(lead, window, arguments):
    if HOSTED:   # Its workers reported their parts' counts with their saves: not refused as being checked.
        check("parts_counted_at_their_saves_%d" % len(report["checks"]), counted_at_once())
    answers = []
    lead.request_tool("merge-%d" % time.monotonic_ns(), MERGE, arguments, answers.append)

    def apply_mutation(target, call):
        with bpy.context.temp_override(window=window):
            bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=target.state["id"])
    started = time.perf_counter()
    while not answers and time.perf_counter() - started < 120:
        lead.pump_events()
        pump_controller_tools([lead], 0, BlenderExecutor(window), ui.document_key(), apply_mutation,
                              budget=ToolDispatchBudget(seconds=60))
    check("merge_answered", bool(answers))
    return answers[0]


def two_parts(lead, executor, window):
    """One worker process builds a knight, then (warm reuse) a car; both merge."""
    _seconds, first = start_one(lead, executor, "Knight")
    knight = drive(first)
    for edit in CASES["chess"]()[0]:
        check("knight_built", knight.call("angelo_scene_program", edit).get("success") is True)
    turn_over(first, knight)
    done = first.state["id"]
    check("knight_chat_is_done", worker_sessions.finished(done))
    number_one = worker_sessions._parts[done][0]
    knight_file = Path(worker_sessions._parts[done][2])
    knight_saved = (os.path.getmtime(knight_file), part_names(knight_file))
    pid = knight.worker.process.pid
    report["timings"]["worker_rss_mb_after_part_1"] = rss_mb(pid)

    number_two = worker_sessions._numbers[lead.state["id"]] + 1   # As the lead's start numbers a queued part.
    worker_sessions._numbers[lead.state["id"]] = number_two
    item = worker_sessions._prepare(lead, {"name": "Car", "brief": "A small red car with four wheels."}, number_two)
    item["references"] = ()
    begun = time.perf_counter()
    check("the_done_workers_process_takes_the_waiting_part", worker_pool.reuse(done, item))
    while worker_sessions._launching:
        worker_sessions._tick()
        time.sleep(0.005)
    second = next(ui._controllers[s] for s in worker_sessions.workers_of(lead.state["id"]) if s != done
                  and worker_sessions._parts.get(s, (None,))[0] == number_two)
    worker_dispatch.settle(second)
    if HOSTED:   # Its brief goes once its chat.open is answered, reconciled on a UI tick (off the main thread).
        deadline = time.monotonic() + 15
        while second.state["id"] not in sent and time.monotonic() < deadline:
            worker_sessions._tick()
            ui_tick()
            time.sleep(0.005)
        check("reused_workers_chat_briefed", second.state["id"] in sent)
    report["timings"]["reuse_to_brief_s"] = round(sent[second.state["id"]][0][0] - begun, 3)
    car = drive(second)
    check("same_process_new_chat", car.worker.process.pid == pid and second is not first)
    check("its_own_number_and_file", worker_sessions._parts[second.state["id"]][:2] == (number_two, "Car")
          and worker_sessions._parts[second.state["id"]][2] != knight_file)
    # Its part is done and saved: its tile says how its turn ended, never ended (parked, as a stopped done one is).
    check("the_done_chat_left_the_worker", worker_routing.bound(first) is None and not parallel_sessions.ended(done))
    check("the_new_part_starts_empty", set(car.objects()) == set(), sorted(car.objects()))
    for edit in CASES["vehicle"]()[0]:
        result = car.call("angelo_scene_program", edit)
        check("car_built_with_its_undo_step", result.get("success") is True and result.get("undoable") is True, result)
    turn_over(second, car)
    report["timings"]["worker_rss_mb_after_part_2"] = rss_mb(pid)
    check("car_chat_is_done", worker_sessions.finished(second.state["id"]))
    car_objects, car_materials, _meshes = part_names(Path(worker_sessions._parts[second.state["id"]][2]))
    check("the_car_holds_nothing_of_the_knight", not ({"Knight Base", "Knight Neck", "Knight Head"} & set(car_objects))
          and "Ebony" not in car_materials, [car_objects, car_materials])
    check("the_knights_file_is_unchanged", (os.path.getmtime(knight_file), part_names(knight_file)) == knight_saved)
    status = executor.execute(STATUS, {})["workers"]
    named = {row["worker"]: row["name"] for row in status}
    check("status_keeps_each_parts_number", named.get(number_one) == "Knight" and named.get(number_two) == "Car",
          status)
    fence = lead.open_external_read()   # The lead's turn, as a merge runs in one.
    merged = lead_merge(lead, window, {"parts": [{"worker": number_one}, {"worker": number_two}]})
    names = {obj.name for obj in bpy.data.objects}
    check("both_parts_merge", not merged.get("error") and {"Knight Base", "Car Body"} <= names, merged)
    lead.close_external_read(fence)


def release(lead, executor):
    lookup(lead, executor)
    pool = settled(lead)
    scratch = [worker.scratch for worker in pool]
    real = worker_pool.IDLE_SECONDS
    worker_pool.IDLE_SECONDS = 1.0
    try:
        lookup(lead, executor)   # Looked up again: their idle time restarts, and no place is free for another.
        check("a_second_lookup_launches_none_past_the_limit", worker_pool.pooled() == pool)
        check("kept_while_idle_time_remains", worker_pool.tick() == worker_pool.TICK and len(worker_pool._pool) == 2)
        time.sleep(1.2)
        check("released_after_its_idle_time", worker_pool.tick() is None and not worker_pool._pool)
    finally:
        worker_pool.IDLE_SECONDS = real
    for thread in list(worker_pool._stopping):
        thread.join(30)
    check("idle_workers_exited", all(worker.process.poll() is not None for worker in pool))
    check("idle_scratch_parts_removed", not any(folder.exists() for folder in scratch if folder is not None))
    lookup(lead, executor)
    pool = settled(lead)
    lead.busy = False   # The turn ends without a start.
    check("released_at_the_turns_end", worker_pool.tick() is None)
    for thread in list(worker_pool._stopping):
        thread.join(30)
    check("turn_end_workers_exited", all(worker.process.poll() is not None for worker in pool))


def died_after_save(lead, executor, window):
    from angelo_agent import turn_context, worker_facts
    _seconds, chat = start_one(lead, executor, "Rook")
    built = drive(chat)
    for edit in CASES["chess"]()[0][:1]:
        check("rook_built", built.call("angelo_scene_program", edit).get("success") is True)
    number, _name, part = worker_sessions._parts[chat.state["id"]]
    saved = part_names(Path(part))[0]
    check("rook_saved_before_its_death", bool(saved), saved)
    built.worker.process.kill()   # SIGKILL here, TerminateProcess on Windows (Popen.kill): no signal Windows lacks.
    built.worker.process.wait(30)
    turn_over(chat, built)   # Its turn ends (its runtime's next call fails).
    status = executor.execute(STATUS, {})   # Status reconciles the confirmed death.
    row = next(r for r in status["workers"] if r["worker"] == number)
    listed = "%d Rook" % number
    observed = turn_context.prepare(window, lead.state["id"]).observation.get(worker_facts.OBSERVED) or []
    check("died_row_failed_and_mergeable", row["state"] == "failed" and row["mergeable"] is True, row)
    check("died_listed_in_status_and_the_observation", listed in status.get("mergeable_not_in_this_scene", ())
          and listed in observed, {"status": status, "observed": observed})
    fence = lead.open_external_read()
    merged = lead_merge(lead, window, {"parts": [{"worker": number}]})
    lead.close_external_read(fence)
    names = {obj.name for obj in bpy.data.objects}
    check("died_part_merges_its_last_save", not merged.get("error") and set(saved) <= names, merged)
    check("died_merge_says_it_ended", any("ended before it finished" in (entry.get("warning") or "")
                                           for entry in merged.get("merged") or ()), merged)
    after = executor.execute(STATUS, {})
    row = next(r for r in after["workers"] if r["worker"] == number)
    check("merged_it_is_mergeable_nowhere", row["merged_into_this_scene"] is True and row["mergeable"] is False
          and listed not in after.get("mergeable_not_in_this_scene", ()), after)
    end_trial(lead)
    _seconds, chat = start_one(lead, executor, "Pawn")   # Killed before its first edit: an empty part.
    empty = drive(chat)
    number = worker_sessions._parts[chat.state["id"]][0]
    empty.worker.process.kill()
    empty.worker.process.wait(30)
    turn_over(chat, empty)
    status = executor.execute(STATUS, {})
    row = next(r for r in status["workers"] if r["worker"] == number)
    check("an_empty_died_part_is_mergeable_nowhere", row["state"] == "failed" and row["mergeable"] is False
          and "%d Pawn" % number not in status.get("mergeable_not_in_this_scene", ()), status)
    collections = len(bpy.data.collections)
    fence = lead.open_external_read()
    merged = lead_merge(lead, window, {"parts": [{"worker": number}]})
    lead.close_external_read(fence)
    check("the_merge_refuses_it_saying_why_with_nothing_added", "holds no objects" in str(merged.get("error"))
          and len(bpy.data.collections) == collections, merged)
    end_trial(lead)


def run_checks():
    _register(bpy, ui)
    parallel_sessions.register()
    parallel_capability._measured[:] = [parallel_capability.Capability(True, "QA", 64.0, 16, 12)]
    ui.prepare_controller = lambda controller=None: None   # No connection here: nothing reaches a provider.
    worker_sessions._new_chat = recorded(worker_sessions._new_chat)
    lead = ui.get_controller()
    no_worker_done_event()   # Its lead is driven by hand (parallel_workers_qa).
    lead.state["options"] = dict(lead.state.get("options") or {}, approval="AUTO_BOUNDED")
    if HOSTED:   # Its workers' chats inherit its connection: Auto is granted to a hosted chat only with one.
        connect(lead)
    lead.set_document(ui.document_key())
    window = bpy.context.window_manager.windows[0]
    executor = BlenderExecutor(window)
    executor.studio_context = lead
    report["worker_limit"] = parallel_access.worker_limit()
    lead.busy = False
    executor.execute(LOOKUP, {"family": "TOOLS", "names": [START]})
    check("no_launch_outside_the_leads_turn", not worker_pool._pool)
    spin_up(lead, executor)
    release(lead, executor)
    died_after_save(lead, executor, window)
    two_parts(lead, executor, window)
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
(OUT / "worker-pool.json").write_text(json.dumps(report, indent=2, default=str))
print("WORKER-POOL-DONE", report.get("success"))
if not report.get("success"):
    sys.stdout.flush()
    os._exit(1)
