# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The lead starts workers (``angelo_workers_start``, ``worker_sessions``) through Angelo's own executor.

This (background) Angelo has a lead chat on its document. Its tool call, run through ``BlenderExecutor`` as an
approved call runs, starts two real workers, each with a new chat (never shown in place of the person's), the lead's
options and approval mode, its live scene and tile, and its brief sent as its first message, observed on the worker's
own document. Also checked: the person's chat stays active; status reports every worker; a worker's chat cannot
start workers; a disabled Angelo is refused before anything starts. Places (``worker_queue``, this run capped): a start
past them is queued, listed with its place in line, shown on the rail and can be taken back; a worker whose turn ended
saves, stops (never died or failed) and its place goes to the next queued part, a real third worker; a message to it
relaunches a worker on its own part file and is delivered there. A done, unmerged part is listed in status and in the
lead's own scene observation, and not once it is merged; an Undo of the merge lists it again (``worker_facts``). The
provider send is recorded, never made: no provider or paid call.

Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-parallel-lead-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_ENABLE_PARALLEL_AGENTS="1")
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import live_view, parallel_sessions, ui, worker_routing, worker_sessions  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.headless_main import _register  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from parallel_workers_qa import WorkerChat, primitive, program  # noqa: E402
import hosted_worker_chat  # noqa: E402
from hosted_worker_chat import HostedWorkerChat, hosted, install_stand_in  # noqa: E402

# Design A′ (the "hosted" variant): a worker's chat is a mirror whose turns run in its worker (the worker observes its
# own document, so Angelo hands its brief no observation); "lanes" (ANGELO_WORKER_CHAT_HOST=0) keeps the chat here.
HOSTED = hosted()
if HOSTED:
    install_stand_in(OUT / "stand-in")   # The model a hosted worker chat's turn runs on (no provider or paid call).

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "platform": sys.platform,
          "variant": "hosted" if HOSTED else "lanes"}
START, STATUS = "angelo_workers_start", "angelo_workers_status"
sent = {}


def check(name, condition, detail=None):
    report["checks"][name] = bool(condition)
    if not condition and detail is not None:
        report.setdefault("details", {})[name] = detail
    assert condition, name


