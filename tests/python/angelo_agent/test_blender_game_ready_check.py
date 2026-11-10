# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The game-ready check in the compiled app, without a window (doc/angelo/game_ready_check.rst).

Generic shapes whose answers are known, never a production asset:

- Unused, nothing of it runs: after the add-on registered and a scene object's handle was read, the meshoptimizer
  library was never opened (``_bpy._angelo_reduce.attempted()``, and the process's loaded images), and no module of the check is
  imported.
- ``_bpy._angelo_reduce``: the bundled meshoptimizer is found and checked; bad arguments raise Python errors, never a
  crash; the native nearest-point and coverage kernels equal the brute-force reference of
  test_native_game_ready_check; the weld splits exactly where UVs, normals or materials differ; simplification keeps
  UV seams and returns index buffers over the same vertices; ``decimate`` gives exactly the Decimate modifier's mesh,
  and the same on a ``snapshot``, which neither freeing nor editing the original changes; Decimate on a snapshot and
  ``convex_hull`` (the bmesh operator's hull) let other Python threads run while they work.
- ``angelo_game_ready_check`` through its host route: the call returns a pending result whose poll gives the facts it
  promises, for LOD objects, both reduction methods, a collider and a skinned mesh (weights kept as attributes, or
  refused with the reason past meshoptimizer's 32), and the scene, its data-blocks and Undo left exactly as they were.
Report: ``results.json`` (observed times are not checked: timing is bench_game_ready_check.py's).
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import math
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy
import bmesh

assert bpy.app.background
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-game-ready-check"))
OUT.mkdir(parents=True, exist_ok=True)
report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
checks, observed = report["checks"], report["observed"]
HEAVY = ("game_ready_host", "game_ready_mesh", "game_ready_metrics", "game_ready_collider")


def check(name, value, detail=None):
    checks[name] = bool(value)
    if detail is not None:
        observed[name] = detail


def loaded_images():
    """The shared libraries this process has loaded (macOS dyld; empty elsewhere)."""
    if sys.platform != "darwin":
        return []
    import ctypes
    dyld = ctypes.CDLL(None)
    dyld._dyld_image_count.restype = ctypes.c_uint32
    dyld._dyld_get_image_name.restype = ctypes.c_char_p
    dyld._dyld_get_image_name.argtypes = [ctypes.c_uint32]
    return [dyld._dyld_get_image_name(i).decode() for i in range(dyld._dyld_image_count())]


# ---- Unused: nothing loads (before anything below imports the check) ----------------------------------------------
from _bpy import _angelo_reduce as R  # noqa: E402

sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import read_host, scene_queries as queries  # noqa: E402,F401
from angelo_agent.tool_specs import validate_call  # noqa: E402

try:
    bpy.ops.mesh.primitive_cube_add()
    queries.object_handle(bpy.context.object)
except Exception:   # The idle proof below holds either way; the scene read is only an ordinary call before it.
    traceback.print_exc()
check("unused_library_never_opened", not R.attempted())
check("unused_library_not_in_process", not any("meshoptimizer" in name for name in loaded_images()),
      [name for name in loaded_images() if "meshopt" in name])
check("unused_modules_not_imported", not any("angelo_agent." + m in sys.modules for m in HEAVY))

import numpy as np  # noqa: E402
import test_native_game_ready_check as reference  # noqa: E402
from angelo_agent import game_ready_host, game_ready_metrics as G  # noqa: E402
from angelo_agent.game_ready_spec import CHECK  # noqa: E402
from angelo_agent.refusal_codes import RefusalError  # noqa: E402

REF = reference.ReferenceKernels()


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def link(name, bm, smooth=True):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    for poly in mesh.polygons:
        poly.use_smooth = smooth
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def uv_sphere(name="Sphere", segments=48, rings=24, radius=1.0):
    bm = bmesh.new()
    bm.loops.layers.uv.new("UVMap")   # calc_uvs fills an existing UV layer only.
    bmesh.ops.create_uvsphere(bm, u_segments=segments, v_segments=rings, radius=radius, calc_uvs=True)
    return link(name, bm)


def cube(name="Cube", size=2.0, smooth=False):
    bm = bmesh.new()
    bm.loops.layers.uv.new("UVMap")
    bmesh.ops.create_cube(bm, size=size, calc_uvs=True)
    return link(name, bm, smooth)


def arrays(obj):
    mesh = obj.data
    mesh.calc_loop_triangles()
    tris = np.empty(len(mesh.loop_triangles) * 3, dtype=np.uint32)
    mesh.loop_triangles.foreach_get("vertices", tris)
    positions = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", positions)
    return positions.reshape(-1, 3), tris


def fingerprint():
    """What a read-only call must leave alone: data-blocks, their names, geometry sizes, transforms and Undo."""
    return (tuple(sorted((id_.name, id_.session_uid) for coll in (bpy.data.meshes, bpy.data.objects)
                         for id_ in coll)),
            tuple((o.name, len(o.data.vertices) if o.type == "MESH" else 0, tuple(map(tuple, o.matrix_world)),
                   tuple(m.name for m in o.modifiers)) for o in bpy.data.objects),
            bpy.data.is_dirty)


calls = []   # (ms in the call, ms to the result) of each check through the host route.


def run(arguments):
    """The host route's answer, polled as the controller polls it. The window is looked up on every call:
    read_factory_settings replaces the window manager, so a window kept from before would be freed memory."""
    window = bpy.context.window_manager.windows[0]
    started = time.perf_counter()
    pending = game_ready_host.execute(window, CHECK, validate_call(CHECK, arguments, host=True))
    returned = time.perf_counter()
    if not hasattr(pending, "poll"):
        raise AssertionError("the check answered on the calling thread: %r" % type(pending))
    result = None
    while result is None:
        time.sleep(0.005)
        result = pending.poll()
    calls.append(((returned - started) * 1000.0, (time.perf_counter() - started) * 1000.0))
    if result.get("error_code"):   # As the controller delivers a refusal while measuring: the result says why.
        raise RefusalError(result["error"], code=result["error_code"])
    return result, calls[-1][1]


def gil_free(work):
    """Whether this thread ran Python in the middle of ``work()`` on another thread (it would not, were the GIL
    held), and how long the work took."""
    import threading
    span, ticks = {}, []

    def other():
        span["start"] = time.perf_counter()
        work()
        span["end"] = time.perf_counter()
    thread = threading.Thread(target=other)
    thread.start()
    while thread.is_alive():
        ticks.append(time.perf_counter())
    thread.join()
    start, end = span["start"], span["end"]
    middle = (start + 0.4 * (end - start), start + 0.6 * (end - start))
    return any(middle[0] < t < middle[1] for t in ticks), round((end - start) * 1000.0, 1)


def library_and_refusals():
    status = R.status()
    check("library_available", status["available"], status)
    check("library_is_the_bundled_one", "Contents/Resources/lib/libmeshoptimizer" in status["library_path"]
          or not sys.platform == "darwin", status["library_path"])
    check("header_is_1_1", status["header_version"] == 1010)
    check("library_opened_once_asked", R.attempted())
    positions = np.zeros((4, 3), dtype=np.float32)
    indices = np.array([0, 1, 2, 0, 2, 3], dtype=np.uint32)
    bad = {
        "index_out_of_range": lambda: R.simplify(positions, np.array([0, 1, 9], dtype=np.uint32), 3, 0.1),
        "not_triangles": lambda: R.simplify(positions, np.array([0, 1], dtype=np.uint32), 0, 0.1),
        "float64_positions": lambda: R.simplify(positions.astype(np.float64), indices, 3, 0.1),
        "nan_position": lambda: R.simplify(np.full((4, 3), np.nan, dtype=np.float32), indices, 3, 0.1),
        "experimental_option": lambda: R.simplify(positions, indices, 3, 0.1, options=1 << 5),
        "sloppy_with_options": lambda: R.simplify(positions, indices, 3, 0.1, mode="SLOPPY", options=1),
        "too_many_attributes": lambda: R.simplify(positions, indices, 3, 0.1, attributes=np.zeros(
            (4, 33), dtype=np.float32), weights=np.ones(33, dtype=np.float32)),
        "attribute_rows": lambda: R.simplify(positions, indices, 3, 0.1, attributes=np.zeros(
            (3, 2), dtype=np.float32), weights=np.ones(2, dtype=np.float32)),
        "lock_value": lambda: R.simplify(positions, indices, 3, 0.1, lock=np.full(4, 2, dtype=np.uint8)),
        "target_above_count": lambda: R.simplify(positions, indices, 9, 0.1),
        "error_above_one": lambda: R.simplify(positions, indices, 3, 2.0),
        "unknown_mode": lambda: R.simplify(positions, indices, 3, 0.1, mode="FAST"),
        "weld_rows": lambda: R.weld(6, [np.zeros(5, dtype=np.float32)]),
        "surface_index": lambda: R.surface(positions, np.array([0, 1, 4], dtype=np.uint32)),
        "raster_views": lambda: R.rasterize(positions, indices, np.zeros(7, dtype=np.float32), 8),
        "raster_resolution": lambda: R.rasterize(positions, indices, np.zeros(8, dtype=np.float32), 0),
        "decimate_not_a_mesh": lambda: R.decimate(bpy.context.scene, 0.5),
        "decimate_ratio": lambda: R.decimate(bpy.data.meshes.new("Empty"), 0.0),
        "decimate_another_capsule": lambda: R.decimate(R.surface(positions, indices), 0.5),
        "snapshot_not_a_mesh": lambda: R.snapshot(bpy.context.scene),
    }
    if hasattr(R, "convex_hull"):
        bad["convex_hull_float64"] = lambda: R.convex_hull(positions.astype(np.float64))
        bad["convex_hull_nan"] = lambda: R.convex_hull(np.full((4, 3), np.nan, dtype=np.float32))
    raised = {}
    for name, call in bad.items():
        try:
            call()
            raised[name] = "no error"
        except (TypeError, ValueError) as error:
            raised[name] = type(error).__name__
    check("bad_arguments_raise_never_crash", all(v != "no error" for v in raised.values()), raised)


def kernels_match_reference():
    rng = np.random.default_rng(7)
    obj = uv_sphere("Ref", 12, 6)
    positions, tris = arrays(obj)
    points = (rng.random((400, 3), dtype=np.float32) - 0.5) * 3.0
    native = R.nearest(R.surface(positions, tris), points)
    brute = REF.nearest(REF.surface(positions, tris), points)
    d_native, d_brute = (np.frombuffer(x[0], dtype=np.float32) for x in (native, brute))
    check("nearest_equals_reference", np.allclose(d_native, d_brute, atol=1e-5),
          {"max_difference": float(np.abs(d_native - d_brute).max())})
    views = G.silhouette_views(np.zeros(3), 1.2, 40)
    native = np.frombuffer(R.rasterize(positions, tris, views, 40), dtype=np.uint8)
    brute = np.frombuffer(REF.rasterize(positions, tris, views, 40), dtype=np.uint8)
    check("rasterize_equals_reference", np.array_equal(native, brute),
          {"pixels_differing": int((native != brute).sum()), "pixels": int(native.size)})


def weld_and_simplify():
    reset()
    flat = cube("Flat")
    smooth = cube("Smooth", smooth=True)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    from angelo_agent import game_ready_mesh as M
    counts = {}
    for obj in (flat, smooth):
        read = M.read(obj, depsgraph, 1.0, R).finish()
        counts[obj.name] = len(M.Reducer(read, R, 1.0, 0.5, 1.0).positions)
        read.release()
    # A flat cube: 4 corners x 6 faces. Smooth with its default box UVs: every face's own UVs still split it.
    check("weld_splits_hard_edges", counts["Flat"] == 24, counts)
    sphere = uv_sphere("Sphere", 64, 32)
    read = M.read(sphere, depsgraph, 1.0, R).finish()
    reducer = M.Reducer(read, R, 1.0, 0.5, 1.0)
    vertices = len(sphere.data.vertices)
    # Smooth: one vertex per position except along the UV seam (one meridian) and at the poles' UV fans.
    check("weld_splits_only_the_seam", vertices < len(reducer.positions) < vertices + 4 * 32 + 2 * 64,
          {"mesh_vertices": vertices, "welded": len(reducer.positions)})
    chain = []
    for ratio in (0.5, 0.25, 0.1):
        indices, error, ms = reducer.simplify(ratio, None, False)
        chain.append({"ratio": ratio, "tris": len(indices) // 3, "error": error, "ms": round(ms, 2),
                      "max_index": int(indices.max())})
        lod = reducer.geometry(indices)
        u_span = np.ptp(lod.corner_uv[:, :, 0], axis=1)
        chain[-1]["u_span_max"] = float(u_span.max())
    source_tris = len(read.geometry.tris)
    check("simplify_reaches_the_targets", all(c["tris"] <= int(source_tris * c["ratio"]) + 1 for c in chain), chain)
    check("simplify_indexes_lod0_vertices", all(c["max_index"] < len(reducer.positions) for c in chain))
    # No LOD triangle bridges the seam (u 0 to 1): a seam-crossing collapse would give a span near 1.
    check("simplify_keeps_the_uv_seam", all(c["u_span_max"] < 0.5 for c in chain))
    attrs = reducer.attributes
    out = R.simplify(reducer.positions, reducer.indices, len(reducer.indices) // 4, 1.0, mode="UPDATE",
                     attributes=attrs, weights=reducer.attribute_weights)
    moved = np.frombuffer(out[2], dtype=np.float32).reshape(-1, 3)
    check("update_mode_returns_moved_vertices", len(out) == 4 and moved.shape == reducer.positions.shape
          and len(out[0]) // 4 <= len(reducer.indices) // 4 + 3)
    sloppy, _ = R.simplify(reducer.positions, reducer.indices, len(reducer.indices) // 10, 1.0, mode="SLOPPY")
    check("sloppy_mode_runs", 0 < len(sloppy) // 4 <= len(reducer.indices) // 10 + 3)
    read.release()


def decimate_equals_the_modifier():
    reset()
    obj = uv_sphere("Dec", 32, 16)
    for ratio in (0.5, 0.2):
        modifier = obj.modifiers.new("D", "DECIMATE")
        modifier.ratio, modifier.use_collapse_triangulate = ratio, True
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh()
        expected = np.empty(len(evaluated.vertices) * 3, dtype=np.float32)
        evaluated.vertices.foreach_get("co", expected)
        obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh_clear()
        obj.modifiers.remove(modifier)
        result = R.decimate(obj.data, ratio, triangulate=True)
        got = np.frombuffer(result["positions"], dtype=np.float32)
        same = len(got) == len(expected) and np.allclose(np.sort(got.reshape(-1, 3), axis=0),
                                                         np.sort(expected.reshape(-1, 3), axis=0))
        check("decimate_equals_modifier_%g" % ratio, same, {"vertices": len(got) // 3,
                                                            "modifier_vertices": len(expected) // 3})
        check("decimate_keeps_uvs_%g" % ratio, result["corner_uvs"] is not None and result["uv_map"] == "UVMap")


def snapshots_and_hulls():
    """A snapshot decimates exactly as its mesh does, keeps that answer after the original is freed or edited, and
    lets other threads run while it works; the native hull is the bmesh operator's."""
    reset()
    obj = uv_sphere("Snap", 256, 128)
    expected = R.decimate(obj.data, 0.2)
    snap = R.snapshot(obj.data)
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    freed = R.snapshot(evaluated.to_mesh())
    evaluated.to_mesh_clear()   # The evaluated mesh is gone; its snapshot shares nothing that went with it.
    obj.data.vertices[0].co.x += 10.0   # An edit of the original after the snapshot was taken.
    obj.data.update()
    for name, mesh in (("snapshot", snap), ("snapshot_of_freed", freed)):
        got = R.decimate(mesh, 0.2)
        check("decimate_on_a_%s_equals_the_mesh" % name, got["positions"] == expected["positions"]
              and got["tris"] == expected["tris"] and got["corner_uvs"] == expected["corner_uvs"])
    edited = R.decimate(obj.data, 0.2)
    check("an_edit_reaches_the_mesh_not_its_snapshot", edited["positions"] != expected["positions"])
    ran, ms = gil_free(lambda: R.decimate(snap, 0.2))
    check("decimate_on_a_snapshot_frees_the_gil", ran, {"decimate_ms": ms})
    del snap, freed
    check("convex_hull_in_a_bullet_build", hasattr(R, "convex_hull") == hasattr(bmesh.ops, "convex_hull"))
    if not hasattr(R, "convex_hull"):
        return
    rng = np.random.default_rng(3)
    points = (rng.normal(size=(200000, 3)) * (1.0, 0.6, 0.3)).astype(np.float32)
    positions, tris = (np.frombuffer(b, dtype=t).reshape(-1, 3) for b, t in zip(R.convex_hull(points),
                                                                                 (np.float32, np.int32)))
    bm = bmesh.new()
    for point in points[:2000]:
        bm.verts.new(point)
    reference_hull = bmesh.ops.convex_hull(bm, input=bm.verts[:])
    faces = [f for f in reference_hull["geom"] if isinstance(f, bmesh.types.BMFace)]
    bm_corners = sorted(tuple(round(c, 5) for c in v.co) for v in {v for f in faces for v in f.verts})
    bm_tris = sum(len(f.verts) - 2 for f in faces)
    bm.free()
    small_positions, small_tris = (np.frombuffer(b, dtype=t).reshape(-1, 3) for b, t in zip(
        R.convex_hull(np.ascontiguousarray(points[:2000])), (np.float32, np.int32)))
    corners = sorted(tuple(round(float(c), 5) for c in v) for v in small_positions[np.unique(small_tris)])
    check("convex_hull_is_the_bmesh_operator_s", corners == bm_corners and len(small_tris) == bm_tris,
          {"vertices": len(corners), "bmesh_vertices": len(bm_corners), "tris": len(small_tris),
           "bmesh_tris": bm_tris})
    inside = np.all(np.isin(np.unique(tris), np.arange(len(positions))))
    check("convex_hull_triangles_index_its_vertices", inside and len(tris) > 0)
    ran, ms = gil_free(lambda: R.convex_hull(points))
    check("convex_hull_frees_the_gil", ran, {"convex_hull_ms": ms})
    check("convex_hull_of_one_point_is_empty", R.convex_hull(np.zeros((8, 3), dtype=np.float32)) is None)


def tool_end_to_end():
    reset()
    source = uv_sphere("Rock", 96, 48)
    lod = uv_sphere("Rock_LOD1", 24, 12)
    lod.location = source.location
    bpy.ops.mesh.primitive_cube_add(size=2.2)
    collider = bpy.context.object
    collider.name = "UCX_Rock_00"
    bpy.context.view_layer.update()
    bpy.ops.ed.undo_push(message="fixture")
    handles = {o.name: queries.object_handle(o) for o in (source, lod, collider)}
    before = fingerprint()
    undo_before = len(bpy.context.window_manager.undo_steps) if hasattr(bpy.context.window_manager,
                                                                         "undo_steps") else None
    result, ms = run({"object": handles["Rock"], "lods": [handles["Rock_LOD1"]], "collider": handles["UCX_Rock_00"],
                      "simplify": {"ratios": [0.5, 0.1]}, "switch_distances": [5, 10, 40]})
    observed["tool_ms"] = round(ms, 1)
    observed["tool_result_bytes"] = len(json.dumps(result, separators=(",", ":")))
    check("tool_scene_unchanged", fingerprint() == before)
    check("tool_reports_read_only", result["read_only"] and not result["scene_changed"])
    names = [(lod_facts["method"], lod_facts.get("ratio")) for lod_facts in result["lods"]]
    check("tool_lists_every_lod", names == [("OBJECT", None), ("MESHOPT", 0.5), ("DECIMATE", 0.5), ("MESHOPT", 0.1),
                                            ("DECIMATE", 0.1)], names)
    needed = ("tris", "hausdorff", "silhouette", "pixel_error", "normals", "uv", "uv_shift", "topology")
    missing = {f["name"]: [k for k in needed if k not in f] for f in result["lods"] if any(k not in f for k in needed)}
    check("tool_every_fact_present", not missing, missing)
    facts = {k for f in result["lods"] for k in f} | {"collider", "lod0_vertex_buffer"}
    described = set(result["measured"])
    check("tool_every_fact_says_what_it_measured", (facts & set(G.glossary(1080, 60, 1024))) <= described
          and all(isinstance(m, str) and len(m) > 30 for m in result["measured"].values()), sorted(described))
    lod1 = result["lods"][0]
    check("tool_lod_object_measured", 0 < lod1["hausdorff"]["max_of_bounds"] < 0.05 and
          lod1["silhouette"]["iou_min"] > 0.95, {k: lod1[k] for k in ("hausdorff", "silhouette")})
    meshopt = result["lods"][1]
    check("tool_meshopt_shares_lod0", meshopt["shares_lod0_vertices"]
          and meshopt["vertices_used"] <= result.get("lod0_vertex_buffer", 0))
    check("tool_switch_pixels", [f["pixel_error"]["at_switch"][0]["distance_m"] for f in result["lods"]]
          == [5, 10, 10, 40, 40])
    hull_built = hasattr(bmesh.ops, "convex_hull")   # Bullet: the release build, not the development preset.
    check("tool_collider_fit", result["collider"]["closed"] and result["collider"]["convex"]
          and result["collider"]["mesh_outside"] == 0.0
          and (result["collider"]["excess_over_hull"] > 0 if hull_built else "hull" in result["collider"]),
          result["collider"])
    if undo_before is not None:
        check("tool_no_undo_step", len(bpy.context.window_manager.undo_steps) == undo_before)
    observed["tool_native_ms"] = result["native_ms"]
    observed["tool_call_and_result_ms"] = [round(x, 1) for x in calls[-1]]
    observed["tool_example"] = {k: result["lods"][1][k] for k in ("name", "tris", "hausdorff", "silhouette")}
    try:
        run({"object": handles["Rock"], "lods": [queries.object_handle(bpy.data.objects["UCX_Rock_00"])],
             "collider": handles["Rock_LOD1"]})
        check("tool_runs_with_object_lods", True)
    except Exception as error:   # noqa: BLE001
        check("tool_runs_with_object_lods", False, repr(error))
    empty_mesh = bpy.data.objects.new("NoFaces", bpy.data.meshes.new("NoFaces"))
    bpy.context.scene.collection.objects.link(empty_mesh)
    beside = uv_sphere("Beside", 24, 12)
    beside.location = (5.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    result, _ = run({"object": handles["Rock"], "lods": [queries.object_handle(empty_mesh),
                                                         queries.object_handle(beside)]})
    check("tool_lod_without_faces_reads_empty", result["lods"][0].get("empty") and result["lods"][0]["tris"] == 0,
          result["lods"][0])
    check("tool_lod_placed_beside_is_apart", result["lods"][1].get("apart") is True)
    camera = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    bpy.context.scene.collection.objects.link(camera)
    try:
        run({"object": queries.object_handle(camera)})
        check("tool_refuses_a_non_mesh", False)
    except RefusalError as error:
        check("tool_refuses_a_non_mesh", "mesh" in str(error), str(error))


def skinned(groups, unused=0):
    reset()
    bm = bmesh.new()
    bm.loops.layers.uv.new("UVMap")
    bmesh.ops.create_cone(bm, cap_ends=True, segments=24, radius1=0.3, radius2=0.3, depth=2.0, calc_uvs=True)
    bmesh.ops.subdivide_edges(bm, edges=[e for e in bm.edges if abs(e.verts[0].co.z - e.verts[1].co.z) > 1.0],
                              cuts=31, use_grid_fill=False)
    body = link("Body", bm)
    armature = bpy.data.objects.new("Rig", bpy.data.armatures.new("Rig"))
    bpy.context.scene.collection.objects.link(armature)
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode="EDIT")
    for i in range(groups):
        bone = armature.data.edit_bones.new("B%02d" % i)
        bone.head, bone.tail = (0, 0, -1 + 2 * i / groups), (0, 0, -1 + 2 * (i + 1) / groups)
    for i in range(unused):   # Bones of the rig that weigh nothing on this mesh.
        bone = armature.data.edit_bones.new("Unused%02d" % i)
        bone.head, bone.tail = (1, 0, 0), (1, 0, 0.1)
    bpy.ops.object.mode_set(mode="OBJECT")
    for i in range(unused):
        body.vertex_groups.new(name="Unused%02d" % i)
    for i in range(groups):
        group = body.vertex_groups.new(name="B%02d" % i)
        for v in body.data.vertices:
            weight = max(0.0, 1.0 - abs((v.co.z + 1) * groups / 2 - (i + 0.5)))
            if weight > 0:
                group.add([v.index], weight, "REPLACE")
    modifier = body.modifiers.new("Armature", "ARMATURE")
    modifier.object = armature
    bpy.context.view_layer.update()
    return run({"object": queries.object_handle(body), "simplify": {"ratios": [0.25]}})[0]


def skinned_meshes():
    result = skinned(8, unused=40)   # A full rig's bones, of which 8 touch this mesh.
    methods = {f["method"]: f for f in result["lods"]}
    check("skinned_weights_kept_as_attributes", "MESHOPT" in methods and "skin_weights" in methods["MESHOPT"]
          and "refused" not in result, {m: f.get("skin_weights") for m, f in methods.items()})
    check("skinned_read_in_rest_pose", "rest pose" in result["source"]["geometry"])
    check("skinned_meshopt_keeps_weights_better_or_equal",
          methods["MESHOPT"]["skin_weights"]["p95"] <= methods["DECIMATE"]["skin_weights"]["p95"] + 0.05,
          {m: f["skin_weights"]["p95"] for m, f in methods.items()})
    result = skinned(40)
    refused = result.get("refused", [])
    check("skinned_past_32_attributes_refused_with_reason", refused and refused[0]["method"] == "MESHOPT"
          and "40 deforming vertex groups weigh something" in refused[0]["reason"], refused)
    check("skinned_past_32_decimate_still_measured", [f["method"] for f in result["lods"]] == ["DECIMATE"])


try:
    library_and_refusals()
    kernels_match_reference()
    weld_and_simplify()
    decimate_equals_the_modifier()
    snapshots_and_hulls()
    tool_end_to_end()
    skinned_meshes()
    report["success"] = all(checks.values())
except Exception:   # noqa: BLE001
    report["error"] = traceback.format_exc()
    print(report["error"])
(OUT / "results.json").write_text(json.dumps(report, indent=2, default=str))
print("GAME_READY_CHECK", "OK" if report["success"] else "FAILED",
      [name for name, ok in checks.items() if not ok])
