# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A material appended after a step that changed its object's slots is refused clearly; staging failures correct.

Regression (parallel-edit live Opus run on e2e 45e28c579dc, sailboat_dock, 2026-10-25): a worker's
angelo_scene_program applied a Boolean to $hull, then gave $hull a new material (slot -1). Applying the Boolean
leaves the hull an empty slot for its operand, so the native operation appended the material at slot 1 while the
program had planned slot 0, and the engine failed with error_code UNKNOWN ("did not produce its required slot"),
which held every later edit of the worker's request. Now the operation is refused before it runs (VALIDATION,
naming the step that changed the slots and what to change), and any other unexplained failure while a program is
only staged is an UNSUPPORTED refusal once its binding reads back unchanged.

Cases: the repro (developed on); held out: a copied material (material_assign copy_as), a Boolean UNION whose
operand has a material, the controls that must keep working (material before the Boolean, an explicit slot, the
same edit split over two programs), and an internal failure injected into a real staged program.
Run inside Angelo: ``Angelo -b --factory-startup --python <this file>``.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-program-material-slots"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import native_programs as native  # noqa: E402
from angelo_agent import program_material_facts  # noqa: E402
from angelo_agent.program_jobs import ProgramError  # noqa: E402
from angelo_agent.request_recovery import RecoveryBarrier  # noqa: E402
from angelo_agent.scene_queries import object_handle, object_revision  # noqa: E402

report = {"binary": bpy.app.binary_path, "version": bpy.app.version_string, "paid_requests": 0, "checks": {},
          "cases": {}, "failures": {}}
window = bpy.context.window


class Commit(bpy.types.Operator):
    bl_idname = "angelo.material_slots_commit"
    bl_label = "Material slots native commit"
    bl_options = {"REGISTER", "UNDO"}
    program_id: bpy.props.StringProperty()

    def execute(self, context):
        report["last_commit"] = native.commit_job(self.program_id, window=context.window)
        return {"FINISHED"} if report["last_commit"].get("success") else {"CANCELLED"}


def check(name, condition):
    report["checks"][name] = bool(condition)


def run(operations, objects=(), writes=()):
    window.view_layer.update()
    rows = {obj.as_pointer(): {"handle": object_handle(obj), "revision": object_revision(window, obj)}
            for obj in objects}
    arguments = {"mode": "APPLY", "read_set": list(rows.values()),
                 "write_set": [rows[obj.as_pointer()] for obj in writes],
                 "operations": operations, "expected_checks": []}
    job = native.begin(window, "angelo_scene_program", arguments)
    for _ in range(200):
        outcome = job.poll()
        if outcome.get("final"):
            return outcome, arguments
        if outcome.get("ready_to_commit"):
            bpy.ops.angelo.material_slots_commit("EXEC_DEFAULT", True, program_id=job.id)
            return report["last_commit"], arguments
    job.cancel()
    raise AssertionError("program did not settle")


def clear():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)


def hull(name="Boat_Hull", alias="hull"):
    return {"kind": "create_primitive", "target": None, "parameters": {
        "name": name, "alias": alias, "location": [0, 0, 0.4], "rotation": [0, 0, 0], "scale": [2.25, 0.8, 0.8],
        "primitive": "UV_SPHERE", "radius": 1.0, "segments": 32, "rings": 16}}


def cutter(name="Boat_Cutter", alias="cut", z=0.9):
    return {"kind": "create_primitive", "target": None, "parameters": {
        "name": name, "alias": alias, "location": [0, 0, z], "rotation": [0, 0, 0], "scale": [6, 3, 1],
        "primitive": "CUBE", "size": 1.0}}


def boolean(target="$hull", operand="$cut", operation="DIFFERENCE"):
    return [{"kind": "modifier_add", "target": target, "parameters": {
                "name": "Cut", "type": "BOOLEAN", "settings": {"operand": operand, "operation": operation,
                                                               "solver": "EXACT"}}},
            {"kind": "modifier_apply", "target": target, "parameters": {"modifier": "Cut"}}]


def paint(target="$hull", name="Boat_Paint", slot=-1):
    return {"kind": "material_principled", "target": target, "parameters": {
        "name": name, "slot": slot, "face_indices": [], "inputs": {"Base Color": [0.9, 0.9, 0.88, 1.0],
                                                                    "Roughness": 0.25},
        "shared": "REFUSE", "also": [], "index_scope": "INITIAL", "topology_step": 0, "face_selector": None}}