def refused(executor, name, arguments):
    try:
        executor.precondition(name, arguments)
    except (RefusalError, ValueError) as error:
        return str(error)
    return None


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True   # The merge is one Undo step (``unmerged``).
    _register(bpy, ui)
    from angelo_agent import session_rail_input
    session_rail_input.register()   # As the add-on registers it: its sessions and, in the background too, its load watch.
    ui.prepare_controller = lambda controller=None: None   # No connection here: nothing reaches a provider.
    made = worker_sessions._new_chat

    def recorded_chat(lead):
        chat = made(lead)
        chat.send = lambda text, observation=None, recovery_evidence=None, chat=chat: sent.setdefault(
            chat.state["id"], []).append({"text": text, "observation": observation})
        return chat
    worker_sessions._new_chat = recorded_chat
    lead = ui.get_controller()
    lead.state["options"] = dict(lead.state.get("options") or {}, approval="ASK")
    if HOSTED:   # Its workers' chats inherit its connection: a hosted chat's model launches only with a usable one.
        hosted_worker_chat.connect(lead)
    window = bpy.context.window_manager.windows[0]
    executor = BlenderExecutor(window)
    executor.studio_context = lead
    parts = [{"name": "Kettle", "brief": "A stovetop kettle, 22 cm tall, brushed steel, black handle."},
             {"name": "Mug", "brief": "A ceramic mug, 9 cm tall, glazed cobalt blue, on the table's left."}]
    arguments = {"parts": parts}
    executor.precondition(START, arguments)
    result = executor.execute(START, arguments)
    check("start_answers_with_the_parts", result.get("success") and [p["name"] for p in result["starting"]]
          == ["Kettle", "Mug"], result)
    workers = worker_sessions.workers_of(lead.state["id"])
    check("two_workers_started_for_the_lead", len(workers) == 2, workers)
    # Workers join in the order they become ready (Windows build4 2026-10-24: Mug before Kettle), never necessarily
    # the order asked: each part is found by its name, and its number is the one given at launch (in request order).
    by_name = {parallel_sessions.names.get(session_id): session_id for session_id in workers}
    for part in parts:
        session_id = by_name.get(part["name"])
        check(part["name"] + "_has_a_tile_and_name", session_id is not None
              and session_id in parallel_sessions.sessions(), by_name)
        chat = ui._controllers[session_id]
        check(part["name"] + "_chat_is_bound_to_a_worker", worker_routing.bound(chat) is not None, {
            "failures": dict(worker_sessions.failures), "died": dict(worker_sessions.died)})   # Why it left its worker.
        check(part["name"] + "_has_a_live_part", live_view.part(session_id) is not None)
        check(part["name"] + "_inherits_the_leads_mode", chat.state["options"].get("approval") == "ASK")
        messages = sent.get(session_id, [])
        check(part["name"] + "_brief_sent_once", len(messages) == 1 and part["brief"] in messages[0]["text"]
              and "Part: " + part["name"] in messages[0]["text"], messages)
        if HOSTED:   # The worker observes its own document for its turn: Angelo's send carries none (HostedTurn).
            from angelo_agent import worker_chat_lifecycle
            check(part["name"] + "_brief_goes_to_its_worker_unobserved_here", messages[0]["observation"] is None
                  and worker_chat_lifecycle.hosted(session_id) is worker_routing._workers.get(session_id), messages)
        else:
            check(part["name"] + "_brief_observed_on_its_worker", isinstance(messages[0]["observation"], dict),
                  messages)
        folder = Path(worker_routing.bound(chat).worker.part).parent
        check(part["name"] + "_part_in_the_state_folder", str(folder).startswith(str(OUT / "state" / "parallel")),
              str(folder))
    check("the_persons_chat_stays_active", ui._sessions.active == lead.state["id"])
    check("lead_tile_first", parallel_sessions.sessions()[0] == lead.state["id"])
    status = executor.execute(STATUS, {})
    numbered = sorted(status["workers"], key=lambda row: row["worker"])   # Launch numbers follow the request.
    check("status_reports_each_worker", [(row["worker"], row["name"]) for row in numbered]
          == [(1, "Kettle"), (2, "Mug")] and all(row["state"] == "idle" for row in status["workers"]), status)
    key = "%s/2" % lead.state["id"]   # A worker whose brief never reached it: listed once, with why (E2E-4 L-5).
    worker_sessions.failures[key] = "DeliveryStopped: the brief was not sent"
    failed = executor.execute(STATUS, {})["workers"]
    del worker_sessions.failures[key]
    check("a_worker_that_never_started_is_listed_once", [(row["worker"], row["state"]) for row in failed]
          == [(1, "idle"), (2, "failed_to_start")], failed)

    worker_executor = BlenderExecutor(window)
    worker_executor.studio_context = ui._controllers[workers[0]]
    check("a_worker_cannot_start_workers", "worker does not start workers" in (refused(
        worker_executor, START, {"parts": [{"name": "Lid", "brief": "A lid."}]}) or ""))
    from angelo_agent import parallel_access
    limit = parallel_access.worker_limit()   # This machine's own count (forced on, it still follows the machine).
    many = {"parts": [{"name": "P%d" % i, "brief": "A part."}
                      for i in range(min(limit - len(workers) + 1, worker_sessions.MAX_AUTO_STARTS))]}
    check("more_than_the_machines_count_at_once_waits_never_refused", refused(executor, START, many) is None, limit)
    places(executor, lead, window, by_name)
    # places() ends with a load, which frees the window held above: what follows reads the live one, as Angelo's own
    # operators build their executor from the window shown now (a held one faulted on Windows, C2 2026-10-25).
    window = bpy.context.window_manager.windows[0]
    check("a_held_executor_never_reads_the_freed_window", executor.window is None
          or executor.window.as_pointer() == window.as_pointer())
    executor = BlenderExecutor(window)
    executor.studio_context = lead
    os.environ["ANGELO_ENABLE_PARALLEL_AGENTS"] = "0"
    check("a_disabled_angelo_refuses", "not enabled" in (refused(executor, START, {"parts": parts[:1]}) or ""))
    os.environ["ANGELO_ENABLE_PARALLEL_AGENTS"] = "1"
    report["success"] = True


