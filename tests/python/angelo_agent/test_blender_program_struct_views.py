# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Typed programs see every RNA view and every ID property of their inputs; the walks' cost, before and after.

Identified by pointer alone, the fingerprint read ``Object.display`` (the Object's own pointer) and
``Object.collision`` (``Object.field``'s data) as references to a struct it had already read, and never read a pose
bone's ID properties: a change to any of them between a program's bind and commit went unseen
(doc/angelo/native_coverage.rst, "Program struct identity"). Also: which real collections the link walk may skip
(rna_fields.link_free_classifier), with the walks' links equal to the full walk's, and timings of the fingerprint on
large inputs (``timings``, signals only; this file runs on builds before the change too, where its checks fail).

Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``. No provider or paid call.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-struct-views-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent import exact_fence, program_dependencies as dependencies  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string,
          "timings": {}, "link_free": {}}
window = bpy.context.window_manager.windows[0]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def settle(job):
    for _ in range(2000):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("cooperative native program did not settle")


def commit(job):
    result = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    return "FINISHED" in result and job.result.get("status") == "committed"


def move(obj, location):
    binding = {"handle": queries.object_handle(obj), "revision": queries.object_revision(window, obj)}
    return validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": [], "write_set": [binding], "operations": [
        {"kind": "transform", "target": binding["handle"], "parameters": {"location": list(location)}}]})


def refuses_after(name, obj, change, restore):
    """A program on ``obj`` binds and stages; ``change`` between bind and commit makes the commit refuse, and with
    nothing changed the same program commits."""
    bpy.ops.ed.undo_push(message="views baseline " + name)
    queries.invalidate()
    job = native.begin(window, "angelo_scene_program", move(obj, (1, 2, 3)))
    check(name + "_stages", settle(job).get("ready_to_commit"))
    change()
    check(name + "_change_refuses_commit", not commit(job))
    native.cleanup_all()
    restore()
    queries.invalidate()
    job = native.begin(window, "angelo_scene_program", move(obj, (0, 0, 0)))
    check(name + "_unchanged_commits", settle(job).get("ready_to_commit") and commit(job))


def mesh_object(name, size=1):
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=size, y_subdivisions=size)
    obj = bpy.context.view_layer.objects.active
    obj.name = name
    return obj


def timed(function, repeat=5):
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        function()
        samples.append(time.perf_counter() - started)
    return round(statistics.median(samples) * 1000, 2)


def timings():
    """Median ms of a bind-time fingerprint and link walk of large inputs (signals: report them with the load)."""
    bpy.ops.preferences.addon_enable(module="rigify")
    bpy.ops.object.armature_human_metarig_add()
    rig = bpy.context.view_layer.objects.active
    rig.name = "TimedRig"
    dense = mesh_object("TimedMesh", 316)   # About 100k vertices.
    curve_data = bpy.data.curves.new("TimedCurve", "CURVE")
    spline = curve_data.splines.new("BEZIER")
    spline.bezier_points.add(4999)
    curve = bpy.data.objects.new("TimedCurve", curve_data)
    window.scene.collection.objects.link(curve)
    material = bpy.data.materials.new("TimedNodes")
    material.use_nodes = True
    for index in range(300):
        material.node_tree.nodes.new("ShaderNodeMath").location = (index * 10.0, 0.0)
    dense.data.materials.append(material)
    for obj in (rig, dense, curve):
        row = report["timings"][obj.name] = {}
        for part, function in (("fingerprint_ms", lambda: dependencies._fingerprint(obj)),
                               ("links_ms", lambda: [list(dependencies._embedded_links(block))
                                                     for block in dependencies._mutable_dependencies(obj)])):
            try:
                row[part] = timed(function)
            except Exception as error:  # noqa: BLE001  A refusal is a result here: recorded, never timed.
                row[part] = "refused: %s: %s" % (type(error).__name__, str(error)[:120])
    classifier = getattr(dependencies, "link_free_types", None)
    if classifier is not None:
        def build():
            with exact_fence.fence():
                classifier()
        report["timings"]["link_free_classifier_first_ms"] = timed(build, repeat=1)   # Primes bpy.types once.
        report["timings"]["link_free_classifier_ms"] = timed(build)


