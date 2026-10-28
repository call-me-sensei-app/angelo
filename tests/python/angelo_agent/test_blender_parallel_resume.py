# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The worker-done event in a real flow (``worker_resume``, resume-lead design (i)): the lead's turn ends while its
real worker builds; the worker builds its part and its turn ends; at the next queue step Angelo continues the lead's
chat once, with the parts not merged into its scene, its row Angelo's; the lead merges through its approved-change path
and nothing fires again. Also: no event while the lead's own turn runs (it fires once that turn ends), none after the
lead's Stop, and none in Ask while ``worker_resume.ASK_TURNS`` is False.

The lead's send is recorded (and its row emitted as ``Controller._start_turn`` emits it), never made; worker chats'
sends are recorded too. No provider or paid call. Run only in a disposable instance with the exact Angelo binary:
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-parallel-resume-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_ENABLE_PARALLEL_AGENTS="1")
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import parallel_sessions, ui, worker_queue, worker_resume, worker_routing, worker_sessions  # noqa
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.headless_main import _register  # noqa: E402
from parallel_workers_qa import WorkerChat, primitive, program  # noqa: E402
from hosted_worker_chat import HostedWorkerChat, connect, counted_at_once, hosted, install_stand_in, ui_tick  # noqa

HOSTED = hosted()   # Design A′: a worker builds its part in its own chat's turn (the "hosted" variant).
if HOSTED:
    install_stand_in(OUT / "stand-in")   # The model that turn runs on: no provider or paid call.
report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "platform": sys.platform,
          "variant": "hosted" if HOSTED else "lanes"}
START = "angelo_workers_start"
sent, lead_sends = {}, []


