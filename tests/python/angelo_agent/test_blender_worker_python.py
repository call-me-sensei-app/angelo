# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Worker Python end to end: a worker chat's ``angelo_execute_python`` runs in its worker under the lead's rules.

This (background) Angelo is the main one. Its chats are bound to real worker Blenders (``Angelo --background``
children), and every script goes through Angelo's own route: the card in Angelo, the person's exact-code review or
a standing consent, Angelo's approve operator, then the worker runs it with its own grouped Undo step and saves its
part. Each case runs on its own and is reported as passed or failed with why (``cases`` in the receipt):

* Ask: the card needs the exact code reviewed (the first Approve opens the review of that card's own chat, though
  another chat is shown), then the script runs in the worker's process; its objects are in the saved part; one
  worker Undo takes the script back whole and Redo brings it back; typed edits still commit; the chat reads the
  script back (``angelo_script``) and reruns it patched. Everyday script shapes do the same;
* Auto asks unless trusted: Decline runs nothing, "Trust scripts for this chat" and "Always allow scripts" run it
  unasked, a script naming preferences or ``angelo_agent`` always asks; worker chats the lead starts inherit its
  "Trust scripts for this chat" when they start and whenever the lead turns it on or off, and an Ask one asks;
* refused with nothing run: a binding that changed while the card waited (stale), a request whose script is not
  the approved one (forged digest), a script that loads, reverts or recovers a file (before its card);
