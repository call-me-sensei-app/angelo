# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shape Cut in Blender (shape_cut, doc/angelo/shape_cut.rst): real Booleans, live cutters, apply-and-clean.

Run by ``run_native_suites.py --include test_blender_shape_cut`` (BACKGROUND). Proves, with volumes measured by
Blender's own ``bmesh`` on the evaluated result:

* every operation on a solid block: CUT, UNION, INTERSECT, SLICE (target + piece = the block), INSET and PANEL, each
  closed afterwards, the volume changed by what the shape says;
* targets of different kinds: a thin closed shell (INSET stays closed where a plain CUT opens it), an open mesh
  (Exact chosen, and why), a curved face of a cylinder, a rotated and scaled object;
* live editing: moving a cutter moves its cut, an Array count change changes the holes, removing a cutter restores
  the target, a vent grid of 18 slots removes 18 slots;
* finishing: Smooth by Angle, Bevel, Weighted Normal stay last, and a cut made afterwards goes before them;
* apply-and-clean: a Mirror before the cuts stays applied, cutters and their Booleans go, the result is closed,
  coplanar splits merge, a slice's piece keeps its geometry;
* the agent's route: ``angelo.shape_cut`` run without drawing (EXEC_DEFAULT, as the shared typed-operation pattern
  runs it): a vent grid, and a refusal that names the overlap and leaves nothing;
* recorded, not asserted: milliseconds for a cut and its evaluation on a 1,280 and an 81,920 face target.

No provider, account or network. Writes results.json under ANGELO_QA_DIR.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import math
import time
import traceback
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import shape_cut, shape_cut_geometry as geo  # noqa: E402

report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
SCENE = bpy.context.scene


def check(name, condition, **observed):
    report["checks"][name] = bool(condition)
    if observed:
        report["observed"][name] = observed


def fresh():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in list(bpy.data.collections):
        bpy.data.collections.remove(collection)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)


def mesh_object(name, build, matrix=None):
    bm = bmesh.new()
    build(bm)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    SCENE.collection.objects.link(obj)
    if matrix is not None:
        obj.matrix_world = matrix
    return obj


def block(name="Block", size=2.0, matrix=None):
    return mesh_object(name, lambda bm: bmesh.ops.create_cube(bm, size=size), matrix)


def depsgraph():
    graph = bpy.context.evaluated_depsgraph_get()
    graph.update()
    return graph


def facts(obj):
    """Volume (world units), open edges and faces of the evaluated object, in world space."""
    graph = depsgraph()
    evaluated = obj.evaluated_get(graph)
    bm = bmesh.new()
    bm.from_mesh(evaluated.to_mesh())
    evaluated.to_mesh_clear()
    bm.transform(obj.matrix_world)
    out = {"volume": bm.calc_volume(signed=False), "open": sum(1 for e in bm.edges if len(e.link_faces) != 2),
           "faces": len(bm.faces)}
    bm.free()
    return out


def face_towards(obj, direction):
    graph = depsgraph()
    mesh = obj.evaluated_get(graph).data
    world = obj.matrix_world.to_3x3().inverted_safe().transposed()
    return max(range(len(mesh.polygons)), key=lambda i: (world @ mesh.polygons[i].normal).normalized().dot(direction))


def cut(obj, spec, direction=(0, 0, 1), face=None):
    graph = depsgraph()
    frame, centre = shape_cut.face_frame(obj, graph, face if face is not None else face_towards(obj, direction))
    spec = dict(spec, center=[centre[0] + spec.get("center", [0, 0])[0], centre[1] + spec.get("center", [0, 0])[1]])
    return shape_cut.cut(SCENE, graph, obj, frame, spec)


def near(a, b, tolerance=1e-4):
    return abs(a - b) <= tolerance * max(1.0, abs(b))


