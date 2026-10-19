# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Change the sculpt surface after leaf ray indices exist, then compare answers.

For each mutation: enter Sculpt Mode on the fixture's `monkey`, ask every probe ray 18 times
(leaves reach the 16-query index threshold), apply the mutation, then record
  `after`: one answer per ray in the same session (indices built before the change), and
  `fresh`: one answer per ray after Object -> Sculpt rebuilds the PBVH (no index, live data).
Run on the change build and on a build without leaf indices: there `after` is the leaf scan's answer
to the same state, and `after` == `fresh` on that build means the change was tagged as Blender expects.

  Angelo -b FIXTURE.blend --python sculpt_ray_mutations.py -- OUT.json [MUTATION]

Brush strokes, hiding and undo run in background mode through a screen override (front
orthographic view; an undo push first, as several sculpt operators need an undo stack there).
The fixture is made by sculpt_ray_far_dump.py MAKE.
"""
import json
import math
import random
import sys

import bmesh
import bpy
import numpy as np
from mathutils import Quaternion

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


def view():
    screen = bpy.data.screens['Layout']
    area = next(a for a in screen.areas if a.type == 'VIEW_3D')
    region = next(r for r in area.regions if r.type == 'WINDOW')
    space = area.spaces.active
    rv3d = space.region_3d
    rv3d.view_perspective = 'ORTHO'
    rv3d.view_rotation = Quaternion((math.sqrt(.5), math.sqrt(.5), 0, 0))  # front: looks along +y
    rv3d.view_location = (0, 0, 0)
    rv3d.view_distance = 3.0
    return dict(screen=screen, area=area, region=region, space_data=space, region_data=rv3d)


def brush(name):
    bpy.ops.brush.asset_activate(
        asset_library_type='ESSENTIALS',
        relative_asset_identifier=f'brushes/essentials_brushes-mesh_sculpt.blend/Brush/{name}')


def stroke(points, size=60):
    """Points in region pixels; the region centre is x = 0, z = 0 on the monkey's front."""
    ctx = view()
    cx, cy = ctx['region'].width / 2, ctx['region'].height / 2
    elements = [{'name': '', 'location': (0, 0, 0), 'mouse': (cx + x, cy + y),
                 'mouse_event': (cx + x, cy + y), 'is_start': i == 0, 'pressure': 1.0,
                 'size': size, 'time': i * .01, 'x_tilt': 0, 'y_tilt': 0}
                for i, (x, y) in enumerate(points)]
    with bpy.context.temp_override(**ctx):
        assert bpy.ops.sculpt.brush_stroke(stroke=elements, mode='NORMAL') == {'FINISHED'}


def ask(obj, origin, direction):
    try:
        hit, co, normal, face, vertex = obj.angelo_sculpt_query(origin, direction=direction,
                                                                 distance=100.0)
    except Exception as error:  # noqa: BLE001 - reported, not hidden
        return ['error', str(error)]
    return [bool(hit), [repr(c) for c in co], [repr(c) for c in normal], face, vertex]


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


def targeted(obj, before):
    """Rays at the faces the mutation moved (from outside, along the face normal)."""
    mesh = obj.data
    now = positions(mesh)
    if len(now) != len(before):
        return []
    moved = np.abs(now - before).max(axis=1) > 0
    faces = [p for p in mesh.polygons if any(moved[v] for v in p.vertices)]
    random.Random(3).shuffle(faces)
    out = []
    for poly in faces[:400]:
        c = poly.center
        n = poly.normal
        for t in (0.0, 0.3):
            corner = now[poly.vertices[0]]
            target = [c[i] * (1 - t) + corner[i] * t for i in range(3)]
            origin = tuple(float(target[i] + n[i] * 0.5) for i in range(3))
            out.append((origin, tuple(float(target[i] - origin[i]) for i in range(3))))
    return out


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


def m_foreach_set(obj):
    obj.data.vertices.foreach_set('co', shear(positions(obj.data)).ravel())


