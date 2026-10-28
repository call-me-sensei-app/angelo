# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Selecting a session only changes what is displayed (break-it E2E-20 R5-M-1; the maintainer, 2026-10-25: "On the
left, show lead + all subagents. When selected, the selected one show as big view. It should be as simple as this").

A lead keeps its own scene whatever the main window shows: its workers keep building, its queue starts, nothing is
invalidated, a merge never lands in the shown scene, and its tile brings its scene back. Only a real file load ends
its job. Cases (each recorded on its own, ``report["cases"]``):

- S1 a click on each tile, then one UI pump and one queue step: the job is unchanged, the window on the lead's scene.
- S2 the window shows another scene: nothing invalidated, workers bound, a queued part starts; a merge meanwhile adds
  nothing there; the lead's tile brings its scene back and the merge lands in it (its ``PART_KEY`` record too).
- S14 a view layer and a scene deleted while held read as None, and a job's and a look's settings restore with
  their scene gone; S15 after S3's load, an executor bound before it reads no window (break-it SW-H-2, SW-M-1).
- S3 a real load (``wm.read_homefile``) still stops everything, each worker with ``worker_queue.REPLACED``.
- S4 Restore while another scene is shown: each lead is still on its own scene.
- S5 the lead's scene deleted while away, then Undo: the lead is back on it and its merge works.
- S6 two leads on two scenes: each merge lands in its own scene.
- S7 a chat with no workers holding a staged preview and a pending approval: a look at another scene and back;
  Accept and Approve both apply in its scene.
- S8 the two leads' scenes swap names after a snapshot: Restore keeps each lead on its own scene (by its name in the
  snapshot); the lead's scene renamed after that, Undo restore brings each lead back to its own scene again.
- S9 a chat's staged program keeps the writer while its scene is not shown: the shown chat's edit still applies in its
  own scene, and the first program stages again and lands in its scene once that is shown (break-it R4-M-1).
- S10 that held chat's stage is stopped off the main thread (a voice session's end: ``cancel_tools``), so only its
  deferred cleanup keeps the writer: it still retires, and the shown chat's edit applies (break-it R5-M-1).
- S11 a held chat's accepted preview keeps the writer between polls: the shown chat's edit applies in its own scene,
  and the exact accepted stage commits in its scene once that is shown (break-it R5-M-2), with one terminal result
  and one commit record, and Undo takes it back before the other chat's edit. S12 the same once the accepted job's
  own poll has made it READY_TO_COMMIT (its stage suspended by that poll).
- S13 S10 with the stage stopped between its polls, allocated: its private Main or bundle is disposed, no cleanup
  residue remains (break-it R4M1-R2-L-2).

Real workers, headless; worker and lead sends are recorded, never made: no provider or paid call. Run only in a
disposable instance with the exact Angelo binary: ``--background --factory-startup --disable-autoexec
--python-exit-code 1``.
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-parallel-scene-switch-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_ENABLE_PARALLEL_AGENTS="1")
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import (document_scene, parallel_sessions, session_rail_input, ui, worker_queue,  # noqa: E402
                          worker_resume, worker_routing, worker_sessions)
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.headless_main import _register  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402
from parallel_workers_qa import WorkerChat, primitive, program  # noqa: E402
from hosted_worker_chat import HostedWorkerChat, connect, hosted, install_stand_in  # noqa: E402

HOSTED = hosted()   # Design A′: a worker builds its part in its own chat's turn (the "hosted" variant).
if HOSTED:
    install_stand_in(OUT / "stand-in")   # The model that turn runs on: no provider or paid call.

report = {"variant": "hosted" if HOSTED else "lanes", "cases": {}, "checks": {}, "paid_requests": 0,
          "binary_path": bpy.app.binary_path, "platform": sys.platform}
START, MERGE = "angelo_workers_start", "angelo_workers_merge"
HOME = "Dining Room"   # The first lead's own scene (renamed from the default); every case uses other names besides.
sent, lead_sends, case_name = {}, {}, [""]


def check(name, condition, detail=None):
    """A case's check, recorded without stopping the case, so each case reports every check it reaches."""
    key = case_name[0] + ":" + name
    report["checks"][key] = bool(condition)
    if not condition:
        report.setdefault("details", {})[key] = detail
    return bool(condition)


def case(name, function, *args):
    case_name[0] = name
    try:
        function(*args)
    except Exception:
        report.setdefault("errors", {})[name] = traceback.format_exc()
    failed = [key for key, passed in report["checks"].items() if key.startswith(name + ":") and not passed]
    report["cases"][name] = "fail" if failed or name in report.get("errors", {}) else "pass"
    print("SCENE-SWITCH", name, report["cases"][name], failed, flush=True)


def window():
    return ui.main_window()   # Read again after each load: a load replaces the window's data.


def scene(name):
    return bpy.data.scenes.get(name) or bpy.data.scenes.new(name)


def tick(count=1):
    """One UI tick (``ui.pump``: every chat's tools, the real approve and commit callbacks) and one step of the
    window's worker timer (``worker_sessions._tick``: ready workers bind, then one ``worker_queue.tick``)."""
    for _ in range(count):
        ui.pump()
        worker_sessions._tick()


def steps(until, seconds=60):
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        tick()
        if until():
            return True
        time.sleep(0.05)
    return False


def chat(session_id):
    return ui._controllers.get(session_id)


def bound(session_id):
    return chat(session_id) is not None and worker_routing.bound(chat(session_id)) is not None


def refused(lead, name, arguments):
    executor = BlenderExecutor(window())
    executor.studio_context = lead
    try:
        executor.precondition(name, arguments)
    except (RefusalError, ValueError) as error:
        return str(error)
    return None