* a script that replaces the file anyway ends as ``DOCUMENT_REPLACED`` and its worker keeps the part as last saved;
  a script that saves as another file keeps the part as the worker's file; a raise is rolled back, or kept and
  saved where Undo cannot restore it; a 3 s script never makes Angelo's main thread wait; other worker traffic
  while a card waits never makes it stale; after a staged program the worker refused, a script's and a typed edit's
  cards still run, and an edit of this Angelo's own document while a worker's card waits never makes it stale (a
  binding is taken from a settled scene: pa-p1's live planter, 2026-10-25); this Angelo's own scene never changes
  but for that edit, which is undone.

Two roles in one file. Started by the runner it is the suite above. Started by the suite as a worker's script
(``-- --worker ...``, ``probed_chat``) it is the shipped worker (``worker_main``) plus three QA requests between
requests: its state, and Undo or Redo as Ctrl+Z would press them in a window; nothing on the edit path changes
(unless a QA switch, ``serve_worker``, is set).

Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``. No provider or paid call.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from unittest.mock import patch

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
ARGUMENTS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-worker-python-qa"))
STATE, PARTS, SCRIPT = OUT / "state", OUT / "parts", "angelo_execute_python"
report = {"checks": {}, "cases": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path,
          "version": bpy.app.version_string, "platform": sys.platform, "timings": {}}
_failed = []
LATE_SAVE = ".qa-late-save"   # Beside a part (``ANGELO_QA_LATE_SAVE=1``): its worker's saves held back (below).
LOAD_BECOMES_PART = ".qa-load-becomes-part"   # (``ANGELO_QA_LOAD_BECOMES_PART=1``) The defect, injected (below).
SWITCHES = {"ANGELO_QA_LATE_SAVE": LATE_SAVE, "ANGELO_QA_LOAD_BECOMES_PART": LOAD_BECOMES_PART}
# A failure bound, never a wait: a due save lands at the worker's next pump, a failed one is tried again each second
# (``worker_chat_part.SAVE_RETRY_SECONDS``); one that has not landed in a minute is a case's failure.
SAVE_SECONDS = 60.0


def serve_worker():
    """The shipped worker, answering three QA requests too: its objects, file, saves and pid; Undo; Redo. Each answer
    says whether a save of the part is still due (``save_pending``: a hosting worker saves once idle,
    ``worker_chat_part``; a lane worker saves inside the edit's reply, so never).

    With a ``LATE_SAVE`` file beside its part, a hosting worker holds its saves back further than the product ever
    does: until the suite asked for its state (then at its next pump), or right before its chat's next message or
    close. A read that does not wait for the save then finds the part's last version every time (Mac 2, 78e17c6e1a1:
    a_hidden_load_is_contained, by load), never by chance; one that waits passes all the same.
    With a ``LOAD_BECOMES_PART`` file, the defect a_hidden_load_is_contained exists for: a script's load (as
    ``worker_script.contain`` finds one) is saved as the part instead of contained, so that case must fail. Both
    switches are QA-only (a direct run, or a runner whose manifest passes them)."""
    from angelo_agent import worker_chat_part, worker_host, worker_main, worker_script
    handle = worker_host.WorkerHost.handle
    part = str(worker_main.parse(ARGUMENTS[1:]).file)
    late = Path(part + LATE_SAVE).is_file()
    if Path(part + LOAD_BECOMES_PART).is_file():
        contain = worker_script.contain

        def load_becomes_part(host, result, before):
            replaced = isinstance(result, dict) and result.get("document_replaced")
            if replaced or worker_script.serial(host.ui) != before:
                host.save()   # The loaded file, saved as the part.
                return dict(result if isinstance(result, dict) else {}), True
            return contain(host, result, before)
        worker_script.contain = load_becomes_part
    save_when_idle, asked = worker_chat_part.save_when_idle, [False]

    def save_when_asked(chats):
        if asked[0]:
            save_when_idle(chats)
            asked[0] = worker_chat_part.unsaved(chats)
    if late:
        worker_chat_part.save_when_idle = save_when_asked

    def handle_with_probes(host, request):
        op, chats = request.get("op"), host.chats
        if late and chats is not None and op in ("chat.send", "chat.close"):
            worker_chat_part.save_now(chats)
        if op not in ("qa_state", "qa_undo", "qa_redo"):
            return handle(host, request)
        outcome, data = None, host.bpy.data
        if op != "qa_state":
            with host.bpy.context.temp_override(window=host.ui.main_window()):
                outcome = sorted(getattr(host.bpy.ops.ed, op[3:])())
        pending = chats is not None and bool(chats.chats) and worker_chat_part.unsaved(chats)   # As save_when_idle.
        asked[0] = asked[0] or pending
        return {"value": {"objects": sorted(o.name for o in data.objects), "outcome": outcome, "pid": os.getpid(),
                          "filepath": data.filepath, "dirty": bool(data.is_dirty), "revision": host.revision,
                          "save_pending": pending}}
    worker_host.WorkerHost.handle = handle_with_probes
    worker_main.main(ARGUMENTS[1:])


# Helpers ------------------------------------------------------------------------------------------------------------

def check(name, condition, detail=None, fatal=True):
    report["checks"][name] = bool(condition)
    if not condition:
        report.setdefault("details", {})[name] = detail
        _failed.append(name)
        if fatal:
            raise AssertionError(name)


def run_case(name, function, *arguments):
    """One case on its own: passed, or failed with its first failed check and why."""
    del _failed[:]
    started, error = time.perf_counter(), None
    try:
        function(*arguments)
    except Exception:   # noqa: BLE001 - a case's failure is recorded; the next case still runs.
        error = traceback.format_exc()[-2400:]
    for chat in arguments:
        settle(chat)
    passed = error is None and not _failed
    report["cases"][name] = {"passed": passed, "failed_checks": list(_failed), "error": error,
                             "seconds": round(time.perf_counter() - started, 3)}
    print("CASE", "PASS" if passed else "FAIL", name, "" if passed else (list(_failed), (error or "")[-300:]),
          flush=True)


def settle(chat):
    """No card or call left behind by a case, and a fresh request for the next one."""
    if HOSTED and isinstance(chat, HostedWorkerChat):
        try:
            for call in list(chat.controller.pending.values()):
                if call.approval and not getattr(call, "executing", False):
                    chat.decline(call)
            chat.wait_idle(60)
        except Exception:   # noqa: BLE001 - an ended chat stays ended; its own case said why.
            pass
        return
    if not isinstance(chat, WorkerChat):
        return
    try:
        for call in list(chat.controller.pending.values()):
            if call.approval and not call.executing:
                chat.controller.decline_tool(call.id)
        deadline = time.monotonic() + 60
        while chat.controller.pending and time.monotonic() < deadline:
            chat.pump()
        chat.new_request()
    except Exception:   # noqa: BLE001 - an ended chat stays ended; its own case said why.
        pass


def probed_chat(name, approval):
    """A chat on a worker that also answers the QA requests (``serve_worker``)."""
    start = worker_routing.start

    def with_probes(controller, part, **options):
        return start(controller, part, **dict(options, script=Path(__file__).resolve()))
    PARTS.mkdir(parents=True, exist_ok=True)
    for switch, suffix in SWITCHES.items():   # Set or cleared each run: a switch never outlives the run that set it.
        marker = Path(str(PARTS / name) + suffix)
        if os.environ.get(switch) == "1":
            marker.write_text("1", encoding="utf-8")
        else:
            marker.unlink(missing_ok=True)
    with patch.object(worker_routing, "start", with_probes):
        chat = worker_chat(STATE, PARTS / name, ROOT, approval=approval)
    chat.part = PARTS / name
    return chat


def adopt(controller):
    """A chat the lead started, driven as ``WorkerChat`` drives its own (hosted: through its mirror)."""
    if HOSTED:
        return HostedWorkerChat.of(controller)
    chat = WorkerChat.__new__(WorkerChat)
    chat.controller, chat.session = controller, controller.state["id"]
    chat.executor, chat.worker = worker_routing.bound(controller), worker_routing._workers[chat.session]
    chat.fence, chat.timings, chat.pumps, chat._count = controller.open_external_read(), [], [], 0
    return chat


def probe(chat, op="qa_state", timeout=SAVE_SECONDS):
    """The worker's answer to ``op``; its state (``qa_state``) once no save of its part is due, so what the state says
    and the part file agree. A hosting worker saves on its main thread once idle (``worker_chat_part``), after the
    answer of the edit reached Angelo: read right after it, the part could still be its last version (Mac 2,
    78e17c6e1a1, a_hidden_load_is_contained: ``disk`` was the empty part before its seed). A lane worker saves inside
    the edit's reply (``WorkerHost.edit``): no save is ever due."""
    deadline = time.monotonic() + timeout
    while True:
        value = chat.executor.ask(op)["value"]
        if op != "qa_state" or not value.get("save_pending"):
            return value
        if time.monotonic() >= deadline:
            raise AssertionError("the worker did not save its part within %d s: %s" % (timeout, value))
        chat.pump()


def part_objects(chat):
    """The objects of ``chat``'s part file, once its worker saved every change it made (``probe``)."""
    probe(chat)
    return file_objects(chat.part)


def file_objects(path):
    with bpy.data.libraries.load(str(path)) as (source, _target):
        return sorted(source.objects)


def same_file(a, b):
    return bool(a) and os.path.realpath(str(a)) == os.path.realpath(str(b))


def main_scene():
    return sorted(obj.name for obj in bpy.data.objects)


def script(chat, code, description="QA script", timeout=120.0):
    """(its card, or None once it was answered without one; the answers list)."""
    key, answers = chat.request(SCRIPT, {"description": description, "code": code})
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        chat.pump()
        if answers:
            return None, answers
        call = chat.card(key) if HOSTED else chat.controller.pending.get(key)
        if call is not None and call.approval and not call.executing and not getattr(call, "automatic", False):
            if HOSTED:
                chat.draw(call)   # The person sees the card.
            return call, answers
    raise AssertionError("%s was neither answered nor shown as a card within %d s" % (key, timeout))


def approve(chat, call, answers=None, review=True):
    """The person's Review (what ``angelo.review_tool`` records), then Approve with Angelo's own operator."""
    if HOSTED:   # Approve's first click opens a script's review, the next one sends (``HostedWorkerChat.approve``).
        if review:
            chat.approve(call)
        else:
            chat.draw(call)
            with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                outcome = bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=chat.session)
            if answers is None:
                return outcome
        return chat.wait(answers, timeout=300) if answers is not None else {'FINISHED'}
    if review:
        chat.controller.review_script(call)
    with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
        outcome = bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=chat.session)
    return chat.wait(answers, timeout=300) if answers is not None else outcome