def rel(a, b, fraction=0.01):
    """Within ``fraction`` of ``b`` itself: for small removed volumes, where ``near`` would pass anything."""
    return abs(a - b) <= fraction * abs(b)


def open_box(bm):
    bmesh.ops.create_cube(bm, size=2.0)
    bm.normal_update()
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.normal.z < -0.5], context="FACES_ONLY")


def operations():
    box = {"shape": "BOX", "size": [0.5, 0.4]}
    area = 0.2
    for operation, extra, expected in (
            ("CUT", {"depth": 0.3}, 8 - area * 0.3),
            ("UNION", {"depth": 0.3}, 8 + area * 0.3),
            ("INTERSECT", {"depth": 0.3}, area * 0.3),
            ("PANEL", {"depth": 0.1, "width": 0.05}, 8 - (area - 0.4 * 0.3) * 0.1),
            ("INSET", {"depth": 0.3, "width": 0.05}, 8 - 0.4 * 0.3 * 0.3)):
        fresh()
        target = block()
        made = cut(target, dict(box, operation=operation, **extra))
        result = facts(target)
        check("%s_changes_volume_by_the_shape" % operation.lower(), near(result["volume"], expected, 1e-3),
              volume=result["volume"], expected=expected, solver=made["solver"])
        check("%s_result_is_closed" % operation.lower(), result["open"] == 0, open=result["open"])
    fresh()
    target = block()
    made = cut(target, dict(box, operation="SLICE"))
    piece = bpy.data.objects[made["piece"]]
    kept, sliced = facts(target)["volume"], facts(piece)["volume"]
    check("slice_target_and_piece_make_the_block", near(kept + sliced, 8.0, 1e-3) and near(sliced, area * 2, 1e-3),
          target=kept, piece=sliced)
    cutter = bpy.data.objects[made["cutters"][0]]
    check("cutter_is_live_wire_parented_and_unrendered",
          cutter.parent == target and cutter.display_type == "WIRE" and cutter.hide_render and
          cutter.users_collection[0].name == shape_cut.COLLECTION and made["solver"] == "MANIFOLD")


def targets():
    fresh()   # A closed shell 2 cm thick: an outer block and an inner one wound inward.
    def shell(bm):
        outer = bmesh.ops.create_cube(bm, size=2.0)["verts"]
        inner = bmesh.ops.create_cube(bm, size=1.96)["verts"]
        bmesh.ops.reverse_faces(bm, faces=list({f for v in inner for f in v.link_faces}))
        return outer
    target = mesh_object("Shell", shell)
    cut(target, {"shape": "CIRCLE", "radius": 0.2, "operation": "INSET", "depth": 0.15, "width": 0.01})
    check("inset_keeps_a_thin_shell_closed", facts(target)["open"] == 0, **facts(target))
    fresh()
    target = mesh_object("Open", open_box)
    made = cut(target, {"shape": "BOX", "size": [0.3, 0.3], "operation": "CUT", "depth": 0.2})
    check("open_target_uses_exact_and_says_why", made["solver"] == "EXACT" and "open" in made["solver_reason"],
          report=made)
    fresh()
    target = mesh_object("Pipe", lambda bm: bmesh.ops.create_cone(bm, cap_ends=True, segments=48, radius1=1.0,
                                                                   radius2=1.0, depth=3.0))
    before = facts(target)["volume"]
    cut(target, {"shape": "CIRCLE", "radius": 0.1, "operation": "CUT", "depth": 0.3}, direction=(1, 0, 0))
    after = facts(target)
    removed = before - after["volume"]   # A 0.1 m hole 0.3 m deep: 0.0094 m3, less where the wall curves away.
    check("cut_on_a_curved_face", after["open"] == 0 and 0.006 < removed < 0.0125, removed=removed, **after)
    fresh()
    turned = Matrix.LocRotScale((3, -2, 1), Matrix.Rotation(0.6, 3, (1, 1, 0)).to_quaternion(), (1.5, 0.5, 2.0))
    target = block(matrix=turned)
    before = facts(target)["volume"]
    cut(target, {"shape": "BOX", "size": [0.2, 0.2], "operation": "CUT", "depth": 0.1}, direction=(0, 0, 1))
    after = facts(target)
    check("cut_on_a_rotated_scaled_object_in_world_units", rel(before - after["volume"], 0.004),
          removed=before - after["volume"])


