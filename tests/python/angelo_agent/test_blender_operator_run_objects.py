# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""operator_run's constraint and data-local object operators through real staged programs.

* ``constraint`` operators name one of the target's constraints; commit gives
  the live object the staged stack by name (copied, moved, deleted, applied,
  inverse cleared), with targets mapped back to the live objects, one Undo
  step and Redo; a failed commit rebuilds the previous stack exactly; missing
  names, unnamed operators and operators that reach other objects are
  refused, and so is any constraint operator on a stack with an Armature
  constraint, whose target list neither commit nor its rollback carries (a
  forced rollback would otherwise drop it while reporting success).
* Object operators whose effect stays on the target: origin and track
  clears, parent-inverse and visual-transform applies (the ``transforms``
  family and the parent inverse), skin marks (the skin layer joins the
  digest and revision) and a stale manual skin edit. Clearing the modifier
  stack is denied: ``modifier_remove`` removes modifiers by name.

Run only in a disposable instance with the exact Angelo binary: ``--background
--factory-startup --disable-autoexec --python-exit-code 1``. No provider or
paid call.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from unittest.mock import patch

import bmesh
import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-operator-run-objects"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, operator_state, scene_queries as queries  # noqa: E402
from angelo_agent.program_dependencies import _fingerprint  # noqa: E402
from operator_run_qa import Driver  # noqa: E402
from program_qa_helpers import commit_operator, no_stages  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}
qa = Driver(report)


def rounded(values, digits=4):
    return [round(value, digits) for value in values]


def link(obj):
    qa.window.scene.collection.objects.link(obj)
    return obj


def mesh_object(name):
    source = qa.objects()["Cube"].data
    return link(bpy.data.objects.new(name, source.copy()))


def setup():
    target = link(bpy.data.objects.new("QA Target", None))
    target.location = (3, 0, 0)
    owner = mesh_object("QA Owner")
    for kind, name in (("COPY_LOCATION", "Follow"), ("CHILD_OF", "Hold"), ("LIMIT_LOCATION", "Limit"),
                       ("TRACK_TO", "Aim")):
        constraint = owner.constraints.new(kind)
        constraint.name = name
        if hasattr(constraint, "target"):
            constraint.target = target
    owner.constraints["Follow"].influence = .5
    owner.constraints["Limit"].use_max_x, owner.constraints["Limit"].max_x = True, 10
    parent = link(bpy.data.objects.new("QA Parent", None))
    parent.location = (0, 2, 0)
    child = mesh_object("QA Child")
    child.parent = parent
    child.matrix_parent_inverse.translation = (1, 1, 1)
    child.location = (0, 0, 1)
    skin = mesh_object("QA Skin")
    bm = bmesh.new()
    bm.from_mesh(skin.data)
    bm.verts.layers.skin.verify()  # Skin radii and marks: a CustomData layer RNA cannot add.
    bm.to_mesh(skin.data)
    bm.free()
    chain = link(bpy.data.objects.new("QA Skin Chain", bpy.data.meshes.new("QA Skin Chain")))
    bm = bmesh.new()
    verts = [bm.verts.new((index * .01, 0, 0)) for index in range(3000)]
    for first, second in zip(verts, verts[1:]):
        bm.edges.new((first, second))
    bm.verts.layers.skin.verify()
    bm.to_mesh(chain.data)
    bm.free()
    stacked = mesh_object("QA Stacked")
    stacked.modifiers.new("Bevel", "BEVEL")
    stacked.modifiers.new("Weighted", "WEIGHTED_NORMAL")
    rigged = mesh_object("QA Armature Constrained")
    rigged.constraints.new("ARMATURE").name = "Deform"
    rig = link(bpy.data.objects.new("QA Deform Rig", bpy.data.armatures.new("QA Deform Rig")))
    deform = mesh_object("QA Deformed").constraints
    deform.new("ARMATURE").name = "Deform"
    slot = deform["Deform"].targets.new()  # A target list: a collection no RNA setting copy carries.
    slot.target, slot.weight = rig, .5
    deform.new("LIMIT_LOCATION").name = "Limit"
    deform.new("TRACK_TO").name = "Aim"
    particles = mesh_object("QA Particles")
    particles.modifiers.new("Hair", "PARTICLE_SYSTEM")
    qa.window.view_layer.update()
    bpy.ops.ed.undo_push(message="operator_run objects QA")
    queries.invalidate()


def stack(name):
    obj = qa.objects()[name]
    return [[item.name, item.type, round(item.influence, 4), getattr(getattr(item, "target", None), "name", None),
             rounded(item.inverse_matrix.translation) if item.type == "CHILD_OF" else None] for item in obj.constraints]


