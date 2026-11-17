# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""SubsurfModifier.gpu_subdivision_status (Blender #58191) in the compiled app, without a window.

The status says whether the viewport subdivides a Subdivision Surface modifier on the GPU, or the
first reason it does not, and is read on the original modifier after the active view layer
evaluates. Two phases in one process:

* before any GPU use, background Blender has no GPU: every modifier reads NO_GPU, or PREFERENCE_OFF
  when the preference is off (which comes first);
* after gpu.init(): GPU on a plain stack, NOT_LAST with Shade Auto Smooth's Smooth by Angle after it,
  SPLIT_NORMALS with sharpness in its input (from a modifier before it, a marked edge, mixed smooth
  and flat faces or custom normals), NOT_EVALUATED when disabled, at level 0 or hidden in Edit Mode,
  and reset when a file is read. Triangle and n-gon meshes are held-out cases.

Evaluation rewrites the status, so it is not one of a typed program's dependencies: the program
fingerprint stays the same while it changes (else every commit on such an object is STALE_DEPENDENCY).

The panel text needs a window: test_blender_gpu_subdivision_status_gui.py.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import math
from pathlib import Path
import tempfile
import traceback

import bmesh
import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.program_dependencies import _fingerprint  # noqa: E402

report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
checks, observed = report["checks"], report["observed"]
EXPECTED_ITEMS = {"NOT_EVALUATED", "GPU", "PREFERENCE_OFF", "NO_GPU", "UNSUPPORTED_GPU", "NOT_LAST",
                  "SPLIT_NORMALS"}


def evaluate():
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for ob in bpy.context.scene.objects:
        ob.update_tag(refresh={'OBJECT', 'DATA'})
    depsgraph.update()
    return depsgraph


def status(ob, name="Subdivision"):
    return ob.modifiers[name].gpu_subdivision_status


def add_object(name, make):
    bm = bmesh.new()
    make(bm)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    ob = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(ob)
    ob.modifiers.new("Subdivision", 'SUBSURF')
    return ob


def cube(bm):
    bmesh.ops.create_cube(bm, size=2.0)


def icosphere(bm):
    bmesh.ops.create_icosphere(bm, subdivisions=2, radius=1.0)


def ngon_cylinder(bm):
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=12, radius1=1.0, radius2=1.0, depth=2.0)


def cylinder(bm):
    # 11.25 degrees between sides, 90 at the caps: Smooth by Angle splits the caps from smooth sides.
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=32, radius1=1.0, radius2=1.0, depth=2.0)


def make_active(ob):
    bpy.ops.object.select_all(action='DESELECT')
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob


try:
    prefs = bpy.context.preferences.system
    prop = bpy.types.SubsurfModifier.bl_rna.properties["gpu_subdivision_status"]
    checks["property_is_read_only_enum"] = prop.type == 'ENUM' and prop.is_readonly and not prop.is_animatable
    checks["property_lists_every_status"] = {item.identifier for item in prop.enum_items} == EXPECTED_ITEMS
    checks["property_is_not_overridable"] = not prop.is_overridable

    # Phase 1: background Blender before any GPU use.
    bpy.ops.wm.read_factory_settings(use_empty=True)
    prefs.use_gpu_subdivision = True
    plain = add_object("Plain", cube)
    checks["unevaluated_reads_not_evaluated"] = status(plain) == 'NOT_EVALUATED'
    unevaluated_fingerprint = _fingerprint(plain)
    evaluate()
    import gpu
    try:
        backend = gpu.platform.backend_type_get()
    except Exception as error:  # The gpu module refuses before gpu.init(): no GPU has started.
        backend = f"not initialized ({error})"
    observed["before_gpu_init"] = {"backend": backend, "status": status(plain)}
    # Nothing in a factory-startup background Blender should have started the GPU yet.
    checks["background_without_gpu_reads_no_gpu"] = status(plain) == 'NO_GPU'
    prefs.use_gpu_subdivision = False
    evaluate()
    checks["preference_off_comes_first"] = status(plain) == 'PREFERENCE_OFF'
    checks["status_changes_leave_the_program_fingerprint"] = _fingerprint(plain) == unevaluated_fingerprint
    prefs.use_gpu_subdivision = True

    # Phase 2: with a GPU.
    gpu.init()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    prefs.use_gpu_subdivision = True
    plain = add_object("Plain", cube)
    triangles = add_object("Triangles", icosphere)
    ngons = add_object("Ngons", ngon_cylinder)
    evaluate()
    observed["with_gpu"] = {ob.name: status(ob) for ob in (plain, triangles, ngons)}
    checks["plain_stack_reads_gpu"] = all(status(ob) == 'GPU' for ob in (plain, triangles, ngons))
    checks["evaluated_modifier_agrees"] = (
        plain.evaluated_get(bpy.context.evaluated_depsgraph_get()).modifiers["Subdivision"].gpu_subdivision_status
        == 'GPU')

    # Shade Auto Smooth puts Smooth by Angle after Subdivision (pinned last): the common hard-surface setup.
    make_active(plain)
    bpy.ops.object.shade_auto_smooth()
    smooth_by_angle = plain.modifiers[-1]
    evaluate()
    observed["auto_smooth_after"] = {"stack": [m.name for m in plain.modifiers], "status": status(plain)}
    checks["smooth_by_angle_after_reads_not_last"] = status(plain) == 'NOT_LAST'
    # A later modifier disabled in the viewport does not count.
    smooth_by_angle.show_viewport = False
    evaluate()
    checks["disabled_later_modifier_reads_gpu"] = status(plain) == 'GPU'
    smooth_by_angle.show_viewport = True
    # Moved before Subdivision, its sharp edges reach Subdivision's input.
    smooth_by_angle.use_pin_to_last = False
    bpy.ops.object.modifier_move_to_index(modifier=smooth_by_angle.name, index=0)
    evaluate()
    observed["auto_smooth_before"] = {"stack": [m.name for m in plain.modifiers], "status": status(plain)}
    # Every edge of a cube is past the angle, so its normals are per face, which GPU subdivision
    # handles: only a mix of sharp and smooth (corner normals) splits them.
    checks["all_sharp_cube_before_reads_gpu"] = status(plain) == 'GPU'
    plain.modifiers.remove(smooth_by_angle)
    # A cylinder's caps are sharp and its sides smooth: corner normals in Subdivision's input.
    split = add_object("Cylinder", cylinder)
    make_active(split)
    bpy.ops.object.shade_auto_smooth()
    split_smooth = split.modifiers[-1]
    split_smooth.use_pin_to_last = False
    bpy.ops.object.modifier_move_to_index(modifier=split_smooth.name, index=0)
    evaluate()
    observed["auto_smooth_before_cylinder"] = {"stack": [m.name for m in split.modifiers],
                                               "status": status(split)}
    checks["sharpness_from_an_earlier_modifier_reads_split_normals"] = status(split) == 'SPLIT_NORMALS'
    # The original mesh has no sharp edges: the reason comes from the modifier's input, not the mesh.
    checks["original_mesh_has_no_sharp_edges"] = "sharp_edge" not in split.data.attributes
    split.modifiers.remove(split_smooth)
    evaluate()
    checks["removing_it_reads_gpu_again"] = status(split) == 'GPU' and status(plain) == 'GPU'

    # Sharpness in the mesh itself, three ways, on the held-out meshes. A sharp edge splits only
    # between smooth faces (a mesh of flat faces has per-face normals already).
    for polygon in triangles.data.polygons:
        polygon.use_smooth = True
    triangles.data.edges[0].use_edge_sharp = True
    triangles.data.update()
    for index, polygon in enumerate(ngons.data.polygons):
        polygon.use_smooth = index % 2 == 0
    ngons.data.update()
    custom = add_object("Custom", cube)
    custom.data.normals_split_custom_set_from_vertices([v.co.normalized() for v in custom.data.vertices])
    evaluate()
    observed["split_normals"] = {ob.name: status(ob) for ob in (triangles, ngons, custom)}
    checks["marked_sharp_edge_reads_split_normals"] = status(triangles) == 'SPLIT_NORMALS'
    checks["mixed_smooth_and_flat_reads_split_normals"] = status(ngons) == 'SPLIT_NORMALS'
    checks["custom_normals_read_split_normals"] = status(custom) == 'SPLIT_NORMALS'

    # Not evaluated: disabled, level 0, or hidden in Edit Mode.
    plain.modifiers["Subdivision"].show_viewport = False
    evaluate()
    checks["disabled_reads_not_evaluated"] = status(plain) == 'NOT_EVALUATED'
    plain.modifiers["Subdivision"].show_viewport = True
    plain.modifiers["Subdivision"].levels = 0
    evaluate()
    checks["level_zero_reads_not_evaluated"] = status(plain) == 'NOT_EVALUATED'
    plain.modifiers["Subdivision"].levels = 1
    evaluate()
    checks["level_one_reads_gpu"] = status(plain) == 'GPU'
    make_active(plain)
    plain.modifiers["Subdivision"].show_in_editmode = False
    bpy.ops.object.mode_set(mode='EDIT')
    evaluate()
    observed["edit_mode_hidden"] = status(plain)
    checks["hidden_in_edit_mode_reads_not_evaluated"] = status(plain) == 'NOT_EVALUATED'
    plain.modifiers["Subdivision"].show_in_editmode = True
    evaluate()
    observed["edit_mode_shown"] = status(plain)
    checks["shown_in_edit_mode_reads_gpu"] = status(plain) == 'GPU'
    bpy.ops.object.mode_set(mode='OBJECT')
    evaluate()

    # The preference still comes first with a GPU, and its own update re-evaluates every Subdivision
    # modifier, including ones that are not last (no tagging by the test).
    not_last = add_object("NotLast", cube)
    not_last.modifiers.new("Weld", 'WELD')
    evaluate()
    checks["not_last_before_the_preference"] = status(not_last) == 'NOT_LAST'
    prefs.use_gpu_subdivision = False
    bpy.context.evaluated_depsgraph_get().update()
    checks["preference_update_reaches_a_modifier_that_is_not_last"] = status(not_last) == 'PREFERENCE_OFF'
    evaluate()
    checks["preference_off_reads_preference_off"] = all(
        status(ob) == 'PREFERENCE_OFF' for ob in (plain, triangles, ngons, custom))
    prefs.use_gpu_subdivision = True
    evaluate()

    # A file keeps no status: reading resets it (an appended, never evaluated copy shows what was read),
    # and evaluation sets it again.
    checks["saved_while_gpu"] = status(plain) == 'GPU'
    path = Path(tempfile.mkdtemp(prefix="angelo-gpu-subdiv-")) / "status.blend"
    # As a copy: the saved file must not become the current one, which cannot be appended from.
    bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
    with bpy.data.libraries.load(str(path), link=False) as (source, target):
        target.objects = ["Plain"]
    appended = target.objects[0]
    observed["appended_unevaluated"] = status(appended)
    checks["reading_a_file_resets_it"] = status(appended) == 'NOT_EVALUATED'
    bpy.ops.wm.open_mainfile(filepath=str(path))
    evaluate()
    checks["evaluation_sets_it_again"] = status(bpy.data.objects["Plain"]) == 'GPU'
    report["success"] = all(checks.values())
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
failed = sorted(name for name, passed in checks.items() if not passed)
print("GPU_SUBDIVISION_STATUS_BACKGROUND failed checks:", json.dumps(failed))
assert report["success"], failed