def run_reviewed(chat, code, description="QA script"):
    call, answers = script(chat, code, description)
    check(description.lower().replace(" ", "_") + "_shows_a_card", call is not None, answers[:1])
    return approve(chat, call, answers)


def seed(chat, name="QA Base"):
    """A typed program first, approved as the chat's mode needs."""
    key, answers = chat.request("angelo_scene_program", program([primitive("CUBE", name, "seed", size=1)]))
    if chat.controller.state["options"]["approval"] == "ASK":
        approve(chat, chat.wait_for_approval(key, answers), review=False)
    result = chat.wait(answers)
    check("seed_%s_committed" % name.replace(" ", "_"), result.get("status") == "committed", result)


def asks(chat, label, code=None):
    """The script shows a card the person answers; Decline runs nothing."""
    marker = "QA Declined " + label[-24:]
    code = code or "import bpy\nbpy.data.objects.new(%r, None)\nresult = 1" % marker
    call, answers = script(chat, code, label)
    check(label, call is not None and not chat.controller.script_authorized(call), answers[:1])
    chat.controller.decline_tool(call.id)
    declined = chat.wait(answers)
    check(label + "_decline_ran_nothing", declined.get("error_code") == "DECLINED" and marker not in chat.objects(),
          declined)


def runs_unasked(chat, label, consent):
    call, answers = script(chat, "import bpy\nresult = {'objects': len(bpy.data.objects)}", label)
    if call is not None:
        chat.controller.decline_tool(call.id)
        chat.wait(answers)
    answer = answers[0] if answers else {}
    check(label, call is None and answer.get("success") is True and answer.get("consent") == consent,
          {"card": call is not None, "answer": {k: answer.get(k) for k in ("success", "consent", "error", "error_code")}})


# Cases --------------------------------------------------------------------------------------------------------------

def python_offered(chat):
    from angelo_agent import external_clients, parallel_access, tool_specs
    offered = parallel_access.for_chat(None, frozenset(tool_specs.TOOL_MAP), chat.controller)
    check("a_worker_runs_python_as_an_edit", SCRIPT in chat.executor.edits, sorted(chat.executor.edits))
    check("a_worker_chat_is_offered_python", SCRIPT in offered, sorted(offered))
    check("a_worker_chat_reads_its_scripts_back", "angelo_script" in offered, sorted(offered))
    check("headless_stays_typed_only", SCRIPT not in external_clients.HEADLESS_TOOLS)


ADD = '''import bpy, os
bpy.ops.mesh.primitive_torus_add(major_radius=1.0, minor_radius=0.25, location=(0, 0, 1))
bpy.context.active_object.name = "QA Torus"
mesh = bpy.data.meshes.new("QA Tri")
mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
scene.collection.objects.link(bpy.data.objects.new("QA Tri", mesh))
result = {"pid": os.getpid(), "background": bpy.app.background}
'''