def start(lead, name):
    """``angelo_workers_start`` for one part; its session id once started, else None (queued)."""
    if HOSTED:   # Its worker's chat inherits its connection: a hosted chat's model launches only with a usable one.
        connect(lead)
    executor = BlenderExecutor(window())
    executor.studio_context = lead
    arguments = {"parts": [{"name": name, "brief": "A %s, 20 cm, plain grey." % name.lower()}]}
    executor.precondition(START, arguments)
    executor.execute(START, arguments)
    return by_name(lead).get(name)


def by_name(lead):
    return {parallel_sessions.names.get(s): s for s in worker_sessions.workers_of(lead.state["id"])}


def build(session_id, name):
    """The worker builds its part (its own program, approved as the person approves it) and its turn ends."""
    worker_chat = chat(session_id)
    if HOSTED:   # Its own turn in its worker builds it; the person approves its card; the turn's end is its end.
        built = HostedWorkerChat.of(worker_chat)
        key, answers = built.request("angelo_scene_program", program([
            primitive("CYLINDER", name + " Body", "body", radius=0.1, depth=0.2, location=[0, 0, 0.1])]))
        built.approve(built.wait_for_approval(key, answers))
        result = built.wait(answers)
        built.wait_idle()
        check(name + "_builds_its_part", result.get("status") == "committed", result)
        check(name + "_is_done_and_parked", steps(lambda: session_id in worker_queue.parked, 60),
              sorted(worker_queue.parked))
        return
    built = WorkerChat.__new__(WorkerChat)
    built.controller, built.session, built._count = worker_chat, session_id, 0
    built.executor = worker_routing.bound(worker_chat)
    built.worker, built.timings, built.pumps = built.executor.worker, [], []
    built.fence = worker_chat.open_external_read()
    key, answers = built.request("angelo_scene_program", program([
        primitive("CYLINDER", name + " Body", "body", radius=0.1, depth=0.2, location=[0, 0, 0.1])]))
    call = built.wait_for_approval(key, answers)
    with bpy.context.temp_override(window=window()):
        bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=session_id)
    result = built.wait(answers)
    worker_chat.close_external_read(built.fence)
    check(name + "_builds_its_part", result.get("status") == "committed", result)
    worker_chat.emit("message", {"role": "user", "text": "Build the %s." % name})   # Its brief's turn ended: done.
    worker_chat.pump_events()
    check(name + "_is_done_and_parked", steps(lambda: session_id in worker_queue.parked, 60),
          sorted(worker_queue.parked))


class Merge:
    """The lead's merge through its approved-change path, applied as Auto applies an edit (``ui.pump``'s approve)."""

    def __init__(self, lead, number):
        self.lead, self.answers, self.options = lead, [], dict(lead.state["options"])
        lead.state["options"] = dict(self.options, approval="AUTO_BOUNDED")
        self.fence = lead.open_external_read()
        lead.request_tool("merge-%d" % time.monotonic_ns(), MERGE, {"parts": [{"worker": number}]},
                          self.answers.append)

    def pump(self, seconds=60):
        deadline = time.perf_counter() + seconds
        while not self.answers and time.perf_counter() < deadline:
            self.lead.pump_events()
            tick()
            time.sleep(0.01)
        return self.answers[0] if self.answers else None

    def close(self):
        self.lead.close_external_read(self.fence)
        self.lead.state["options"] = self.options


def contents(name):
    """What a scene holds: its object names and its record of merged parts."""
    found = bpy.data.scenes.get(name)
    if found is None:
        return None
    return sorted(o.name for o in found.objects), sorted((found.get(parallel_sessions.PART_KEY) or {}).keys())


def holds(scene_name, session_id):
    """Whether the worker's merged part is in that scene: its objects and its ``PART_KEY`` record."""
    number, name, path = worker_sessions._parts[session_id]
    found = bpy.data.scenes.get(scene_name)
    record = (found.get(parallel_sessions.PART_KEY) or {}) if found is not None else {}
    objects = found is not None and any(o.name.startswith(name + " Body") for o in found.objects)
    return objects, parallel_sessions._part_key(path) in record


def job_state(lead):
    sessions = worker_sessions.workers_of(lead.state["id"])
    folders = [Path(worker_sessions._parts[s][2]).parent for s in sessions if s in worker_sessions._parts]
    folders += [i["worker"].part.parent for i in worker_queue.queue if hasattr(i.get("worker"), "part")]
    return {"document": lead.document, "invalid": lead.document_invalid, "scene": window().scene.name_full,
            "queue": [(i.get("number"), i.get("kind")) for i in worker_queue.queue],
            "bound": sorted(s for s in sessions if bound(s)), "parked": sorted(worker_queue.parked),
            "batches": dict(worker_resume._batches), "folders": {str(f): f.is_dir() for f in folders},
            "died": dict(worker_sessions.died)}


def new_lead(scene_name, text):
    """A chat holding work on ``scene_name`` (its document captured while the window showed it)."""
    from angelo_agent.controller import Controller
    shown = ui._sessions.active
    identity = ui._sessions.create(ui.get_controller().state.get("provider") or "codex_subscription")
    ui._sessions.activate(shown)
    lead = Controller(ui.state_directory(), session_id=identity)
    lead.state["options"] = dict(lead.state.get("options") or {}, approval="ASK")   # Its workers' builds ask.
    ui._controllers[identity] = lead
    window().scene = scene(scene_name)
    lead.set_document(ui.document_key())
    lead.state["messages"].append({"role": "user", "text": text})
    record(lead)
    return lead


