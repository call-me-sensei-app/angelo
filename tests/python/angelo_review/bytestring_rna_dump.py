# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Byte-string RNA properties read through pyrna.

Checks each byte string against an independent read (foreach_get, the packed source bytes,
the attribute API) and prints a digest per case, so the same script on the previous build and on
the change must print identical lines. Covers the direct path (plain getters) and the fallback (ID-property
storage, Python get/set callbacks), with embedded NULs, every byte value, non-ASCII text, empty
values and large values.

  Angelo -b --factory-startup --python bytestring_rna_dump.py
"""
import hashlib
import random
import sys

import bpy
import numpy as np

failures = []


def check(name, value, expected=None):
    digest = hashlib.sha256(value).hexdigest()[:16] if isinstance(value, bytes) else repr(value)
    ok = expected is None or value == expected
    if not ok:
        failures.append(name)
    print(f'CASE {name} {type(value).__name__} len={len(value) if value is not None else None} '
          f'sha={digest} {"ok" if ok else "MISMATCH"}', flush=True)


def mesh_from(verts, faces=()):
    mesh = bpy.data.meshes.new('m')
    mesh.from_pydata(verts, [], list(faces))
    return mesh


def foreach_positions(mesh):
    co = np.empty(len(mesh.vertices) * 3, np.float32)
    mesh.vertices.foreach_get('co', co)
    return co.tobytes()


def expected_topology(mesh):
    corners = np.empty(len(mesh.loops), np.int32)
    mesh.loops.foreach_get('vertex_index', corners)
    sizes = np.empty(len(mesh.polygons), np.int32)
    mesh.polygons.foreach_get('loop_total', sizes)
    offsets = np.zeros(len(sizes) + 1, np.int32)
    np.cumsum(sizes, out=offsets[1:])
    return corners.tobytes() + (offsets.tobytes() if len(sizes) else b'')


def expected_weights(mesh):
    out = bytearray()
    for v in mesh.vertices:
        for g in v.groups:
            out += np.array([v.index, g.group], np.int32).tobytes() + np.float32(g.weight).tobytes()
    return bytes(out)


rng = random.Random(3)
# Positions whose bytes cover NUL, 0xFF, NaN payloads, -0.0, denormals and infinities.
special = np.array([0.0, -0.0, 1e-45, -1e-45, np.inf, -np.inf, 1.0, -2.5, 3.4e38],
                   np.float32)
raw = np.frombuffer(bytes(range(256)) * 3, np.float32)  # every byte value, includes NaN patterns
values = np.concatenate([special, raw])
verts = [tuple(float(x) for x in values[i:i + 3]) for i in range(0, len(values) - 2, 3)]
cases = {
    'empty': mesh_from([]),
    'one_vertex': mesh_from([(0.0, 0.0, 0.0)]),
    'special_floats': mesh_from(verts),
}
bpy.ops.mesh.primitive_monkey_add()
monkey = bpy.context.object
mod = monkey.modifiers.new('S', 'SUBSURF')
mod.levels = 4
bpy.ops.object.modifier_apply(modifier=mod.name)
cases['monkey_126k'] = monkey.data
grid = bpy.ops.mesh.primitive_grid_add(x_subdivisions=1000, y_subdivisions=1000)
cases['grid_1M'] = bpy.context.object.data

for name, mesh in cases.items():
    # foreach_set keeps NaN payloads bit-exact, unlike from_pydata through Python floats.
    if name == 'special_floats':
        mesh.vertices.foreach_set('co', values[:len(mesh.vertices) * 3])
    check(f'{name}/positions', mesh.angelo_sculpt_positions, foreach_positions(mesh))
    check(f'{name}/topology', mesh.angelo_sculpt_topology, expected_topology(mesh))
    check(f'{name}/weights_none', mesh.angelo_vertex_group_weights, b'')
    # Two reads are equal, distinct objects; the empty value is the shared singleton.
    a, b = mesh.angelo_sculpt_positions, mesh.angelo_sculpt_positions
    print(f'CASE {name}/two_reads equal={a == b} same_object={a is b and len(a) > 0}', flush=True)

# Vertex-group weights: groups, zero and negative weights, a vertex in many groups.
obj = bpy.data.objects.new('weighted', cases['monkey_126k'])
bpy.context.collection.objects.link(obj)
groups = [obj.vertex_groups.new(name=f'G{i}') for i in range(5)]
for v in range(0, len(obj.data.vertices), 3):
    groups[v % 5].add([v], rng.choice([0.0, 1.0, 0.5, 1e-30, 0.25]), 'REPLACE')
    if v % 7 == 0:
        for g in groups:
            g.add([v], 0.125, 'ADD')
check('weights/monkey', obj.data.angelo_vertex_group_weights, expected_weights(obj.data))

# PackedFile.data: every byte value, NULs, non-UTF-8, and a large payload.
for label, payload in (('all_bytes', bytes(range(256)) * 4), ('nul_only', b'\0' * 1000),
                       ('large_32MiB', np.random.default_rng(1).integers(0, 256, 32 << 20, np.uint8).tobytes()),
                       ('one_byte', b'\xff')):
    image = bpy.data.images.new(label, 4, 4)
    image.pack(data=payload, data_len=len(payload))
    check(f'packed/{label}', image.packed_file.data, payload)

# String attribute values: non-ASCII UTF-8 and the 254-byte limit (no NUL: setter is strlen-based).
mesh = cases['one_vertex']
attribute = mesh.attributes.new('s', 'STRING', 'POINT')
for label, text in (('ascii', b'hello'), ('utf8', 'héllo 世界 \U0001f600'.encode()),
                    ('latin1', b'\xe9\xff\xfe'), ('max', b'x' * 254), ('over', b'y' * 400), ('empty', b'')):
    attribute.data[0].value = text
    check(f'string_attribute/{label}', attribute.data[0].value, text[:254])

# The fallback: Python properties stored as ID properties, and with get/set callbacks.
store = {}
bpy.types.Object.review_bytes = bpy.props.StringProperty(subtype='BYTE_STRING')
bpy.types.Object.review_bytes_cb = bpy.props.StringProperty(
    subtype='BYTE_STRING', get=lambda self: store.get('v', ''), set=lambda self, v: store.__setitem__('v', v))
for label, text in (('ascii', b'abc'), ('utf8', 'züß'.encode()), ('empty', b''),
                    ('large', b'q' * (1 << 20))):
    obj.review_bytes = text
    check(f'idprop/{label}', obj.review_bytes, text)
    obj.review_bytes_cb = text
    check(f'callback/{label}', obj.review_bytes_cb, text)

print('FAILURES', failures, flush=True)
sys.exit(1 if failures else 0)