def editing():
    fresh()
    target = block()
    made = cut(target, {"shape": "BOX", "size": [0.4, 0.4], "operation": "CUT", "depth": 0.5})
    cutter = bpy.data.objects[made["cutters"][0]]
    cutter.location.x += 1.0   # Half the box now hangs past the edge.
    check("moving_a_cutter_moves_its_cut", rel(8 - facts(target)["volume"], 0.16 * 0.5 / 2),
          volume=facts(target)["volume"])
    shape_cut.remove_cutter(cutter)
    check("removing_a_cutter_restores_the_target", near(facts(target)["volume"], 8.0) and not target.modifiers)
    fresh()
    target = block("Panel", size=1.0)
    vent = {"shape": "BOX", "size": [0.02, 0.005], "operation": "CUT", "depth": 0.01,
            "array": {"columns": 3, "rows": 6, "spacing": [0.03, 0.01]}}
    made = cut(target, vent)
    check("vent_grid_removes_eighteen_slots", rel(1 - facts(target)["volume"], 18 * 0.02 * 0.005 * 0.01),
          removed=1 - facts(target)["volume"])
    array = bpy.data.objects[made["cutters"][0]].modifiers["Rows"]
    array.count = 4
    check("array_count_edits_the_holes", rel(1 - facts(target)["volume"], 12 * 0.02 * 0.005 * 0.01))


def finishing_and_apply():
    fresh()   # Half a block, open on the mirror plane, mirrored into a closed 2 x 1 x 1 m block whose seam merges.

    def half_block(bm):
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.translate(bm, verts=bm.verts, vec=(0.5, 0, 0))
        on_plane = [f for f in bm.faces if all(abs(v.co.x) < 1e-6 for v in f.verts)]
        bmesh.ops.delete(bm, geom=on_plane, context="FACES_ONLY")
    target = mesh_object("Half", half_block)
    mirror = target.modifiers.new("Mirror", "MIRROR")
    mirror.use_axis[0] = True
    cut(target, {"shape": "BOX", "size": [0.3, 0.3], "operation": "CUT", "depth": 0.2, "center": [0.2, 0.1]})
    names = shape_cut.finish(target, bevel_width=0.01)
    cut(target, {"shape": "CIRCLE", "radius": 0.1, "operation": "CUT", "depth": 0.2, "center": [-0.2, -0.2]})
    order = [m.type for m in target.modifiers]
    check("finishing_stays_last_and_later_cuts_go_before_it",
          order[-3:] == ["NODES", "BEVEL", "WEIGHTED_NORMAL"] and order.count("BOOLEAN") == 2, order=order,
          names=names)
    finish = shape_cut.finishing(target)
    finish["bevel"].name = "My bevel"   # Renamed by hand: finishing again still finds it rather than adding another.
    shape_cut.finish(target, bevel_width=0.02)
    check("finishing_again_finds_its_renamed_modifiers",
          [m.type for m in target.modifiers].count("BEVEL") == 1
          and near(target.modifiers["My bevel"].width, 0.02, 1e-6)   # Stored as a 32-bit float.
          and set(shape_cut.finishing(target)) == {"sharp", "bevel", "weighted"},
          order=[m.name for m in target.modifiers])
    for modifier in shape_cut.finishing(target).values():
        modifier.show_viewport = False
    before = facts(target)
    for modifier in target.modifiers:
        modifier.show_viewport = True
    applied = shape_cut.apply_and_clean(depsgraph(), target)
    for modifier in shape_cut.finishing(target).values():
        modifier.show_viewport = False
    after = facts(target)
    check("apply_keeps_the_cut_result_and_the_mirror", near(after["volume"], before["volume"], 1e-3) and
          not any(m.type in {"BOOLEAN", "MIRROR"} for m in target.modifiers) and
          not bpy.data.collections[shape_cut.COLLECTION].objects, before=before, after=after, facts=applied)
    check("apply_leaves_a_closed_tidier_mesh", applied["non_manifold_edges"] == 0 and
          applied["faces"] <= applied["faces_before"] and applied["sharp_edges"] > 0, facts=applied)
    fresh()
    target = block()
    made = cut(target, {"shape": "BOX", "size": [0.5, 0.5], "operation": "SLICE"})
    piece = bpy.data.objects[made["piece"]]
    shape_cut.apply_and_clean(depsgraph(), target)
    check("apply_keeps_the_slice_piece", near(facts(piece)["volume"], 0.5, 1e-3) and not piece.modifiers,
          piece=facts(piece))


