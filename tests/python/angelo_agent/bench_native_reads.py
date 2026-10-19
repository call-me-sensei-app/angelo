# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's native reads against stock Blender's fastest Python routes, on the dense castle scene (unbilled).

Run it yourself, on macOS or Windows, from the repository root, with BLENDER the
Angelo (or a stock Blender 5.2) executable (one line)::

    BLENDER -b --factory-startup --python tests/python/angelo_agent/bench_native_reads.py -- --out DIR --web

With no ``--scene`` it builds the heavy scene of ``scenario-heavy`` from its
seed (``heavy_plan``: 1,969 objects, 2,662,867 vertices), saves it to
``DIR/heavy-scene.blend`` and opens that file, timing the load. In Angelo every
path of ``bench_native_reads_paths`` is timed both ways in the same process
and scene; in a stock Blender only the stock routes run (so the two binaries'
stock routes can be compared too). Before timing, every route of a path must
return the same data as the first stock route (``parity``).

Each route is timed ``--runs`` times after one warm-up, each run after a
garbage collection with the collector off, on the main thread: wall time,
CPU time, and on macOS the main thread's retired instructions
(``perf_counters.read_thread_counts``; Windows reports them as null). The
fastest *supported* stock route is the comparison; the others are listed.

``per_edit_check`` is the whole safety check of one edit: the edited object's
exact revision and program fingerprint, then the whole-document check that
nothing else changed. Angelo's own functions against the same reads done
with the fastest stock routes (attribute arrays, bmesh's deform layer,
BLAKE2b), the document check being the same code on both sides (it already
uses stock Blender's fastest routes).

The report names the binary it timed by that binary's own build, read inside
it (``build_hash`` and ``build_branch`` from ``bpy.app``; ``angelo_commit`` is
that hash for an Angelo build), and the harness checkout that timed it apart
(``harness_commit``). A binary that reports no build hash is refused before
anything is built or timed: the report would have to guess what it measured.

Writes ``DIR/native-reads-full.json`` and, with ``--web``, the website files
in ``DIR/web``: ``native-reads.json``, and a point cloud of the scene
(``castle-points.bin``, little-endian float32 x, y, z, every n-th vertex of
every object in collection order, each object's in vertex-index order, about
300,000 points) with ``castle-points.json``.
"""

import argparse
import gc
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import bpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
for _path in (str(ROOT / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import bench_native_reads_paths as reads  # noqa: E402
import perf_counters  # noqa: E402

POINTS = 300000
BUILD_HASH = re.compile(r"[0-9a-f]{7,40}")   # Blender's build info: 12 hex digits.


def arguments():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="native-reads-results")
    parser.add_argument("--scene", default="", help="a .blend to open instead of building the heavy scene")
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--web", action="store_true", help="also write the website files in OUT/web")
    parser.add_argument("--only", default="", help="comma-separated path ids")
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])


def angelo_build():
    return hasattr(bpy.types.Mesh, "bl_rna") and "angelo_sculpt_positions" in bpy.types.Mesh.bl_rna.properties


def machine():
    name = platform.processor()
    try:
        if sys.platform == "darwin":
            name = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True,
                                  timeout=10).stdout.strip() or name
    except (OSError, subprocess.SubprocessError):
        pass
    return {"cpu": name, "cores": os.cpu_count(), "platform": platform.platform(), "machine": platform.machine(),
            "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None}


def harness_commit():
    """The checkout this harness runs from (its git HEAD; an extracted tree's ANGELO_SOURCE_COMMIT), not the binary."""
    try:
        found = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                               timeout=20, env=dict(os.environ, GIT_LFS_SKIP_SMUDGE="1"))
        return found.stdout.strip() or os.environ.get("ANGELO_SOURCE_COMMIT", "")
    except (OSError, subprocess.SubprocessError):
        return os.environ.get("ANGELO_SOURCE_COMMIT", "")


def build_identity(app):
    """The timed binary's own build, as ``bpy.app`` reports it inside that binary.

    ``build_hash`` is the commit Blender's build info recorded when the binary was built. A build without build
    info reports "Unknown" ("unknown" outside a git checkout): refused (SystemExit), never filled in from the
    harness checkout, which RC8's report named and which had to be corrected by hand. ``build_branch`` ends in
    "(modified)" when the built tree had uncommitted or unpushed changes; the hash then names their base.
    """
    def text(name):
        value = getattr(app, name, None) or ""
        return (value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)).strip()
    found = text("build_hash")
    if not BUILD_HASH.fullmatch(found):
        raise SystemExit("bench_native_reads: %s reports no build hash (bpy.app.build_hash is %r), so the report "
                         "could not name the binary it timed; time a binary built with build info (from a git "
                         "checkout)" % (text("binary_path") or "this Blender", found))
    return {"build_hash": found, "build_branch": text("build_branch")}


