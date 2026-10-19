# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One private copy per live material of an object, however a borrowing stage was privatized.

Usage::

    Angelo --background --factory-startup --python tests/python/angelo_agent/test_blender_borrowed_materials_shared_slots.py

Staging copies one private material per live material of an object, however
many of its slots hold it (``ProgramObjectsMixin.copy_object``): two copies
would split the material's faces between two names, and a check over all of
its faces would count half of them (stage-names). A material-blind program
borrows its materials instead (``stage_borrowing``), and a borrowing stage is
privatized later, by ``stage_borrowing.privatize``: when it is retained for a
job's inspection, and when it owns an animated copy before its first
evaluation (``owns_animated``). The integration of both found that
``privatize`` cloned per slot. This suite pins one copy per live material per
object on every path, on an object whose shared material fills two slots
beside a second material:

* a borrowing move that commits (the slots hold the live material);
* a job inspection (``job_context`` retains, and so privatizes) that then
  commits; the PREVIEW path, which never borrows, as a control;
* the forced ``owns_animated`` unborrow before the first evaluation;
* a DATA slot and an OBJECT-linked slot holding the same material.

Each case also checks that a ``material_assignment`` check with ``all_faces``
counts every face, that commit gives the object its live materials back with
no copy left behind, and that no private name reaches the result.
"""

import json
import os
from pathlib import Path
import sys
import traceback

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries, stage_borrowing, stage_names  # noqa: E402
from angelo_agent.program_objects import MATERIAL_SOURCE  # noqa: E402
from program_qa_helpers import args_for, no_stages  # noqa: E402

OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.file_preview_type = "NONE"
report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
MOVE = {"kind": "transform", "parameters": {"location": [1., 2., 3.]}}


def check(name, condition):
    report["checks"][name] = bool(condition)


def settle(job):
    for _ in range(400):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("Program did not settle")


def names():
    return sorted(material.name for material in bpy.data.materials)


def shared_object(name, object_slot=False):
    """Two quads, both on the shared material; a second material in another slot holds no face.

    ``object_slot``: data slots (Shared, Other) with the second slot linked to the object and holding Shared,
    so a DATA slot and an OBJECT slot hold the same material. Otherwise data slots (Shared, Shared, Other).
    """
    window = bpy.context.window
    shared, other = bpy.data.materials.new(name + " Shared"), bpy.data.materials.new(name + " Other")
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (2, 0, 0), (2, 1, 0)], [],
                     [(0, 1, 2, 3), (1, 4, 5, 2)])
    for material in ((shared, other) if object_slot else (shared, shared, other)):
        mesh.materials.append(material)
    mesh.polygons[0].material_index, mesh.polygons[1].material_index = 0, 1
    obj = bpy.data.objects.new(name, mesh)
    window.scene.collection.objects.link(obj)
    if object_slot:
        obj.material_slots[1].link = "OBJECT"
        obj.material_slots[1].material = shared
    window.view_layer.update()
    return window, obj, shared, other


def slots(obj):
    """Every material the object's slots and data hold: (link, index, material)."""
    held = [("DATA", index, material) for index, material in enumerate(obj.data.materials)]
    return held + [("OBJECT", index, slot.material) for index, slot in enumerate(obj.material_slots)
                   if slot.link == "OBJECT"]


def copies(obj, live):
    """For each live material: the distinct IDs that stand for it in the object's slots (it, or private copies)."""
    by_uid = {int(material.session_uid): material.name for material in live}
    found = {}
    for _link, _index, material in slots(obj):
        if material is None:
            continue
        uid = int(material[MATERIAL_SOURCE]) if MATERIAL_SOURCE in material.keys() else int(material.session_uid)
        found.setdefault(by_uid.get(uid, "unknown"), set()).add(material.as_pointer())
    return {name: len(pointers) for name, pointers in sorted(found.items())}


def faces_on(obj, shared_uid):
    """Faces whose slot holds the shared material or a copy of it, and the face count."""
    def source(material):
        if material is None:
            return None
        return int(material[MATERIAL_SOURCE]) if MATERIAL_SOURCE in material.keys() else int(material.session_uid)
    held = [source(slot.material) for slot in obj.material_slots]
    return sum(1 for face in obj.data.polygons if held[face.material_index] == shared_uid), len(obj.data.polygons)


def all_faces(obj, shared):
    return {"kind": "material_assignment", "target": queries.object_handle(obj), "material": shared.name,
            "all_faces": True}


def begin(window, obj, shared, mode="APPLY"):
    operations = [dict(MOVE, target=queries.object_handle(obj))]
    return native.begin(window, "angelo_scene_program",
                        args_for(window, operations, writes=[obj], mode=mode, checks=[all_faces(obj, shared)]))


def staged_object(stage):
    return next(iter(stage.objects.values()))


def inspect(label, job, obj, shared, other, *, borrowed):
    """The stage as an inspection sees it: one ID per live material, and the all-faces check over all faces."""
    with native.job_context(job.id) as stage:
        held = staged_object(stage)
        counts = copies(held, (shared, other))
        report["observed"][label + ":copies"] = counts
        check(label + ":one_id_per_live_material", counts.get(shared.name) == 1 and counts.get(other.name) == 1)
        live = {material.as_pointer() for material in (shared, other)}
        private = all(material is None or material.as_pointer() not in live for _l, _i, material in slots(held))
        check(label + ":private_copies" if not borrowed else label + ":live_materials", private != borrowed)
        report["observed"][label + ":faces"] = faces_on(held, int(shared.session_uid))
        check(label + ":every_face_on_the_shared_material", faces_on(held, int(shared.session_uid)) == (2, 2))
        verdict = job.backend.validate(stage, all_faces(obj, shared))
        row = next((row for row in verdict.get("checks", ()) if row.get("kind") == "material_assignment"), {})
        report["observed"][label + ":check"] = {key: row.get(key) for key in ("status", "evidence", "observed")} or row
        check(label + ":all_faces_check_passes", verdict.get("success") is True)