def record(lead):
    """Its sends are recorded, never made (the worker-done event continues it through ``send``)."""
    lead.send = lambda text, observation=None, recovery_evidence=None, host_request=None, lead=lead: lead_sends.setdefault(
        lead.state["id"], []).append({"text": text[:200], "parts": list(host_request or ())})


def s1(lead, home):
    before = job_state(lead)
    for tile in list(parallel_sessions.sessions()):
        session_rail_input.switch(tile)
        ui.pump()
        worker_queue.tick()
        after = job_state(lead)
        check("tile_%d_changes_nothing" % parallel_sessions.sessions().index(tile), after == before,
              {"tile": tile, "before": before, "after": after})
    session_rail_input.switch(lead.state["id"])
    check("the_window_still_shows_the_leads_scene", window().scene.name_full == home, window().scene.name_full)


def s2(lead, home, away, kettle, mug_name):
    window().scene = scene(away)   # The person's Scene selector: another scene of this file.
    tick(3)
    check("nothing_invalidated", not lead.document_invalid and lead.document[1] == bpy.data.scenes[home].session_uid,
          lead.document)
    check("workers_stay_bound", bound(kettle))
    build(kettle, "Kettle")
    check("a_queued_part_starts", steps(lambda: mug_name in by_name(lead) and bound(by_name(lead)[mug_name]), 60),
          dict(job_state(lead), failures=dict(worker_sessions.failures), names=dict(parallel_sessions.names),
               launching=[(i.get("number"), repr(i.get("error"))) for i in worker_sessions._launching],
               started=dict(worker_sessions._started), parking=sorted(worker_queue._parking)))
    check("not_invalidated_after_its_steps", not lead.document_invalid)
    number = worker_sessions._parts[kettle][0]
    shown_before = contents(away)
    check("a_merge_meanwhile_is_refused_by_its_precondition",
          refused(lead, MERGE, {"parts": [{"worker": number}]}) is not None)
    attempt = Merge(lead, number)
    answer = attempt.pump(5)
    check("a_merge_meanwhile_appends_nothing_to_the_shown_scene", contents(away) == shown_before
          and (answer is None or not answer.get("success")), {"answer": answer, "contents": contents(away)})
    session_rail_input.switch(lead.state["id"])
    check("the_leads_tile_brings_its_scene_back", window().scene.name_full == home, window().scene.name_full)
    if answer is not None:   # Refused: the lead merges again; held (waiting while away): it goes on now.
        attempt.close()
        attempt = Merge(lead, number)
    answer = attempt.pump(60)
    attempt.close()
    check("the_merge_lands_in_the_leads_scene", answer and answer.get("success") and holds(home, kettle) == (True, True),
          {"answer": answer, "home": holds(home, kettle), "away": holds(away, kettle)})
    check("part_key_on_the_leads_scene_not_the_other", holds(away, kettle) == (False, False), contents(away))


def s5(lead, home, away, mug):
    window().scene = scene(home)   # Setup: the lead's scene shown while its part is built.
    if mug not in worker_queue.parked:
        build(mug, "Mug")
    window().scene = scene(away)
    bpy.ops.ed.undo_push(message="Before the lead's scene is deleted")
    bpy.data.scenes.remove(bpy.data.scenes[home])
    bpy.ops.ed.undo_push(message="The lead's scene deleted")
    document = lead.document
    tick(3)
    check("not_invalidated_while_its_scene_is_gone", not lead.document_invalid and lead.document == document
          and document_scene.scene_of(lead) is None, lead.document)
    check("its_parked_worker_stays_parked", mug in worker_queue.parked and mug not in worker_sessions.died,
          dict(worker_sessions.died))
    bpy.ops.ed.undo()
    check("undo_brings_its_scene_back_by_its_session_uid", getattr(document_scene.scene_of(lead), "name_full", None)
          == home, [s.name_full for s in bpy.data.scenes])
    session_rail_input.switch(lead.state["id"])
    check("its_tile_shows_it", window().scene.name_full == home, window().scene.name_full)
    attempt = Merge(lead, worker_sessions._parts[mug][0])
    answer = attempt.pump(60)
    attempt.close()
    check("its_merge_works", answer and answer.get("success") and holds(home, mug) == (True, True)
          and holds(away, mug) == (False, False), {"answer": answer, "home": holds(home, mug)})


def s6(lead, home, other_name):
    os.environ["ANGELO_PARALLEL_WORKER_CAP"] = "4"
    other = new_lead(other_name, "Build a tea service with workers")
    teapot = start(other, "Teapot")   # Started while its scene is shown.
    window().scene = scene(home)
    saucer = start(lead, "Saucer")
    check("both_started", teapot is not None and saucer is not None, {"teapot": teapot, "saucer": saucer})
    build(teapot, "Teapot")
    build(saucer, "Saucer")
    window().scene = scene(other_name)   # The other lead's scene is shown.
    held = Merge(lead, worker_sessions._parts[saucer][0])
    answer = held.pump(5)
    check("a_lead_away_merges_nothing_into_the_shown_scene", holds(other_name, saucer) == (False, False)
          and (answer is None or not answer.get("success")), {"answer": answer, "other": contents(other_name)})
    theirs = Merge(other, worker_sessions._parts[teapot][0])
    answer_other = theirs.pump(60)
    theirs.close()
    check("the_shown_leads_merge_lands_in_its_scene", answer_other and answer_other.get("success")
          and holds(other_name, teapot) == (True, True), {"answer": answer_other})
    session_rail_input.switch(lead.state["id"])
    check("the_leads_tile_brings_its_scene_back", window().scene.name_full == home, window().scene.name_full)
    if answer is not None:
        held.close()
        held = Merge(lead, worker_sessions._parts[saucer][0])
    answer = held.pump(60)
    held.close()
    check("each_merge_lands_in_its_own_scene", answer and answer.get("success") and holds(home, saucer) == (True, True)
          and holds(other_name, saucer) == (False, False) and holds(home, teapot) == (False, False),
          {"answer": answer, "home": contents(home), "other": contents(other_name)})
    report["second_lead"] = other.state["id"]
    return other


