# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Retopology benchmark: Angelo's Retopology against QuadriFlow and voxel remesh on the same input, the same face
target, the same machine and the same grading (doc/angelo/retopology.rst, "Measurement"). One process per asset and
method, in the built app, on a quiet machine:

    Angelo --background --factory-startup --python bench_retopology.py -- OUT.jsonl METHOD TARGET ASSET

METHOD is ANGELO, QUADRIFLOW or VOXEL. Every mesh object in ASSET (.glb/.gltf/.fbx/.obj/.ply/.stl/.blend) is joined
into one source, as a person retopologizes a whole imported model; transforms are applied. Each run appends one JSON
line, refusals and crashes included (a run that never appends is a crash, counted by run.zsh).

Settings are each method's documented defaults plus what makes the comparison fair, the same for every asset:
- ANGELO: ``object.retopology`` defaults, ``target_faces``, no symmetry, a new object.
- QUADRIFLOW: ``object.quadriflow_remesh`` with ``target_faces``, Preserve Sharp and Preserve Mesh Boundary on (its
  feature options, so it is not handicapped against Angelo's), no symmetry, fixed seed 0.
- VOXEL: ``object.voxel_remesh`` with the voxel size that lands nearest the target: a first size from area / target,
  then one correction by the measured face ratio (both runs' time is reported; the second is graded).

Grading is identical for all three: the game-ready check (``angelo_game_ready_check``) with the result as the LOD of
the source (two-sided Hausdorff over the bounds diagonal, silhouette IoU, normal deviation, open edges), topology facts
of the result (quad ratio, valences, irregular vertices, face-count error) and feature recall (the share of the
source's sharp-edge midpoints, dihedral over 35 degrees, within a quarter of the target edge length of a result edge).
Nothing here is a threshold; every run is reported.
"""

import json
import math
import os
import platform
import sys
import time
import traceback
from pathlib import Path

import bpy
import bmesh
from mathutils import Vector
from mathutils.kdtree import KDTree

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

SHARP_DEGREES = 35.0
RECALL_OF_EDGE = 0.25


def footprint_peak():
    """macOS lifetime peak physical footprint in bytes (0 elsewhere): rusage_info_v4's ri_lifetime_max_phys_footprint."""
    if platform.system() != "Darwin":
        return 0
    import ctypes
    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        buffer = (ctypes.c_uint64 * 64)()
        if libc.proc_pid_rusage(os.getpid(), 4, ctypes.byref(buffer)) != 0:
            return 0
        return int(buffer[2 + 28])
    except OSError:
        return 0


def load_source(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    suffix = path.suffix.lower()
    if suffix in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path))
    elif suffix == ".obj":
        bpy.ops.wm.obj_import(filepath=str(path))
    elif suffix == ".ply":
        bpy.ops.wm.ply_import(filepath=str(path))
    elif suffix == ".stl":
        bpy.ops.wm.stl_import(filepath=str(path))
    elif suffix == ".blend":
        with bpy.data.libraries.load(str(path)) as (source, target):
            target.objects = list(source.objects)
        for obj in target.objects:
            if obj is not None:
                bpy.context.scene.collection.objects.link(obj)
    else:
        raise ValueError("unsupported asset: " + path.name)
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise ValueError("no mesh objects")
    for obj in bpy.context.scene.objects:
        obj.select_set(obj.type == "MESH")
    bpy.context.view_layer.objects.active = meshes[0]
    for obj in meshes:   # Armatures and parents go; the mesh keeps where it was.
        matrix = obj.matrix_world.copy()
        obj.parent = None
        obj.matrix_world = matrix
        for modifier in list(obj.modifiers):
            if modifier.type == "ARMATURE":
                obj.modifiers.remove(modifier)
    if len(meshes) > 1:
        bpy.ops.object.join()
    source = bpy.context.view_layer.objects.active
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    source.name = "Source"
    return source


def duplicate(obj, name):
    copy = obj.copy()
    copy.data = obj.data.copy()
    copy.name = name
    bpy.context.scene.collection.objects.link(copy)
    return copy


def activate(obj):
    for other in bpy.context.scene.objects:
        other.select_set(other == obj)
    bpy.context.view_layer.objects.active = obj


def surface_area(obj):
    return sum(p.area for p in obj.data.polygons)


def run_method(method, source, target):
    """The result object, the timings and the method's own notes."""
    notes, runs_ms = {}, []
    if method == "ANGELO":
        activate(source)
        started = time.perf_counter()
        result = bpy.ops.object.retopology(target_faces=target, use_mesh_symmetry=False, keep_original=True)
        runs_ms.append((time.perf_counter() - started) * 1000.0)
        if "FINISHED" not in result:
            return None, runs_ms, notes
        out = bpy.context.view_layer.objects.active
        notes["facts"] = out.get("_angelo_retopology").to_dict() if out.get("_angelo_retopology") else None
        return out, runs_ms, notes
    if method == "QUADRIFLOW":
        out = duplicate(source, "Result")
        activate(out)
        started = time.perf_counter()
        result = bpy.ops.object.quadriflow_remesh(target_faces=target, use_preserve_sharp=True,
                                                  use_preserve_boundary=True, use_mesh_symmetry=False, seed=0,
                                                  mode="FACES")
        runs_ms.append((time.perf_counter() - started) * 1000.0)
        if "FINISHED" not in result or len(out.data.polygons) == 0:
            return None, runs_ms, notes
        return out, runs_ms, notes
    if method == "VOXEL":
        area = surface_area(source)
        size = math.sqrt(area / target)
        out = None
        for attempt in range(2):
            if out is not None:
                bpy.data.objects.remove(out)
            out = duplicate(source, "Result")
            activate(out)
            out.data.remesh_voxel_size = size
            started = time.perf_counter()
            result = bpy.ops.object.voxel_remesh()
            runs_ms.append((time.perf_counter() - started) * 1000.0)
            faces = len(out.data.polygons)
            notes.setdefault("voxel_sizes", []).append(size)
            notes.setdefault("faces_per_attempt", []).append(faces)
            if "FINISHED" not in result or faces == 0:
                return None, runs_ms, notes
            size *= math.sqrt(faces / target)
        return out, runs_ms, notes
    raise ValueError("unknown method " + method)


def topology(obj, target):
    mesh = obj.data
    faces = len(mesh.polygons)
    quads = sum(1 for p in mesh.polygons if p.loop_total == 4)
    bm = bmesh.new()
    bm.from_mesh(mesh)
    valence = {"3-": 0, "4": 0, "5": 0, "6+": 0}
    boundary = 0
    for v in bm.verts:
        if not v.link_edges:
            continue
        if any(e.is_boundary for e in v.link_edges):
            boundary += 1
            continue
        k = len(v.link_edges)
        valence["3-" if k <= 3 else ("4" if k == 4 else ("5" if k == 5 else "6+"))] += 1
    non_manifold = sum(1 for e in bm.edges if len(e.link_faces) > 2)
    bm.free()
    interior = sum(valence.values())
    return {"faces": faces, "quad_ratio": quads / faces if faces else 0.0, "valence": valence,
            "irregular": interior - valence["4"], "irregular_of_interior": (interior - valence["4"]) / interior
            if interior else 0.0, "boundary_vertices": boundary, "non_manifold_edges": non_manifold,
            "face_count_error": faces / target - 1.0}


def feature_recall(source, result, target_edge):
    """Share of the source's sharp-edge midpoints within RECALL_OF_EDGE x target edge of a result edge."""
    bm = bmesh.new()
    bm.from_mesh(source.data)
    limit = math.radians(SHARP_DEGREES)
    mids = [(e.verts[0].co + e.verts[1].co) * 0.5 for e in bm.edges
            if len(e.link_faces) == 2 and e.calc_face_angle(0.0) > limit]
    bm.free()
    if not mids:
        return None
    step = 0.1 * target_edge
    samples = []
    mesh = result.data
    for edge in mesh.edges:
        a, b = mesh.vertices[edge.vertices[0]].co, mesh.vertices[edge.vertices[1]].co
        count = max(1, int((b - a).length / step))
        samples.extend(a.lerp(b, i / count) for i in range(count + 1))
    tree = KDTree(len(samples))
    for i, co in enumerate(samples):
        tree.insert(co, i)
    tree.balance()
    radius = RECALL_OF_EDGE * target_edge
    hit = sum(1 for m in mids if tree.find(m)[2] <= radius + step)
    return {"sharp_edges": len(mids), "recall": hit / len(mids)}


def borders(obj):
    """Edges used by one face (welded within 1e-6 of the bounds diagonal, as the cleaned input is) and their total
    length: the same reference for the source and every method's result, so a result's border length well above its
    source's counts holes the method made, and one below counts holes it closed."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)
    if bm.verts:
        extent = [max(v.co[i] for v in bm.verts) - min(v.co[i] for v in bm.verts) for i in range(3)]
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6 * math.sqrt(sum(x * x for x in extent)))
    edges = [e for e in bm.edges if len(e.link_faces) == 1]
    result = {"edges": len(edges), "length": sum(e.calc_length() for e in edges)}
    bm.free()
    return result


def grade(source, result):
    from angelo_agent import game_ready_host, scene_queries
    from angelo_agent.game_ready_spec import CHECK, validate_game_ready
    windows = bpy.context.window_manager.windows
    # A background process has no window: the check needs only the scene and view layer it reads from.
    window = windows[0] if windows else type("Window", (), {"scene": bpy.context.scene,
                                                             "view_layer": bpy.context.view_layer})()
    args = validate_game_ready(CHECK, {"object": scene_queries.object_handle(source),
                                       "lods": [scene_queries.object_handle(result)]})
    facts = game_ready_host.check(window, args)
    lod = facts["lods"][0]
    return {"hausdorff_max_of_bounds": lod["hausdorff"]["max_of_bounds"],
            "hausdorff_mean_m": lod["hausdorff"]["mean_m"],
            "source_to_result_max_m": lod["hausdorff"].get("source_to_lod_max_m"),
            "iou_min": lod.get("silhouette", {}).get("iou_min"),
            "normals_p95_deg": lod.get("normals", {}).get("p95"),
            "open_edges": lod["topology"]["open_edges"]}


def main():
    out_path, method, target, asset = sys.argv[sys.argv.index("--") + 1:][:4]
    target, asset = int(target), Path(asset)
    record = {"asset": asset.name, "method": method, "target": target, "app": bpy.app.version_string,
              "build": bpy.app.build_hash.decode() if isinstance(bpy.app.build_hash, bytes) else bpy.app.build_hash}
    footprint_before = footprint_peak()
    try:
        source = load_source(asset)
        record["source"] = {"faces": len(source.data.polygons), "vertices": len(source.data.vertices),
                            "area": surface_area(source), "borders": borders(source)}
        result, runs_ms, notes = run_method(method, source, target)
        record["runs_ms"] = [round(ms, 1) for ms in runs_ms]
        record["notes"] = notes
        record["peak_growth_mb"] = round((footprint_peak() - footprint_before) / 2 ** 20, 1)
        if result is None:
            record["status"] = "refused_or_failed"
        else:
            record["status"] = "ok"
            record["topology"] = topology(result, target)
            record["borders"] = borders(result)
            target_edge = math.sqrt(record["source"]["area"] / target)
            record["feature_recall"] = feature_recall(source, result, target_edge)
            record["grade"] = grade(source, result)
    except Exception as error:   # Recorded, never hidden: a failure on any side is a failure.
        record["status"] = "error"
        record["error"] = "".join(traceback.format_exception_only(type(error), error)).strip()[:500]
    with open(out_path, "a") as handle:
        handle.write(json.dumps(record) + "\n")


main()