def commit_undo_save(chat):
    lead_before = main_scene()
    seed(chat)
    before = probe(chat)
    call, answers = script(chat, ADD, "Add a torus and a triangle")
    check("ask_script_shows_a_card", call is not None, answers[:1])
    check("the_card_needs_its_exact_code_reviewed", not chat.controller.script_authorized(call))
    approve(chat, call, review=False)   # The first click, the shown chat being another: it opens this card's review.
    for _ in range(20):
        chat.pump()
    check("the_first_approve_ran_nothing", not answers and not call.executing, answers[:1])
    check("the_first_approve_reviews_this_chats_own_card", chat.controller.script_authorized(call), fatal=False)
    started = time.perf_counter()
    result = approve(chat, call, answers)   # Reviewed (by that click, or here): the second click runs it.
    report["timings"]["script_after_approve_s"] = round(time.perf_counter() - started, 3)
    ran = result.get("result") or {}
    check("the_script_ran_in_the_worker", result.get("success") is True and ran.get("pid") == chat.worker.process.pid
          and ran.get("pid") != os.getpid(), result)
    check("its_consent_is_the_exact_code_review", result.get("consent") == "exact_code_review"
          and result.get("execution_mode") == "trusted_unconfined", result)
    after = probe(chat)
    check("the_worker_has_the_scripts_objects", {"QA Torus", "QA Tri"} <= set(after["objects"]), after)
    check("the_part_was_saved_after_the_script", after["revision"] == before["revision"] + 1 and not after["dirty"],
          {"before": before, "after": after})
    check("the_part_on_disk_has_them", {"QA Torus", "QA Tri"} <= set(part_objects(chat)), part_objects(chat))
    check("this_angelos_scene_is_unchanged", main_scene() == lead_before, main_scene())
    undone = probe(chat, "qa_undo")
    check("one_worker_undo_takes_the_script_back_whole", undone["outcome"] == ["FINISHED"]
          and undone["objects"] == before["objects"], {"before": before, "undo": undone})
    redone = probe(chat, "qa_redo")
    check("redo_brings_it_back", redone["objects"] == after["objects"], redone)
    key, answers = chat.request("angelo_scene_program", program([primitive("CUBE", "QA After", "after", size=0.5)]))
    approve(chat, chat.wait_for_approval(key, answers), review=False)
    typed = chat.wait(answers)
    check("a_typed_program_after_the_script_commits", typed.get("status") == "committed", typed)
    chat.new_request()
    read = chat.call("angelo_script", {"script": result.get("script")})
    check("the_chat_reads_the_script_back", read.get("code") == ADD, read)
    key, answers = chat.request(SCRIPT, {"description": "Rerun patched", "base": result.get("script"),
                                         "edits": [{"find": '"QA Torus"', "replace": '"QA Torus Again"'}]})
    patched = approve(chat, chat.wait_for_approval(key, answers), answers)
    check("a_patched_rerun_runs_in_the_worker", patched.get("success") is True
          and "QA Torus Again" in part_objects(chat), patched)


SHAPES = {
    "bmesh": "import bpy, bmesh\nbm = bmesh.new()\nbmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=0.5, "
             "radius2=0.2, depth=1.0)\nmesh = bpy.data.meshes.new('Vase')\nbm.to_mesh(mesh); bm.free()\n"
             "scene.collection.objects.link(bpy.data.objects.new('Vase', mesh))",
    "operator_modifier_apply": "import bpy\nbpy.ops.mesh.primitive_cube_add(size=1, location=(2, 0, 0))\n"
             "cube = bpy.context.active_object; cube.name = 'Rounded'\ncube.modifiers.new('Sub', 'SUBSURF').levels = 2\n"
             "bpy.ops.object.modifier_apply(modifier='Sub')\nbpy.ops.object.shade_smooth()",
    "edit_mode_bevel": "import bpy\nbpy.ops.mesh.primitive_cube_add(size=1, location=(-2, 0, 0))\n"
             "bpy.context.active_object.name = 'Beveled'\nbpy.ops.object.mode_set(mode='EDIT')\n"
             "bpy.ops.mesh.select_all(action='SELECT')\nbpy.ops.mesh.bevel(offset=0.1, segments=2)\n"
             "bpy.ops.object.mode_set(mode='OBJECT')",
    "material": "import bpy\nmat = bpy.data.materials.new('Brass'); mat.use_nodes = True\n"
             "mat.node_tree.nodes['Principled BSDF'].inputs['Metallic'].default_value = 1.0\n"
             "obj = bpy.data.objects.new('Plate', bpy.data.meshes.new('Plate')); scene.collection.objects.link(obj)\n"
             "obj.data.materials.append(mat)",
    "curve": "import bpy\ncurve = bpy.data.curves.new('Pipe', 'CURVE'); curve.dimensions = '3D'; curve.bevel_depth = 0.05\n"
             "spline = curve.splines.new('POLY'); spline.points.add(3)\n"
             "for i, p in enumerate(spline.points): p.co = (i, (i % 2) * 0.5, 0, 1)\n"
             "scene.collection.objects.link(bpy.data.objects.new('Pipe', curve))",
    "geometry_nodes": "import bpy\ng = bpy.data.node_groups.new('Grow', 'GeometryNodeTree')\n"
             "g.interface.new_socket('Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')\n"
             "g.interface.new_socket('Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')\n"
             "i, o, s = g.nodes.new('NodeGroupInput'), g.nodes.new('NodeGroupOutput'), g.nodes.new('GeometryNodeSubdivideMesh')\n"
             "g.links.new(i.outputs[0], s.inputs[0]); g.links.new(s.outputs[0], o.inputs[0])\n"
             "obj = bpy.data.objects.new('GN Host', bpy.data.meshes.new('GN')); scene.collection.objects.link(obj)\n"
             "obj.modifiers.new('GN', 'NODES').node_group = g",
    "keys_and_parent": "import bpy\nhub = bpy.data.objects.new('Hub', None); scene.collection.objects.link(hub)\n"
             "arm = bpy.data.objects.new('Arm', bpy.data.meshes.new('Arm')); scene.collection.objects.link(arm)\n"
             "hub.location = (0, 0, 3); arm.parent = hub; arm.matrix_parent_inverse = hub.matrix_world.inverted()\n"
             "for frame, z in ((1, 0.0), (24, 1.57)):\n    hub.rotation_euler = (0, 0, z); "
             "hub.keyframe_insert('rotation_euler', frame=frame)",
    "many_linked_copies": "import bpy\nmesh = bpy.data.meshes.new('Stud')\n"
             "mesh.from_pydata([(0, 0, 0), (0.1, 0, 0), (0, 0.1, 0)], [], [(0, 1, 2)])\nfor i in range(200):\n"
             "    o = bpy.data.objects.new('Stud.%03d' % i, mesh); o.location = (i % 20 * 0.2, i // 20 * 0.2, -1)\n"
             "    scene.collection.objects.link(o)",
}


