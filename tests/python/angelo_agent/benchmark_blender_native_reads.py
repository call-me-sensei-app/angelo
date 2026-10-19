# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The instruction ratchet of every native read ``bench_native_reads`` compares with stock Blender.

Run in an isolated process, after other benchmarks and builds have stopped::

    Angelo --background --factory-startup --python-exit-code 1 \\
        --python tests/python/angelo_agent/benchmark_blender_native_reads.py -- [--samples 7]

It builds the dense castle scene of ``scenario-heavy`` from its seed (1,969
objects, 2,662,867 vertices, a 250 MB file) in ``$ANGELO_QA_DIR``, opens it, and
times Angelo's route of each of the ten reads of ``bench_native_reads_paths``
exactly as ``bench_native_reads`` does (one warm-up, then ``--samples`` calls,
each after a collection with the collector off, counting the main thread's
retired instructions). Before timing, the Angelo route must return the same data
as the stock route it is compared with (the fastest supported one), so a
ceiling never pins a read that answers wrongly. The stock route's own count is
recorded beside it in the report, for the "never slower than stock" comparison
of ``doc/angelo/performance.rst``; only Angelo's routes are ratcheted, as
``native_reads/<read>`` (``test_blender_perf_ratchet``).

``blend_listing`` runs with the product's own limits and deadline
(``blend_file_listing``): a scene of this kind must be listed, never refused. The report is
``native-reads.json``; the scene and the copy listed are deleted afterwards.
"""

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy

assert "--background" in sys.argv and "--factory-startup" in sys.argv
sys.path.insert(0, str(Path(__file__).resolve().parent))
import benchmark_fixtures as fixtures  # noqa: E402
import bench_native_reads as bench  # noqa: E402
import bench_native_reads_paths as reads  # noqa: E402

OUT = fixtures.isolate(fixtures.default_output("angelo-native-reads-benchmark"))


def parse(argv):
    parser = argparse.ArgumentParser(description="Native-read instruction ratchet")
    parser.add_argument("--samples", type=int, default=7, help="measured calls per read after one warm-up")
    args = parser.parse_args(argv)
    if not 1 <= args.samples <= 30:
        parser.error("Use 1-30 samples")
    return args


def compared_route(path):
    """The stock route a read is compared with: the last supported one listed (the fastest found)."""
    return [route for route in path.stock if route.supported][-1]


def measure_read(path, setup, samples):
    stock, angelo = compared_route(path), path.angelo
    expected, answer = stock.call(setup), angelo.call(setup)
    assert bench.same(expected, answer), "%s: Angelo's route does not return the stock route's data" % path.id
    row = {"angelo": dict(bench.timed(lambda: angelo.call(setup), samples), route=angelo.label),
           "stock": dict(bench.timed(lambda: stock.call(setup), samples), route=stock.label)}
    print("NATIVE_READ_RATCHET", path.id, json.dumps({side: {key: row[side][key] for key in (
        "median_ms", "instructions")} for side in ("angelo", "stock")}), flush=True)
    return row


def main():
    args = parse(fixtures.script_arguments())
    path, scene = bench.load_scene(SimpleNamespace(scene=""), OUT)
    setup = bench.prepare(path, OUT)
    report = {"environment": fixtures.environment(), "samples": args.samples, "warmup": 1, "scene": scene,
              "counter": "main thread", "reads": {}}
    ratchet = fixtures.Ratchet()
    sculpting = [item for item in reads.PATHS if item.id.startswith("sculpt_")]
    try:
        for item in [item for item in reads.PATHS if item not in sculpting] + sculpting:
            if item in sculpting and bpy.context.object is not setup.sculpt:
                bpy.context.view_layer.objects.active = setup.sculpt
                bpy.ops.object.mode_set(mode="SCULPT")
            row = report["reads"][item.id] = measure_read(item, setup, args.samples)
            ratchet.add("native_reads/" + item.id, {"instructions": {"median": row["angelo"]["instructions"]}})
        if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    finally:
        for leftover in (setup.listing, path):
            leftover.unlink(missing_ok=True)   # Two 250 MB files made only to be read.
    report["ratchet"] = ratchet.section()
    report["success"] = True
    (OUT / "native-reads.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    print("NATIVE_READS_RATCHET_DONE", json.dumps({"success": True, "results": str(OUT / "native-reads.json")}),
          flush=True)


main()