def committed(label, outcome, obj, live_slots, before):
    text = json.dumps(outcome, default=str)
    check(label + ":committed", outcome.get("success") is True and outcome.get("status") == "committed")
    check(label + ":originals_restored", [(link, index, material) for link, index, material in slots(obj)] == live_slots)
    check(label + ":no_copy_left", names() == before)
    check(label + ":no_private_name_in_result", stage_names.PREFIX not in text and "__angelo" not in text)
    check(label + ":moved", list(obj.location) == [1., 2., 3.] and no_stages())


def borrowing_move_commits():
    window, obj, shared, other = shared_object("Borrowed")
    live_slots, before = slots(obj), names()
    job = begin(window, obj, shared)
    result = settle(job)
    rows = sorted(row[1:3] for row in (job.stage.extras or {}).get(stage_borrowing.BORROWED, ()))
    report["observed"]["borrowed:rows"] = rows
    check("borrowed:every_slot_borrowed", rows == [("DATA", 0), ("DATA", 1), ("DATA", 2)])
    check("borrowed:ready", bool(result.get("ready_to_commit")))
    committed("borrowed", job.commit(), obj, live_slots, before)


def inspection_privatizes_then_commits():
    window, obj, shared, other = shared_object("Inspected")
    live_slots, before = slots(obj), names()
    job = begin(window, obj, shared)
    check("inspected:ready", bool(settle(job).get("ready_to_commit")))
    inspect("inspected", job, obj, shared, other, borrowed=False)
    committed("inspected", job.commit(), obj, live_slots, before)


def preview_accept_control():
    window, obj, shared, other = shared_object("Preview")
    live_slots, before = slots(obj), names()
    result = settle(begin(window, obj, shared, mode="PREVIEW"))
    check("preview:retained", result.get("status") == "preview")
    with native.preview_context(result["preview_id"], window=window) as stage:
        counts = copies(staged_object(stage), (shared, other))
        report["observed"]["preview:copies"] = counts
        check("preview:one_copy_per_live_material", counts.get(shared.name) == 1 and counts.get(other.name) == 1)
    accepted = native.begin(window, "angelo_accept_preview", {"preview_id": result["preview_id"]})
    check("preview:accept_ready", bool(settle(accepted).get("ready_to_commit")))
    committed("preview", accepted.commit(), obj, live_slots, before)


def owned_animation_unborrows():
    window, obj, shared, other = shared_object("Unborrowed")
    live_slots, before = slots(obj), names()
    owns, backend = stage_borrowing.owns_animated, native.BlenderProgramBackend
    unborrow, calls = backend.unborrow, []
    try:  # Force the path: finish_stage unborrows a borrowing stage before its first evaluation.
        stage_borrowing.owns_animated = stage_borrowing.borrows_any
        backend.unborrow = lambda self, stage: calls.append(stage_borrowing.borrows_any(stage)) or unborrow(self, stage)
        job = begin(window, obj, shared)
        result = settle(job)
    finally:
        stage_borrowing.owns_animated, backend.unborrow = owns, unborrow
    report["observed"]["unborrowed:result"] = {key: result.get(key) for key in ("status", "error_code", "error")}
    check("unborrowed:unborrowed_once_before_the_first_evaluation", calls[:1] == [True])
    check("unborrowed:all_faces_check_passed_on_the_privatized_stage", bool(result.get("ready_to_commit")))
    inspect("unborrowed", job, obj, shared, other, borrowed=False)
    committed("unborrowed", job.commit(), obj, live_slots, before)


def data_and_object_slot_share_a_material():
    window, obj, shared, other = shared_object("Object slot", object_slot=True)
    live_slots, before = slots(obj), names()
    job = begin(window, obj, shared)
    result = settle(job)
    rows = sorted(row[1:3] for row in (job.stage.extras or {}).get(stage_borrowing.BORROWED, ()))
    report["observed"]["object_slot:rows"] = rows
    check("object_slot:data_and_object_slots_borrowed", rows == [("DATA", 0), ("DATA", 1), ("OBJECT", 1)])
    check("object_slot:ready", bool(result.get("ready_to_commit")))
    inspect("object_slot", job, obj, shared, other, borrowed=False)
    committed("object_slot", job.commit(), obj, live_slots, before)


try:
    for test in (borrowing_move_commits, inspection_privatizes_then_commits, preview_accept_control,
                 owned_animation_unborrows, data_and_object_slot_share_a_material):
        try:
            test()
        except Exception:
            report["checks"][test.__name__ + ":raised"] = False
            report["observed"][test.__name__ + ":error"] = traceback.format_exc()
            traceback.print_exc()
        finally:
            native.cleanup_all()
    report["success"] = all(report["checks"].values())
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    native.cleanup_all()
    (OUT / "results.json").write_text(json.dumps(report, indent=2, default=str))
failed = sorted(name for name, passed in report["checks"].items() if not passed)
print("BORROWED_SHARED_SLOTS failed checks:", json.dumps(failed))
assert report["success"], failed