def shapes(chat):
    """Everyday script shapes, unrelated to any brief: each runs, is one Undo step, and is in the saved part."""
    for name, code in SHAPES.items():
        chat.new_request()
        before = probe(chat)
        result = run_reviewed(chat, code + "\nresult = 1", name)
        state, undone, redone = probe(chat), probe(chat, "qa_undo"), probe(chat, "qa_redo")
        check("shape_" + name, result.get("success") is True and undone["objects"] == before["objects"]
              and redone["objects"] == state["objects"] != before["objects"]
              and state["revision"] == before["revision"] + 1 and set(state["objects"]) <= set(part_objects(chat)),
              {"result": {k: result.get(k) for k in ("success", "error")}, "before": len(before["objects"]),
               "after": len(state["objects"]), "undo": len(undone["objects"])})


def auto_rules(chat):
    from angelo_agent import always_allow
    asks(chat, "auto_untrusted_script_asks")
    always_allow.set_enabled(True)
    try:
        runs_unasked(chat, "always_allow_covers_an_auto_worker_chat", "always_allow")
    finally:
        always_allow.set_enabled(False)
    chat.controller.set_trusted_scripts(True)
    runs_unasked(chat, "trusted_worker_chat_runs_unasked", "chat_session")
    asks(chat, "a_script_naming_preferences_still_asks",
         "import bpy\nresult = bpy.context.preferences.view.show_tooltips")
    asks(chat, "a_script_naming_angelo_agent_still_asks", "import sys\nresult = 'angelo_agent' in sys.modules")
    chat.controller.set_trusted_scripts(False)
    asks(chat, "untrusted_again_it_asks_again")


def inherited_trust(lead):
    """Worker chats the lead starts (``angelo_workers_start``) follow its "Trust scripts for this chat"."""
    from angelo_agent import worker_sessions
    from angelo_agent.blender_tools import BlenderExecutor
    from hosted_worker_chat import connect
    connect(lead)   # As a lead that ran a turn has one (the window adopts a connection at its first send); both variants.
    lead.set_options(approval="AUTO_BOUNDED")
    executor = BlenderExecutor(bpy.context.window_manager.windows[0])
    executor.studio_context = lead
    made, chats = worker_sessions._new_chat, []

    def quiet(owner):   # The brief is never sent to a provider.
        chat = made(owner)
        chat.send = lambda *args, **kwargs: None
        return chat

    def start(name):
        before = set(worker_sessions.workers_of(lead.state["id"]))
        started = executor.execute("angelo_workers_start", {"parts": [{"name": name, "brief": "A small part."}]})
        new = set(worker_sessions.workers_of(lead.state["id"])) - before
        check("the_lead_started_" + name, len(new) == 1, started)
        chats.append(adopt(ui._controllers[new.pop()]))
        return chats[-1]
    try:
        with patch.object(worker_sessions, "_new_chat", quiet), patch.object(ui, "prepare_controller", lambda c=None: None):
            early = start("Shelf")
            asks(early, "before_the_lead_trusts_its_worker_asks")
            lead.set_trusted_scripts(True)
            runs_unasked(early, "once_the_lead_trusts_its_running_worker_runs_unasked", "chat_session")
            early.stop()
            born = start("Lamp")
            runs_unasked(born, "a_worker_started_by_a_trusting_lead_runs_unasked", "chat_session")
            asks(born, "an_inherited_trust_never_covers_preferences", "import bpy\nresult = bpy.context.preferences.filepaths.temporary_directory")
            lead.set_options(approval="ASK")
            asking = start("Rug")
            asks(asking, "an_ask_worker_asks_though_its_lead_trusts")
            asking.stop()
            lead.set_trusted_scripts(False)
            asks(born, "once_the_lead_withdraws_trust_its_worker_asks_again")
    finally:
        if lead.trusted_scripts:
            lead.set_trusted_scripts(False)
        for chat in chats:
            try:
                chat.stop()
            except Exception:   # noqa: BLE001 - stopped already.
                pass


def stale(chat):
    """Another chat on the same worker moves the object while the script's card waits: refused, nothing ran."""
    chat.new_request()
    call, answers = script(chat, "import bpy\nbpy.data.objects['QA Base'].location.x = 3\nresult = 1", "Stale")
    check("stale_script_shows_a_card", call is not None, answers[:1])
    chat.controller.review_script(call)
    other = WorkerChat.__new__(WorkerChat)
    other.controller = Controller(str(STATE), session_id=str(uuid.uuid4()))
    other.controller.state["options"] = dict(other.controller.state.get("options") or {}, approval="AUTO_BOUNDED")
    other.session = other.controller.state["id"]
    worker_routing.bind(other.controller, chat.worker, chat.executor.worker_id + "-b")
    other.fence, other.timings, other.pumps, other._count = other.controller.open_external_read(), [], [], 0
    ui._controllers[other.session] = other.controller
    try:
        moved = other.call("angelo_set_transform", {"object": "QA Base", "location": [7, 0, 0]})
        check("the_other_chat_moved_it", moved.get("success") is True, moved)
        result = approve(chat, call, answers, review=False)
    finally:
        other.controller.remote_executor = None   # It leaves the worker without stopping it.
        ui._controllers.pop(other.session, None)
    check("a_stale_script_is_refused", result.get("error_code") == "STALE_REVISION" and result.get("success") is not True,
          result)
    check("a_stale_script_ran_nothing", chat.objects()["QA Base"]["location"][0] == 7.0)