def m_co_assign(obj):
    co = shear(positions(obj.data))
    for v in obj.data.vertices:
        if v.co.y < 0:
            v.co = co[v.index]


def m_co_assign_view_layer_update(obj):
    m_co_assign(obj)
    bpy.context.view_layer.update()


def m_bmesh_to_mesh(obj):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    co = shear(positions(obj.data))
    for v in bm.verts:
        v.co = co[v.index]
    bm.to_mesh(obj.data)
    bm.free()


ZIGZAG = [(x, 60 * math.sin(x / 25)) for x in range(-200, 201, 10)]


def make_stroke(name, points=ZIGZAG, size=60):
    def mutate(obj):
        brush(name)
        stroke(points, size)
    return mutate


def m_draw_undo(obj):
    make_stroke('Draw')(obj)
    with bpy.context.temp_override(**view()):
        bpy.ops.ed.undo()


def m_draw_undo_redo(obj):
    m_draw_undo(obj)
    with bpy.context.temp_override(**view()):
        bpy.ops.ed.redo()


def hide_right_half(obj):
    mesh = obj.data
    mask = (mesh.attributes.get('.sculpt_mask')
            or mesh.attributes.new('.sculpt_mask', 'FLOAT', 'POINT'))
    mask.data.foreach_set('value', (positions(mesh)[:, 0] > 0).astype(np.float32))
    with bpy.context.temp_override(**view()):
        bpy.ops.paint.hide_show_masked(action='HIDE')
        bpy.ops.paint.mask_flood_fill(mode='VALUE', value=0.0)


def reveal(obj):
    with bpy.context.temp_override(**view()):
        bpy.ops.paint.hide_show_all(action='SHOW')


def m_hide_draw_reveal(obj):
    hide_right_half(obj)
    brush('Draw')
    stroke([(-4, y) for y in range(-150, 151, 10)], size=40)
    reveal(obj)


def m_hide_grab_reveal(obj):
    hide_right_half(obj)
    brush('Grab')
    stroke([(-6, 0), (-30, 0), (-60, 10)], size=80)
    reveal(obj)


def m_hide_filter_reveal(obj):
    hide_right_half(obj)
    with bpy.context.temp_override(**view()):
        bpy.ops.sculpt.mesh_filter(type='INFLATE', strength=0.8)
    reveal(obj)


def m_hide_reveal(obj):
    hide_right_half(obj)
    reveal(obj)


def m_mesh_filter(obj):
    with bpy.context.temp_override(**view()):
        bpy.ops.sculpt.mesh_filter(type='INFLATE', strength=0.8)


def m_mask_face_sets(obj):
    mesh = obj.data
    mask = (mesh.attributes.get('.sculpt_mask')
            or mesh.attributes.new('.sculpt_mask', 'FLOAT', 'POINT'))
    mask.data.foreach_set('value', np.ones(len(mesh.vertices), np.float32))
    with bpy.context.temp_override(**view()):
        bpy.ops.sculpt.face_sets_init(mode='LOOSE_PARTS')


def m_dyntopo_on_off(obj):
    with bpy.context.temp_override(**view()):
        bpy.ops.sculpt.dynamic_topology_toggle()
        bpy.ops.sculpt.dynamic_topology_toggle()


def p_shape_key(obj):
    bpy.ops.object.mode_set(mode='OBJECT')
    obj.shape_key_add(name='Basis')
    key = obj.shape_key_add(name='K')
    co = np.empty(len(key.data) * 3, np.float32)
    key.data.foreach_get('co', co)
    key.data.foreach_set('co', shear(co.reshape(-1, 3)).ravel())
    obj.active_shape_key_index = 0
    enter(obj)


def m_shape_key_switch(obj):
    obj.active_shape_key_index = 1
    bpy.context.view_layer.update()


def m_shape_key_switch_no_update(obj):
    obj.active_shape_key_index = 1


def m_shape_key_draw(obj):
    obj.active_shape_key_index = 1
    bpy.context.view_layer.update()
    make_stroke('Draw')(obj)