def identity(app, native):
    """Which binary was timed and which harness checkout timed it, kept apart."""
    build = build_identity(app)
    return {"binary": app.binary_path, "blender": app.version_string, "angelo_build": native, **build,
            "angelo_commit": build["build_hash"] if native else None, "harness_commit": harness_commit()}


# ---------------------------------------------------------------- the scene

def load_scene(args, out):
    """The scene file (built from the seed when not given), opened and timed; returns its facts."""
    built = None
    path = Path(args.scene).resolve() if args.scene else out / "heavy-scene.blend"
    if not args.scene:
        import heavy_scene_blend as blend
        blend.remove_factory(bpy)
        built = blend.build(bpy, bpy.context.scene)
        built.update(blend.save_start(bpy, path))
    started = time.perf_counter()
    bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False)
    load_s = time.perf_counter() - started
    meshes = {obj.data for obj in bpy.context.scene.objects if obj.type == "MESH"}
    return path, {"file": path.name, "bytes": path.stat().st_size, "load_s": round(load_s, 3),
                  "objects": len(bpy.context.scene.objects), "meshes": len(meshes),
                  "vertices": sum(len(mesh.vertices) for mesh in meshes),
                  "scene_vertices": sum(len(obj.data.vertices) for obj in bpy.context.scene.objects
                                        if obj.type == "MESH"), "built": built}


def prepare(path, out):
    """What the routes read, gathered once: unique meshes in scene order, the weighted ones, the statue, a copy of
    the scene's file to list (Blender will not list the open file)..."""
    import shutil
    listing = out / "listing-copy.blend"
    if not listing.is_file() or listing.stat().st_size != path.stat().st_size:
        shutil.copyfile(path, listing)
    scene = bpy.context.scene
    meshes, seen, weighted = [], set(), []
    for obj in scene.objects:
        if obj.type == "MESH" and obj.data.name not in seen:
            seen.add(obj.data.name)
            meshes.append(obj.data)
            if len(obj.vertex_groups):
                weighted.append(obj.data)
    statue = scene.objects.get("Courtyard Statue")
    count = len(statue.data.vertices)
    image = bpy.data.images.new("Native Reads Image", 1024, 1024)
    rays = [((1.5 * ((i * 37) % 100 / 50.0 - 1.0), -3.0, 0.2 + 2.0 * ((i * 61) % 100) / 100.0), (0.0, 1.0, 0.0))
            for i in range(1000)]
    return SimpleNamespace(scene=scene, meshes=meshes, weighted=weighted, sculpt=statue, image=image, listing=listing,
                           terrain=scene.objects["Terrain"], rays=rays,
                           indices=[(i * 7919) % count for i in range(1000)],
                           depsgraph=bpy.context.evaluated_depsgraph_get,
                           window=SimpleNamespace(scene=scene, view_layer=bpy.context.view_layer))


def vertices_read(path_id, setup):
    if path_id == "vertex_group_weights":
        return sum(len(mesh.vertices) for mesh in setup.weighted)
    return sum(len(mesh.vertices) for mesh in setup.meshes)


# ---------------------------------------------------------------- timing

