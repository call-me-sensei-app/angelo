# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Voxel remesh guard (Blender #77868) in the compiled app, without a window.

* The estimate against what the remesh really makes, on shapes the constants were not fitted on
  (oblique faces, a sphere, a subdivided head, an open sheet, overlapping parts, adaptivity, saddle
  quads, crossed quads, a star prism with concave non-planar caps): never low by more than 10%, and
  high only where parts overlap. Its area is the remesher's own triangles' (``loop_triangles``),
  each counted positive: a face's vector area cancels across a non-planar or crossed face.
* Far too small a size is refused at once, from a script, in Sculpt Mode and in the modifier, with
  the estimate and a size that fits; nothing changes (an offered size is not written), nothing hangs.
* A non-finite position is refused, not guessed.
* The offered size estimates as fitting; an accepted size becomes the mesh's after the remesh.
* A new Remesh modifier keeps Blender's 0.1 on mid-sized objects and follows the surface on large
  and small ones. Zero keeps Blender's own message.

The dialogs need a window: test_blender_voxel_remesh_guard_gui.py.
"""

import json
import math
import os
from pathlib import Path
import re
import sys
import time
import traceback

import bmesh
import bpy
from mathutils import Euler, Matrix

assert bpy.app.background and "--factory-startup" in sys.argv
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
checks, observed = report["checks"], report["observed"]
REFUSAL = re.compile(r"Voxel size \S+ would make about [\d,]+ vertices and need about .+ of memory; only .+ is "
                     r"free\. Use a voxel size of (\S+) or larger")
TARGET = 200_000  # Vertices per held-out remesh: big enough to be exact, small enough to be quick.


def make_active(obj):
    """The operator remeshes the active object."""
    for other in bpy.context.view_layer.objects:
        other.select_set(other == obj)
    bpy.context.view_layer.objects.active = obj


def link(name, bm):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    make_active(obj)
    return obj


def cube(name, size, matrix=Matrix()):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1, matrix=matrix @ Matrix.Diagonal((*size, 1)) if isinstance(size, tuple)
                          else matrix @ Matrix.Scale(size, 4))
    return link(name, bm)


def held_out_shapes():
    """Shapes unlike the measured ones, built without operators that depend on a window."""
    shapes = {}
    shapes["rotated_cube"] = cube("Rotated", 2, Euler((.4, .7, .2)).to_matrix().to_4x4())
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=24, radius=1.3)
    shapes["sphere"] = link("Sphere", bm)
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=24, radius=1.3)
    shapes["sphere_adaptive"] = link("Sphere adaptive", bm)
    bm = bmesh.new()
    bmesh.ops.create_monkey(bm)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=1, use_grid_fill=True)
    shapes["monkey"] = link("Monkey", bm)
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=30, y_segments=30, size=1.5)
    shapes["open_sheet"] = link("Sheet", bm)
    bm = bmesh.new()  # Two overlapping boxes: the estimate counts the hidden inner faces too.
    bmesh.ops.create_cube(bm, size=2)
    bmesh.ops.create_cube(bm, size=2, matrix=Matrix.Translation((1, .5, .3)))
    shapes["overlapping_parts"] = link("Overlap", bm)
    shapes["saddle_quads"] = saddle_sheet()
    shapes["crossed_quads"] = crossed_tiles()
    shapes["star_prism"] = star_prism()
    return shapes


def saddle_sheet(cells=12, side=.25, height=.25):
    """A sheet of non-planar quads: corners alternate up and down, so each quad's vector area is only
    its flat shadow while its two triangles stand steeply (about 5x the shadow here)."""
    bm = bmesh.new()
    rows = [[bm.verts.new((i * side, j * side, height * (-1) ** (i + j))) for j in range(cells + 1)]
            for i in range(cells + 1)]
    for i in range(cells):
        for j in range(cells):
            bm.faces.new((rows[i][j], rows[i + 1][j], rows[i + 1][j + 1], rows[i][j + 1]))
    return link("Saddles", bm)


def crossed_tiles(cells=8, side=.25):
    """Crossed (bow-tie) quads, one per cell: each face's vector area is zero."""
    bm = bmesh.new()
    for i in range(cells):
        for j in range(cells):
            x, y = i * side, j * side
            bm.faces.new([bm.verts.new(co) for co in ((x, y, 0), (x + side, y + side, 0), (x + side, y, 0),
                                                      (x, y + side, 0))])
    return link("Crossed", bm)