def s4(lead, home, away, other, other_name):
    window().scene = scene(away)   # A snapshot taken while another scene is shown: the restored file shows it too.
    point = ui.capture_checkpoint(lead)
    lead.state["messages"].append({"role": "user", "text": "Light the room.", "checkpoint": point["id"],
                                   "checkpoint_scope": "before_request"})
    outcome = bpy.ops.angelo.restore_checkpoint(checkpoint_id=point["id"])
    shown = ui.document_key()
    check("restored", "FINISHED" in outcome, sorted(outcome))
    check("the_window_shows_the_other_scene_still", window().scene.name_full == away, window().scene.name_full)
    check("the_lead_is_still_on_its_scene", not lead.document_invalid and lead.document == (
        shown[0], bpy.data.scenes[home].session_uid), {"lead": lead.document, "shown": shown})
    if other is not None:
        check("the_second_lead_on_its_own", other.document == (shown[0], bpy.data.scenes[other_name].session_uid),
              other.document)
    tick(2)
    check("still_valid_after_ticks", not lead.document_invalid and document_scene.away(lead, ui.document_key()))


def s8(lead, home, other, other_name):
    def swap(first, second):   # The two scenes trade names (a third name first: names are unique).
        first_name, second_name = first.name, second.name
        first.name, second.name, first.name = "Swapping", first_name, second_name

    point = ui.capture_checkpoint(lead)
    lead.state["messages"].append({"role": "user", "text": "Hang the lamps.", "checkpoint": point["id"],
                                   "checkpoint_scope": "before_request"})
    from angelo_agent import checkpoint_scene_map
    kept = (Path(lead.checkpoints.root) / checkpoint_scene_map.FOLDER / lead.checkpoints.session_id
            / (point["id"] + ".json"))
    check("its_scene_map_is_kept_beside_the_checkpoints", kept.is_file() and "scenes" not in lead.checkpoints.load(
        point["id"]) and not set(p.name for p in lead.checkpoints.folder(point["id"]).iterdir()) - {
        "checkpoint.json", "scene.blend", "sculpt.runtime"}, [p.name for p in lead.checkpoints.folder(point["id"]).iterdir()])
    swap(bpy.data.scenes[home], bpy.data.scenes[other_name])   # The lead's scene is now called other_name.
    outcome = bpy.ops.angelo.restore_checkpoint(checkpoint_id=point["id"])
    shown = ui.document_key()
    check("restored", "FINISHED" in outcome, sorted(outcome))
    check("each_lead_on_its_own_scene_by_its_name_in_the_snapshot", not lead.document_invalid and (
        lead.document, other.document) == ((shown[0], bpy.data.scenes[home].session_uid),
                                           (shown[0], bpy.data.scenes[other_name].session_uid)),
          {"lead": lead.document, "other": other.document, "scenes": [(s.name, s.session_uid) for s in bpy.data.scenes]})
    document_scene.scene_of(lead).name = "Pantry"   # Renamed after the Restore.
    safety = next(m["restore_checkpoint"] for m in reversed(ui.get_controller().state["messages"])
                  if m.get("restore_checkpoint"))
    outcome = bpy.ops.angelo.restore_checkpoint(checkpoint_id=safety)   # Undo restore: names as before the Restore.
    shown = ui.document_key()
    check("undo_restored", "FINISHED" in outcome, sorted(outcome))
    check("undo_restore_brings_each_lead_back_to_its_own_scene", not lead.document_invalid and (
        lead.document, other.document) == ((shown[0], bpy.data.scenes[other_name].session_uid),
                                           (shown[0], bpy.data.scenes[home].session_uid)),
          {"lead": lead.document, "other": other.document, "scenes": [(s.name, s.session_uid) for s in bpy.data.scenes]})
    swap(document_scene.scene_of(lead), document_scene.scene_of(other))   # Their names back for the later cases.
    check("names_back", document_scene.scene_of(lead).name_full == home
          and document_scene.scene_of(other).name_full == other_name)


def solo_chat(scene_name, text, approval):
    """A chat with no workers on its own scene (shown while it binds), holding the person's request; with the turn
    binding its requests hold, as an app's or a worker's calls hold one."""
    from angelo_agent.controller import Controller
    shown = ui._sessions.active
    identity = ui._sessions.create(ui.get_controller().state.get("provider") or "codex_subscription")
    ui._sessions.activate(shown)
    solo = Controller(ui.state_directory(), session_id=identity)
    ui._controllers[identity] = solo
    window().scene = scene(scene_name)
    solo.set_document(ui.document_key())
    solo.state["options"] = dict(solo.state.get("options") or {}, approval=approval)
    solo.state["messages"].append({"role": "user", "text": text})
    return solo, identity, solo.open_external_read()