def sample(call):
    gc.collect()
    gc.disable()
    try:
        counts = perf_counters.read_thread_counts()
        cpu, wall = time.process_time_ns(), time.perf_counter_ns()
        call()
        wall, cpu = time.perf_counter_ns() - wall, time.process_time_ns() - cpu
        after = perf_counters.read_thread_counts()
    finally:
        gc.enable()
    return {"wall_ms": wall / 1e6, "cpu_ms": cpu / 1e6,
            "instructions": after["instructions"] - counts["instructions"] if counts and after else None}


def timed(call, runs):
    call()   # Warm-up: lazily built caches are not the read's cost.
    samples = [sample(call) for _ in range(runs)]
    walls = [s["wall_ms"] for s in samples]
    instructions = [s["instructions"] for s in samples if s["instructions"] is not None]
    return {"median_ms": round(statistics.median(walls), 3), "range_ms": [round(min(walls), 3), round(max(walls), 3)],
            "cpu_ms": round(statistics.median(s["cpu_ms"] for s in samples), 3),
            "instructions": int(statistics.median(instructions)) if instructions else None, "runs": runs}


def same(first, second):
    if isinstance(first, list) and isinstance(second, list) and len(first) == len(second):
        return all(same(a, b) for a, b in zip(first, second))
    if hasattr(first, "tobytes") or hasattr(second, "tobytes"):
        return bytes(memoryview(first).cast("B")) == bytes(memoryview(second).cast("B"))
    return first == second


def measure_path(path, setup, runs, native):
    routes = [("stock", route) for route in path.stock] + ([("angelo", path.angelo)] if native else [])
    entry = {"id": path.id, "label": path.label, "unit": path.unit, "per_vertex": path.per_vertex, "note": path.note,
             "routes": []}
    reference = None
    for side, route in routes:
        row = {"side": side, "route": route.label, "supported": route.supported}
        try:
            result = route.call(setup)
            reference = result if reference is None else reference
            row["parity"] = same(reference, result)
            row.update(timed(lambda: route.call(setup), runs))
        except Exception as error:   # Recorded, never hidden: a route that cannot run here is a fact.
            row["error"] = "%s: %s" % (type(error).__name__, str(error)[:300])
        entry["routes"].append(row)
        print("NATIVE_READ", path.id, side, json.dumps({k: row.get(k) for k in ("median_ms", "instructions",
                                                                                "parity", "error")}), flush=True)
    if path.unit == "per million vertices":
        entry["vertices"] = vertices_read(path.id, setup)
    if path.id == "blend_listing" and native:   # What the product's own limits make of this file.
        try:
            entry["product_limits"] = {"objects_listed": len(reads.listing_product(setup))}
        except Exception as error:
            entry["product_limits"] = {"error": "%s: %s" % (type(error).__name__, str(error)[:300])}
    return entry


# ---------------------------------------------------------------- the safety check of one edit

def stock_revision(obj):
    """The edited object's exact state through stock Blender's fastest routes, hashed with BLAKE2b."""
    import hashlib
    import numpy
    digest, mesh = hashlib.blake2b(digest_size=32), obj.data
    digest.update(numpy.array(obj.matrix_world, dtype=numpy.float32).tobytes())
    for name, domain, kind, width, value in (("position", mesh.vertices, numpy.float32, 3, "vector"),
                                             (".edge_verts", mesh.edges, numpy.int32, 2, "value"),
                                             (".corner_vert", mesh.loops, numpy.int32, 1, "value"),
                                             (".corner_edge", mesh.loops, numpy.int32, 1, "value")):
        buffer = numpy.empty(len(domain) * width, kind)
        mesh.attributes[name].data.foreach_get(value, buffer)
        digest.update(buffer.tobytes())
    for collection, prop, kind in ((mesh.polygons, "loop_total", numpy.int32), (mesh.polygons, "material_index",
                                                                                 numpy.int32),
                                   (mesh.polygons, "use_smooth", numpy.bool_), (mesh.vertices, "select", numpy.bool_),
                                   (mesh.vertices, "hide", numpy.bool_), (mesh.edges, "select", numpy.bool_),
                                   (mesh.edges, "hide", numpy.bool_), (mesh.polygons, "select", numpy.bool_),
                                   (mesh.polygons, "hide", numpy.bool_)):
        buffer = numpy.empty(len(collection), kind)
        collection.foreach_get(prop, buffer)
        digest.update(buffer.tobytes())
    for records in reads.weights_bmesh(SimpleNamespace(weighted=[mesh])):
        digest.update(records)
    for layer in mesh.uv_layers:
        buffer = numpy.empty(len(layer.uv) * 2, numpy.float32)
        layer.uv.foreach_get("vector", buffer)
        digest.update(buffer.tobytes())
    return digest.hexdigest()