def refused_clearly(result, arguments, step):
    barrier = RecoveryBarrier()
    barrier.refused("angelo_scene_program", arguments, result)
    return (result.get("error_code") == "VALIDATION" and result.get("scene_changed") is False
            and not result.get("partial_changes_possible") and step in result.get("error", "")
            and "Nothing was changed" in result.get("error", "") and barrier.pending is None
            and bpy.data.objects.get("Boat_Hull") is None)


def repro():
    clear()
    result, arguments = run([hull(), cutter(), *boolean(), paint()])
    check("repro_is_refused_before_it_runs_naming_the_boolean", refused_clearly(result, arguments,
                                                                                "operation 3 (modifier_apply)"))
    return {key: result.get(key) for key in ("error_code", "error", "refusal_details")}


def copied_material():
    clear()
    result, arguments = run([hull(), cutter(), *boolean(), paint(target="$cut", name="Cutter_Paint"),
                             {"kind": "material_assign", "target": "$hull", "parameters": {
                                 "material": "Cutter_Paint", "source": None, "slot": -1, "face_indices": [],
                                 "copy_as": "Hull_Copy", "also": [], "index_scope": "INITIAL", "topology_step": 0,
                                 "face_selector": None}}])
    check("a_copied_material_after_the_boolean_is_refused_too", refused_clearly(result, arguments, "modifier_apply"))
    return {key: result.get(key) for key in ("error_code", "error")}


def union_with_material():
    clear()
    result, arguments = run([hull(), cutter(z=0.0), paint(target="$cut", name="Cutter_Paint"),
                             *boolean(operation="UNION"), paint()])
    check("a_union_whose_operand_has_a_material_is_refused_clearly", refused_clearly(result, arguments,
                                                                                     "operation 4 (modifier_apply)"))
    return {key: result.get(key) for key in ("success", "error_code", "error")}


def assign(target, material, also=()):
    return {"kind": "material_assign", "target": target, "parameters": {
        "material": material, "source": None, "slot": -1, "face_indices": [], "copy_as": None, "also": list(also),
        "index_scope": "INITIAL", "topology_step": 0, "face_selector": None}}


def mast():
    return {"kind": "create_primitive", "target": None, "parameters": {
        "name": "Boat_Mast", "alias": "mast", "location": [0.9, 0, 2.9], "rotation": [0, 0, 0], "scale": [1, 1, 1],
        "primitive": "CYLINDER", "radius": 0.05, "depth": 5.0, "segments": 12, "cap_ends": True}}


def reused_restated_and_also():
    """Held out from the fix's first version (break-it review): a material the program stated earlier, given to the
    hull after the Boolean by material_assign, by restating it, and as an ``also`` object of another object's
    assignment. Each appends to the hull's slots as the new material did."""
    found = {}
    cases = {"assign_reuse": [hull(), cutter(), mast(), paint(target="$mast", name="Mast_Paint"), *boolean(),
                              assign("$hull", "Mast_Paint")],
             "restated": [hull(), cutter(), mast(), paint(target="$mast", name="Mast_Paint"), *boolean(),
                          paint(target="$hull", name="Mast_Paint")],
             "also_target": [hull(), cutter(), mast(), paint(target="$mast", name="Mast_Paint"), *boolean(),
                             assign("$mast", "Mast_Paint", also=["$hull"])]}
    for name, operations in cases.items():
        clear()
        result, arguments = run(operations)
        found[name] = {key: result.get(key) for key in ("success", "error_code", "error")}
        check("%s_after_the_boolean_is_refused_clearly" % name,
              refused_clearly(result, arguments, "operation 5 (modifier_apply)"))
    return found


