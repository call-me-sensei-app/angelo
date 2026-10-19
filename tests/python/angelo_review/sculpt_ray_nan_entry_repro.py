# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reproduction: Object.angelo_sculpt_query returns a farther hit than the leaf scan.

A ray with a zero z component whose origin lies exactly at a PBVH node's minimum z makes
`isect_ray_aabb_v3` return a NaN entry for that node (0 * inf in the z slab). pbvh::raycast
(the path without leaf indices) puts leaves in a binary tree and scans every leaf whose entry is not >= the best
hit, so a NaN entry is harmless. raycast_mesh_tree keeps a std::push_heap/pop_heap heap ordered
by entry and stops at the first popped entry >= the best hit; NaN breaks the heap's strict weak
order, a nearer leaf stays below it, and the walk stops before reaching it.

The mesh here is one quad per material, so the PBVH splits leaves by material: vertical walls
(x = c) crossed by the ray, and horizontal tiles at z = 0 (the ray's height) that give NaN
entries. The ray from (-10, 0.25, 0) along +x first meets the wall at x = 3.

  Angelo -b --factory-startup --python sculpt_ray_nan_entry_repro.py

Prints one JSON line per case and exits 1 if any answer differs from the nearest wall.
"""
import json
import sys

import bpy

# (kind, a0, a1, b0, b1, c): kind 0 = wall x=c, y in [a0,a1], z in [b0,b1];
# kind 1 = tile z=c, x in [a0,a1], y in [b0,b1]. Face i has material index i.
CASES = {
    'trial7': [(0, -8, 1, -7, 3, 5), (1, 4, 6, -3, 1, 0), (0, -7, 7, -9, 8, 6),
               (1, 0, 7, -5, 8, 0), (0, -7, 8, -7, 4, 4), (1, 5, 8, -2, 9, 0),
               (1, 7, 10, -7, 1, 0), (0, -6, 9, -7, 5, 3)],
    'trial80': [(1, 0, 2, -9, 7, 0), (0, -7, 4, -8, 3, 4), (0, -4, 3, -1, 9, 5),
                (1, 0, 1, -8, 4, 0), (1, 5, 11, -9, 4, 0), (1, 2, 3, -3, 8, 0),
                (0, -8, 5, -9, 8, 3), (1, 4, 12, -6, 3, 0), (0, -8, 8, -6, 3, 3)],
    'trial143': [(0, -4, 4, -4, 1, 8), (1, 4, 11, -8, 8, 0), (0, -9, 6, -1, 9, 3),
                 (1, 1, 8, -2, 8, 0), (1, 1, 6, -5, 8, 0), (0, -5, 8, -6, 4, 5),
                 (1, 4, 6, -2, 4, 0), (1, 7, 10, -2, 9, 0), (0, -8, 9, -2, 6, 2)],
}
ORIGIN = (-10.0, 0.25, 0.0)


def build(parts):
    verts, faces = [], []
    for kind, a0, a1, b0, b1, c in parts:
        base = len(verts)
        if kind == 0:
            verts += [(c, a0, b0), (c, a1, b0), (c, a1, b1), (c, a0, b1)]
        else:
            verts += [(a0, b0, c), (a1, b0, c), (a1, b1, c), (a0, b1, c)]
        faces.append((base, base + 1, base + 2, base + 3))
    mesh = bpy.data.meshes.new('walls')
    mesh.from_pydata([tuple(map(float, v)) for v in verts], [], faces)
    for i, poly in enumerate(mesh.polygons):
        poly.material_index = i
    obj = bpy.data.objects.new('walls', mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    return obj


def expected(parts):
    walls = [c for kind, a0, a1, b0, b1, c in parts
             if kind == 0 and a0 < ORIGIN[1] < a1 and b0 < ORIGIN[2] < b1 and c > ORIGIN[0]]
    return min(walls) - ORIGIN[0]


failed = False
for name, parts in CASES.items():
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    obj = build(parts)
    bpy.ops.object.mode_set(mode='SCULPT')
    hit, co, normal, face, vertex = obj.angelo_sculpt_query(ORIGIN, direction=(1.0, 0.0, 0.0),
                                                             distance=100.0)
    distance = co[0] - ORIGIN[0] if hit else None
    ok = hit and distance == expected(parts)
    failed |= not ok
    print(json.dumps({'case': name, 'hit': hit, 'distance': distance,
                      'expected': expected(parts), 'face': face, 'ok': bool(ok)}))
sys.exit(1 if failed else 0)