def places(executor, lead, window, by_name):
    """Two places in this run, both taken by the idle Kettle and Mug workers (their briefs' turns never end here)."""
    from angelo_agent import session_rail_draw, worker_process, worker_queue
    os.environ["ANGELO_PARALLEL_WORKER_CAP"] = "2"
    teapot = {"parts": [{"name": "Teapot", "brief": "A teapot, 15 cm tall, white porcelain."}]}
    executor.precondition(START, teapot)
    answer = executor.execute(START, teapot)
    check("a_start_past_the_limit_is_queued", answer.get("starting") == [] and [
        (q["worker"], q["position"]) for q in answer.get("queued", ())] == [(3, 1)], answer)
    status = executor.execute(STATUS, {})
    queued = [(row["worker"], row["position"]) for row in status["workers"] if row["state"] == "queued"]
    check("status_lists_the_queued_part_and_the_places", queued == [(3, 1)]
          and "At most 2 workers run at once in this run" in status.get("places", ""), status)
    tiles = [(tile.title, tile.kind) for tile in session_rail_draw.states()]
    check("the_rail_shows_the_queued_part", ("Teapot", "queued") in tiles, tiles)
    taken = executor.execute(STATUS, {"take_back": [3]})
    check("a_queued_part_is_taken_back", taken.get("taken_back") == [3] and not any(
        row["state"] == "queued" for row in taken["workers"]) and not worker_queue.queue, taken)
    saucer = {"parts": [{"name": "Saucer", "brief": "A saucer, 14 cm across, white porcelain."}]}
    executor.precondition(START, saucer)
    check("the_next_part_waits_too", [q["worker"] for q in executor.execute(START, saucer).get("queued", ())] == [4])
    kettle_id = by_name["Kettle"]
    kettle = ui._controllers[kettle_id]
    worker = worker_routing.bound(kettle).worker
    part = Path(worker.part)
    kettle_body = program([primitive("CYLINDER", "Kettle Body", "body", radius=0.1, depth=0.2, location=[0, 0, 0.1])])
    if HOSTED:   # Its turn runs in its worker: the stand-in model builds it, the person approves its card here.
        built = HostedWorkerChat.of(kettle)
        key, answers = built.request("angelo_scene_program", kettle_body)
        call = built.wait_for_approval(key, answers)
        built.approve(call)
        result = built.wait(answers)
        built.wait_idle()   # Its turn ended with the model's reply: it is done.
    else:
        built = WorkerChat.__new__(WorkerChat)   # It builds its part first: an empty part merges nowhere.
        built.controller, built.session, built._count = kettle, kettle_id, 0
        built.executor, built.worker, built.timings, built.pumps = worker_routing.bound(kettle), worker, [], []
        built.fence = kettle.open_external_read()
        key, answers = built.request("angelo_scene_program", kettle_body)
        call = built.wait_for_approval(key, answers)
        with bpy.context.temp_override(window=window):
            bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=kettle_id)
        result = built.wait(answers)
        kettle.close_external_read(built.fence)
    check("the_kettle_builds_its_part", result.get("status") == "committed", result)
    if not HOSTED:
        kettle.emit("message", {"role": "user", "text": "Build the kettle."})   # Its brief's turn ended: it is done.
        kettle.pump_events()
    status = executor.execute(STATUS, {})   # A background Angelo steps the places here: it saves, stops, Saucer starts.
    row = next(row for row in status["workers"] if row["worker"] == 1)
    started = {parallel_sessions.names.get(s): s for s in worker_sessions.workers_of(lead.state["id"])}
    saucer = worker_routing.bound(ui._controllers[started["Saucer"]]) if "Saucer" in started else None
    # Its place frees: its warm process takes the queued part (worker_pool.reuse), else it stops; never died.
    check("a_done_worker_saves_and_stops_never_died", kettle_id in worker_queue.parked
          and (getattr(saucer, "worker", None) is worker or worker.ended.wait(30))
          and worker_routing.bound(kettle) is None and kettle_id not in worker_sessions.died
          and row["state"] not in ("failed", "ended") and part.exists(), row)
    check("its_place_went_to_the_queued_part", "Saucer" in started and worker_routing.bound(
        ui._controllers[started["Saucer"]]) is not None and len(sent.get(started["Saucer"], ())) == 1, started)
    unmerged(executor, lead, window)
    # Merged once, its parked chat ended (break-it E2E-19A M-3): a message is refused and launches nothing.
    refusals, launches = [], len(worker_process.live())
    # Hosted: a mirror's turn is its worker's own (no observation to refuse here), so the send itself is the one that
    # refuses (its chat is no longer open in any worker); the lanes refuse while preparing the turn.
    send = ((lambda turn: (kettle.send("Make the handle red."), refusals.append("sent"))) if HOSTED
            else (lambda turn: refusals.append("sent")))
    worker_routing.deliver(kettle, window, "Make the handle red.", send, refusals.append)
    check("a_merged_parked_worker_ends_and_a_message_launches_nothing", kettle_id not in worker_queue.parked
          and worker_routing.bound(kettle) is None and len(refusals) == 1 and refusals[0] != "sent"
          and len(worker_process.live()) == launches, refusals)
    saucer_id = started["Saucer"]   # Done and parked, never merged: a message starts it again on its own part.
    saucer_chat = ui._controllers[saucer_id]
    saucer_worker = worker_routing.bound(saucer_chat).worker
    saucer_part = Path(saucer_worker.part)
    if HOSTED:   # A mirror's rows come from its worker's journal only: its turn really runs there and ends.
        saucer_done = HostedWorkerChat.of(saucer_chat)
        saucer_done.say("Build the saucer.", [{"reply": "Saucer built."}])
        saucer_done.wait_idle()
    else:
        saucer_chat.emit("message", {"role": "user", "text": "Build the saucer."})
        saucer_chat.pump_events()
    executor.execute(STATUS, {})   # It saves and stops (its place: nothing waits for it).
    check("the_saucer_is_parked", saucer_id in worker_queue.parked and worker_routing.bound(saucer_chat) is None,
          sorted(worker_queue.parked))
    os.environ["ANGELO_PARALLEL_WORKER_CAP"] = "3"   # A place for its relaunch.
    delivered = []
    if HOSTED:   # Its message really goes (the brief's recorder taken off): the worker's turn is what it reaches.
        saucer_driver = HostedWorkerChat.of(saucer_chat)
        saucer_driver.queue_turn([{"reply": "Gold rim noted."}])

        def send(turn):
            delivered.append(turn.observation)
            saucer_chat.send("Make the rim gold. " + hosted_worker_chat.stand_in.tag(saucer_id))
    else:
        send = lambda turn: delivered.append(turn.observation)   # noqa: E731
    worker_routing.deliver(saucer_chat, window, "Make the rim gold.", send, delivered.append)
    again = worker_routing.bound(saucer_chat)
    check("a_message_relaunches_it_on_its_own_part", again is not None and Path(again.worker.part) == saucer_part
          and again.worker is not saucer_worker and saucer_id not in worker_queue.parked,
          {"part": str(saucer_part), "bound": again is not None, "parked": saucer_id in worker_queue.parked,
           "delivered": [str(item)[:300] for item in delivered]})
    if HOSTED:   # The worker observes its own document: the message reached the model there.
        reached = lambda: any("Make the rim gold." in str(row.get("text"))   # noqa: E731
                              for row in saucer_driver.model_log("turn_started"))
        deadline = time.monotonic() + 30
        while not reached() and time.monotonic() < deadline:
            saucer_driver.pump()
        check("the_message_reaches_it_there", len(delivered) == 1 and reached(), delivered)
    else:
        check("the_message_reaches_it_there", len(delivered) == 1 and isinstance(delivered[0], dict), delivered)
    os.environ.pop("ANGELO_PARALLEL_WORKER_CAP", None)
    # A load that replaces the lead's document stops its workers, each saying why (break-it E2E-18 M-3).
    running = [s for s in worker_sessions.workers_of(lead.state["id"])
               if s in ui._controllers and worker_routing.bound(ui._controllers[s]) is not None]
    lead.state["messages"].append({"role": "user", "text": "Build the tea set."})   # As a real lead's request.
    bpy.ops.wm.read_homefile(use_empty=True)
    rows = {row["worker"]: row for row in worker_sessions.status(lead)["workers"]}
    check("a_load_that_replaces_the_leads_document_stops_its_workers", lead.document_invalid and running and all(
        worker_routing.bound(ui._controllers[s]) is None and worker_sessions.died.get(s) == worker_queue.REPLACED
        and rows[worker_sessions._parts[s][0]]["state"] == "failed" for s in running),
          {"invalid": lead.document_invalid, "running": running, "rows": rows})