def views():
    fielded = mesh_object("Fielded")
    fielded.modifiers.new("Collision", "COLLISION")   # Creates the object's field data, which both views share.
    fielded.field.type = "FORCE"
    window.view_layer.update()
    check("collision_shares_field_data", fielded.collision.as_pointer() == fielded.field.as_pointer())
    check("display_is_the_object", fielded.display.as_pointer() == fielded.as_pointer())
    outer = fielded.collision.thickness_outer
    refuses_after("collision_view", fielded, lambda: setattr(fielded.collision, "thickness_outer", outer + .1),
                  lambda: setattr(fielded.collision, "thickness_outer", outer))
    refuses_after("display_view", fielded, lambda: setattr(fielded.display, "show_shadows", False),
                  lambda: setattr(fielded.display, "show_shadows", True))
    strength = fielded.field.strength
    refuses_after("field_view", fielded, lambda: setattr(fielded.field, "strength", strength + 1),
                  lambda: setattr(fielded.field, "strength", strength))


def pose_bone_properties():
    data = bpy.data.armatures.new("Switched")
    rig = bpy.data.objects.new("Switched", data)
    window.scene.collection.objects.link(rig)
    window.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    for name, head in (("Arm", (0, 0, 0)), ("Hand", (0, 0, 1))):
        bone = data.edit_bones.new(name)
        bone.head, bone.tail = head, (head[0], head[1], head[2] + 1)
    bpy.ops.object.mode_set(mode="OBJECT")
    rig.pose.bones["Hand"]["IK_FK"] = 0.0
    data.bones["Arm"]["note"] = 1
    refuses_after("pose_bone_id_property", rig, lambda: rig.pose.bones["Hand"].__setitem__("IK_FK", 1.0),
                  lambda: rig.pose.bones["Hand"].__setitem__("IK_FK", 0.0))
    refuses_after("bone_id_property", rig, lambda: data.bones["Arm"].__setitem__("note", 2),
                  lambda: data.bones["Arm"].__setitem__("note", 1))


def link_free_types():
    """Which real collections are link free, and the walks' links on a groomed object equal the full walk's."""
    with exact_fence.fence():
        free = dependencies.link_free_types()
        cases = {"ParticleSystem.particles": True, "Particle.hair_keys": True, "Particle.particle_keys": True,
                 "Object.modifiers": False, "Object.constraints": False, "NodeTree.nodes": False,
                 "Armature.bones": False, "Object.particle_systems": False}
        for case, expected in cases.items():
            owner, name = case.split(".")
            found = free(getattr(bpy.types, owner).bl_rna.properties[name])
            report["link_free"][case] = found
            check("link_free_" + case, found is expected)
        check("link_free_placeholder_ShapeKey.data", free(bpy.types.ShapeKey.bl_rna.properties["data"]) is False)
        for case in ("Spline.points", "Spline.bezier_points", "Mesh.vertices", "Lattice.points"):
            owner, name = case.split(".")
            report["link_free"][case] = free(getattr(bpy.types, owner).bl_rna.properties[name])
    groomed = mesh_object("Groomed", 20)
    groomed.modifiers.new("Hair", "PARTICLE_SYSTEM")
    settings = groomed.particle_systems[0].settings
    settings.type, settings.count, settings.hair_step = "HAIR", 2000, 8
    window.view_layer.update()
    expected = sorted((holder.bl_rna.identifier, prop.identifier, target.name)
                      for holder, prop, target in dependencies._walk_links(groomed))
    with exact_fence.fence():
        fenced = sorted((holder.bl_rna.identifier, prop.identifier, target.name)
                        for holder, prop, target in dependencies._walk_links(groomed))
    check("groomed_fenced_links_equal_full_walk", fenced == expected)
    report["timings"]["groomed_links_ms"] = {"full": timed(lambda: list(dependencies._walk_links(groomed))),
                                             "fenced": timed(lambda: _fenced_walk(groomed))}


def _fenced_walk(obj):
    with exact_fence.fence():
        list(dependencies._walk_links(obj))


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    timings()   # First: the same measurement on builds before the change, whose checks below fail.
    views()
    pose_bone_properties()
    link_free_types()
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "struct-views.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_STRUCT_VIEWS", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