def transferred_slot():
    """Held out (break-it round 2): a pre-existing Boolean in TRANSFER mode, whose operand has a material, applied by
    the program: the slot it adds holds that material, so a new material appended after it still goes past it."""
    clear()
    mesh = bpy.data.meshes.new("Hull_Mesh")
    import bmesh
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=24, v_segments=12, radius=1.0)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Boat_Hull", mesh)
    window.scene.collection.objects.link(obj)
    cube_mesh = bpy.data.meshes.new("Cutter_Mesh")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bm.to_mesh(cube_mesh)
    bm.free()
    operand = bpy.data.objects.new("Boat_Cutter", cube_mesh)
    operand.location = (0, 0, 0.9)
    window.scene.collection.objects.link(operand)
    operand.data.materials.append(bpy.data.materials.new("Cutter_Red"))
    modifier = obj.modifiers.new("Cut", "BOOLEAN")
    modifier.object, modifier.operation = operand, "DIFFERENCE"
    modifier.solver, modifier.material_mode = "EXACT", "TRANSFER"
    window.view_layer.update()
    handle = object_handle(obj)
    result, arguments = run([{"kind": "modifier_apply", "target": handle, "parameters": {"modifier": "Cut"}},
                             paint(target=handle)], objects=[obj, operand], writes=[obj])
    barrier = RecoveryBarrier()
    barrier.refused("angelo_scene_program", arguments, result)
    check("a_slot_a_transfer_boolean_adds_with_its_material_is_refused_clearly",
          result.get("error_code") == "VALIDATION" and "operation 0 (modifier_apply)" in result.get("error", "")
          and barrier.pending is None and len(obj.material_slots) == 0 and len(obj.modifiers) == 1)
    return {key: result.get(key) for key in ("success", "error_code", "error")}


def controls():
    found = {}
    clear()
    result, _ = run([hull(), cutter(), paint(), *boolean()])
    obj = bpy.data.objects.get("Boat_Hull")
    found["before"] = result.get("success")
    check("material_before_the_boolean_still_works",
          result.get("success") and obj is not None and any(s.material and s.material.name == "Boat_Paint"
                                                            for s in obj.material_slots))
    clear()
    result, _ = run([hull(), cutter(), *boolean(), paint(slot=0)])
    obj = bpy.data.objects.get("Boat_Hull")
    found["explicit_slot"] = result.get("success")
    check("an_explicit_slot_after_the_boolean_still_works",
          result.get("success") and obj is not None and obj.material_slots[0].material is not None
          and obj.material_slots[0].material.name == "Boat_Paint")
    clear()
    first, _ = run([hull(), cutter(), *boolean()])
    obj = bpy.data.objects.get("Boat_Hull")
    second, _ = run([paint(target=object_handle(obj))], objects=[obj], writes=[obj]) if obj else ({}, None)
    found["split"] = (first.get("success"), second.get("success"), second.get("error"))
    check("the_same_edit_split_over_two_programs_works",
          first.get("success") and second.get("success") and [s.material.name if s.material else None
                                                              for s in obj.material_slots] == [None, "Boat_Paint"])
    return found


def injected_internal_failure():
    clear()
    real = program_material_facts.finish_operation   # The name program_objects calls at each operation.

    def broken(stage, operation, proof):
        if operation["kind"] == "material_principled":
            raise ProgramError("injected engine invariant failure")
        return real(stage, operation, proof)
    program_material_facts.finish_operation = broken
    try:
        result, arguments = run([hull(), paint()])
    finally:
        program_material_facts.finish_operation = real
    barrier = RecoveryBarrier()
    barrier.refused("angelo_scene_program", arguments, result)
    check("an_internal_failure_while_staging_is_a_correctable_refusal",
          result.get("error_code") == "UNSUPPORTED" and result.get("scene_changed") is False
          and not result.get("partial_changes_possible")
          and "material_principled (operation 1" in result.get("error", "")
          and "Angelo's program engine" in result.get("error", "")
          and result.get("refusal_details", {}).get("engine_error") == "UNKNOWN" and barrier.pending is None
          and bpy.data.objects.get("Boat_Hull") is None)
    return {key: result.get(key) for key in ("error_code", "error", "refusal_details")}


bpy.context.preferences.filepaths.file_preview_type = "NONE"
bpy.utils.register_class(Commit)
try:
    for name, callback in (("repro", repro), ("copied_material", copied_material),
                           ("union_with_material", union_with_material),
                           ("reused_restated_and_also", reused_restated_and_also),
                           ("transferred_slot", transferred_slot), ("controls", controls),
                           ("injected_internal_failure", injected_internal_failure)):
        try:
            report["cases"][name] = callback()
        except Exception:
            report["failures"][name] = traceback.format_exc()
finally:
    bpy.utils.unregister_class(Commit)
    report["all_passed"] = not report["failures"] and bool(report["checks"]) and all(report["checks"].values())
    (OUT / "program-material-slots.json").write_text(json.dumps(report, indent=2, allow_nan=False, default=str))
    print("PROGRAM_MATERIAL_SLOTS " + json.dumps({"all_passed": report["all_passed"], "checks": report["checks"],
                                                  "failures": report["failures"]}, default=str))
assert report["all_passed"], report
