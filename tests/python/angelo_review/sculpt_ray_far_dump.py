# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Dump angelo_sculpt_query answers for oblique rays aimed at vertices from near and far origins.

A ray aimed at a vertex hits every triangle around it at one depth, on
the corner of each triangle's box, so only the box padding keeps those triangles from a
rounding rejection. The padding scales with the leaf's coordinates (1e-5 of the largest), the
slab test's rounding with the ray's distance.

  Angelo -b --factory-startup --python sculpt_ray_far_dump.py -- MAKE FIXTURE.blend
  Angelo -b FIXTURE.blend --python sculpt_ray_far_dump.py -- scan OUT.json
  Angelo -b FIXTURE.blend --python sculpt_ray_far_dump.py -- index OUT.json

`scan` rebuilds the PBVH (Object -> Sculpt) every 8 rays and asks each ray once, so no leaf
reaches the 16-query threshold: the leaf scan, through the build's traversal. `index` asks every
ray 18 times and records the last answer: leaf indices. A build without leaf indices has none, so its
two modes agree. Floats are written with repr, so diffs are bit-exact.
"""
import json
import math
import random
import sys

import bpy

argv = sys.argv[sys.argv.index('--') + 1:]
MODE = argv[0]


def make(path):
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj)
    bpy.ops.mesh.primitive_monkey_add()
    monkey = bpy.context.object
    monkey.name = 'monkey'
    mod = monkey.modifiers.new('S', 'SUBSURF')
    mod.levels = 3
    bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=256, ring_count=128, radius=1.0)
    bpy.context.object.name = 'sphere'
    bpy.ops.wm.save_as_mainfile(filepath=path)


def ask(obj, origin, direction):
    hit, co, normal, face, vertex = obj.angelo_sculpt_query(origin, direction=direction,
                                                             distance=1e10)
    return [bool(hit), [repr(c) for c in co], [repr(c) for c in normal], face, vertex]


def rays(obj):
    rng = random.Random(11)
    verts = [v.co.copy() for v in obj.data.vertices]
    out = []
    for far in (3.0, 10.0, 100.0, 1000.0, 10000.0):
        for _ in range(250):
            target = rng.choice(verts)
            outward = target.normalized() if target.length > 1e-6 else target.copy()
            noise = [rng.uniform(-.5, .5) for _ in range(3)]
            d = [outward[i] + noise[i] for i in range(3)]
            n = math.sqrt(sum(c * c for c in d)) or 1.0
            origin = tuple(float(target[i] + d[i] / n * far) for i in range(3))
            out.append((far, origin, tuple(float(target[i] - origin[i]) for i in range(3))))
    return out


def enter(obj):
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for other in bpy.context.view_layer.objects:
        other.select_set(other == obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='SCULPT')


if MODE == 'MAKE':
    make(argv[1])
    sys.exit(0)

result = {}
for name in ('monkey', 'sphere'):
    obj = bpy.data.objects[name]
    enter(obj)
    rows = []
    for i, (far, origin, direction) in enumerate(rays(obj)):
        if MODE == 'scan':
            if i % 8 == 0:
                enter(obj)
            answer = ask(obj, origin, direction)
        else:
            for _ in range(18):
                answer = ask(obj, origin, direction)
        rows.append({'far': far, 'origin': [repr(c) for c in origin],
                     'direction': [repr(c) for c in direction], 'answer': answer})
    result[name] = rows
with open(argv[1], 'w') as f:
    json.dump(result, f)
print('wrote', argv[1])
