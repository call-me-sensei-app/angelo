# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared pieces of the long one-shot tests' stand-ins (``long_kit_apartment``, ``long_kit_street``).

The long tests are large multi-part scenes (a furnished apartment, a high street) written to take one chat tens of
minutes. Their stand-ins are the plainest builds that follow their briefs: every stated size, place and height is
exact, everything else is a simple placeholder shape. This module holds the geometry the two builders share and a
material cache that makes a material only when something uses it (the briefs ask for no unused leftovers).
Pure data API through ``scenario_build_kit``; metres, Z up. A piece is built around its own origin with its front
toward -Y, then placed with ``place`` (``facing`` is the world direction its front looks in, in degrees from +X).
"""

import math

import scenario_build_kit as kit


def combine(parts):
    vertices, faces = [], []
    for part_vertices, part_faces in parts:
        shift = len(vertices)
        vertices += part_vertices
        faces += [tuple(shift + i for i in face) for face in part_faces]
    return vertices, faces


def box(size, centre):
    return kit.box_geometry(size, centre)


def cylinder(radius, depth, centre, segments=20, axis="Z"):
    return kit.cylinder_geometry(radius, depth, segments, centre, axis=axis)


def sphere(radius, centre, segments=16, rings=8, scale=(1.0, 1.0, 1.0)):
    return kit.uv_sphere_geometry(radius, segments, rings, centre, scale=scale)


class Materials:
    """``get(key)`` makes the material the first time a piece asks for it, from ``palette`` {key: (name, rgb, ...)}."""

    def __init__(self, bpy, palette):
        self.bpy, self.palette, self.made = bpy, palette, {}

    def get(self, key):
        if key not in self.made:
            name, colour, *rest = self.palette[key]
            roughness, metallic = (list(rest) + [0.5, 0.0][len(rest):])[:2]
            self.made[key] = kit.principled(self.bpy, name, colour, roughness=roughness, metallic=metallic)
        return self.made[key]


def place(bpy, materials, name, geometry, at, facing=-90.0, keys=("wood",), faces_of=None, collection=None):
    """One object from ``geometry`` at ``at`` (x, y[, z]) turned so its front (-Y) looks toward ``facing`` degrees.

    ``faces_of``: [(material slot, face count)] in face order, when a piece has more than one material.
    """
    location = (at[0], at[1], at[2] if len(at) > 2 else 0.0)
    obj = kit.mesh_object(bpy, name, *geometry, location=location, collection=collection)
    obj.rotation_euler = (0.0, 0.0, math.radians(facing + 90.0))
    kit.assign(obj, *[materials.get(key) for key in keys])
    if faces_of:
        index = 0
        for slot, count in faces_of:
            for polygon in obj.data.polygons[index:index + count]:
                polygon.material_index = slot
            index += count
    return obj


def multi(parts):
    """``[(slot, geometry)]`` -> (combined geometry, faces_of) for ``place``."""
    geometry = combine([part for _slot, part in parts])
    return geometry, [(slot, len(part[1])) for slot, part in parts]


# ------------------------------------------------------------------ furniture archetypes (front toward -Y)

def table(width, depth, top, leg=0.05, slab=0.04):
    legs = [box((leg, leg, top - slab), (sx * (width / 2 - leg), sy * (depth / 2 - leg), (top - slab) / 2))
            for sx in (-1, 1) for sy in (-1, 1)]
    return combine(legs + [box((width, depth, slab), (0.0, 0.0, top - slab / 2))])


def round_table(radius, top, slab=0.04):
    return combine([cylinder(radius, slab, (0.0, 0.0, top - slab / 2), 32),
                    cylinder(0.04, top - slab, (0.0, 0.0, (top - slab) / 2), 12),
                    cylinder(radius * 0.5, 0.03, (0.0, 0.0, 0.015), 24)])


def chair(seat, width=0.45, depth=0.45, back=0.9, leg=0.04):
    legs = [box((leg, leg, seat - 0.04), (sx * (width / 2 - leg), sy * (depth / 2 - leg), (seat - 0.04) / 2))
            for sx in (-1, 1) for sy in (-1, 1)]
    return combine(legs + [box((width, depth, 0.04), (0.0, 0.0, seat - 0.02)),
                           box((width, 0.04, back - seat), (0.0, depth / 2 - 0.02, (seat + back) / 2))])


def sofa(width, depth, seat, back_height=0.85, arm=0.18):
    return combine([box((width, depth, seat), (0.0, 0.0, seat / 2)),
                    box((width, 0.2, back_height - seat), (0.0, depth / 2 - 0.1, (seat + back_height) / 2)),
                    box((arm, depth, 0.2), (-(width - arm) / 2, 0.0, seat + 0.1)),
                    box((arm, depth, 0.2), ((width - arm) / 2, 0.0, seat + 0.1))])


def stool(seat, radius=0.19):
    return combine([cylinder(radius, 0.05, (0.0, 0.0, seat - 0.025), 24),
                    cylinder(0.025, seat - 0.05, (0.0, 0.0, (seat - 0.05) / 2), 10),
                    cylinder(radius * 1.1, 0.02, (0.0, 0.0, 0.01), 24)])


def bookcase(width, depth, height, shelves=5):
    boards = [box((width, depth, 0.03), (0.0, 0.0, min(height - 0.015, 0.015 + i * (height - 0.03) / shelves)))
              for i in range(shelves + 1)]
    sides = [box((0.03, depth, height), (sx * (width / 2 - 0.015), 0.0, height / 2)) for sx in (-1, 1)]
    return combine(boards + sides)


def books(width, depth, height, shelves=5):
    """A row of book blocks on every shelf but the top of a ``bookcase`` of the same size."""
    rows, step = [], (height - 0.03) / shelves
    for i in range(shelves):
        z = 0.03 + i * step
        x = -width / 2 + 0.05
        n = 0
        while x < width / 2 - 0.12:
            w = 0.03 + 0.012 * ((i * 7 + n * 3) % 4)
            h = min(step - 0.05, 0.18 + 0.03 * ((i + n) % 3))
            rows.append(box((w, depth * 0.8, h), (x + w / 2, 0.0, z + h / 2)))
            x += w + 0.004
            n += 1
    return combine(rows)


def lamp(height, shade=0.2):
    """A standard lamp: (geometry, faces_of) with the base and pole in slot 0 and the shade in slot 1."""
    return multi([(0, cylinder(0.14, 0.03, (0.0, 0.0, 0.015), 20)),
                  (0, cylinder(0.012, height - 0.25, (0.0, 0.0, (height - 0.25) / 2), 8)),
                  (1, cylinder(shade, 0.25, (0.0, 0.0, height - 0.125), 20))])


def plant(height, pot=0.18):
    return multi([(0, cylinder(pot, pot * 1.6, (0.0, 0.0, pot * 0.8), 16)),
                  (1, sphere(height * 0.28, (0.0, 0.0, height - height * 0.28), 14, 7, (1.0, 1.0, 1.1)))])


def rug(width, depth):
    return box((width, depth, 0.01), (0.0, 0.0, 0.005))
