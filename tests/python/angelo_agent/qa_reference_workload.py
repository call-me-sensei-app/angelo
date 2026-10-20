# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The machine factor's reference workload, run inside the app by qa_machine.py (``--background --factory-startup``).

Pure computation, the kind the timed suites do between Blender calls: a mathutils BVH over a 120 x 120 grid and
200,000 ray casts, a numpy pass and a plain Python loop (about 0.18 s on the reference Mac). It creates no data-block, so nothing an add-on does on
data changes enters the factor: the factor measures the machine, never the product. Median of three runs, timed
in-process (startup excluded); prints ANGELO_QA_REFERENCE with the seconds.
"""

import json
import statistics
import time

import numpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

SIDE = 120


def once():
    started = time.perf_counter()
    verts = [(x * 0.1, y * 0.1, 0.05 * ((x * y) % 7)) for y in range(SIDE + 1) for x in range(SIDE + 1)]
    polys = [(y * (SIDE + 1) + x, y * (SIDE + 1) + x + 1, (y + 1) * (SIDE + 1) + x + 1, (y + 1) * (SIDE + 1) + x)
             for y in range(SIDE) for x in range(SIDE)]
    tree = BVHTree.FromPolygons(verts, polys)
    down = Vector((0.0, 0.0, -1.0))
    hits = sum(tree.ray_cast(Vector(((i % SIDE) * 0.1 + 0.05, (i // SIDE % SIDE) * 0.1 + 0.05, 5.0)), down)[0]
               is not None for i in range(200000))
    grid = numpy.array(verts, dtype=numpy.float64)
    spread = float(numpy.linalg.norm(grid - grid.mean(axis=0), axis=1).sum())
    total = 0.0
    for i in range(3000000):
        total += (i % 97) * 0.5
    assert hits == 200000 and spread > 0 and total > 0
    return time.perf_counter() - started


runs = [once() for _ in range(3)]
print("ANGELO_QA_REFERENCE " + json.dumps({"seconds": round(statistics.median(runs), 4),
                                           "runs": [round(run, 4) for run in runs]}), flush=True)
