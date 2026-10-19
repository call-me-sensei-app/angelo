# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Position writes in Sculpt Mode that the leaf ray indices' write-version check may not see.

The leaf ray indices are dropped when the position storage's ImplicitSharingInfo version
changes. That version rises when write access is *acquired* (`vert_positions_for_write`, an
attribute's `data` lookup), not when bytes are written. So a reference taken before the queries
(a MeshVertex, its `co` vector, an attribute element, the iterator of `for v in mesh.vertices`)
writes later without a new version. Shape keys are another path: with an active key the PBVH
reads the key's data, whose version is not tracked (-1).

For each case: enter Sculpt Mode on the fixture's `monkey`, take the references, ask every probe
ray 18 times (leaves reach the 16-query index threshold), write through the references, then
record one answer per ray. Run on the change and on a build without leaf indices (the leaf scan,
stale only through the PBVH's own bounds) and diff `after`. Controls (foreach_set, attribute foreach_set,
bmesh.to_mesh twice) acquire write access after the queries and must match that build.

  Angelo -b FIXTURE.blend --python sculpt_ray_version_bypass.py -- OUT.json [CASE]

The fixture is made by sculpt_ray_far_dump.py MAKE.
"""
import json
import random
import sys

import bmesh
import bpy
import numpy as np

ARGS = sys.argv[sys.argv.index('--') + 1:]
OUT = ARGS[0]
ONLY = ARGS[1] if len(ARGS) > 1 else None
FIXTURE = bpy.data.filepath


def enter(obj):
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for other in bpy.context.view_layer.objects:
        other.select_set(other == obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='SCULPT')


def ask(obj, origin, direction):
    hit, co, normal, face, vertex = obj.angelo_sculpt_query(origin, direction=direction,
                                                             distance=100.0)
    return [bool(hit), [repr(c) for c in co], [repr(c) for c in normal], face, vertex]


def positions(mesh):
    co = np.empty(len(mesh.vertices) * 3, np.float32)
    mesh.vertices.foreach_get('co', co)
    return co.reshape(-1, 3)


def shear(co):
    co = co.copy()
    front = co[:, 1] < 0
    co[front, 0] += 0.15 * np.sin(co[front, 2] * 7)
    co[front, 1] -= 0.08
    return co


def probes(obj):
    rng = random.Random(5)
    verts = [v.co.copy() for v in obj.data.vertices]
    out = []
    for _ in range(160):
        target = rng.choice(verts)
        o = [target[i] + rng.uniform(-1, 1) + (-2.5 if i == 1 else 0) for i in range(3)]
        out.append((tuple(o), tuple(target[i] - o[i] for i in range(3))))
    for x in np.linspace(-1.2, 1.2, 49):
        for z in np.linspace(-0.9, 0.9, 25):
            out.append(((float(x), -3.0, float(z)), (0.0, 1.0, 0.0)))
    return out


def front(mesh):
    return [i for i, c in enumerate(positions(mesh)) if c[1] < 0]


# Each case: take(obj) -> state, before the queries (targets are computed there too: even a
# foreach_get acquires write access and raises the version); write(obj, state) after them,
# touching the mesh only through the references taken.
def take_vertex_refs(obj):
    target = shear(positions(obj.data))
    return target, [obj.data.vertices[i] for i in front(obj.data)]


def write_vertex_refs(obj, state):
    target, refs = state
    for v in refs:
        v.co = target[v.index]


def take_co_vectors(obj):
    target = shear(positions(obj.data))
    return target, [(i, obj.data.vertices[i].co) for i in front(obj.data)]


def write_co_vectors(obj, state):
    target, vectors = state
    for i, v in vectors:
        v[:] = target[i]


def take_attribute_items(obj):
    target = shear(positions(obj.data))
    data = obj.data.attributes['position'].data
    return target, [(i, data[i]) for i in front(obj.data)]


def write_attribute_items(obj, state):
    target, items = state
    for i, item in items:
        item.vector = target[i]


def take_target(obj):
    return shear(positions(obj.data))


def write_foreach_set(obj, target):
    obj.data.vertices.foreach_set('co', target.ravel())


def write_attribute_foreach_set(obj, target):
    obj.data.attributes['position'].data.foreach_set('vector', target.ravel())


def write_bmesh_twice(obj, target):
    for _ in range(2):
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        for v in bm.verts:
            v.co = target[v.index]
        bm.to_mesh(obj.data)
        bm.free()


def take_shape_key(obj):
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.shape_key_add(name='Basis')
    key = obj.shape_key_add(name='K')
    obj.active_shape_key_index = 1
    enter(obj)
    key = obj.data.shape_keys.key_blocks['K']
    co = np.empty(len(key.data) * 3, np.float32)
    key.data.foreach_get('co', co)
    return key, shear(co.reshape(-1, 3))


def write_shape_key(obj, state):
    key, target = state
    key.data.foreach_set('co', target.ravel())


CASES = {
    'held_vertex_refs': (take_vertex_refs, write_vertex_refs),
    'held_co_vectors': (take_co_vectors, write_co_vectors),
    'held_attribute_items': (take_attribute_items, write_attribute_items),
    'shape_key_active_data': (take_shape_key, write_shape_key),
    'control_foreach_set': (take_target, write_foreach_set),
    'control_attribute_foreach_set': (take_target, write_attribute_foreach_set),
    'control_bmesh_to_mesh_twice': (take_target, write_bmesh_twice),
}


def loop_case(obj):
    """One loop: query at a vertex, move it, through the same `for v in mesh.vertices` iterator."""
    answers = []
    rays = probes(obj)
    for _ in range(3):  # Leaves reach the index threshold.
        for o, d in rays:
            ask(obj, o, d)
    target = shear(positions(obj.data))
    for v in obj.data.vertices:
        if v.co.y < 0:
            v.co = target[v.index]
            if v.index % 7 == 0:
                o, d = rays[v.index % len(rays)]
                answers.append(ask(obj, o, d))
    return answers


result = {}
for name, (take, write) in list(CASES.items()) + [('for_loop_query_and_move', (None, None))]:
    if ONLY and name != ONLY:
        continue
    bpy.ops.wm.open_mainfile(filepath=FIXTURE)
    obj = bpy.data.objects['monkey']
    enter(obj)
    if take is None:
        result[name] = {'after': loop_case(obj)}
        print(name, len(result[name]['after']), 'answers', flush=True)
        continue
    state = take(obj)
    obj = bpy.data.objects['monkey']
    rays = probes(obj)
    for origin, direction in rays:
        for _ in range(18):
            ask(obj, origin, direction)
    write(obj, state)
    after = [ask(obj, o, d) for o, d in rays]
    result[name] = {'after': after}
    print(name, len(after), 'answers', flush=True)
with open(OUT, 'w') as f:
    json.dump(result, f)
print('wrote', OUT)
