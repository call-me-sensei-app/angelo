# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Dump Object.angelo_sculpt_query answers for a deterministic ray set.

Run the same script on two builds and diff the JSON: the leaf scan (a build without leaf indices) is the reference.

  Angelo -b --factory-startup --python sculpt_ray_dump.py -- OUT.json [repeats]

Every ray is asked `repeats` times in a row (default 18, so leaves reach the 16-query index
threshold) and the first and last answers are both recorded. Floats are written with repr, so a
diff is bit-exact.
"""
import json
import math
import random
import sys

import bpy

argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
OUT = argv[0]
REPEATS = int(argv[1]) if len(argv) > 1 else 18


def reset():
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()


def apply_subsurf(obj, levels, simple):
    mod = obj.modifiers.new('S', 'SUBSURF')
    mod.subdivision_type = 'SIMPLE' if simple else 'CATMULL_CLARK'
    mod.levels = levels
    bpy.ops.object.modifier_apply(modifier=mod.name)


def fixtures():
    reset()
    bpy.ops.mesh.primitive_cube_add(size=2)
    apply_subsurf(bpy.context.object, 6, True)
    yield 'simple_cube'
    reset()
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=240, y_subdivisions=240, size=4)
    yield 'flat_grid'
    reset()
    bpy.ops.mesh.primitive_monkey_add()
    apply_subsurf(bpy.context.object, 3, False)
    yield 'monkey'


def rays(obj):
    verts = [v.co.copy() for v in obj.data.vertices]
    rng = random.Random(7)
    sample = rng.sample(verts, min(120, len(verts)))
    out = []
    for v in sample:
        x, y, z = v
        # Horizontal rays at a vertex's exact height (direction z exactly 0), both signs.
        out += [((-5.0, y, z), (1.0, 0.0, 0.0)), ((5.0, y, z), (-1.0, 0.0, 0.0)),
                ((x, -5.0, z), (0.0, 1.0, 0.0)), ((x, 5.0, z), (0.0, -1.0, 0.0)),
                ((-5.0, -5.0 + (y - x), z), (1.0, 1.0, 0.0))]
        # Vertical rays through the vertex (ties between faces sharing it).
        out += [((x, y, 5.0), (0.0, 0.0, -1.0)), ((x, y, -5.0), (0.0, 0.0, 1.0))]
        # Rays along x and y through exact coordinates (two zero components).
        out += [((x, -5.0, z), (0.0, 1.0, 0.0))]
    for _ in range(300):
        o = tuple(rng.uniform(-4, 4) for _ in range(3))
        target = rng.choice(verts)
        d = tuple(t - s for t, s in zip(target, o))
        out.append((o, d))
    return out


def ask(obj, origin, direction):
    hit, co, normal, face, vertex = obj.angelo_sculpt_query(origin, direction=direction,
                                                             distance=100.0)
    return [bool(hit), [repr(c) for c in co], [repr(c) for c in normal], face, vertex]


result = {}
for name in fixtures():
    obj = bpy.context.object
    bpy.ops.object.mode_set(mode='SCULPT')
    rows = []
    for origin, direction in rays(obj):
        first = ask(obj, origin, direction)
        last = first
        for _ in range(REPEATS - 1):
            last = ask(obj, origin, direction)
        rows.append({'origin': [repr(c) for c in origin], 'direction': [repr(c) for c in direction],
                     'first': first, 'last': last})
    result[name] = rows
    print(name, len(obj.data.vertices), 'vertices', len(rows), 'rays')

with open(OUT, 'w') as f:
    json.dump(result, f)
print('wrote', OUT)
