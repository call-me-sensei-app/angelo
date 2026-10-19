# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reproduction: a leaf ray index misses a surface the leaf scan hits.

LeafRayIndex pads each triangle box by 1e-5 of the leaf's largest coordinate (at least 1), but
the slab test's rounding grows with the ray's distance from the box. For a ray from ~1,000 units
away aimed at a vertex of a unit sphere, the padded corner boxes of the hit triangles can be
rejected, and the index answers a different triangle, or the far side of the sphere.

For each ray this script takes the leaf scan's answer (the first query on a freshly built PBVH,
before any leaf reaches the 16-query index threshold) and the index's answer (the 18th query of
the same ray), in the same process, so it needs no second build.

  Angelo -b --factory-startup --python sculpt_ray_index_far_repro.py [-- FAR]

Exits 1 when the index returns a different hit location than the scan for any ray.
"""
import json
import math
import random
import sys

import bpy

FAR = float(sys.argv[sys.argv.index('--') + 1]) if '--' in sys.argv else 1000.0

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()
bpy.ops.mesh.primitive_uv_sphere_add(segments=256, ring_count=128, radius=1.0)
obj = bpy.context.object


def fresh():
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.mode_set(mode='SCULPT')


def ask(origin, direction):
    hit, co, normal, face, vertex = obj.angelo_sculpt_query(origin, direction=direction,
                                                             distance=1e10)
    return hit, tuple(co), face


rng = random.Random(11)
verts = [v.co.copy() for v in obj.data.vertices]
bpy.ops.object.mode_set(mode='SCULPT')
differences = []
for i in range(250):
    target = rng.choice(verts)
    outward = target.normalized()
    noise = [rng.uniform(-.5, .5) for _ in range(3)]
    d = [outward[k] + noise[k] for k in range(3)]
    n = math.sqrt(sum(c * c for c in d))
    origin = tuple(float(target[k] + d[k] / n * FAR) for k in range(3))
    direction = tuple(float(target[k] - origin[k]) for k in range(3))
    fresh()
    scan = ask(origin, direction)
    for _ in range(17):
        index = ask(origin, direction)
    if index != scan:
        unit = [c / math.sqrt(sum(x * x for x in direction)) for c in direction]
        depth = [sum((p[k] - origin[k]) * unit[k] for k in range(3)) for p in (scan[1], index[1])]
        differences.append({'ray': i, 'origin': origin, 'direction': direction,
                            'scan': {'face': scan[2], 'depth': depth[0]},
                            'index': {'hit': index[0], 'face': index[2], 'depth': depth[1]},
                            'location_differs': scan[1] != index[1]})
for row in differences:
    print(json.dumps(row))
moved = [row for row in differences if row['location_differs']]
print(f'far={FAR}: {len(differences)} of 250 rays answered differently by the index, '
      f'{len(moved)} at a different location', flush=True)
sys.exit(1 if moved else 0)