def s7(home, away):
    solo, identity, fence = solo_chat(home, "Stage a cube, then add a cone.", "ASK")

    def asked(name, mode):
        answers = []
        call_id = "%s-%d" % (name, time.monotonic_ns())
        solo.request_tool(call_id, "angelo_scene_program", dict(program([primitive(
            "CUBE" if mode == "PREVIEW" else "CONE", name, "it", location=[0, 0, 0.5])]), mode=mode), answers.append)
        key = solo.state["id"] + "/" + call_id
        steps(lambda: (solo.pending.get(key) is not None and solo.pending[key].approval
                       and not solo.pending[key].executing) or answers, 30)
        report.setdefault("s7_requests", {})[name] = {"pending": key in solo.pending, "answers": answers[:1]}
        return key, answers
    key, answers = asked("Staged Cube", "PREVIEW")
    if key in solo.pending and not answers:   # A stage asks first in Ask.
        with bpy.context.temp_override(window=window()):
            bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=solo.pending[key].id, session_id=identity)
    steps(lambda: answers, 60)
    previews = solo.native_previews()
    if not check("a_staged_preview", answers and answers[0].get("status") == "preview" and previews, answers):
        return
    approval_key, approval_answers = asked("Approved Cone", "APPLY")
    check("a_pending_approval", approval_key in solo.pending and not approval_answers)
    window().scene = scene(away)   # A look at another scene.
    tick(3)
    check("the_look_keeps_both", solo.native_previews() and approval_key in solo.pending
          and not solo.document_invalid, {"previews": solo.native_previews(), "invalid": solo.document_invalid})
    executor = BlenderExecutor(window())
    executor.studio_context = solo
    descriptor = previews[-1]
    try:
        solo.accept_native_preview(descriptor["preview_id"], executor, ui.document_key(), descriptor["preview_token"])
        accepted_away = True
    except ValueError:
        accepted_away = False
    tick(3)
    check("accept_while_away_applies_nothing_there", not accepted_away and "Staged Cube" not in [
        o.name for o in bpy.data.scenes[away].objects])
    window().scene = bpy.data.scenes[home]   # Back.
    tick(2)
    bpy.ops.ed.undo_push(message="Before the approval")   # A background Blender starts its Undo history on request.
    with bpy.context.temp_override(window=window()):
        bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=solo.pending[approval_key].id, session_id=identity)
    steps(lambda: approval_answers, 60)
    check("approve_applies_in_its_scene", approval_answers and "Approved Cone" in [
        o.name for o in bpy.data.scenes[home].objects] and "Approved Cone" not in [
        o.name for o in bpy.data.scenes[away].objects], approval_answers)
    solo.close_external_read(fence)
    executor = BlenderExecutor(window())
    executor.studio_context = solo
    descriptor = next(p for p in solo.native_previews() if p["preview_id"] == descriptor["preview_id"])
    bpy.ops.ed.undo_push(message="Before the accept")
    solo.accept_native_preview(descriptor["preview_id"], executor, ui.document_key(), descriptor["preview_token"])
    steps(lambda: not solo.busy and "Staged Cube" in bpy.data.objects, 60)
    check("accept_applies_in_its_scene", "Staged Cube" in [o.name for o in bpy.data.scenes[home].objects]
          and "Staged Cube" not in [o.name for o in bpy.data.scenes[away].objects],
          [m.get("text", "")[:120] for m in solo.state["messages"][-3:]])



def s9(home, away):
    def ask(chat, name):
        answers, call_id = [], "%s-%d" % (name, time.monotonic_ns())
        chat.request_tool(call_id, "angelo_scene_program", program([primitive("CUBE", name, "it")]), answers.append)
        return chat.state["id"] + "/" + call_id, answers

    def where(name):
        return sorted(s.name for s in bpy.data.scenes if name in s.objects)
    first, _, first_fence = solo_chat(home, "Build a wall.", "AUTO_BOUNDED")
    second, _, second_fence = solo_chat(away, "Add a stool.", "AUTO_BOUNDED")
    window().scene = scene(home)
    key, answers = ask(first, "Wall")
    staged = None
    for _ in range(200):   # One tick at a time: the tick that stages it, before it commits.
        tick()
        call = first.pending.get(key)
        staged = first.native_jobs.get(call.id) if call is not None else None
        if staged is not None or answers:
            break
    from angelo_agent.program_jobs import WRITER
    if not check("a_program_staged_and_keeping_the_writer", staged is not None and WRITER.owner is staged, answers):
        return
    window().scene = scene(away)   # The person looks at the second chat's scene: the first is held, mid-stage.
    second_key, second_answers = ask(second, "Stool")
    steps(lambda: second_answers, 30)
    check("the_shown_chats_edit_applies_in_its_scene", second_answers and second_answers[0].get("status") == "committed"
          and where("Stool") == [away], {"answers": second_answers[:1], "in": where("Stool")})
    check("the_held_chats_stage_was_given_up_unapplied", staged.phase == "CANCELLED" and key in first.pending
          and not answers and where("Wall") == [], {"phase": staged.phase, "in": where("Wall"), "answers": answers})
    window().scene = scene(home)   # Its scene shown again.
    steps(lambda: answers, 30)
    check("its_program_stages_again_and_lands_in_its_scene", answers and answers[0].get("status") == "committed"
          and where("Wall") == [home], {"answers": answers[:1], "in": where("Wall")})
    first.close_external_read(first_fence)
    second.close_external_read(second_fence)

def where(name):
    return sorted(s.name for s in bpy.data.scenes if name in s.objects)


def ask(chat, name, mode="APPLY"):
    answers, call_id = [], "%s-%d" % (name, time.monotonic_ns())
    chat.request_tool(call_id, "angelo_scene_program", dict(program([primitive("CUBE", name, "it")]), mode=mode),
                      answers.append)
    return chat.state["id"] + "/" + call_id, answers