def per_edit(setup, runs, names):
    """Angelo's check of one edit against the same reads through the fastest stock routes, per edited object."""
    from angelo_agent import scene_queries, view_scene_check
    from angelo_agent.program_dependencies import _fingerprint
    window, found = setup.window, {}
    document = lambda: view_scene_check.SceneCheck(window, deep=True).finish(())
    for name in names:
        obj = setup.scene.objects[name]
        parts = {"exact revision": lambda: scene_queries.object_revision(window, obj),
                 "program fingerprint": lambda: _fingerprint(obj),
                 "stock revision (same reads, fastest stock routes)": lambda: stock_revision(obj),
                 "document check (nothing else changed)": document}
        times = {}
        for label, call in parts.items():
            try:
                times[label] = timed(call, runs)
            except Exception as error:
                times[label] = {"error": "%s: %s" % (type(error).__name__, str(error)[:300])}
        angelo = sum(times[k].get("median_ms", 0.0) for k in ("exact revision", "program fingerprint",
                                                             "document check (nothing else changed)"))
        stock = 2 * times["stock revision (same reads, fastest stock routes)"].get("median_ms", 0.0) + \
            times["document check (nothing else changed)"].get("median_ms", 0.0)
        found[name] = {"vertices": len(obj.data.vertices), "vertex_groups": len(obj.vertex_groups), "parts": times,
                       "angelo_ms": round(angelo, 3), "stock_ms": round(stock, 3),
                       "complete": not any("error" in value for value in times.values())}
        print("PER_EDIT", name, json.dumps({key: found[name][key] for key in ("angelo_ms", "stock_ms")}), flush=True)
    return found


def main():
    args = arguments()
    native = angelo_build()
    report = identity(bpy.app, native)   # First: a binary it cannot name is refused before minutes of building.
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    bpy.context.preferences.filepaths.file_preview_type = "NONE"
    path, scene = load_scene(args, out)
    setup = prepare(path, out)
    report.update({"machine": machine(), "scene": scene, "runs": args.runs, "counters": perf_counters.availability(),
                   "paths": [], "retired": list(reads.RETIRED)})
    wanted = [value for value in args.only.split(",") if value]
    sculpting = [p for p in reads.PATHS if p.id.startswith("sculpt_")]
    for item in [p for p in reads.PATHS if p not in sculpting] + sculpting:
        if wanted and item.id not in wanted:
            continue
        if item in sculpting and bpy.context.object is not setup.sculpt:
            bpy.context.view_layer.objects.active = setup.sculpt
            bpy.ops.object.mode_set(mode="SCULPT")
        report["paths"].append(measure_path(item, setup, args.runs, native))
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    if native and not wanted:
        report["per_edit_check"] = per_edit(setup, args.runs, ("Castle Wall North", "Courtyard Statue"))
    (out / "native-reads-full.json").write_text(json.dumps(report, indent=1, default=str))
    if args.web:
        import bench_native_reads_web as web
        web.write(out / "web", report, setup)
    setup.listing.unlink()   # A 250 MB copy made only to be listed.
    print("NATIVE_READS_DONE", json.dumps({"out": str(out), "paths": len(report["paths"])}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
