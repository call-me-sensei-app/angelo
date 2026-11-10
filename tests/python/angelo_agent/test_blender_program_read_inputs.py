# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Data another object holds in a mode, through real staged programs (``program_admission.admit_input`` and
verify's ``assert_still_admitted``; doc/angelo/agent_execution.rst, "Input admission").

Linked duplicates A and B share one mesh. With B in Edit Mode, or in a Sculpt session with dynamic topology (whose
strokes live in B's BMesh until B leaves the mode, a save or an Undo push), a program reading or writing A is refused
at bind, naming B. When B enters either mode after the program was staged, the fresh precondition a commit runs
first and the commit itself give the same refusal (UNSUPPORTED, held_by B), and A is unchanged.

Held out (not the cases the fix was developed on): shared Lattice data in Edit Mode and a shared mesh in Weight Paint
are refused the same way; two Object Mode users of one mesh, and a rig whose armature another rig is posing, commit.
The holder scan is timed on 10,000 linked duplicates.

Run only in a disposable instance with the exact Angelo binary: ``--background --factory-startup
--disable-autoexec --python-exit-code 1``. No provider or paid call.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-read-inputs-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import commit_operator, no_stages  # noqa: E402

report = {"checks": {}, "details": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path,
          "version": bpy.app.version_string}
window = bpy.context.window_manager.windows[0]


def check(name, condition, detail=None):
    report["checks"][name] = bool(condition)
    if detail is not None:
        report["details"][name] = detail


def handle(obj):
    return queries.object_handle(obj)


def binding(obj):
    return {"handle": handle(obj), "revision": queries.object_revision(window, obj)}


def program(writes, reads=()):
    target = writes[0]
    return validate_call("angelo_scene_program", {
        "mode": "APPLY", "read_set": [binding(obj) for obj in reads], "write_set": [binding(obj) for obj in writes],
        "operations": [{"kind": "transform", "target": handle(target), "parameters": {"location": [1, 2, 3]}}]})


def settle(job):
    for _ in range(400):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("cooperative native program did not settle")


LAST = {}


def begin(writes, reads=()):
    """(job, settled result) when it stages, or (None, refusal text) when bind refuses (at begin or its first poll)."""
    queries.invalidate()
    try:
        LAST["arguments"] = program(writes, reads)
        job = native.begin(window, "angelo_scene_program", LAST["arguments"])
    except Exception as error:   # A refusal at begin: its text is the evidence.
        return None, str(error)
    result = settle(job)
    if result.get("ready_to_commit"):
        return job, result
    return None, result.get("error") or json.dumps(result, default=str)[:600]


def commit(job):
    bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    return job.result


def mode(obj, value):
    for other in window.view_layer.objects:
        if other.mode != "OBJECT" and other is not obj:
            window.view_layer.objects.active = other
            bpy.ops.object.mode_set(mode="OBJECT")
    window.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode=value)


def shared_pair():
    mesh = bpy.data.meshes.new("QA Shared")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0), (0.3, 0.2, 0.8)], [], [(0, 1, 2), (0, 1, 3), (1, 2, 3)])
    a, b = bpy.data.objects.new("QA A", mesh), bpy.data.objects.new("QA B", mesh)
    for obj in (a, b):
        window.scene.collection.objects.link(obj)
    b.location.x = 3
    window.view_layer.update()
    return a, b


def enter_dyntopo(obj):
    mode(obj, "SCULPT")
    if not obj.data.is_editmode and not getattr(bpy.context.sculpt_object, "use_dynamic_topology_sculpting", False):
        bpy.ops.sculpt.dynamic_topology_toggle()
    return bpy.context.sculpt_object is not None and bpy.context.sculpt_object.use_dynamic_topology_sculpting


def held_at_bind(a, b, enter, label):
    """A program reading or writing A is refused at bind while B holds the shared mesh, naming B."""
    held = enter(b)
    check(label + "_entered", held is not False, b.mode)
    for name, writes, reads in (("write", [a], []), ("read", [bpy.data.objects["QA Reader"]], [a])):
        try:
            job, refused = begin(writes, reads)
        except Exception as error:   # binding() could not read A's revision: also a refusal, recorded as such.
            job, refused = None, "revision: " + str(error)
        text = refused if isinstance(refused, str) else json.dumps(refused, default=str)
        check("%s_%s_refused_naming_b" % (label, name), job is None and "'QA B'" in text, text)
        native.cleanup_all()
    mode(b, "OBJECT")