def transform(name):
    obj = qa.objects()[name]
    return [rounded(obj.location), rounded(obj.rotation_euler), rounded(obj.matrix_parent_inverse.translation),
            rounded(obj.matrix_world.translation), [item.name for item in obj.constraints]]


def constraints():
    owner, target = qa.objects()["QA Owner"], qa.objects()["QA Target"]
    before = stack("QA Owner")
    qa.applied("constraint_stack", [
        qa.op(owner, "constraint.copy", properties={"constraint": "Follow"}),
        qa.op(owner, "constraint.move_to_index", properties={"constraint": "Follow.001", "index": 0}),
        qa.op(owner, "constraint.delete", properties={"constraint": "Limit"}),
        qa.op(owner, "constraint.move_down", properties={"constraint": "Hold"}),
        qa.op(owner, "constraint.childof_clear_inverse", properties={"constraint": "Hold"})],
        [owner], stack, lambda after: [row[0] for row in after["QA Owner"]] == ["Follow.001", "Follow", "Aim", "Hold"]
        and after["QA Owner"][0][2:4] == [.5, "QA Target"] and after["QA Owner"][3][4] == [0, 0, 0]
        and qa.objects()["QA Owner"].constraints["Aim"].target == qa.objects()["QA Target"], reads=[target])
    qa.check("constraint_stack_untouched_after_undo", stack("QA Owner") == before)
    owner, target = qa.objects()["QA Owner"], qa.objects()["QA Target"]
    qa.applied("constraint_apply", [qa.op(owner, "constraint.apply", properties={"constraint": "Follow"})], [owner],
               transform, lambda after: "Follow" not in after["QA Owner"][4] and after["QA Owner"][0][0] == 1.5,
               reads=[target])
    owner, target = qa.objects()["QA Owner"], qa.objects()["QA Target"]
    qa.refused("constraint_missing", [qa.op(owner, "constraint.delete", properties={"constraint": "Nope"})], [owner],
               "CONSTRAINT_MISSING", reads=[target])
    qa.check("constraint_unnamed_refused", qa.refused_by_validation([qa.op(owner, "constraint.delete")], [owner],
                                                                    "CONSTRAINT_NAME"))
    qa.check("constraint_selection_refused", qa.refused_by_validation(
        [qa.op(owner, "constraint.delete", select="ALL", properties={"constraint": "Limit"})], [owner], "SELECTION"))
    qa.check("constraint_copy_to_selected_denied", qa.refused_by_validation(
        [qa.op(owner, "constraint.copy_to_selected", properties={"constraint": "Limit"})], [owner], "OTHER_OBJECTS"))
    qa.check("followpath_animate_denied", qa.refused_by_validation(
        [qa.op(owner, "constraint.followpath_path_animate", properties={"constraint": "Limit"})], [owner],
        "OTHER_OBJECTS"))
    rigged = qa.objects()["QA Armature Constrained"]
    qa.refused("armature_constraint_copy", [qa.op(rigged, "constraint.copy", properties={"constraint": "Deform"})],
               [rigged], "CONSTRAINT_TARGETS")


def failing_sync(*args):
    """operator_state.sync_constraints, then a failure: the commit rolls the live stack back."""
    _sync_constraints(*args)
    raise RuntimeError("injected failure after the constraint stack sync")


_sync_constraints = operator_state.sync_constraints


def armature_constraint_stacks():
    """No constraint operator runs beside an Armature constraint: commit and its rollback rebuild constraints from
    their RNA settings, which hold no target list. Were one staged, a failed commit would drop the targets."""
    def targets():
        return [[getattr(item.target, "name", None), round(item.weight, 4)]
                for item in qa.objects()["QA Deformed"].constraints["Deform"].targets]

    before = targets()
    for label, operation in (("delete", ("constraint.delete", {"constraint": "Limit"})),
                             ("track_clear", ("object.track_clear", {}))):
        deformed, rig = qa.objects()["QA Deformed"], qa.objects()["QA Deform Rig"]
        prints = [_fingerprint(deformed), _fingerprint(rig)]
        job, result = qa.run(qa.program([qa.op(deformed, operation[0], properties=operation[1])], writes=[deformed],
                                        reads=[rig]))
        if result.get("ready_to_commit"):
            with patch.object(operator_state, "sync_constraints", failing_sync):
                qa.commit(job)
            queries.invalidate()
        report.setdefault("refusals", {})["armature_stack_" + label] = result.get("error")
        qa.check("armature_stack_%s_refused" % label, qa.failed_with(result, "CONSTRAINT_TARGETS") and no_stages()
                 and [_fingerprint(qa.objects()["QA Deformed"]), _fingerprint(qa.objects()["QA Deform Rig"])] == prints)
        qa.check("armature_stack_%s_targets_kept" % label, targets() == before == [["QA Deform Rig", .5]])