def one_unit_polls():
    """Each poll of a staged program does one unit and each action quantum one operation (a poll always completes
    one: ``program_jobs``), so a case can stop between polls with the stage allocated; restores the budgets."""
    from contextlib import contextmanager
    from angelo_agent import program_jobs

    @contextmanager
    def scope():
        names = ("MAX_FUSED_POLL_SECONDS", "MAX_AMORTIZED_POLL_SECONDS", "MAX_QUANTUM_ACTION_SECONDS")
        saved = {name: getattr(program_jobs, name) for name in names}
        try:
            for name in names:
                setattr(program_jobs, name, 0.)
            yield
        finally:
            for name, value in saved.items():
                setattr(program_jobs, name, value)
    return scope()


def disposed(stage):
    """Whether a suspended stage's private resources are gone: its bundle file, or its private native Main (a
    discarded one is consumed: ``_angelo_stage.attach`` refuses it, and attaches nothing)."""
    if getattr(stage, "native_owner", None) is not None:
        from _bpy import _angelo_stage
        try:
            _angelo_stage.attach(stage.native_owner)
        except RuntimeError as error:
            return "consumed" in str(error)
        _angelo_stage.discard(stage.native_owner)   # Not disposed: the case fails; leave nothing behind.
        return False
    bundle = getattr(stage, "bundle", "")
    return bool(bundle) and not Path(bundle).exists()


def s10(home, away, allocated=False, names=("Ledge", "Bench")):
    """``allocated``: the stage stopped between its polls, allocated (S13: a private native Main or a bundle), rather
    than registered before its first poll (S10: its stopped cleanup keeps only the writer)."""
    import threading
    from angelo_agent import program_storage
    from angelo_agent.program_jobs import WRITER
    first, _, first_fence = solo_chat(home, "Build a wall.", "AUTO_BOUNDED")
    second, _, second_fence = solo_chat(away, "Add a stool.", "AUTO_BOUNDED")
    window().scene = scene(home)
    scenes = sorted(s.name for s in bpy.data.scenes)
    staged = None
    with one_unit_polls():
        answers, key = [], "%s-%d" % (names[0], time.monotonic_ns())
        parts = [primitive("CUBE", names[0] + str(i), "it%d" % i) for i in range(4 if allocated else 1)]
        first.request_tool(key, "angelo_scene_program", program(parts), answers.append)
        key = first.state["id"] + "/" + key
        for _ in range(200):   # One tick at a time: registered (S10), or polled with its stage allocated (S13).
            tick()
            call = first.pending.get(key)
            staged = first.native_jobs.get(call.id) if call is not None else None
            if answers or staged is not None and (not allocated or staged.stage is not None):
                break
    saved = getattr(staged, "stage", None)
    report.setdefault("s10_stage", {})[str(allocated)] = {
        "phase": getattr(staged, "phase", None), "native": getattr(saved, "native_owner", None) is not None,
        "bundle": bool(getattr(saved, "bundle", ""))}
    if not check("a_program_staged_and_keeping_the_writer", staged is not None and WRITER.owner is staged
                 and not staged.closed and not staged.ready_to_commit and (saved is not None) == allocated,
                 {"answers": answers[:1], "stage": report["s10_stage"][str(allocated)]}):
        return
    window().scene = scene(away)
    tick()   # The first chat is held.
    stopper = threading.Thread(target=lambda: first.cancel_tools("Voice ended; no queued change was applied"))
    stopper.start()   # A voice session's end, on its own thread: the stage can only be queued for cleanup.
    stopper.join()
    check("only_its_deferred_cleanup_keeps_the_writer", first._native_cleanup and not first.native_jobs
          and WRITER.owner is staged, {"cleanup": len(first._native_cleanup), "owner": repr(WRITER.owner)})
    _, second_answers = ask(second, names[1])
    steps(lambda: second_answers, 30)
    check("the_shown_chats_edit_applies_in_its_scene", second_answers and second_answers[0].get("status") == "committed"
          and where(names[1]) == [away], {"answers": second_answers[:1], "owner": repr(WRITER.owner),
                                          "cleanup": len(first._native_cleanup), "held": first._held})
    result = staged.result or {}
    check("the_stopped_stage_retired_unapplied", staged.phase == "CANCELLED" and staged.stage is None
          and not first._native_cleanup and not any(where(names[0] + str(i)) for i in range(4))
          and not result.get("cleanup_error") and not result.get("cleanup_pending"),
          {"phase": staged.phase, "result": {k: result.get(k) for k in ("status", "cleanup_error", "cleanup_pending")}})
    if allocated:
        check("its_private_stage_is_disposed", disposed(saved) and not program_storage.has_pending_cleanup()
              and sorted(s.name for s in bpy.data.scenes) == scenes,
              {"stage": report["s10_stage"][str(allocated)], "pending": program_storage.has_pending_cleanup(),
               "scenes": sorted(s.name for s in bpy.data.scenes)})
    window().scene = scene(home)
    first.close_external_read(first_fence)
    second.close_external_read(second_fence)