FORGED = "import bpy\nbpy.data.objects.new('QA Forged %s', None)\nresult = 1"


def forged(chat):
    """Only the exact script approved in Angelo runs: requests sent straight to the worker with another script under
    the approved digest, a wrong digest or none are refused there, with nothing run."""
    chat.new_request()
    sent, real = [], chat.executor._ask_bound

    def recorded(op, timeout=None, effects=False, **fields):
        if op == "execute" and fields.get("name") == SCRIPT:
            sent.append(dict(fields))
        return real(op, timeout, effects, **fields)
    chat.executor._ask_bound = recorded
    try:
        call, answers = script(chat, "import bpy\nresult = len(bpy.data.objects)", "Approved")
        check("forged_control_shows_a_card", call is not None, answers[:1])
        digest = call.invocation_digest
        done = approve(chat, call, answers)
    finally:
        del chat.executor._ask_bound
    check("forged_control_ran", done.get("success") is True, done)
    fields = sent[-1] if sent else {}
    carriers = [key for key, value in fields.items() if value == digest]
    check("the_approved_scripts_digest_is_sent_with_it", carriers, sorted(fields))
    disk = part_objects(chat)
    for label, digest_value in (("another_script_under_the_approved_digest", digest), ("a_wrong_digest", "0" * 64),
                                ("no_digest", None)):
        arguments = {"description": "Forged", "code": FORGED % label}
        request = {key: value for key, value in fields.items() if key not in carriers}
        request.update(arguments=arguments, approved=values.encode(chat.executor.precondition(SCRIPT, arguments)))
        request.update({key: digest_value for key in carriers if digest_value is not None})
        try:
            outcome = real("execute", 60, True, **request)["result"]
        except Exception as error:   # noqa: BLE001 - a refusal raised in the worker is a refusal too.
            outcome = {"raised": type(error).__name__, "error": str(error)}
        check("forged_" + label + "_refused", outcome.get("success") is not True and outcome.get("error")
              and outcome.get("scene_changed") is not True, outcome)
    names = chat.objects()
    check("forged_requests_ran_nothing", not [name for name in names if name.startswith("QA Forged")], sorted(names))
    check("forged_requests_left_the_part_as_saved", part_objects(chat) == disk)


def raises(chat):
    chat.new_request()
    before, disk = probe(chat), part_objects(chat)
    code = ("import bpy\nscene.collection.objects.link(bpy.data.objects.new('QA Half', bpy.data.meshes.new('QA Half')))\n"
            "raise RuntimeError('boom')\n")
    result = run_reviewed(chat, code, "Raise after a change")
    after = probe(chat)
    check("a_raise_is_reported", "boom" in str(result.get("error")) and result.get("success") is not True, result)
    check("a_raise_is_rolled_back_in_the_worker", result.get("main_data_restored") is True
          and after["objects"] == before["objects"], {"result": result, "after": after})
    check("a_raise_leaves_the_part_on_disk_as_saved", part_objects(chat) == disk)


LOADS = {"read_homefile": "import bpy\nbpy.ops.wm.read_homefile(use_empty=True)\nresult = 1",
         "open_mainfile": "import bpy\nbpy.ops.wm.open_mainfile(filepath=bpy.data.filepath)\nresult = 1"}


def load_refused(chat):
    """A script that names a file-replacing operator is refused before its card: nothing ran, the part is kept."""
    disk = part_objects(chat)
    for operator, code in LOADS.items():
        chat.new_request()
        call, answers = script(chat, code, "Load " + operator)
        answer = answers[0] if answers else {}
        check("a_script_running_%s_is_refused_before_its_card" % operator, call is None
              and answer.get("error_code") == "UNSUPPORTED" and answer.get("scene_changed") is False
              and operator in str(answer.get("error")), {"card": call is not None, "answer": answer})
    state = probe(chat)
    check("refused_loads_left_the_worker_on_its_part", same_file(state["filepath"], chat.part)
          and part_objects(chat) == disk and set(disk) <= set(state["objects"]), state)


def responsive(chat):
    """A 3 s script: Angelo's main thread pumps on and never waits for it."""
    chat.new_request()
    call, answers = script(chat, "import time\ntime.sleep(3)\nresult = 'slept'", "Sleep three seconds")
    check("sleep_script_shows_a_card", call is not None, answers[:1])
    chat.controller.review_script(call)
    chat.pumps.clear()
    started = time.perf_counter()
    result = approve(chat, call, answers, review=False)
    seconds, waits = time.perf_counter() - started, [s for s, waiting in chat.pumps if waiting]
    report["timings"]["three_second_script"] = {"seconds": round(seconds, 3), "pumps_while_waiting": len(waits),
                                                "max_pump_ms_while_waiting": round(max(waits, default=0) * 1000, 3)}
    check("the_sleep_ran_in_the_worker", result.get("success") is True and seconds >= 3, result)
    check("angelos_main_thread_never_waits_for_a_worker_script", len(waits) >= 100 and max(waits) < 0.025,
          report["timings"]["three_second_script"])