def operator_exec():
    """angelo.shape_cut without drawing (EXEC_DEFAULT), as the agent's shape_cut operation runs it."""
    from angelo_agent import shape_cut_tool
    if not hasattr(bpy.types, "ANGELO_OT_shape_cut"):
        bpy.utils.register_class(shape_cut_tool.ANGELO_OT_shape_cut)
    fresh()
    panel = block("Panel", size=1.0)
    bpy.context.view_layer.objects.active = panel
    result = bpy.ops.angelo.shape_cut("EXEC_DEFAULT", shape="BOX", size=(0.02, 0.005), depth=0.01,
                                      towards=(0, 0, 1), columns=3, rows=6, spacing=(0.03, 0.01))
    check("exec_makes_the_vent_grid", result == {"FINISHED"} and
          rel(1 - facts(panel)["volume"], 18 * 0.02 * 0.005 * 0.01), result=sorted(result))
    fresh()
    panel = block("Panel", size=1.0)
    bpy.context.view_layer.objects.active = panel
    try:
        result = bpy.ops.angelo.shape_cut("EXEC_DEFAULT", shape="BOX", size=(0.6, 0.6), depth=0.1,
                                          towards=(0, 0, 1), columns=2, rows=1, spacing=(0.5, 0))
    except RuntimeError as error:   # bpy.ops raises with the operator's ERROR report.
        result = str(error)
    check("exec_refusal_names_the_problem_and_leaves_nothing",
          "overlap" in str(result) and not panel.modifiers and len(bpy.data.objects) == 1, result=str(result))


def timings():
    for subdivisions in (3, 6):   # 320 and 20,480 faces (20 * 4 ** (n - 1)); the key says what was measured.
        fresh()
        target = mesh_object("Ball", lambda bm: bmesh.ops.create_icosphere(bm, subdivisions=subdivisions, radius=1))
        start = time.perf_counter()
        cut(target, {"shape": "BOX", "size": [0.3, 0.3], "operation": "CUT", "depth": 0.2})
        made = time.perf_counter()
        facts(target)
        report["observed"]["timing_%d_faces" % len(target.data.polygons)] = {"faces": len(target.data.polygons),
                                                "cut_ms": round((made - start) * 1000, 2),
                                                "evaluate_ms": round((time.perf_counter() - made) * 1000, 2)}


try:
    operations()
    targets()
    editing()
    finishing_and_apply()
    operator_exec()
    timings()
    check("geometry_module_is_pure", "bpy" not in geo.__dict__)
    report["success"] = all(report["checks"].values()) and bool(report["checks"])
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    (OUT / "results.json").write_text(json.dumps(report, indent=2, default=str))
failed = sorted(name for name, passed in report["checks"].items() if not passed)
print("SHAPE_CUT failed checks:", json.dumps(failed))
assert report["success"], failed or report.get("error")
