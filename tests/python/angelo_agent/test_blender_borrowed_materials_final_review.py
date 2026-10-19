# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Edge cases of staging that borrows live materials, in a disposable process.

Pins:

* Nothing evaluates a new stage before ``finish_stage``. A particle system
  makes ``copy_object`` run ``bpy.ops.particle.new()``, which updates only the
  artist's view layer; so the ``owns_animated`` unborrow there happens before
  anything could write through a borrowed slot, and the move commits with the
  artist's unkeyed value kept.
* Element views hold no IDs: a Python ``PointerProperty`` registered on their
  item types cannot store one (read-only), as well as no ID properties.
* The scene reader is the size gate the skipped element views rely on: hair
  and Grease Pencil objects of 60,000 points are refused before admission;
  50,000-point ones are admitted and a move commits.
* Commit repoints a swapped hair data block's ``surface`` to the live object;
  a text object's declared ``follow_curve`` and a curve's OBJECT-linked slot
  stay live through a borrowing move.
"""

import json
import os
from pathlib import Path
import sys
import traceback
from unittest import mock

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries, stage_borrowing  # noqa: E402
from angelo_agent import program_objects  # noqa: E402
from angelo_agent.program_dependencies import _mutable_dependencies  # noqa: E402
from program_qa_helpers import no_stages, spent_budget  # noqa: E402

OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.file_preview_type = "NONE"
report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
W = bpy.context.window
TWEAKED = .9


def check(name, condition):
    report["checks"][name] = bool(condition)


def link(obj):
    W.scene.collection.objects.link(obj)
    return obj


def entry(obj):
    return {"handle": queries.object_handle(obj), "revision": queries.object_revision(W, obj)}


def run(label, operations, writes, reads=()):
    """Stage, settle and commit; returns what happened, with the borrowed slot count after preparation."""
    W.view_layer.update()
    args = {"mode": "APPLY", "read_set": [entry(o) for o in reads], "write_set": [entry(o) for o in writes],
            "operations": operations, "expected_checks": []}
    job = native.begin(W, "angelo_scene_program", args)
    with spent_budget():
        result = job.poll()
    rec = {"borrowed": len((getattr(job.stage, "extras", None) or {}).get(stage_borrowing.BORROWED, ()))}
    for _ in range(200):
        if result.get("final") or result.get("ready_to_commit"):
            break
        result = job.poll()
    rec.update(status=result.get("status"), error_code=result.get("error_code"))
    if result.get("ready_to_commit"):
        rec["status"] = job.commit().get("status")
    elif not result.get("final"):
        job.cancel()
    native.cleanup_all()
    rec["no_stages"] = no_stages()
    report["observed"][label] = rec
    return rec


def move(obj):
    return {"kind": "transform", "target": queries.object_handle(obj), "parameters": {"location": [1., 2., 3.]}}


def quad(name, material):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    mesh.materials.append(material)
    return link(bpy.data.objects.new(name, mesh))


def no_stage_evaluation_before_finish_stage():
    events, phase = [], {"now": "before"}

    def handler(_scene, depsgraph):
        events.append((phase["now"], bool(depsgraph.scene.get("_angelo_program_stage"))))

    original = program_objects.ProgramObjectsMixin.finish_stage

    def finish(self, stage):
        phase["now"] = "finish_stage"
        return original(self, stage)

    bpy.app.handlers.depsgraph_update_post.append(handler)
    try:
        for label, missed in (("particles", False), ("particles_owned_animation", True)):
            material = bpy.data.materials.new("Final " + label)
            obj = quad("Final " + label, material)
            obj.modifiers.new("Particles", "PARTICLE_SYSTEM").particle_system.settings.count = 10
            patches = [mock.patch.object(program_objects.ProgramObjectsMixin, "finish_stage", finish)]
            if missed:  # A data type the closure walk missed: _plan borrows although the mesh's Action keys the material.
                action = bpy.data.actions.new("Final " + label)
                obj.data.animation_data_create()
                obj.data.animation_data.action = action
                curve = action.fcurve_ensure_for_datablock(obj.data, "materials[0].roughness")
                for frame in (1, 20):
                    curve.keyframe_points.insert(frame, .2)
                patches.append(mock.patch.object(stage_borrowing, "_copied", lambda original: [
                    block for block in _mutable_dependencies(original) if block != original.data]))
            W.scene.frame_set(1)
            material.roughness = TWEAKED
            W.view_layer.update()
            events.clear()
            phase["now"] = "staging"
            for patch in patches:
                patch.start()
            try:
                rec = run(label, [move(obj)], [obj])
            finally:
                for patch in patches:
                    patch.stop()
                phase["now"] = "before"
            staged_before = [stage for when, stage in events if when == "staging" and stage]
            check(label + ":no_stage_evaluation_before_finish_stage", not staged_before)
            check(label + ":artist_view_layer_updated_by_the_operator", any(when == "staging" for when, _ in events))
            check(label + ":commits_with_tweak_kept", rec["status"] == "committed" and round(material.roughness, 4) == TWEAKED
                  and list(obj.location) == [1., 2., 3.] and rec["no_stages"])
            check(label + ":" + ("unborrowed" if missed else "borrowed"), (rec["borrowed"] == 0) == missed)
    finally:
        bpy.app.handlers.depsgraph_update_post.remove(handler)


def element_view_items_cannot_store_an_id():
    data = bpy.data.hair_curves.new("Final views")
    data.add_curves([3])
    target = link(bpy.data.objects.new("Final view target", None))
    stored = {}
    for name in ("points", "curves", "position_data", "curve_offset_data", "normals"):
        item = getattr(data, name)[0]
        kind = type(item)
        kind.review_pointer = bpy.props.PointerProperty(type=bpy.types.Object)
        try:
            item.review_pointer = target
            stored[name] = item.review_pointer is not None
        except AttributeError:
            stored[name] = False
        finally:
            del kind.review_pointer
    report["observed"]["element_view_python_pointer_stored"] = stored
    check("element_view_items_cannot_store_a_python_pointer", not any(stored.values()))


def reader_is_the_size_gate():
    for kind in ("hair", "gp"):
        for points, admitted in ((50000, True), (60000, False)):
            if kind == "hair":
                data = bpy.data.hair_curves.new("Final %s %d" % (kind, points))
                data.add_curves([10] * (points // 10))
            else:
                data = bpy.data.grease_pencils.new("Final %s %d" % (kind, points))
                data.layers.new("Layer").frames.new(1).drawing.add_strokes([10] * (points // 10))
            obj = link(bpy.data.objects.new("Final %s %d" % (kind, points), data))
            W.view_layer.update()
            try:
                queries.object_revision(W, obj)
                refused = None
            except Exception as error:
                refused = str(error)[:120]
            label = "size_gate_%s_%d" % (kind, points)
            if admitted:
                rec = run(label, [move(obj)], [obj])
                check(label + ":admitted_and_moved", refused is None and rec["status"] == "committed")
            else:
                report["observed"][label] = refused
                check(label + ":refused_by_the_reader", refused is not None and "budget" in refused)
            bpy.data.objects.remove(obj)


def repoints_and_live_links():
    surface_mesh = bpy.data.meshes.new("Final surface")
    surface_mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [], [(0, 1, 2)])
    surface = link(bpy.data.objects.new("Final surface", surface_mesh))
    hair = bpy.data.hair_curves.new("Final hair")
    hair.add_curves([4, 4])
    for index, point in enumerate(hair.points):
        point.position = (index * .1, 0, 0)
    hair.materials.append(bpy.data.materials.new("Final hair"))
    hair.surface = surface
    obj = link(bpy.data.objects.new("Final hair", hair))
    rec = run("hair_surface_swap", [{"kind": "operator_run", "target": queries.object_handle(obj),
                                     "parameters": {"operator": "curves.subdivide", "properties": {"number_cuts": 1}}}],
              [obj], reads=[surface])
    check("hair_swap_repoints_surface_to_live", rec["status"] == "committed" and obj.data != hair
          and obj.data.surface == surface)
    text = bpy.data.curves.new("Final text", "FONT")
    text.body = "Hi"
    text.materials.append(bpy.data.materials.new("Final text"))
    path_data = bpy.data.curves.new("Final path", "CURVE")
    path_data.splines.new("POLY").points.add(1)  # An empty curve has no exact revision (non-finite bounds).
    path = link(bpy.data.objects.new("Final path", path_data))
    text.follow_curve = path
    text_obj = link(bpy.data.objects.new("Final text", text))
    rec = run("text_follow_curve", [move(text_obj)], [text_obj], reads=[path])
    check("text_follow_curve_borrowed_committed_live", rec["borrowed"] == 1 and rec["status"] == "committed"
          and text_obj.data.follow_curve == path)
    curve = bpy.data.curves.new("Final curve", "CURVE")
    curve.splines.new("POLY").points.add(1)
    materials = [bpy.data.materials.new("Final curve %d" % index) for index in range(3)]
    curve.materials.append(materials[0])
    curve.materials.append(materials[1])
    curve_obj = link(bpy.data.objects.new("Final curve", curve))
    curve_obj.material_slots[1].link = "OBJECT"
    curve_obj.material_slots[1].material = materials[2]
    users = [m.users for m in materials]
    rec = run("curve_object_slot", [move(curve_obj)], [curve_obj])
    check("curve_object_slot_borrowed_restored", rec["borrowed"] == 3 and rec["status"] == "committed"
          and [(s.link, s.material) for s in curve_obj.material_slots] == [("DATA", materials[0]), ("OBJECT", materials[2])]
          and list(curve.materials) == materials[:2] and [m.users for m in materials] == users)


try:
    for test in (no_stage_evaluation_before_finish_stage, element_view_items_cannot_store_an_id,
                 reader_is_the_size_gate, repoints_and_live_links):
        try:
            test()
        finally:
            native.cleanup_all()
    report["success"] = all(report["checks"].values())
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    native.cleanup_all()
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
failed = sorted(name for name, passed in report["checks"].items() if not passed)
print("BORROWED_FINAL_REVIEW failed checks:", json.dumps(failed))
assert report["success"], failed