def entered_after_bind(a, b, enter, label):
    """B enters the mode after A's program was staged: the commit is refused as stale, naming B."""
    before = [tuple(vertex.co) for vertex in a.data.vertices]
    job, staged = begin([a])
    ready = job is not None and isinstance(staged, dict) and staged.get("ready_to_commit")
    check(label + "_staged_before", ready, staged if job is None else staged.get("error"))
    if not ready:
        return
    enter(b)
    try:   # What the chat controller runs first before a commit: a fresh precondition (bind).
        native.precondition(window, "angelo_scene_program", LAST["arguments"])
        fresh = None
    except Exception as error:
        fresh = error
    check(label + "_fresh_precondition_same_refusal", fresh is not None and "'QA B'" in str(fresh)
          and getattr(fresh, "code", None) == "UNSUPPORTED" and getattr(fresh, "details", {}).get("held_by") == "QA B",
          [type(fresh).__name__, str(fresh), getattr(fresh, "details", None)])
    result = commit(job)
    error = result.get("error") or ""
    check(label + "_commit_refused_naming_b", result.get("status") != "committed" and "'QA B'" in error
          and "nothing was applied" in error and result.get("error_code") == "UNSUPPORTED",
          [result.get("status"), result.get("error_code"), error])
    mode(b, "OBJECT")
    check(label + "_a_unchanged", tuple(a.location) != (1, 2, 3)
          and [tuple(vertex.co) for vertex in a.data.vertices] == before)


def edit(obj):
    mode(obj, "EDIT")
    return obj.data.is_editmode


def held_modes():
    a, b = shared_pair()
    reader = bpy.data.objects.new("QA Reader", bpy.data.meshes.new("QA Reader"))   # Reads A through a Shrinkwrap.
    window.scene.collection.objects.link(reader)
    reader.modifiers.new("Shrinkwrap", "SHRINKWRAP").target = a
    window.view_layer.update()
    held_at_bind(a, b, edit, "edit_mode")
    held_at_bind(a, b, enter_dyntopo, "dyntopo")
    entered_after_bind(a, b, edit, "edit_mode_after_bind")
    entered_after_bind(a, b, enter_dyntopo, "dyntopo_after_bind")


def refused_naming(label, writes, holder_name):
    job, refused = begin(writes)
    text = refused if isinstance(refused, str) else json.dumps(refused, default=str)
    check(label, job is None and "'%s'" % holder_name in text and "nothing was applied" in text, text)
    native.cleanup_all()


def commits(label, writes):
    job, staged = begin(writes)
    result = commit(job) if job is not None else {"error": staged}
    check(label, result.get("status") == "committed" and tuple(writes[0].location) == (1, 2, 3), result.get("error"))


def held_out():
    lattice = bpy.data.lattices.new("QA Lattice")
    la, lb = bpy.data.objects.new("QA Lattice A", lattice), bpy.data.objects.new("QA Lattice B", lattice)
    for obj in (la, lb):
        window.scene.collection.objects.link(obj)
    mode(lb, "EDIT")
    refused_naming("held_out_shared_lattice_in_edit_mode_refused", [la], "QA Lattice B")
    mode(lb, "OBJECT")
    mesh = bpy.data.meshes.new("QA Painted")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    pa, pb = bpy.data.objects.new("QA Paint A", mesh), bpy.data.objects.new("QA Paint B", mesh)
    for obj in (pa, pb):
        window.scene.collection.objects.link(obj)
    mode(pb, "WEIGHT_PAINT")
    refused_naming("held_out_shared_mesh_in_weight_paint_refused", [pa], "QA Paint B")
    mode(pb, "OBJECT")
    commits("held_out_two_object_mode_users_commit", [pa])
    armature = bpy.data.armatures.new("QA Shared Rig")
    ra, rb = bpy.data.objects.new("QA Rig A", armature), bpy.data.objects.new("QA Rig B", armature)
    for obj in (ra, rb):
        window.scene.collection.objects.link(obj)
    mode(ra, "EDIT")
    armature.edit_bones.new("Bone").tail = (0, 0, 1)
    mode(ra, "POSE")
    commits("held_out_rig_beside_a_posed_twin_commits", [rb])
    mode(ra, "OBJECT")


def holder_scan_cost():
    import time
    from angelo_agent import exact_fence, program_admission
    mesh = bpy.data.meshes.new("QA Scatter")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    made = [bpy.data.objects.new("QA Scatter", mesh) for _ in range(10000)]
    timings = []
    for _ in range(5):
        started = time.perf_counter()
        with exact_fence.fence():
            program_admission._holders()
        timings.append((time.perf_counter() - started) * 1000.0)
    report["measurements"] = {"holder_scan_10000_objects_ms": sorted(timings), "objects": len(bpy.data.objects)}
    bpy.data.batch_remove(made)
    bpy.data.meshes.remove(mesh)


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    for case in (held_modes, held_out, holder_scan_cost):
        try:
            case()
        except Exception:
            report["details"][case.__name__ + "_error"] = traceback.format_exc()
            check(case.__name__ + "_ran", False)
        finally:
            native.cleanup_all()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = all(report["checks"].values())


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "read-inputs.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_READ_INPUTS", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), json.dumps(report, indent=2, default=str)