def constraint_rollback():
    """A commit that fails after the stack sync gives the live object its exact previous stack back."""
    owner, target = qa.objects()["QA Owner"], qa.objects()["QA Target"]
    before, fingerprint = stack("QA Owner"), _fingerprint(owner)
    job, staged = qa.run(qa.program([qa.op(owner, "constraint.delete", properties={"constraint": "Follow"}),
                                     qa.op(owner, "constraint.copy", properties={"constraint": "Limit"})],
                                    writes=[owner], reads=[target]))
    with patch.object(operator_state, "sync_constraints", failing_sync):
        committed = qa.commit(job)
    owner = qa.objects()["QA Owner"]
    qa.check("constraint_failed_commit_rolls_back", staged.get("ready_to_commit") and not committed
             and stack("QA Owner") == before and _fingerprint(owner) == fingerprint and no_stages())
    queries.invalidate()


def object_operators():
    child, parent = qa.objects()["QA Child"], qa.objects()["QA Parent"]
    before = transform("QA Child")
    qa.applied("origin_clear", [qa.op(child, "object.origin_clear")], [child], transform,
               lambda after: after["QA Child"][2] != before[2] and after["QA Child"][0] == before[0], reads=[parent])
    child, parent = qa.objects()["QA Child"], qa.objects()["QA Parent"]
    qa.applied("parent_inverse_apply", [qa.op(child, "object.parent_inverse_apply")], [child], transform,
               lambda after: after["QA Child"][3] == before[3] and after["QA Child"][2] != before[2], reads=[parent])
    owner, target = qa.objects()["QA Owner"], qa.objects()["QA Target"]
    world = transform("QA Owner")[3]
    qa.applied("track_clear_keep_transform", [qa.op(owner, "object.track_clear", properties={"type": "CLEAR_KEEP_TRANSFORM"})],
               [owner], transform, lambda after: "Aim" not in after["QA Owner"][4] and after["QA Owner"][0] == world,
               reads=[target])
    owner, target = qa.objects()["QA Owner"], qa.objects()["QA Target"]
    qa.applied("visual_transform_apply", [qa.op(owner, "object.visual_transform_apply")], [owner], transform,
               lambda after: after["QA Owner"][0] == world, reads=[target])
    skin = qa.objects()["QA Skin"]
    roots = lambda name: [vertex.use_root for vertex in qa.objects()[name].data.skin_vertices[0].data]
    qa.applied("skin_root_mark", [qa.op(skin, "object.skin_root_mark", select="INDICES", vertex_indices=[3])], [skin],
               roots, lambda after: after["QA Skin"][3])

    radius = tuple(qa.objects()["QA Skin"].data.skin_vertices[0].data[0].radius)

    def edit():
        qa.objects()["QA Skin"].data.skin_vertices[0].data[0].radius = (2, 2)

    def restore():
        qa.objects()["QA Skin"].data.skin_vertices[0].data[0].radius = radius

    qa.stale("skin", [qa.op(qa.objects()["QA Skin"], "object.skin_radii_equalize")], qa.objects()["QA Skin"], edit, restore)
    # skin_root_mark recurses once per connected vertex: a 3,000-vertex chain is refused before it could
    # overflow the stack (a 10,000-vertex chain crashed Blender).
    chain = qa.objects()["QA Skin Chain"]
    qa.refused("skin_root_recursion", [qa.op(chain, "object.skin_root_mark")], [chain], "RECURSION_BOUND")
    # Clearing the stack at once would drop Multires, bind, hook, particle or cache data a failed commit cannot
    # rebuild; modifier_remove removes modifiers by name under its own rules.
    for name in ("QA Stacked", "QA Particles"):
        qa.check("modifiers_clear_denied_" + name.split()[-1].lower(), qa.refused_by_validation(
            [qa.op(qa.objects()[name], "object.modifiers_clear")], [qa.objects()[name]], "COVERED"))
    stacked = qa.objects()["QA Stacked"]
    qa.check("vertex_parent_denied", qa.refused_by_validation([qa.op(stacked, "object.vertex_parent_set")], [stacked],
                                                              "OTHER_OBJECTS"))
    qa.check("bind_data_denied", qa.refused_by_validation([qa.op(stacked, "object.meshdeform_bind")], [stacked],
                                                          "MODIFIER_BIND_DATA"))


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    queries.invalidate()
    setup()
    constraints()
    constraint_rollback()
    armature_constraint_stacks()
    object_operators()
    qa.check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "operator-run-objects.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_OPERATOR_RUN_OBJECTS", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