def s11(home, away, ready=False, crate="Crate", stool="Footstool"):
    """``ready``: the look away once the accepted job's own poll has made it READY_TO_COMMIT (S12), not just after it
    started, its stage the preview's own (S11). Then: one terminal result and one commit journal record for the
    accept, and Undo takes the accepted edit back first, the other chat's edit second (break-it R4M1-R2-L-2)."""
    from angelo_agent.program_jobs import WRITER
    first, identity, first_fence = solo_chat(home, "Stage a crate.", "AUTO_BOUNDED")
    second, _, second_fence = solo_chat(away, "Add a stool.", "AUTO_BOUNDED")
    window().scene = scene(home)
    key, answers = ask(first, crate, "PREVIEW")
    steps(lambda: answers or (first.pending.get(key) is not None and first.pending[key].approval
                              and not first.pending[key].executing), 30)
    if key in first.pending and not answers:   # A stage that asks first.
        with bpy.context.temp_override(window=window()):
            bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=first.pending[key].id, session_id=identity)
    steps(lambda: answers, 60)
    previews = first.native_previews()
    if not check("a_retained_preview", answers and answers[0].get("status") == "preview" and previews, answers[:1]):
        return
    first.close_external_read(first_fence)
    journal, finished = [], []
    record_journal, record_finish = first._journal_tool, first.finish_tool
    first._journal_tool = lambda call, phase: (journal.append((call.id, phase)), record_journal(call, phase))[1]
    first.finish_tool = lambda call, result, **options: (finished.append((call.id, (result or {}).get("status"))),
                                                         record_finish(call, result, **options))[1]
    scenes = sorted(s.name for s in bpy.data.scenes)
    executor = BlenderExecutor(window())
    executor.studio_context = first
    bpy.ops.ed.undo_push(message="Before the accept")
    first.accept_native_preview(previews[-1]["preview_id"], executor, ui.document_key(), previews[-1]["preview_token"])
    accepting = None
    for _ in range(200):   # The tick that starts the accepted job (S11), or its poll that makes it ready (S12).
        tick()
        accepting = next(iter(first.native_jobs.values()), None)
        if not first.busy or accepting is not None and (not ready or accepting.ready_to_commit or accepting.closed):
            break
    stage = getattr(accepting, "stage", None)
    report.setdefault("s11_phase", {})[str(ready)] = {"phase": getattr(accepting, "phase", None),
        "native": getattr(stage, "native_owner", None) is not None, "bundle": bool(getattr(stage, "bundle", ""))}
    if not check("the_accepted_preview_keeps_the_writer", accepting is not None and WRITER.owner is accepting
                 and getattr(accepting, "accepting_preview", False) and not accepting.closed
                 and accepting.phase == ("READY_TO_COMMIT" if ready else "REVALIDATE")
                 and accepting.ready_to_commit == ready, report["s11_phase"][str(ready)]):
        return
    window().scene = scene(away)   # The person looks at the second chat's scene between the accepted job's polls.
    _, second_answers = ask(second, stool)
    steps(lambda: second_answers, 30)
    check("the_shown_chats_edit_applies_in_its_scene", second_answers and second_answers[0].get("status") == "committed"
          and where(stool) == [away], {"answers": second_answers[:1], "owner": repr(WRITER.owner),
                                       "phase": accepting.phase})
    check("the_accepted_stage_waits_unapplied", not accepting.closed and where(crate) == [],
          {"phase": accepting.phase, "in": where(crate)})
    window().scene = scene(home)   # Its scene shown again: the same stage, checked again, commits there.
    steps(lambda: not first.busy, 30)
    check("the_exact_accepted_stage_lands_in_its_scene", accepting.result and accepting.result.get("status")
          == "committed" and accepting.preview_id == previews[-1]["preview_id"] and where(crate) == [home],
          {"result": (accepting.result or {}).get("status"), "in": where(crate)})
    accept_id = accepting.invocation_id
    check("one_terminal_result_and_one_commit_record", finished.count((accept_id, "committed")) == 1
          and [f for f in finished if f[0] == accept_id] == [(accept_id, "committed")]
          and [p for c, p in journal if c == accept_id].count("commit_started") == 1,
          {"finished": [f for f in finished if f[0] == accept_id], "journal": [p for c, p in journal if c == accept_id]})
    first._journal_tool, first.finish_tool = record_journal, record_finish
    with bpy.context.temp_override(window=window()):
        bpy.ops.ed.undo()
        after_one = (where(crate), where(stool))
        bpy.ops.ed.undo()
        after_two = (where(crate), where(stool))
        bpy.ops.ed.redo()
        bpy.ops.ed.redo()
    check("undo_takes_the_accepted_edit_back_first_then_the_other_chats", after_one == ([], [away])
          and after_two == ([], []) and (where(crate), where(stool)) == ([home], [away])
          and sorted(s.name for s in bpy.data.scenes) == scenes,
          {"after_one": after_one, "after_two": after_two, "redone": (where(crate), where(stool)),
           "scenes": sorted(s.name for s in bpy.data.scenes)})
    second.close_external_read(second_fence)


def s14(held):
    """Held objects freed for real (``live_target``; break-it SW-M-1, SW-M-2): a view layer and a scene deleted while
    held read as None, and a job's settings (a look's too) restore with its scene gone, without an error. Then an
    executor is bound to the window for S15, after S3's load."""
    from angelo_agent import live_target
    from angelo_agent.job_settings import Overrides
    from angelo_agent.view_render_restore import LookSettings
    doomed = scene("Doomed")
    layer = doomed.view_layers.new("Doomed layer")
    holder = type("Holder", (), {"scene": live_target.SceneAttribute(), "layer": live_target.ViewLayerAttribute()})()
    holder.scene, holder.layer = doomed, layer
    overrides, look = Overrides(doomed, "held-job"), LookSettings(doomed, "held-look")
    overrides.apply({"render.resolution_x": 640})
    look.apply({"render.resolution_y": 480})
    check("held_objects_read_live_while_they_exist", holder.scene == doomed and holder.layer == layer)
    doomed.view_layers.remove(layer)
    check("a_deleted_view_layer_reads_as_none", holder.layer is None and holder.scene is not None)
    bpy.data.scenes.remove(doomed)
    check("a_deleted_scene_reads_as_none", holder.scene is None)
    outcomes = []
    for settings in (overrides, look):
        try:
            outcomes.append(settings.restore())
        except Exception as error:   # noqa: BLE001 - the case records it
            outcomes.append(type(error).__name__)
    check("a_job_and_a_look_restore_with_their_scene_deleted", outcomes == [[], []], outcomes)
    from angelo_agent.blender_tools import BlenderExecutor
    held.update(executor=BlenderExecutor(window()), pointer=window().as_pointer())