def check(name, condition, detail=None):
    report["checks"][name] = bool(condition)
    if not condition and detail is not None:
        report.setdefault("details", {})[name] = detail
    assert condition, name


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    _register(bpy, ui)
    from angelo_agent import session_rail_input
    session_rail_input.register()
    ui.prepare_controller = lambda controller=None: None   # No connection here: nothing reaches a provider.
    made = worker_sessions._new_chat

    def recorded_chat(lead):
        chat = made(lead)
        chat.send = lambda text, observation=None, recovery_evidence=None, chat=chat: sent.setdefault(
            chat.state["id"], []).append(text)
        return chat
    worker_sessions._new_chat = recorded_chat
    lead = ui.get_controller()
    window = bpy.context.window_manager.windows[0]
    executor = BlenderExecutor(window)
    executor.studio_context = lead

    def lead_send(text, observation=None, recovery_evidence=None, host_request=None):   # As Controller.send's row.
        parts = list(host_request or ())
        lead_sends.append({"text": text, "parts": parts, "observed": isinstance(observation, dict)})
        lead.emit("message", {"role": "user", "text": text, "request_kind": "angelo_event", "parts": parts})
        lead.pump_events()
    lead.send = lead_send

    def start(name, approval="ASK"):   # Its worker inherits Ask: its program waits for the approve operator.
        lead.state["options"] = dict(lead.state.get("options") or {}, approval=approval)
        if HOSTED:   # Its worker's chat inherits its connection: a hosted chat's model launches only with a usable one.
            connect(lead)
        arguments = {"parts": [{"name": name, "brief": "A %s, 20 cm, plain grey." % name.lower()}]}
        executor.precondition(START, arguments)
        executor.execute(START, arguments)
        found = {parallel_sessions.names.get(s): s for s in worker_sessions.workers_of(lead.state["id"])}
        check(name + "_started", name in found, found)
        return found[name]

    def build(session_id, name):
        chat = ui._controllers[session_id]
        body = program([primitive("CYLINDER", name + " Body", "body", radius=0.1, depth=0.2, location=[0, 0, 0.1])])
        if HOSTED:   # Its own turn in its worker builds it; the person approves its card; the turn's end is its end.
            built = HostedWorkerChat.of(chat)
            key, answers = built.request("angelo_scene_program", body)
            built.approve(built.wait_for_approval(key, answers))
            result = built.wait(answers)
            built.wait_idle()
            check(name + "_builds_its_part", result.get("status") == "committed", result)
            return
        built = WorkerChat.__new__(WorkerChat)
        built.controller, built.session, built._count = chat, session_id, 0
        built.executor = worker_routing.bound(chat)
        built.worker, built.timings, built.pumps = built.executor.worker, [], []
        built.fence = chat.open_external_read()
        key, answers = built.request("angelo_scene_program", program([
            primitive("CYLINDER", name + " Body", "body", radius=0.1, depth=0.2, location=[0, 0, 0.1])]))
        call = built.wait_for_approval(key, answers)
        with bpy.context.temp_override(window=window):
            bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=session_id)
        result = built.wait(answers)
        chat.close_external_read(built.fence)
        check(name + "_builds_its_part", result.get("status") == "committed", result)
        chat.emit("message", {"role": "user", "text": "Build the %s." % name})   # Its brief's turn ended: done.
        chat.pump_events()

    def steps(until=lambda: False, seconds=60):
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            worker_queue.tick()
            if HOSTED:   # The window's tool pump also reads parts' counts under their workers' leases (A').
                ui_tick()
            if until():
                return True
            time.sleep(0.05)
        return False

    # The real flow: the lead's turn has ended (idle) while its worker builds.
    kettle = start("Kettle")
    lead.state["options"] = dict(lead.state["options"], approval="AUTO_BOUNDED")
    steps(seconds=1)
    check("no_event_while_its_worker_works", lead_sends == [] and worker_resume.pending(lead.state["id"]), lead_sends)
    build(kettle, "Kettle")
    check("once_its_worker_has_ended_angelo_continues_the_lead", steps(lambda: lead_sends), lead_sends)
    steps(seconds=1)
    event = lead_sends[0]
    check("once_and_with_the_part_and_its_state", len(lead_sends) == 1 and event["parts"] == ['1 "Kettle"']
          and '1 "Kettle" (state ' in event["text"] and event["text"].startswith("From Angelo, not the person") and
          event["observed"], lead_sends)
    row = next((m for m in reversed(lead.state["messages"]) if m.get("role") == "user"), {})
    check("its_row_is_angelos", row.get("request_kind") == "angelo_event" and row.get("parts") == ['1 "Kettle"'], row)
    from angelo_agent.controller import ToolDispatchBudget
    from angelo_agent.tool_pump import pump_controller_tools
    bpy.ops.ed.undo_push(message="Before the merge")
    fence, answers = lead.open_external_read(), []   # The event's turn: the lead merges, as Auto applies an edit.
    if HOSTED:
        check("kettle_counted_at_its_save", counted_at_once())
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
    check("the_lead_merges", answers and answers[0].get("success") is True and "Kettle" in bpy.data.collections,
          answers)
    steps(seconds=1)
    check("nothing_fires_again", len(lead_sends) == 1, lead_sends)

    # The person's next request (a start in Angelo's own turn opens no batch): the lead's own turn runs when its
    # worker ends, and the event waits for that turn's end, then fires once.
    lead.emit("message", {"role": "user", "text": "Add a mug."})
    lead.pump_events()
    mug = start("Mug")
    lead.state["options"] = dict(lead.state["options"], approval="AUTO_BOUNDED")
    lead.busy = True
    build(mug, "Mug")
    steps(lambda: mug in worker_queue.parked, 30)
    steps(seconds=1)
    check("never_during_the_leads_turn", len(lead_sends) == 1, lead_sends)
    lead.busy = False
    check("once_that_turn_ends", steps(lambda: len(lead_sends) == 2) and lead_sends[1]["parts"] == ['2 "Mug"'],
          lead_sends)

    # Stop on the lead ends its batch: its worker's end continues nothing.
    lead.emit("message", {"role": "user", "text": "Add a teapot."})
    lead.pump_events()
    teapot = start("Teapot")
    lead.state["options"] = dict(lead.state["options"], approval="AUTO_BOUNDED")
    worker_resume.stop(lead)   # The person's Stop, as ANGELO_OT_stop calls it.
    build(teapot, "Teapot")
    steps(lambda: teapot in worker_queue.parked, 30)
    steps(seconds=1)
    check("stop_ends_the_batch", len(lead_sends) == 2 and not worker_resume.pending(lead.state["id"]), lead_sends)

    # Ask: the event starts the lead's turn with no click, its own merge of the batch's parts runs with no card, and
    # any other edit in that turn still asks (the maintainer, 2026-10-25).
    lead.emit("message", {"role": "user", "text": "Add a saucer."})
    lead.pump_events()
    saucer = start("Saucer")   # In Ask (start's default).
    build(saucer, "Saucer")
    check("ask_starts_the_leads_turn", steps(lambda: len(lead_sends) == 3) and '4 "Saucer"' in lead_sends[2]["parts"],
          lead_sends)   # With every part still not merged into the scene (the Mug and the Teapot too).
    fence, answers = lead.open_external_read(), []
    lead.busy = True   # The event's turn runs.
    if HOSTED:
        check("parts_counted_before_the_ask_merge", counted_at_once())
    lead.request_tool("merge-ask-%d" % time.monotonic_ns(), "angelo_workers_merge", {"parts": [{"worker": 4}]},
                      answers.append)
    edit_answers = []
    lead.request_tool("edit-ask-%d" % time.monotonic_ns(), "angelo_scene_program", program([
        primitive("CUBE", "Stray", "stray", size=0.1)]), edit_answers.append)

    def unseen(target, call):   # The approve operator for an automatic call only: a card would wait for the person.
        with bpy.context.temp_override(window=window):
            bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=target.state["id"])
    deadline = time.perf_counter() + 60
    while not answers and time.perf_counter() < deadline:
        lead.pump_events()
        pump_controller_tools([lead], 0, BlenderExecutor(window), ui.document_key(), unseen,
                              budget=ToolDispatchBudget(seconds=60))
    check("in_ask_its_merge_runs_with_no_card", answers and answers[0].get("success") is True
          and "Saucer" in bpy.data.collections, answers)
    for _ in range(20):   # The edit reaches its card; nothing approves it for the person.
        lead.pump_events()
        pump_controller_tools([lead], 0, BlenderExecutor(window), ui.document_key(), unseen,
                              budget=ToolDispatchBudget(seconds=60))
    check("any_other_edit_in_that_turn_asks", edit_answers == [] and "Stray" not in bpy.data.objects and any(
        call.name == "angelo_scene_program" and call.approval and not call.executing for call in lead.pending.values()),
          {"answers": edit_answers, "pending": [c.name for c in lead.pending.values()]})
    lead.close_external_read(fence)
    lead.busy = False
    if HOSTED:
        merge_during_a_parks_close(lead, start, build, steps, apply, window)
    report["success"] = True


