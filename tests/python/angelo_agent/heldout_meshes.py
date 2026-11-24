# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The held-out UV meshes (doc/angelo/uv_layout.rst, "Benchmark against Blender's packer"), generated with fixed seeds
and never used for development: a hard-surface kit, a smart-projected organic mesh, a triangle soup, an architectural
set unwrapped by its sharp edges, and a scan-like surface of about 10,000 islands. Shared by the benchmark
(``bench_uv_pack_heldout.py``, whose rule was fixed before its first run) and ``test_blender_uv_layout`` (the margin a
layout picks for each). Moved here unchanged from the benchmark: the same code and seeds make the same meshes.
Blender only (``bpy``)."""

import math
import random

import bmesh
import bpy
from mathutils import Vector, noise


def link(name, bm):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def hard_surface_kit():
    """16 beveled parts (boxes, cylinders, wedges) of spread sizes: hundreds of islands once smart-projected."""
    rng = random.Random(5101)
    bm = bmesh.new()
    for k in range(16):
        part = bmesh.new()
        kind = k % 3
        if kind == 0:
            bmesh.ops.create_cube(part, size=1.0)
        elif kind == 1:
            bmesh.ops.create_cone(part, cap_ends=True, segments=rng.choice((8, 12, 16)), radius1=0.5, radius2=0.5,
                                  depth=1.0)
        else:
            bmesh.ops.create_cube(part, size=1.0)
            top = [v for v in part.verts if v.co.z > 0 and v.co.x > 0]
            bmesh.ops.translate(part, verts=top, vec=(0.0, 0.0, -0.8))
        bmesh.ops.bevel(part, geom=part.edges[:], offset=0.05, segments=2, affect='EDGES')
        scale = Vector((rng.uniform(0.2, 2.0), rng.uniform(0.2, 2.0), rng.uniform(0.2, 1.5)))
        offset = Vector((rng.uniform(-6, 6), rng.uniform(-6, 6), rng.uniform(0, 3)))
        for v in part.verts:
            v.co = v.co * scale + offset
        mesh = bpy.data.meshes.new("part")
        part.to_mesh(mesh)
        part.free()
        bm.from_mesh(mesh)
        bpy.data.meshes.remove(mesh)
    return link("HardSurfaceKit", bm), ("SMART", 66.0)


def organic():
    """A subdivided sphere displaced by noise (a sculpt-like blob), smart-projected."""
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=5, radius=1.0)
    for v in bm.verts:
        n = noise.fractal(v.co * 2.3 + Vector((5.2, 1.3, 7.7)), 0.6, 2.1, 4)
        v.co *= 1.0 + 0.35 * n
    return link("Organic", bm), ("SMART", 66.0)


def triangle_soup():
    """3,000 unconnected triangles of spread sizes and shapes (a generated asset with no shared edges)."""
    rng = random.Random(5303)
    bm = bmesh.new()
    for _ in range(3000):
        base = Vector((rng.uniform(-10, 10), rng.uniform(-10, 10), rng.uniform(-10, 10)))
        size = 0.02 + rng.random() ** 3 * 1.5
        points = [base + Vector((rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 1))) * size for _ in range(3)]
        bm.faces.new([bm.verts.new(p) for p in points])
    return link("TriangleSoup", bm), ("SMART", 66.0)


def architecture():
    """Walls with window and door openings (each wall built from panels), a stair and trim: unwrapped by seams on
    its sharp edges, as a modeller would."""
    rng = random.Random(5404)
    bm = bmesh.new()

    def box(lo, hi):
        made = bmesh.ops.create_cube(bm, size=1.0)["verts"]
        for v in made:
            v.co = Vector(lo[i] + (v.co[i] + 0.5) * (hi[i] - lo[i]) for i in range(3))
    for wall in range(6):   # Six walls of panels around openings.
        x0, length, height = wall * 7.0, 6.0, 3.0
        cursor = 0.0
        while cursor < length:
            width = rng.uniform(0.6, 1.6)
            gap = rng.choice((0.0, 0.0, 0.9, 1.2))
            box((x0 + cursor, 0, 0), (x0 + min(cursor + width, length), 0.25, height))
            if gap and cursor + width + gap < length:   # A window: a sill and a lintel across the gap.
                box((x0 + cursor + width, 0, 0), (x0 + cursor + width + gap, 0.25, 0.9))
                box((x0 + cursor + width, 0, 2.2), (x0 + cursor + width + gap, 0.25, height))
            cursor += width + gap
        box((x0, -0.1, height), (x0 + length, 0.35, height + 0.15))   # Trim.
    for step in range(16):   # A stair.
        box((-3.0, step * 0.3, 0), (-1.8, step * 0.3 + 0.3, (step + 1) * 0.18))
    return link("Architecture", bm), ("SEAMS", 60.0)


def scan():
    """A dense noisy surface (a scan-like ground), smart-projected at a low angle: about 10,000 islands."""
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=120, y_segments=120, size=10.0)
    for v in bm.verts:
        v.co.z = 1.6 * noise.fractal(v.co * 0.9 + Vector((3.1, 9.4, 2.6)), 0.7, 2.2, 6)
    return link("Scan", bm), ("SMART", 25.0)


HELD_OUT = (hard_surface_kit, organic, triangle_soup, architecture, scan)


def edit(obj):
    if bpy.context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for other in bpy.context.view_layer.objects:
        other.select_set(other is obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')


def unwrap(obj, how):
    method, angle = how
    edit(obj)
    if method == "SMART":
        bpy.ops.uv.smart_project(angle_limit=math.radians(angle), island_margin=0.0, scale_to_bounds=False)
    else:
        bpy.ops.mesh.select_all(action='DESELECT')
        bpy.ops.mesh.edges_select_sharp(sharpness=math.radians(angle))
        bpy.ops.mesh.mark_seam(clear=False)
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.uv.unwrap(method='ANGLE_BASED', margin=0.0)
    bpy.ops.object.mode_set(mode='OBJECT')
    uvs = [0.0] * (len(obj.data.loops) * 2)
    obj.data.uv_layers.active.data.foreach_get("uv", uvs)
    return uvs