def unmerged(executor, lead, window):
    """The Kettle (worker 1) is done and its part saved, not merged: status and the lead's own scene observation say
    so; merged through the lead's approved-change path it is in this scene and listed nowhere; an Undo of that merge
    brings both back (``worker_facts``; a lead in 3 of 3 live Opus trials ended its turn with finished parts never
    merged). The still-working Mug reports how long since its brief and its rounds. Each status is kept for the
    byte costs (``report["facts"]``)."""
    import time
    from angelo_agent import turn_context
    from angelo_agent.tool_pump import pump_controller_tools
    from angelo_agent.controller import ToolDispatchBudget
    field = "worker_parts_mergeable_not_in_this_scene"

    def seen(name):
        status = executor.execute(STATUS, {})
        rows = {row["worker"]: row for row in status["workers"]}
        observed = turn_context.prepare(window, lead.state["id"]).observation
        report.setdefault("facts", {})[name] = {"status": status, "observed": observed.get(field)}
        return status, rows, observed.get(field)
    status, rows, observed = seen("done_unmerged")
    check("a_finished_unmerged_part_is_listed_in_status", rows[1]["merged_into_this_scene"] is False
          and status.get("mergeable_not_in_this_scene") == ["1 Kettle"], status)
    check("and_in_the_leads_scene_observation", observed == ["1 Kettle"], observed)
    check("a_working_worker_reports_its_time_since_its_brief_and_rounds", rows[2]["merged_into_this_scene"] is False
          and rows[2].get("elapsed_s", -1) >= 0 and rows[2].get("rounds") == 0 and "built_in_s" not in rows[2], rows[2])
    bpy.ops.ed.undo_push(message="Before the merge")   # A background Blender starts its Undo history on request.
    options = dict(lead.state["options"])
    lead.state["options"] = dict(options, approval="AUTO_BOUNDED")   # The merge applies as an Auto edit does.
    fence, answers = lead.open_external_read(), []
    lead.request_tool("merge-%d" % time.monotonic_ns(), "angelo_workers_merge", {"parts": [{"worker": 1}]},
                      answers.append)

    def apply(target, call):
        with bpy.context.temp_override(window=window):
            bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=target.state["id"])
    deadline = time.perf_counter() + 60
    while not answers and time.perf_counter() < deadline:
        lead.pump_events()
        pump_controller_tools([lead], 0, BlenderExecutor(window), ui.document_key(), apply,
                              budget=ToolDispatchBudget(seconds=60))
    lead.close_external_read(fence)
    lead.state["options"] = options
    check("the_kettle_merges", answers and answers[0].get("success") is True and "Kettle" in bpy.data.collections,
          answers)
    status, rows, observed = seen("merged")
    check("merged_it_is_in_this_scene_and_listed_nowhere", rows[1]["merged_into_this_scene"] is True
          and "mergeable_not_in_this_scene" not in status and observed is None, {"status": status, "observed": observed})
    bpy.ops.ed.undo()
    status, rows, observed = seen("after_undo")
    check("an_undo_of_the_merge_brings_it_back", "Kettle" not in bpy.data.collections and rows[1]["merged_into_this_scene"]
          is False and status.get("mergeable_not_in_this_scene") == ["1 Kettle"] and observed == ["1 Kettle"],
          {"status": status, "observed": observed})


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    try:
        worker_routing.stop_all()
    except Exception:
        report.setdefault("cleanup_error", traceback.format_exc())
(OUT / "parallel-lead.json").write_text(json.dumps(report, indent=2, default=str))
print("PARALLEL-LEAD-DONE", report.get("success"))
if not report.get("success"):
    sys.stdout.flush()   # os._exit skips it: the DONE line must reach a piped log too.
    os._exit(1)   # Report written and workers stopped above: a failed run never exits 0.
