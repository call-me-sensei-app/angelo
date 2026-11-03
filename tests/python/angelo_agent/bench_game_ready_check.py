# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The game-ready check's benchmark: quality and time of meshoptimizer against Blender's Decimate on the same inputs,
and the check's own cost by phase. Run alone on a quiet machine, in the built app:

    Angelo --background --factory-startup --python bench_game_ready_check.py -- OUT.json ASSET [ASSET...]

ASSET is a .glb/.gltf, .fbx, .obj or .blend file; every mesh object in it with at least ``MIN_TRIS`` triangles is a
case (each read as the check reads it). ``--synthetic`` adds the development meshes (a subdivided Suzanne at 1M
triangles, a UV sphere): those were used while building the check, the asset files are held out.

For each case: ``REPS`` repetitions in ABAB order (A = meshopt weld and reduce, B = Decimate, alternating which runs
first), timing each reduction at each ratio, then one full ``angelo_game_ready_check`` call whose facts and
per-phase times are recorded. Nothing here is a threshold: the report lists every case, its facts and the spread.
"""

import json
import statistics
import sys
import time
import traceback
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from _bpy import _angelo_reduce as R  # noqa: E402
from angelo_agent import game_ready_host, game_ready_mesh as M, scene_queries  # noqa: E402
from angelo_agent.game_ready_spec import CHECK, validate_game_ready  # noqa: E402

RATIOS = (0.5, 0.25, 0.1)
REPS = 4
MIN_TRIS = 500
CASE_BUDGET_S = 240   # Past this, a case's remaining repetitions are skipped (recorded, never silently).


def import_asset(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    suffix = path.suffix.lower()
    if suffix in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path))
    elif suffix == ".obj":
        bpy.ops.wm.obj_import(filepath=str(path))
    elif suffix == ".blend":
        with bpy.data.libraries.load(str(path)) as (source, target):
            target.objects = list(source.objects)
        for obj in target.objects:
            if obj is not None:
                bpy.context.scene.collection.objects.link(obj)
    else:
        raise ValueError("unsupported asset: " + path.name)
    bpy.context.view_layer.update()
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def synthetic():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_monkey_add()
    monkey = bpy.context.object
    monkey.name = "Suzanne_subdiv5"
    modifier = monkey.modifiers.new("Subdivision", "SUBSURF")
    modifier.levels = 5
    bpy.ops.object.shade_smooth()
    bpy.ops.object.modifier_apply(modifier="Subdivision")
    bpy.ops.mesh.primitive_uv_sphere_add(segments=128, ring_count=64, location=(4, 0, 0))
    bpy.context.object.name = "UVSphere_128"
    bpy.ops.object.shade_smooth()
    return [monkey, bpy.context.object]


def spread(values):
    values = sorted(values)
    return {"median": round(statistics.median(values), 2), "min": round(values[0], 2), "max": round(values[-1], 2),
            "n": len(values)}


def time_reductions(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    read = M.read(obj, depsgraph, 1.0, R)
    times = {"meshopt_weld": [], "decimate": {r: [] for r in RATIOS}, "meshopt": {r: [] for r in RATIOS}}
    started_case = time.perf_counter()
    completed = 0
    try:
        for rep in range(REPS):
            if rep >= 2 and time.perf_counter() - started_case > CASE_BUDGET_S:
                break   # One full A and B pair at least; the cut is reported as reps_completed.
            order = ("A", "B") if rep % 2 == 0 else ("B", "A")
            for arm in order:
                if arm == "A":
                    started = time.perf_counter()
                    reducer = M.Reducer(read, R, 1.0, 0.5, 1.0)
                    times["meshopt_weld"].append((time.perf_counter() - started) * 1000.0)
                    if reducer.refusal():
                        continue
                    for ratio in RATIOS:
                        times["meshopt"][ratio].append(reducer.simplify(ratio, None, False)[2])
                else:
                    for ratio in RATIOS:
                        times["decimate"][ratio].append(M.decimate(read, ratio, R, 1.0)[1])
            completed += 1
    finally:
        read.release()
    return {"reps_completed": completed, "meshopt_weld_ms": spread(times["meshopt_weld"]),
            "meshopt_ms": {str(r): spread(v) for r, v in times["meshopt"].items() if v},
            "decimate_ms": {str(r): spread(v) for r, v in times["decimate"].items()}}


def modifier_against_native(obj, ratio=0.1):
    """Blender's Decimate modifier (Collapse, triangulated) evaluated on the object, against _angelo_reduce.decimate
    on the same evaluated mesh: the same algorithm, so the times should match; a gap would be the call's own."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    modifier = obj.modifiers.new("BenchDecimate", "DECIMATE")
    modifier.ratio, modifier.use_collapse_triangulate = ratio, True
    started = time.perf_counter()
    depsgraph.update()
    evaluated = obj.evaluated_get(depsgraph)
    faces = len(evaluated.data.polygons)
    modifier_ms = (time.perf_counter() - started) * 1000.0
    obj.modifiers.remove(modifier)
    depsgraph.update()
    read = M.read(obj, depsgraph, 1.0, R)
    try:
        native = M.decimate(read, ratio, R, 1.0)
    finally:
        read.release()
    return {"ratio": ratio, "modifier_ms": round(modifier_ms, 1), "native_ms": round(native[1], 1),
            "modifier_faces": faces, "native_tris": native[0].triangle_count}