def merge_during_a_parks_close(lead, start, build, steps, apply, window):
    """A merge asked while a park's ``chat.close`` is in flight (each close held 1 s here) waits for that close, then
    merges: never refused because its worker no longer hosts the chat (Mac 2 under load, 3c351c9; R9)."""
    from angelo_agent import worker_chat_closing, worker_chat_lifecycle
    from angelo_agent.controller import ToolDispatchBudget
    from angelo_agent.tool_pump import pump_controller_tools
    lead.emit("message", {"role": "user", "text": "Add a plate."})
    lead.pump_events()
    plate = start("Plate")
    worker_resume.cancel(lead.state["id"])   # This section merges by itself; no event of its batch.
    send = worker_chat_lifecycle._send

    def slow_close(session, op, worker, timeout, **fields):
        if op == "chat.close" and session == plate:
            time.sleep(1.0)   # The park's close is in flight for a second, as under heavy load.
        return send(session, op, worker, timeout, **fields)
    worker_chat_lifecycle._send = slow_close
    try:
        build(plate, "Plate")
        closing = steps(lambda: worker_chat_closing.state(plate) == worker_chat_closing.CLOSING, 30)
        check("the_parks_close_is_in_flight", closing, worker_chat_closing.state(plate))
        lead.state["options"] = dict(lead.state["options"], approval="AUTO_BOUNDED")
        fence, answers = lead.open_external_read(), []
        lead.request_tool("merge-park-%d" % time.monotonic_ns(), "angelo_workers_merge", {"parts": [{"worker": 5}]},
                          answers.append)
        deadline = time.perf_counter() + 60
        while not answers and time.perf_counter() < deadline:
            lead.pump_events()
            pump_controller_tools([lead], 0, BlenderExecutor(window), ui.document_key(), apply,
                                  budget=ToolDispatchBudget(seconds=60))
            time.sleep(0.01)
        lead.close_external_read(fence)
        check("a_merge_during_a_parks_close_merges", answers and answers[0].get("success") is True
              and "Plate" in bpy.data.collections, answers)
        check("its_park_then_ends", steps(lambda: plate not in worker_queue._parking   # Merged: its worker ends.
                                            and worker_chat_closing.state(plate) is None, 30),
              worker_chat_closing.state(plate))
    finally:
        worker_chat_lifecycle._send = send


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    try:
        worker_routing.stop_all()
    except Exception:
        report.setdefault("cleanup_error", traceback.format_exc())
(OUT / "parallel-resume.json").write_text(json.dumps(report, indent=2, default=str))
print("PARALLEL-RESUME-DONE", report.get("success"))
if not report.get("success"):
    sys.stdout.flush()
    os._exit(1)