def traffic(chat):
    """The rail's picture, a layout, a turn observation and a scene read while a card waits: never stale."""
    for label, poke in (("thumbnail", lambda: chat.executor.thumbnail(256, "SOLID", True)),
                        ("layout", lambda: chat.executor.ask("layout")),
                        ("turn_observation", lambda: chat.executor.prepare_turn(chat_id=chat.session,
                                                                                message="x").commit()),
                        ("scene_read", lambda: chat.executor.execute("angelo_inspect_scene", {}))):
        chat.new_request()
        call, answers = script(chat, "import bpy\nbpy.data.objects['QA Base'].location.z += 1\nresult = 1", label)
        check(label + "_script_shows_a_card", call is not None, answers[:1])
        chat.controller.review_script(call)
        try:
            poke()
            poked = "ok"
        except Exception as error:   # noqa: BLE001 - a machine that cannot draw refuses the picture; the card stays.
            poked = "%s: %s" % (type(error).__name__, str(error)[:160])
        result = approve(chat, call, answers, review=False)
        check("a_card_survives_" + label, result.get("success") is True, {"poke": poked, "result": result})


def after_a_refused_program(chat):
    """A staged program the worker refused (object_join of objects the program made) leaves the worker's scene to be
    evaluated; a background worker evaluates it only at the next operator, the approve click's own. Every binding is
    taken from a settled scene, so the cards after it run (pa-p1's planter: every one was STALE_REVISION)."""
    seed(chat, "QA Refused Base")

    def refused():
        chat.new_request()
        key, answers = chat.request("angelo_scene_program", program([
            primitive("CUBE", "QA Join A", "a", size=1), primitive("CUBE", "QA Join B", "b", size=1, location=[2, 0, 0]),
            {"kind": "object_join", "target": "$a", "parameters": {"sources": ["$b"]}}]))
        approve(chat, chat.wait_for_approval(key, answers), review=False)
        result = chat.wait(answers)
        check("the_program_is_refused_with_nothing_changed", result.get("error_code") == "VALIDATION"
              and result.get("scene_changed") is False, result)
    refused()
    chat.new_request()
    call, answers = script(chat, "import bpy\nbpy.data.objects['QA Refused Base'].location.y = 2\nresult = 1",
                           "After a refused program")
    result = approve(chat, call, answers)
    check("a_script_after_a_refused_program_runs", result.get("success") is True, result)
    refused()
    chat.new_request()
    key, answers = chat.request("angelo_set_transform", {"object": "QA Refused Base", "location": [0, 4, 0]})
    approve(chat, chat.wait_for_approval(key, answers), review=False)
    result = chat.wait(answers)
    check("a_typed_edit_after_a_refused_program_commits", result.get("success") is True, result)


def the_leads_edit_while_a_card_waits(chat):
    """The lead's own document changes (an edit here, as a merge makes) while a worker's card waits: the card binds
    the worker's document, so it still runs."""
    chat.new_request()
    call, answers = script(chat, "import bpy\nresult = len(bpy.data.objects)", "While the lead edits")
    lead = bpy.data.objects.new("QA Lead Edit", None)
    bpy.context.scene.collection.objects.link(lead)
    bpy.context.view_layer.update()
    try:
        result = approve(chat, call, answers)
    finally:
        bpy.data.objects.remove(lead)
    check("a_workers_card_survives_the_leads_edit", result.get("success") is True, result)


def save_as_and_unrestorable(chat):
    """A script that saves as another file: the part stays the worker's file and has the script's data. A raise
    Undo cannot restore (pixels): kept and saved, as the lead keeps it."""
    seed(chat)
    other = OUT / "elsewhere.blend"
    code = ("import bpy\nscene.collection.objects.link(bpy.data.objects.new('QA Moved', bpy.data.meshes.new('M')))\n"
            "bpy.ops.wm.save_as_mainfile(filepath=%r)\nresult = {'saved_as': bpy.data.filepath}\n" % str(other))
    result = run_reviewed(chat, code, "Save as another file")
    state = probe(chat)
    check("save_as_ran", result.get("success") is True and other.is_file(), result)
    check("after_save_as_the_part_is_still_the_workers_file", same_file(state["filepath"], chat.part), state)
    check("after_save_as_the_part_has_the_scripts_data", "QA Moved" in part_objects(chat), part_objects(chat))
    check("after_save_as_the_chat_keeps_its_document", chat.controller.document_invalid is False)
    seed(chat, "QA Later")
    check("a_later_edit_saves_the_part_never_the_other_file", "QA Later" in part_objects(chat)
          and "QA Later" not in file_objects(other), {"part": part_objects(chat), "other": file_objects(other)})
    chat.new_request()
    code = ("import bpy\nimg = bpy.data.images.new('QA Px', 4, 4)\nimg.pixels[0] = 1.0\n"
            "scene.collection.objects.link(bpy.data.objects.new('QA Kept', bpy.data.meshes.new('K')))\n"
            "raise RuntimeError('after pixels')\n")
    result = run_reviewed(chat, code, "Pixels then raise")
    state = probe(chat)
    check("an_unrestorable_raise_says_so", result.get("main_data_restored") is False, result)
    check("an_unrestorable_raise_is_kept_and_saved", "QA Kept" in state["objects"]
          and "QA Kept" in part_objects(chat), {"live": state["objects"], "disk": part_objects(chat)})
    check("after_it_the_chat_keeps_its_document", chat.controller.document_invalid is False)
    check("a_worker_chat_is_never_told_to_restore_a_snapshot",   # Restore refuses worker chats.
          "restore the pre-request snapshot" not in str(result.get("recovery")), result.get("recovery"), fatal=False)


HIDDEN_LOAD = "import bpy\ngetattr(bpy.ops.wm, 'read_' + 'homefile')(use_empty=True)\nresult = 1\n"