def facts(obj):
    window = bpy.context.window_manager.windows[0]
    args = validate_game_ready(CHECK, {"object": scene_queries.object_handle(obj),
                                       "simplify": {"ratios": list(RATIOS)}})
    started = time.perf_counter()
    result = game_ready_host.execute(window, CHECK, args)
    return result, (time.perf_counter() - started) * 1000.0


def summarize(result):
    rows = []
    for lod in result["lods"]:
        rows.append({k: v for k, v in {
            "method": lod["method"], "ratio": lod.get("ratio"), "tris": lod["tris"],
            "hausdorff_max_of_bounds": lod["hausdorff"]["max_of_bounds"],
            "hausdorff_mean_m": lod["hausdorff"]["mean_m"],
            "iou_min": lod.get("silhouette", {}).get("iou_min"),
            "distance_at_1px_m": lod["pixel_error"]["distance_at_1px_m"],
            "normals_p95_deg": lod.get("normals", {}).get("p95"),
            "uv_shift_p95": lod.get("uv_shift", {}).get("p95"),
            "seam_length_m": lod.get("uv", {}).get("seam_length_m"),
            "stretch_p95": lod.get("uv", {}).get("stretch", {}).get("p95"),
            "skin_p95": lod.get("skin_weights", {}).get("p95"),
            "open_edges": lod["topology"]["open_edges"],
            "reduce_ms": lod.get("reduce_ms")}.items() if v is not None})
    return rows


def main(argv):
    out = Path(argv[0])
    paths = [Path(p) for p in argv[1:] if p != "--synthetic"]
    report = {"binary": bpy.app.binary_path, "version": bpy.app.version_string, "build_hash":
              bpy.app.build_hash.decode() if isinstance(bpy.app.build_hash, bytes) else bpy.app.build_hash,
              "meshoptimizer": R.status(), "reps": REPS, "ratios": RATIOS, "cases": []}
    groups = [("synthetic (development)", None)] if "--synthetic" in argv else []
    groups += [(p.name, p) for p in paths]
    for label, path in groups:
        try:
            objects = synthetic() if path is None else import_asset(path)
        except Exception:   # noqa: BLE001
            report["cases"].append({"asset": label, "error": traceback.format_exc(limit=2)})
            continue
        names = [o.name for o in objects]
        for name in names:
            obj = bpy.data.objects[name]
            obj.data.calc_loop_triangles()
            if len(obj.data.loop_triangles) < MIN_TRIS:
                continue
            case = {"asset": label, "object": name[:64], "held_out": path is not None,
                    "tris": len(obj.data.loop_triangles)}
            try:
                case["decimate_modifier_vs_native"] = modifier_against_native(obj)
                case["timings"] = time_reductions(obj)
                result, ms = facts(obj)
                case["tool_ms"] = round(ms, 1)
                case["native_ms"] = result["native_ms"]
                case["source"] = {k: result["source"][k] for k in ("tris", "vertices", "geometry")}
                case["source_uv"] = result["source"]["uv"] if isinstance(result["source"]["uv"], dict) else None
                case["lod0_vertices"] = result.get("lod0_vertex_buffer")
                case["refused"] = result.get("refused")
                case["lods"] = summarize(result)
            except Exception:   # noqa: BLE001
                case["error"] = traceback.format_exc(limit=3)
            report["cases"].append(case)
            out.write_text(json.dumps(report, indent=1, default=str))
            print("case", label, name, case.get("tool_ms"), "error" in case, flush=True)
    out.write_text(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
