# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Never-slower floor rows inside the app: .blend listing and cutting, Angelo against stock Blender (unbilled).

The same script runs in both binaries, on the same files and scenes, and each
row names the binary that ran it (``bench_native_reads.identity``). Run it on a
quiet machine (``bench_floor_launch.py --rows`` drives both binaries, quiet
wait included)::

    BLENDER -b --python tests/python/angelo_agent/bench_floor_blender.py -- \\
        --out DIR --phase listing|cut|bulk [--prepare] [--objects 0,1000,5000,10000] [--runs 7]

``listing`` lists the objects of another .blend, plain and Zstandard-compressed
copies of the same scene (``--prepare`` builds the heavy castle scene from its
seed, about 250 MB, and saves both copies; later runs reuse them, so both
binaries list the same bytes). Routes: stock ``bpy.data.libraries.load`` (in
every binary) and, in Angelo, the scan its ``angelo_blend_file`` tool makes
(``bench_native_reads_paths.listing_angelo``, the product's own limits). Every
route must return the same names as the stock route (parity) before timing.

``cut`` subtracts one box from a wall in a scene of N other objects (separate
cube meshes, every tenth with a Bevel modifier), each run on a fresh wall and
cutter. Routes: stock Blender's Boolean modifier with its defaults, applied
with ``object.modifier_apply``, then the cutter deleted (what a person or an
MCP script does), in every binary; and in Angelo ``kit.cut`` then
``kit.finish`` (which deletes the spent cutter), the product route, which also
proves the cut (an intersection, a volume check). Both leave the same kind of
result: one cut wall, no cutter.

``bulk`` is the Windows slowdown test of 2026-10-19 (``scene_build_bench``):
build 2,000 cube objects with their own meshes over 300 shared materials, then
20,000 over 3,000, one view-layer update each, then set ``hide_render`` on
every object (touch-all). It is the same Python in both binaries, so a gap is
Blender-side work (compiled core or build configuration). Timed as whole
phases, ``--runs`` times each, alternating sizes, after one warm-up build.

Each route is timed ``--runs`` times after one warm-up on the main thread
(``bench_native_reads.sample``: wall, CPU and main-thread instructions with the
garbage collector off). Writes ``DIR/floor-<phase>-<binary kind>.json``.
"""

import argparse
import json
import os
from pathlib import Path
import statistics
import sys
import time

import bpy

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bench_native_reads as reads  # noqa: E402  (identity, machine, sample; its main runs only as __main__)
import bench_native_reads_paths as paths  # noqa: E402

PLAIN, COMPRESSED = "floor-listing-plain.blend", "floor-listing-zstd.blend"


def arguments():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    parser.add_argument("--phase", choices=("listing", "cut", "bulk"), required=True)
    parser.add_argument("--prepare", action="store_true", help="listing: build and save the two copies first")
    parser.add_argument("--objects", default="0,1000,5000,10000", help="cut: other objects in the scene")
    parser.add_argument("--runs", type=int, default=7)
    args = parser.parse_args(argv)
    args.counts = [int(value) for value in args.objects.split(",") if value.strip()]
    if not 3 <= args.runs <= 25 or any(not 0 <= count <= 50000 for count in args.counts):
        parser.error("Use 3-25 runs and 0-50,000 objects")
    return args


def timed(call, runs):
    """Warm-up, then ``runs`` samples; median and spread of each metric."""
    call()
    samples = [reads.sample(call) for _ in range(runs)]

    def spread(name):
        values = [sample[name] for sample in samples if sample[name] is not None]
        if not values:
            return None
        median = statistics.median(values)
        return {"median": round(median, 3), "min": round(min(values), 3), "max": round(max(values), 3),
                "relative_range": round((max(values) - min(values)) / median, 5) if median else None}
    return {"runs": runs, "wall_ms": spread("wall_ms"), "cpu_ms": spread("cpu_ms"),
            "instructions": spread("instructions")}


# ---------------------------------------------------------------- listing

def prepare_listing(out):
    """The heavy castle scene from its seed, saved plain and compressed beside each other."""
    import heavy_scene_blend as blend
    blend.remove_factory(bpy)
    built = blend.build(bpy, bpy.context.scene)
    for name, compress in ((PLAIN, False), (COMPRESSED, True)):
        outcome = bpy.ops.wm.save_as_mainfile(filepath=str(out / name), copy=True, compress=compress,
                                              check_existing=False)
        assert "FINISHED" in outcome, outcome
    return {key: value for key, value in built.items() if isinstance(value, (int, float, str))}


def listing(args, out, native):
    built = prepare_listing(out) if args.prepare else None
    rows = {}
    for name in (PLAIN, COMPRESSED):
        setup = type("Setup", (), {"listing": out / name})()
        routes = {"stock libraries.load": paths.listing_library}
        if native:
            routes["angelo metadata_scan (product limits)"] = paths.listing_angelo
        expected = paths.listing_library(setup)
        row = {"file": name, "bytes": (out / name).stat().st_size, "objects": len(expected), "routes": {}}
        for label, route in routes.items():
            if route(setup) != expected:
                raise SystemExit("bench_floor_blender: %s lists different objects than the stock route" % label)
            row["routes"][label] = timed(lambda: route(setup), args.runs)
        rows[name] = row
        print("FLOOR_LISTING", name, json.dumps({label: value["wall_ms"]["median"]
                                                 for label, value in row["routes"].items()}), flush=True)
    return {"built": built, "files": rows}


# ---------------------------------------------------------------- cut

def box(name, size, location):
    import bmesh
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for vertex in bm.verts:
        vertex.co = (vertex.co.x * size[0], vertex.co.y * size[1], vertex.co.z * size[2])
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    bpy.context.scene.collection.objects.link(obj)
    return obj


def populate(count):
    """``count`` other objects: separate cube meshes on a grid, every tenth with a Bevel modifier."""
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        bpy.data.meshes.remove(mesh)
    for index in range(count):
        obj = box("Floor prop %05d" % index, (0.5, 0.5, 0.5), (index % 100 * 2.0, 10 + index // 100 * 2.0, 0))
        if index % 10 == 0:
            obj.modifiers.new("Bevel", "BEVEL")
    bpy.context.view_layer.update()


def wall_and_cutter(index):
    wall = box("Floor wall %03d" % index, (4.0, 0.3, 3.0), (-20.0, -20.0 - index * 5, 1.5))
    cutter = box("Floor cutter %03d" % index, (1.0, 1.0, 1.5), (-20.0, -20.0 - index * 5, 1.2))
    bpy.context.view_layer.update()
    return wall, cutter


def stock_cut(wall, cutter):
    modifier = wall.modifiers.new("Cut", "BOOLEAN")
    modifier.operation, modifier.object = "DIFFERENCE", cutter
    with bpy.context.temp_override(object=wall, active_object=wall, selected_objects=[wall]):
        outcome = bpy.ops.object.modifier_apply(modifier=modifier.name)
    assert "FINISHED" in outcome, outcome
    bpy.data.objects.remove(cutter, do_unlink=True)


def angelo_cut(wall, cutter):
    from angelo_agent.script_kit import ScriptKit
    kit = ScriptKit(bpy, bpy.context.scene)
    kit.cut(wall.name, cutter.name)
    kit.finish()


def cut_route(route, runs):
    """``runs`` timed cuts after one warm-up, each on a fresh wall and cutter made outside the timed call."""
    made = iter(range(1, runs + 2))
    pending = {}

    def prepare():
        pending["pair"] = wall_and_cutter(next(made))

    def call():
        wall, cutter = pending.pop("pair")
        route(wall, cutter)
    prepare()
    call()   # Warm-up.
    samples = []
    for _ in range(runs):
        prepare()
        samples.append(reads.sample(call))
    values = {name: [sample[name] for sample in samples if sample[name] is not None]
              for name in ("wall_ms", "cpu_ms", "instructions")}
    return {"runs": runs, **{name: ({"median": round(statistics.median(found), 3), "min": round(min(found), 3),
                                     "max": round(max(found), 3)} if found else None)
                             for name, found in values.items()}}


def cut(args, native):
    rows = {}
    solver = bpy.data.objects.new("probe", bpy.data.meshes.new("probe")).modifiers.new("probe", "BOOLEAN").solver
    for count in args.counts:
        populate(count)
        routes = {"stock Boolean modifier + apply": stock_cut}
        if native:
            routes["angelo kit.cut + kit.finish"] = angelo_cut
        row = {"objects": count, "routes": {}}
        for label, route in routes.items():
            started = time.perf_counter()
            row["routes"][label] = cut_route(route, args.runs)
            row["routes"][label]["phase_s"] = round(time.perf_counter() - started, 2)
        rows[str(count)] = row
        print("FLOOR_CUT", count, json.dumps({label: value["wall_ms"]["median"]
                                              for label, value in row["routes"].items()}), flush=True)
    return {"boolean_default_solver": solver, "scenes": rows}


# ---------------------------------------------------------------- bulk

CUBE = ([(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)],
        [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])


def bulk_build(objects, materials):
    """One build then touch-all on an emptied file; returns each part's sample (wall, CPU, main-thread instructions)."""
    populate(0)
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)
    scene, data = bpy.context.scene, bpy.data

    def build():
        shared = [data.materials.new("Heavy Material %04d" % index) for index in range(materials)]
        for index in range(objects):
            mesh = data.meshes.new("Heavy Mesh %05d" % index)
            mesh.from_pydata(CUBE[0], [], CUBE[1])
            mesh.materials.append(shared[index % len(shared)])
            scene.collection.objects.link(data.objects.new("Heavy Object %05d" % index, mesh))
        bpy.context.view_layer.update()

    def touch():
        for obj in data.objects:
            obj.hide_render = False
    return {"build": reads.sample(build), "touch_all": reads.sample(touch)}


