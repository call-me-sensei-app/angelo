# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How the typed ``uv_unwrap`` action scales with mesh size, both methods, one island (no seams).

Run alone, in a disposable instance with the exact Angelo binary::

    Angelo --background --factory-startup --disable-autoexec --python-exit-code 1 \\
      --python tests/python/angelo_agent/benchmark_blender_uv_unwrap_work.py -- [--json OUT] [--sizes 5000,20000,80000]

Each size is a fresh private staged grid (quads) or a triangulated grid, everything
selected (an empty ``face_indices`` selects all faces; explicit indices are capped at
4,096). The action runs through the kernel (``apply_operation``) with its work bound
lifted, so the time is the unwrap itself. Times are wall clock on a shared machine; the
result is native_coverage.rst's "operator_run work bounds" table row for ``uv_unwrap``.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import bmesh
import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import action_mesh  # noqa: E402
from angelo_agent.action_kernels import StagedActionContext, apply_operation  # noqa: E402
from angelo_agent.action_operator_spec import WORK_BOUNDS  # noqa: E402


def stage():
    scene = bpy.data.scenes.new("UV Work Stage")
    collection = bpy.data.collections.new("UV Work Stage Objects")
    scene.collection.children.link(collection)
    return StagedActionContext(scene, collection)


def grid(context, vertices, triangulated):
    side = max(int(vertices ** .5) - 1, 1)
    data, bm = bpy.data.meshes.new("UV Work Grid"), bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=side, y_segments=side, size=10)
    if triangulated:
        bmesh.ops.triangulate(bm, faces=list(bm.faces))
    counts = (len(bm.verts), len(bm.edges), len(bm.faces))
    bm.to_mesh(data)
    bm.free()
    context.track_id(data)
    obj = context.add_object(context.track_id(bpy.data.objects.new("grid", data)), "grid")
    return obj, counts


def timed(vertices, triangulated, method):
    context = stage()
    try:
        obj, counts = grid(context, vertices, triangulated)
        started = time.perf_counter()
        try:
            apply_operation(context, {"kind": "uv_unwrap", "target": "$grid", "parameters": {"method": method}})
            outcome = "OK"
        except ValueError as error:
            outcome = str(error).split(":")[0][:40]
        return {"vertices": counts[0], "edges": counts[1], "faces": counts[2], "elements": sum(counts),
                "triangulated": triangulated, "method": method, "seconds": round(time.perf_counter() - started, 3),
                "outcome": outcome}
    finally:
        bpy.data.batch_remove(ids=list(set(context.owned_ids) | set(context.created_ids)))


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--json")
    parser.add_argument("--sizes", default="5000,20000,80000")
    args = parser.parse_args(argv)
    WORK_BOUNDS["uv.unwrap"] = float("inf")  # Measure the unwrap, not the bound that refuses it.
    if hasattr(action_mesh, "UNWRAP_BOUND"):
        action_mesh.UNWRAP_BOUND = float("inf")
    rows = []
    for vertices in map(int, args.sizes.split(",")):
        for triangulated in (False, True):
            for method in ("ANGLE_BASED", "CONFORMAL"):
                rows.append(timed(vertices, triangulated, method))
                print("ANGELO_UV_UNWRAP_WORK", json.dumps(rows[-1]), flush=True)
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=1, sort_keys=True))


main()