def star_prism(points=12, outer=1.2, inner=.5, height=1.0, crown=.3):
    """A closed prism on a concave star: flat bottom, a top whose points rise and notches dip."""
    bm = bmesh.new()
    bottom, top = [], []
    for k in range(2 * points):
        radius, angle = (outer if k % 2 == 0 else inner), math.pi * k / points
        x, y = radius * math.cos(angle), radius * math.sin(angle)
        bottom.append(bm.verts.new((x, y, 0)))
        top.append(bm.verts.new((x, y, height + (crown if k % 2 == 0 else -crown))))
    bm.faces.new(bottom[::-1])
    bm.faces.new(top)
    for k in range(2 * points):
        n = (k + 1) % (2 * points)
        bm.faces.new((bottom[k], bottom[n], top[n], top[k]))
    return link("Star", bm)


def triangles_area(mesh):
    """What the estimate must count, from Python: Σ |Ax| + |Ay| + |Az| over the loop triangles (the
    remesher's), doubled when an edge borders one face or more than two."""
    mesh.calc_loop_triangles()
    area = 0.0
    for triangle in mesh.loop_triangles:
        a, b, c = (mesh.vertices[v].co for v in triangle.vertices)
        cross = (b - a).cross(c - a)
        area += .5 * (abs(cross.x) + abs(cross.y) + abs(cross.z))
    faces_per_edge = {}
    for polygon in mesh.polygons:
        for key in polygon.edge_keys:
            faces_per_edge[key] = faces_per_edge.get(key, 0) + 1
    return area * (2 if any(count != 2 for count in faces_per_edge.values()) else 1)


def remesh(**properties):
    """Run the operator as a script does; returns (result, error text, seconds)."""
    started = time.monotonic()
    try:
        result = bpy.ops.object.voxel_remesh(**properties)
        return sorted(result), "", time.monotonic() - started
    except RuntimeError as error:
        return ["CANCELLED"], str(error), time.monotonic() - started


def estimate(obj, size, **kwargs):
    vertices, peak, free, kind, suggested = obj.data.angelo_voxel_remesh_estimate(voxel_size=size, **kwargs)
    return {"vertices": vertices, "peak_bytes": peak, "free_bytes": free, "size": kind, "suggested": suggested}