def bulk(args):
    sizes = ((2000, 300), (20000, 3000))
    bulk_build(*sizes[0])   # Warm-up.
    runs = {"%d_objects" % objects: [] for objects, _ in sizes}
    for _ in range(args.runs):
        for objects, materials in sizes:
            runs["%d_objects" % objects].append(bulk_build(objects, materials))
    result = {}
    for key, samples in runs.items():
        result[key] = {part: {name: (lambda values: {"median": round(statistics.median(values), 3),
                                                     "min": round(min(values), 3), "max": round(max(values), 3)}
                                     if values else None)([sample[part][name] for sample in samples
                                                           if sample[part][name] is not None])
                              for name in ("wall_ms", "cpu_ms", "instructions")} for part in ("build", "touch_all")}
        print("FLOOR_BULK", key, json.dumps({part: value["wall_ms"]["median"] for part, value in result[key].items()}),
              flush=True)
    return {"sizes": [{"objects": objects, "materials": materials} for objects, materials in sizes], "runs": args.runs,
            "results": result}


def main():
    args = arguments()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    native = reads.angelo_build()
    report = {"phase": args.phase, "identity": reads.identity(bpy.app, native), "machine": reads.machine(),
              "angelo_addon_enabled": "angelo_agent" in bpy.context.preferences.addons,
              # bench_native_reads puts the checkout's add-on folder first on sys.path; the app's own copy wins only
              # when the add-on was already enabled, so the report names the copy the cut route ran.
              "angelo_agent_file": getattr(sys.modules.get("angelo_agent"), "__file__", None),
              "factory_startup": "--factory-startup" in sys.argv,
              "load_at_start": os.getloadavg()[0] if hasattr(os, "getloadavg") else None}
    report["result"] = {"listing": lambda: listing(args, out, native), "cut": lambda: cut(args, native),
                        "bulk": lambda: bulk(args)}[args.phase]()
    report["angelo_agent_file_after"] = getattr(sys.modules.get("angelo_agent"), "__file__", None)
    report["load_at_end"] = os.getloadavg()[0] if hasattr(os, "getloadavg") else None
    path = out / ("floor-%s-%s.json" % (args.phase, "angelo" if native else "stock"))
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("FLOOR_BLENDER_DONE", path, flush=True)


main()