def p_displace(obj):
    bpy.ops.object.mode_set(mode='OBJECT')
    mod = obj.modifiers.new('D', 'DISPLACE')
    mod.strength = 0.05
    enter(obj)


def m_displace_strength(obj):
    obj.modifiers['D'].strength = 0.3
    bpy.context.view_layer.update()


def m_displace_draw(obj):
    make_stroke('Draw')(obj)


def m_transform_object(obj):
    obj.location = (3.0, -2.0, 1.0)
    obj.rotation_euler = (0.3, 0.2, 0.1)
    bpy.context.view_layer.update()


def m_revert_file(obj):
    bpy.ops.wm.revert_mainfile()
    enter(bpy.data.objects['monkey'])


MUTATIONS = {
    'foreach_set': (None, m_foreach_set),
    'co_assign': (None, m_co_assign),
    'co_assign_view_layer_update': (None, m_co_assign_view_layer_update),
    'bmesh_to_mesh': (None, m_bmesh_to_mesh),
    'draw': (None, make_stroke('Draw')),
    'grab': (None, make_stroke('Grab', [(0, 0), (40, 20), (80, 40)], 80)),
    'smooth': (None, make_stroke('Smooth', ZIGZAG, 80)),
    'pose': (None, make_stroke('Pose', [(-120, 40), (-80, 90)], 80)),
    'boundary': (None, make_stroke('Boundary', [(150, -60), (190, -20)], 80)),
    'elastic': (None, make_stroke('Elastic Grab', [(0, 0), (50, 30)], 80)),
    'snake_hook': (None, make_stroke('Snake Hook', [(0, 0), (30, 10), (60, 30)], 60)),
    'draw_undo': (None, m_draw_undo),
    'draw_undo_redo': (None, m_draw_undo_redo),
    'hide_reveal': (None, m_hide_reveal),
    'hide_draw_reveal': (None, m_hide_draw_reveal),
    'hide_grab_reveal': (None, m_hide_grab_reveal),
    'hide_filter_reveal': (None, m_hide_filter_reveal),
    'mesh_filter': (None, m_mesh_filter),
    'mask_face_sets': (None, m_mask_face_sets),
    'dyntopo_on_off': (None, m_dyntopo_on_off),
    'shape_key_switch': (p_shape_key, m_shape_key_switch),
    'shape_key_switch_no_update': (p_shape_key, m_shape_key_switch_no_update),
    'shape_key_draw': (p_shape_key, m_shape_key_draw),
    'displace_strength': (p_displace, m_displace_strength),
    'displace_draw': (p_displace, m_displace_draw),
    'transform_object': (None, m_transform_object),
    'revert_file': (None, m_revert_file),
}

result = {}
for name, (prepare, mutate) in MUTATIONS.items():
    if ONLY and name != ONLY:
        continue
    bpy.ops.wm.open_mainfile(filepath=FIXTURE)
    obj = bpy.data.objects['monkey']
    enter(obj)
    bpy.ops.ed.undo_push(message='fixture')
    if prepare:
        prepare(obj)
        bpy.ops.ed.undo_push(message='prepared')
    rays = probes(obj)
    for origin, direction in rays:
        for _ in range(18):
            ask(obj, origin, direction)
    before = positions(bpy.data.objects['monkey'].data)
    status = 'ok'
    try:
        mutate(obj)
    except Exception as error:  # noqa: BLE001 - recorded in the output
        status = f'mutation failed: {error}'
    obj = bpy.data.objects['monkey']
    moved = int((np.abs(positions(obj.data) - before).max(axis=1) > 0).sum())
    rays = rays + targeted(obj, before)
    after = [ask(obj, o, d) for o, d in rays]
    enter(obj)
    fresh = [ask(obj, o, d) for o, d in rays]
    result[name] = {'status': status, 'moved': moved, 'after': after, 'fresh': fresh}
    print(name, status, 'moved', moved, 'after!=fresh',
          sum(a != f for a, f in zip(after, fresh)), 'of', len(rays), flush=True)
with open(OUT, 'w') as f:
    json.dump(result, f)
print('wrote', OUT)