def s15(held):
    """After S3's load: the executor bound before it never reads the window it held, nor the window that may now sit at
    the same address (a load is a new document: break-it SW-H-2)."""
    executor, now = held.get("executor"), window()
    report["s15_address_reused"] = now is not None and now.as_pointer() == held.get("pointer")
    check("a_binding_from_before_a_load_reads_as_none", executor is not None and executor.window is None
          and document_scene.shown_scene(executor) is None, report["s15_address_reused"])


def s3(lead):
    os.environ["ANGELO_PARALLEL_WORKER_CAP"] = "1"
    window().scene = bpy.data.scenes[HOME]
    start(lead, "Lid")
    start(lead, "Spoon")
    steps(lambda: any(bound(s) for s in by_name(lead).values() if s not in worker_queue.parked), 60)
    running = [s for s in worker_sessions.workers_of(lead.state["id"]) if bound(s)]
    queued = [i for i in worker_queue.queue if i.get("lead") is lead]
    folders = [i["worker"].part.parent for i in queued]
    bpy.ops.wm.read_homefile(use_empty=True)
    check("a_load_invalidates_the_lead", lead.document_invalid)
    check("it_stops_every_running_worker_with_replaced", running and all(
        not bound(s) and worker_sessions.died.get(s) == worker_queue.REPLACED for s in running),
          {"running": running, "died": {s: worker_sessions.died.get(s) for s in running}})
    check("its_queued_parts_are_dropped", queued and not any(i.get("lead") is lead for i in worker_queue.queue)
          and not any(f.is_dir() for f in folders), {"queued": len(queued)})


def run_checks():
    # These cases are a person's window. This process is headless, and a background Angelo is unattended (another
    # scene invalidates a chat there, as in 1.0.5: ``document_scene.unattended``), so it stands for an attended one.
    document_scene.unattended = lambda: False
    bpy.context.preferences.edit.use_global_undo = True   # A merge is one Undo step; S5 undoes a deletion.
    import gpu
    gpu.init()   # S7's staged preview draws its pictures offscreen, as a window's Angelo does.
    _register(bpy, ui)
    bpy.utils.register_class(ui.ANGELO_OT_restore_checkpoint)   # S4's Restore, as the add-on registers it.
    session_rail_input.register()
    ui.prepare_controller = lambda controller=None: None   # No connection here: nothing reaches a provider.
    made = worker_sessions._new_chat

    def recorded_chat(lead):
        worker_chat = made(lead)
        worker_chat.send = lambda text, observation=None, recovery_evidence=None, c=worker_chat: sent.setdefault(
            c.state["id"], []).append(text[:200])
        return worker_chat
    worker_sessions._new_chat = recorded_chat
    home = HOME
    bpy.context.window_manager.windows[0].scene.name = home   # The lead's own scene (not the default name).
    lead = ui.get_controller()
    record(lead)
    lead.state["options"] = dict(lead.state.get("options") or {}, approval="ASK")   # Its workers' builds ask.
    lead.state["messages"].append({"role": "user", "text": "Build a dining set with workers"})   # Its work so far.
    lead.set_document(ui.document_key())
    os.environ["ANGELO_PARALLEL_WORKER_CAP"] = "1"
    kettle = start(lead, "Kettle")
    start(lead, "Mug")   # Queued: one place in this run.
    report["setup"] = {"kettle": kettle, "queued": len(worker_queue.queue), "sessions": parallel_sessions.sessions()}
    assert kettle is not None and len(worker_queue.queue) == 1, report["setup"]
    case("S1", s1, lead, home)
    case("S2", s2, lead, home, "Kitchen", kettle, "Mug")
    case("S5", s5, lead, home, "Library", by_name(lead).get("Mug"))
    other = []
    case("S6", lambda: other.append(s6(lead, home, "Workshop")))
    case("S4", s4, lead, home, "Garden", other[0] if other else None, "Workshop")
    case("S8", s8, lead, home, other[0] if other else None, "Workshop")
    case("S7", s7, "Studio", "Kitchen")
    case("S9", s9, "Atelier", "Loft")
    case("S10", s10, "Porch", "Attic")
    case("S13", s10, "Gallery", "Studio Loft", True, ("Plinth", "Easel"))
    case("S11", s11, "Cellar", "Garage")
    case("S12", s11, "Pantry", "Shed", True, "Chest", "Ottoman")
    held = {}
    case("S14", s14, held)
    case("S3", s3, lead)
    case("S15", s15, held)
    report["lead_sends"], report["worker_sends"] = lead_sends, {k: len(v) for k, v in sent.items()}
    report["success"] = all(result == "pass" for result in report["cases"].values())


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    os.environ.pop("ANGELO_PARALLEL_WORKER_CAP", None)
    try:
        worker_routing.stop_all()
    except Exception:
        report.setdefault("cleanup_error", traceback.format_exc())
(OUT / "parallel-scene-switch.json").write_text(json.dumps(report, indent=2, default=str))
print("PARALLEL-SCENE-SWITCH-DONE", report.get("success"), json.dumps(report["cases"]))
if not report.get("success"):
    sys.stdout.flush()   # os._exit skips it: the DONE line must reach a piped log too.
    os._exit(1)   # Report written and workers stopped above: a failed run never exits 0.