def hidden_load_contained(chat):
    """A load the name check cannot see: DOCUMENT_REPLACED, the worker lives on its part as last saved."""
    seed(chat)
    disk, lead = part_objects(chat), main_scene()
    # The load (an empty file) differs from the part only if the part holds the seed: else the check below passes
    # whatever the load did (Mac 2, 78e17c6e1a1: ``disk`` read before the seed's save was empty).
    check("the_seed_is_in_the_saved_part", "QA Base" in disk, disk)
    result = run_reviewed(chat, HIDDEN_LOAD, "Load through a built name")
    check("a_hidden_load_ends_as_document_replaced", result.get("error_code") == "DOCUMENT_REPLACED", result)
    try:
        state = probe(chat)   # Once no save is due: the part file is what the worker lives on.
    except Exception as error:   # noqa: BLE001 - a worker that died here is the failure this case looks for.
        state = {"raised": str(error)}
    check("the_loaded_file_never_became_the_part", file_objects(chat.part) == disk, file_objects(chat.part))
    check("the_worker_lives_on_its_part_as_last_saved", same_file(state.get("filepath"), chat.part)
          and set(disk) <= set(state.get("objects", ())), state)
    check("this_angelo_is_unharmed", main_scene() == lead, main_scene())


# The suite ----------------------------------------------------------------------------------------------------------

def run_suite():
    PARTS.mkdir(parents=True, exist_ok=True)
    bpy.context.preferences.edit.use_global_undo = True
    _register(bpy, ui)   # The approve operator, as the window has it,
    if not hasattr(bpy.types, "ANGELO_OT_review_tool"):   # and the card's Review, which the first Approve opens.
        bpy.utils.register_class(ui.ANGELO_OT_review_tool)
    before = main_scene()
    run_case("worker_chats_inherit_the_leads_trust", inherited_trust, ui.get_controller())
    ask, auto = probed_chat("ask.blend", "ASK"), worker_chat(STATE, PARTS / "auto.blend", ROOT)
    try:
        for name, case, chat in (("a_worker_chat_is_offered_python", python_offered, ask),
                                 ("ask_commit_undo_save", commit_undo_save, ask),
                                 ("everyday_script_shapes", shapes, ask),
                                 ("auto_asks_unless_trusted", auto_rules, auto),
                                 # Lanes only: two chats bound to one worker, and a lane request to forge; a hosted
                                 # worker hosts one chat, and its Approve carries the drawn card's digest (C6).
                                 *(() if HOSTED else (("stale_binding_refused", stale, ask),
                                                      ("forged_digest_refused", forged, ask))),
                                 ("a_raise_is_rolled_back", raises, ask),
                                 ("file_loads_refused_before_the_card", load_refused, ask),
                                 ("a_3s_script_never_blocks_angelo", responsive, ask),
                                 ("other_traffic_while_a_card_waits", traffic, ask),
                                 ("cards_after_a_refused_program_run", after_a_refused_program, ask),
                                 ("the_leads_edit_never_makes_a_workers_card_stale",
                                  the_leads_edit_while_a_card_waits, ask)):
            run_case(name, case, chat)
    finally:
        ask.stop()
        auto.stop()
    saved_as = probed_chat("save-as.blend", "ASK")
    run_case("save_as_and_unrestorable_raise", save_as_and_unrestorable, saved_as)
    saved_as.stop()
    loader = probed_chat("hidden-load.blend", "ASK")
    run_case("a_hidden_load_is_contained", hidden_load_contained, loader)
    loader.stop()
    run_case("this_angelos_scene_never_changed", lambda: check("main_scene_unchanged", main_scene() == before,
                                                                main_scene()))
    report["passed_cases"] = sorted(name for name, case in report["cases"].items() if case["passed"])
    report["failed_cases"] = sorted(name for name, case in report["cases"].items() if not case["passed"])
    report["success"] = not report["failed_cases"]


if ARGUMENTS[:1] == ["--worker"]:
    serve_worker()
else:
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                      ANGELO_STATE_DIR=str(STATE), ANGELO_ENABLE_PARALLEL_AGENTS="1")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import uuid  # noqa: E402
    from angelo_agent import ui, worker_routing  # noqa: E402
    from angelo_agent import worker_values as values  # noqa: E402
    from angelo_agent.controller import Controller  # noqa: E402
    from angelo_agent.headless_main import _register  # noqa: E402
    from parallel_workers_qa import WorkerChat, primitive, program, worker_chat  # noqa: E402
    from hosted_worker_chat import HostedWorkerChat, hosted, install_stand_in  # noqa: E402
    HOSTED = hosted()   # Design A′: each chat runs in its worker, played by the stand-in model (the "hosted" variant).
    report["variant"] = "hosted" if HOSTED else "lanes"
    if HOSTED:
        install_stand_in(OUT / "stand-in")
    try:
        run_suite()
    except Exception:
        report.update(success=False, error=traceback.format_exc())
    finally:
        try:
            worker_routing.stop_all()
        except Exception:
            report.setdefault("cleanup_error", traceback.format_exc())
    (OUT / "worker-python.json").write_text(json.dumps(report, indent=2, default=str))
    print("WORKER-PYTHON-DONE", report.get("success"), json.dumps(report.get("failed_cases")))
    if not report.get("success"):
        sys.stdout.flush()   # os._exit skips it: the DONE line must reach a piped log too.
        os._exit(1)   # Report written and workers stopped above: a failed run never exits 0.