try:
    bpy.ops.wm.read_factory_settings(use_empty=True)

    # Estimate against reality, held out.
    ratios, areas = {}, {}
    for name, obj in held_out_shapes().items():
        area = triangles_area(obj.data)
        estimated_area = estimate(obj, 1.0)["vertices"]  # = the projected area at size 1
        areas[name] = {"triangles": round(area, 4), "estimate": round(estimated_area, 4)}
        size = math.sqrt(area / TARGET)
        if name == "sphere_adaptive":
            obj.data.remesh_voxel_adaptivity = .5  # Adaptivity only lowers the count.
        predicted = estimate(obj, size)["vertices"]
        obj.data.remesh_voxel_size = size
        make_active(obj)  # Every shape is made before the first remesh; each remeshes itself.
        result, error, seconds = remesh()
        actual = len(obj.data.vertices)
        ratios[name] = {"estimate": round(predicted), "actual": actual, "actual_over_estimate":
                        round(actual / predicted, 3) if predicted > 0 else math.inf, "result": result}
    observed["estimate_vs_actual"] = ratios
    observed["estimate_area_vs_triangles"] = areas
    checks["estimate_area_is_the_remeshers_triangles"] = all(
        abs(row["estimate"] - row["triangles"]) <= 1e-4 * row["triangles"] for row in areas.values())
    checks["estimate_is_never_low_by_more_than_10_percent"] = all(
        row["result"] == ["FINISHED"] and row["actual_over_estimate"] <= 1.10 for row in ratios.values())
    checks["estimate_is_close_where_nothing_overlaps"] = all(
        .85 <= ratios[name]["actual_over_estimate"] for name in ("rotated_cube", "sphere"))

    # Refused from a script: 2 m cube at 0.00001 would be ~240 billion vertices.
    obj = cube("Tiny voxels", 2)
    obj.data.remesh_voxel_size = .1
    result, error, seconds = remesh(voxel_size=.00001)
    observed["script_refusal"] = {"result": result, "error": error, "seconds": round(seconds, 3)}
    match = REFUSAL.search(error)
    checks["script_refused_with_the_estimate"] = result == ["CANCELLED"] and match is not None
    checks["script_refusal_is_immediate"] = seconds < 2.0
    checks["refusal_writes_nothing"] = len(obj.data.vertices) == 8 and abs(obj.data.remesh_voxel_size - .1) < 1e-7
    suggested = float(match.group(1)) if match else 0.0
    observed["suggested"] = suggested
    checks["offered_size_fits"] = suggested > 0 and estimate(obj, suggested)["size"] == 'FITS'

    # An accepted size (the dialog's) runs and becomes the mesh's.
    accepted = math.sqrt(24 / 500_000)
    result, error, seconds = remesh(voxel_size=accepted)
    observed["accepted_run"] = {"result": result, "error": error, "vertices": len(obj.data.vertices)}
    checks["accepted_size_runs_and_becomes_the_meshs"] = (result == ["FINISHED"]
                                                          and abs(obj.data.remesh_voxel_size - accepted) < 1e-7
                                                          and abs(len(obj.data.vertices) - 500_000) < 25_000)

    # Sculpt Mode reaches the same exec (its Ctrl+R invokes; the dialogs are the GUI suite's).
    obj = cube("Sculpt", 2)
    bpy.ops.object.mode_set(mode='SCULPT')
    obj.data.remesh_voxel_size = .00001
    result, error, seconds = remesh()
    observed["sculpt_refusal"] = {"result": result, "error": error, "mode": obj.mode}
    checks["sculpt_mode_refused_and_kept"] = (result == ["CANCELLED"] and REFUSAL.search(error) is not None
                                              and obj.mode == 'SCULPT' and len(obj.data.vertices) == 8)
    bpy.ops.object.mode_set(mode='OBJECT')

    # A non-finite position cannot be estimated: refused, not guessed.
    obj = cube("Not finite", 2)
    obj.data.vertices[0].co.x = float("nan")
    obj.data.remesh_voxel_size = .1
    result, error, seconds = remesh()
    observed["non_finite"] = {"result": result, "error": error, "size": estimate(obj, .1)["size"]}
    checks["non_finite_positions_are_refused"] = result == ["CANCELLED"] and estimate(obj, .1)["size"] == 'TOO_LARGE'

    # A new modifier: Blender's 0.1 in the middle, the surface's size at the extremes.
    sizes = {}
    for name, size in (("Default cube", 2), ("Beam", (20, .5, .5)), ("Building", 200), ("Bead", .02)):
        obj = cube(name, size)
        sizes[name] = {"modifier": obj.modifiers.new("Remesh", 'REMESH').voxel_size,
                       "rna": obj.data.angelo_voxel_remesh_default_size()}
    observed["new_modifier_voxel_size"] = sizes
    checks["new_modifier_size_matches_the_api"] = all(abs(row["modifier"] - row["rna"]) < 1e-9 for row in sizes.values())
    checks["mid_sized_objects_keep_blenders_size"] = (abs(sizes["Default cube"]["modifier"] - .1) < 1e-6
                                                      and abs(sizes["Beam"]["modifier"] - .1) < 1e-6)
    building, bead = bpy.data.objects["Building"], bpy.data.objects["Bead"]
    checks["large_objects_start_near_a_million_vertices"] = (
        800_000 <= estimate(building, sizes["Building"]["modifier"], use_mesh_settings=False)["vertices"] <= 1_000_000)
    checks["small_objects_start_near_the_default_cubes_count"] = (
        1_900 <= estimate(bead, sizes["Bead"]["modifier"], use_mesh_settings=False)["vertices"] <= 2_400)

    # The modifier refuses at once and passes the mesh through; a fitting size runs again.
    modifier = building.modifiers["Remesh"]
    modifier.mode = 'VOXEL'
    modifier.voxel_size = .0001  # 200 m cube: ~2.4e13 vertices
    started = time.monotonic()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    evaluated = building.evaluated_get(depsgraph).data
    seconds = time.monotonic() - started
    observed["modifier_refusal"] = {"vertices": len(evaluated.vertices), "seconds": round(seconds, 3)}
    checks["modifier_refuses_and_passes_the_mesh_through"] = len(evaluated.vertices) == 8 and seconds < 2.0
    modifier.voxel_size = 10
    depsgraph.update()
    checks["modifier_runs_again_at_a_fitting_size"] = len(building.evaluated_get(depsgraph).data.vertices) > 8

    # Zero keeps Blender's own message.
    obj = cube("Zero", 2)
    obj.data.remesh_voxel_size = 0
    result, error, seconds = remesh()
    checks["zero_keeps_blenders_message"] = result == ["CANCELLED"] and "voxel size of 0.0" in error
    report["success"] = all(checks.values())
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
failed = sorted(name for name, passed in checks.items() if not passed)
print("VOXEL_REMESH_GUARD failed checks:", json.dumps(failed))
assert report["success"], failed
