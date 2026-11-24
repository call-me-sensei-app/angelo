# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pack Islands' Search against Blender's packer as shipped, on a held-out set (doc/angelo/uv_layout.rst,
"Benchmark against Blender's packer").

The rule, fixed with this script before any of its results was seen (coordinator's plan, 2026-11-18):

* Both sides get the same islands: each mesh is unwrapped once, and every trial first restores those UVs. The same
  margin (Fraction, 2^4 / 2048), rotation Any, shape Concave (Exact Shape), scale to fit, UDIM 1001. The only
  difference is ``search``: 0 (Blender 5.3's packer as shipped) or 16 (UV Layout's default).
* The held-out set is generated, with the seeds below; none of it was used in development (that was
  ``test_blender_uv_layout``'s box kits and plates): a hard-surface kit, a smart-projected organic mesh, a triangle
  soup, an architectural set unwrapped by its sharp edges, and a scan-like surface of about 10,000 islands.
* ABAB: trial pairs alternate the side that runs first, 6 trials a side per mesh. Every trial is kept. No exclusions:
  a trial that fails counts and is reported as a failure.
* Reported per mesh and side: efficiency (island area inside the unit tile after packing, which Search being
  deterministic should make the same in every trial; a difference is reported), and wall time (median, min, max).
  The gain is Search 16's efficiency over Search 0's, for every mesh, losses included. The build and machine are
  recorded.

Run in a disposable instance::

    blender --background --factory-startup --python bench_uv_pack_heldout.py -- --out heldout.json
"""

import argparse
import json
import math
import os
import platform
import random
import statistics
import sys
import time
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector, noise

TRIALS = 6
SIDES = {"blender": 0, "search16": 16}
MARGIN = 2 ** 4 / 2048


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # This folder: the shared held-out set.
from heldout_meshes import HELD_OUT, edit, link, unwrap  # noqa: E402,F401  The set, moved unchanged.


def islands(obj):
    from bpy_extras import bmesh_utils
    edit(obj)
    bm = bmesh.from_edit_mesh(obj.data)
    count = len(bmesh_utils.bmesh_linked_uv_islands(bm, bm.loops.layers.uv.active))
    bpy.ops.object.mode_set(mode='OBJECT')
    return count


def efficiency(obj):
    """Island area inside the unit tile (UDIM 1001) after packing."""
    mesh = obj.data
    layer = mesh.uv_layers.active.data
    total = 0.0
    for poly in mesh.polygons:
        pts = [layer[i].uv for i in poly.loop_indices]
        area = 0.0
        for k in range(len(pts)):
            a, b = pts[k], pts[(k + 1) % len(pts)]
            area += a.x * b.y - b.x * a.y
        total += abs(area) / 2.0
    return total


def trial(obj, uvs, search):
    obj.data.uv_layers.active.data.foreach_set("uv", uvs)   # The same islands, every trial.
    edit(obj)
    started = time.perf_counter()
    bpy.ops.uv.pack_islands(udim_source='CLOSEST_UDIM', rotate=True, rotate_method='ANY', scale=True,
                            merge_overlap=False, margin_method='FRACTION', margin=MARGIN, shape_method='CONCAVE',
                            search=search)
    seconds = time.perf_counter() - started
    bpy.ops.object.mode_set(mode='OBJECT')
    return {"seconds": seconds, "efficiency": efficiency(obj)}


def summary(rows):
    done = [r for r in rows if "seconds" in r]
    times = [r["seconds"] for r in done]
    effs = sorted({round(r["efficiency"], 6) for r in done})
    return {"trials": len(rows), "failures": len(rows) - len(done), "efficiency": effs[0] if len(effs) == 1 else effs,
            "seconds_median": statistics.median(times) if times else None, "seconds_min": min(times, default=None),
            "seconds_max": max(times, default=None)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--only", default="", help="comma-separated mesh names (a partial run is reported as one)")
    parser.add_argument("--fixtures-only", action="store_true",
                        help="build and unwrap the set and count its islands; no pack runs (sizing the set only)")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    out = Path(args.out)
    report = {"rule": __doc__.split("Run in a disposable")[0].strip(), "trials_per_side": TRIALS, "sides": SIDES,
              "margin_fraction": MARGIN, "machine": platform.platform(), "cpu_count": os.cpu_count(),
              "binary": bpy.app.binary_path, "build_hash": str(bpy.app.build_hash), "meshes": {}}
    for build in HELD_OUT:
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj)
        obj, how = build()
        if args.only and obj.name not in args.only.split(","):
            continue
        uvs = unwrap(obj, how)
        row = {"faces": len(obj.data.polygons), "islands": islands(obj), "unwrap": list(how),
               "trials": {side: [] for side in SIDES}}
        report["meshes"][obj.name] = row
        if args.fixtures_only:
            print("FIXTURE %s faces=%d islands=%d" % (obj.name, row["faces"], row["islands"]), flush=True)
            out.write_text(json.dumps(report, indent=2))
            continue
        for t in range(TRIALS):
            order = list(SIDES) if t % 2 == 0 else list(reversed(SIDES))   # ABAB: who goes first alternates.
            for side in order:
                try:
                    result = trial(obj, uvs, SIDES[side])
                except Exception as error:   # noqa: BLE001 - every trial counts; a failure is reported.
                    result = {"failed": repr(error)}
                    if bpy.context.mode != 'OBJECT':
                        bpy.ops.object.mode_set(mode='OBJECT')
                result["trial"], result["first"] = t, order[0]
                result["load"] = round(os.getloadavg()[0], 2) if hasattr(os, "getloadavg") else None   # 1-minute, after
                row["trials"][side].append(result)
                out.write_text(json.dumps(report, indent=2))
        row["summary"] = {side: summary(row["trials"][side]) for side in SIDES}
        a, b = row["summary"]["blender"]["efficiency"], row["summary"]["search16"]["efficiency"]
        row["gain"] = (b / a - 1.0) if isinstance(a, float) and isinstance(b, float) and a > 0 else None
        out.write_text(json.dumps(report, indent=2))
        print("HELDOUT %s islands=%d gain=%s" % (obj.name, row["islands"], row["gain"]), flush=True)
    print("HELDOUT DONE", flush=True)


try:
    main()
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)
sys.exit(0)
