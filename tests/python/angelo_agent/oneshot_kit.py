# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stand-in builders shared by the one-shot tests: a broadleaf tree and a stone cottage.

A stand-in is the plainest thing that follows a brief to the letter. A
test's closed checks are derived from it, the reference job proves that the
checks pass on it and fail on the factory scene, and the dry run's scripted
models build it. It is not a target look: where a brief attaches a picture,
the picture is the look and the stand-in only shows that the brief can be
met. Everything is deterministic (seeded) and made of plain meshes.

* ``tree``: '<prefix> Trunk', limbs '<prefix> Branch NN' that rise from a
  fork and divide, and '<prefix> Foliage', a few thousand separate leaf
  cards gathered around the branch tips.
* ``cottage``: '<prefix> Walls' with gable ends, a pitched '<prefix> Roof'
  of tile courses, '<prefix> Chimney', '<prefix> Door', windows
  '<prefix> Window NN' with frames, glazing bars and glowing glass, and a
  wall 'Lantern' with its 'Lantern Light'.
"""

import math
import random

import scenario_build_kit as kit


# ---------------------------------------------------------------- small vector helpers

def clear_default_scene(bpy):
    """The factory scene's Cube, Camera and Light removed with the data only they used (the cube's mesh and its
    'Material', which a save would otherwise write unused): the stand-ins follow "no unused leftovers" too."""
    found = [bpy.data.objects.get(name) for name in ("Cube", "Camera", "Light")]
    data = [(obj.data, obj.type) for obj in found if obj is not None and obj.data is not None]
    materials = [slot for item, _kind in data for slot in getattr(item, "materials", None) or () if slot is not None]
    kit.remove_default_objects(bpy)
    collections = {"MESH": bpy.data.meshes, "CAMERA": bpy.data.cameras, "LIGHT": bpy.data.lights}
    for item, kind in data:
        if item.users == 0 and kind in collections:
            collections[kind].remove(item)
    for material in materials:
        if material.users == 0:
            bpy.data.materials.remove(material)


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(a, factor):
    return (a[0] * factor, a[1] * factor, a[2] * factor)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a):
    length = math.sqrt(_dot(a, a)) or 1.0
    return (a[0] / length, a[1] / length, a[2] / length)


def bezier(start, control, end, steps):
    """``steps + 1`` points of a quadratic curve from ``start`` to ``end``."""
    points = []
    for index in range(steps + 1):
        t = index / steps
        points.append(_add(_add(_scale(start, (1 - t) ** 2), _scale(control, 2 * t * (1 - t))), _scale(end, t * t)))
    return points


def tube_geometry(points, radii, segments=8, offset=0):
    """Vertices and faces of a round tube along a polyline, capped at both ends.

    The rings are carried along the line without twisting (each ring's
    reference direction is the last one, made square to the new tangent).
    """
    vertices, faces, normal, count = [], [], None, len(points)
    for index, (point, radius) in enumerate(zip(points, radii)):
        tangent = _unit(_sub(points[min(index + 1, count - 1)], points[max(index - 1, 0)]))
        if normal is None:
            normal = _unit(_cross(tangent, (1.0, 0.0, 0.0) if abs(tangent[0]) < 0.9 else (0.0, 1.0, 0.0)))
        else:
            normal = _unit(_sub(normal, _scale(tangent, _dot(normal, tangent))))
        binormal = _cross(tangent, normal)
        for step in range(segments):
            angle = 2.0 * math.pi * step / segments
            vertices.append(_add(point, _add(_scale(normal, radius * math.cos(angle)),
                                             _scale(binormal, radius * math.sin(angle)))))
    for ring in range(count - 1):
        for step in range(segments):
            a, b = offset + ring * segments + step, offset + ring * segments + (step + 1) % segments
            faces.append((a, b, b + segments, a + segments))
    faces.append(tuple(offset + step for step in reversed(range(segments))))
    faces.append(tuple(offset + (count - 1) * segments + step for step in range(segments)))
    return vertices, faces


def merged(parts):
    """One vertex list and one face list from ``[(vertices, faces)]`` whose faces index their own vertices."""
    vertices, faces = [], []
    for part_vertices, part_faces in parts:
        shift = len(vertices)
        vertices += part_vertices
        faces += [tuple(index + shift for index in face) for face in part_faces]
    return vertices, faces


# ---------------------------------------------------------------- the tree

def _taper(low, high, count):
    return [low + (high - low) * index / (count - 1) for index in range(count)]


def tree_plan(height=6.3, seed=7):
    """The tree's skeleton: ``{"trunk", "limbs": [[(points, radii), ...]], "tips", "fork"}`` (pure numbers)."""
    rng, s = random.Random(seed), height / 6.3
    fork = 2.35 * s
    levels = [-0.15, 0.15, 0.6, 1.1, 1.6, 2.0, 2.35]
    trunk = ([(0.06 * s * math.sin(1.3 * z), 0.04 * s * math.sin(0.9 * z + 1.0), z * s) for z in levels],
             [radius * s for radius in (0.24, 0.21, 0.19, 0.17, 0.155, 0.14, 0.13)])
    top = trunk[0][-1]
    limbs, tips = [], []
    for index in range(7):
        leader = index == 6
        azimuth = math.radians(60.0 * index + rng.uniform(-12.0, 12.0))
        reach = s * (0.3 if leader else rng.uniform(1.1, 1.45))
        tip = (top[0] + reach * math.cos(azimuth), top[1] + reach * math.sin(azimuth),
               0.9 * height if leader else s * rng.uniform(4.5, 5.2))
        control = (top[0] + 0.3 * reach * math.cos(azimuth), top[1] + 0.3 * reach * math.sin(azimuth),
                   fork + 0.6 * (tip[2] - fork))
        line = bezier((top[0], top[1], fork - 0.12 * s), control, tip, 10)
        tubes = [(line, _taper(0.085 * s, 0.02 * s, len(line)))]
        tips.append(tip)
        for step, side in ((5, 1.0), (7, -1.0), (9, 1.0)):
            turn = azimuth + side * math.radians(rng.uniform(40.0, 70.0))
            rise, length = math.radians(rng.uniform(20.0, 45.0)), s * rng.uniform(0.6, 0.85)
            start = line[step]
            end = (start[0] + length * math.cos(rise) * math.cos(turn),
                   start[1] + length * math.cos(rise) * math.sin(turn), start[2] + length * math.sin(rise))
            middle = _add(_scale(_add(start, end), 0.5), (0.0, 0.0, 0.08 * s))
            twig = bezier(start, middle, end, 3)
            tubes.append((twig, _taper(0.032 * s, 0.012 * s, len(twig))))
            tips.append(end)
        limbs.append(tubes)
    return {"trunk": trunk, "limbs": limbs, "tips": tips, "fork": fork, "scale": s}


def leaf_cards(tips, count, scale, seed):
    """``count`` separate leaf cards (one quad each) in clumps around the branch tips."""
    rng, vertices, faces = random.Random(seed + 101), [], []
    across, tall, half_long, half_wide = 0.6 * scale, 0.44 * scale, 0.085 * scale, 0.05 * scale
    for index in range(count):
        centre = tips[index % len(tips)]
        while True:   # A point inside the clump's ellipsoid.
            offset = (rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1))
            if _dot(offset, offset) <= 1.0:
                break
        middle = (centre[0] + across * offset[0], centre[1] + across * offset[1], centre[2] + tall * offset[2])
        along = _unit((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-0.6, 0.6)))
        side = _unit(_cross(along, (rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1) + 1.5)))
        a, b = _scale(along, half_long), _scale(side, half_wide)
        base = len(vertices)
        vertices += [_sub(_sub(middle, a), b), _sub(_add(middle, a), b), _add(_add(middle, a), b),
                     _add(_sub(middle, a), b)]
        faces.append((base, base + 1, base + 2, base + 3))
    return vertices, faces


def tree(bpy, prefix="Tree", origin=(0.0, 0.0, 0.0), height=6.3, seed=7, leaves=3200, materials=None):
    """A plain broadleaf tree rooted at ``origin``; returns its objects ``{"trunk", "branches", "foliage"}``.

    ``materials``: ``(bark, leaf)`` to share between trees; made when not given.
    """
    plan = tree_plan(height, seed)
    bark, leaf = materials or (
        kit.principled(bpy, prefix + " Bark", (0.16, 0.115, 0.075), roughness=0.9),
        kit.principled(bpy, prefix + " Leaves", (0.10, 0.30, 0.06), roughness=0.6))
    trunk = kit.mesh_object(bpy, prefix + " Trunk", *tube_geometry(*plan["trunk"], segments=12), location=origin,
                            smooth=True)
    kit.assign(trunk, bark)
    branches = []
    for index, tubes in enumerate(plan["limbs"]):
        limb = kit.mesh_object(bpy, "%s Branch %02d" % (prefix, index + 1), *merged(
            [tube_geometry(points, radii, segments=6) for points, radii in tubes]), location=origin, smooth=True)
        kit.assign(limb, bark)
        branches.append(limb)
    foliage = kit.mesh_object(bpy, prefix + " Foliage", *leaf_cards(plan["tips"], leaves, plan["scale"], seed),
                              location=origin)
    kit.assign(foliage, leaf)
    return {"trunk": trunk, "branches": branches, "foliage": foliage}


# ---------------------------------------------------------------- the cottage

WALL, OVERHANG, COURSES = 0.35, 0.35, 12
WINDOW_SIZE, SILL = (1.0, 1.2), 1.0
STONE, TERRACOTTA, TIMBER = (0.36, 0.33, 0.29), (0.55, 0.22, 0.12), (0.25, 0.13, 0.06)
WARM_GLASS, LANTERN_AT = (1.0, 0.62, 0.25), (1.0, 1.9)   # The lantern: metres right of the door, metres up.


def _prism(profile, x_low, x_high):
    """A prism along X whose cross-section is ``profile`` (``[(y, z)]``, in order around it)."""
    count = len(profile)
    vertices = [(x_low, y, z) for y, z in profile] + [(x_high, y, z) for y, z in profile]
    faces = [(index, (index + 1) % count, (index + 1) % count + count, index + count) for index in range(count)]
    return vertices, faces + [tuple(reversed(range(count))), tuple(range(count, 2 * count))]


def cottage_plan(size=(8.0, 5.5), eaves=2.6, ridge=5.4):
    """Where the cottage's parts are, about its own origin (front wall facing -Y): pure numbers."""
    half_x, half_y = size[0] / 2, size[1] / 2
    front = -half_y - 0.03   # Doors and windows sit a little proud of the wall, so nothing hides them.
    windows = [  # (centre x, centre y, centre z, the wall's outward axis, width, height)
        (-2.4, front, SILL + WINDOW_SIZE[1] / 2, "-y", *WINDOW_SIZE),
        (2.4, front, SILL + WINDOW_SIZE[1] / 2, "-y", *WINDOW_SIZE),
        (-half_x - 0.03, -1.3, SILL + WINDOW_SIZE[1] / 2, "-x", *WINDOW_SIZE),
        (half_x + 0.03, 0.0, SILL + WINDOW_SIZE[1] / 2, "+x", *WINDOW_SIZE),
        (half_x + 0.03, 0.0, eaves + 1.15, "+x", 0.6, 0.7),
    ]
    return {"half": (half_x, half_y), "eaves": eaves, "ridge": ridge, "slope": (ridge - eaves) / half_y,
            "front": front, "windows": windows, "door": (0.0, front, 1.0, 0.9, 2.0),
            "chimney": (-half_x + 0.8, 0.0, 0.8, 6.5), "lantern": (LANTERN_AT[0], -half_y - 0.14, LANTERN_AT[1])}


def _walls(plan):
    half_x, half_y = plan["half"]
    eaves, ridge = plan["eaves"], plan["ridge"]
    parts = [kit.box_geometry((2 * half_x, WALL, eaves), (0.0, side * (half_y - WALL / 2), eaves / 2))
             for side in (-1, 1)]
    parts += [kit.box_geometry((WALL, 2 * half_y, eaves), (side * (half_x - WALL / 2), 0.0, eaves / 2))
              for side in (-1, 1)]
    gable = [(-half_y, eaves), (half_y, eaves), (0.0, ridge - 0.12)]
    parts += [_prism(gable, side * half_x - (WALL if side > 0 else 0.0), side * half_x + (WALL if side < 0 else 0.0))
              for side in (-1, 1)]
    return merged(parts)


def _roof(plan):
    """Two slopes of overlapping tile courses and a ridge cap."""
    half_x, half_y = plan["half"]
    ridge, slope = plan["ridge"], plan["slope"]
    low_x, high_x = -half_x - 0.3, half_x + 0.3
    run = half_y + OVERHANG
    parts = []
    for side in (-1, 1):
        for course in range(COURSES):
            near, far = run * course / COURSES, run * (course + 1) / COURSES   # Distance from the ridge line.
            profile = [(side * near, ridge - slope * near), (side * far, ridge - slope * far + 0.05),
                       (side * far, ridge - slope * far - 0.14), (side * near, ridge - slope * near - 0.14)]
            parts.append(_prism(profile, low_x, high_x))
    parts.append(kit.box_geometry((high_x - low_x, 0.3, 0.12), (0.0, 0.0, ridge + 0.02)))
    return merged(parts)


def _window(centre, outward, width, height):
    """``(frame and glazing bars, glass)`` of one window: boxes in the wall's plane, 6 cm deep."""
    def box(across, up, wide, tall, depth=0.06):
        size = (wide, depth, tall) if outward.endswith("y") else (depth, wide, tall)
        offset = (across, 0.0, up) if outward.endswith("y") else (0.0, across, up)
        return kit.box_geometry(size, _add(centre, offset))
    frame = 0.07
    parts = [box(side * (width / 2 - frame / 2), 0.0, frame, height) for side in (-1, 1)]
    parts += [box(0.0, side * (height / 2 - frame / 2), width, frame) for side in (-1, 1)]
    parts += [box(0.0, 0.0, 0.035, height - 2 * frame), box(0.0, 0.0, width - 2 * frame, 0.035)]
    return merged(parts), box(0.0, 0.0, width - 2 * frame, height - 2 * frame, depth=0.02)


def cottage(bpy, prefix="Cottage", origin=(0.0, 0.0, 0.0), size=(8.0, 5.5), eaves=2.6, ridge=5.4, lantern=True):
    """A plain stone cottage standing on ``origin`` with its front wall facing -Y; returns its plan."""
    plan = cottage_plan(size, eaves, ridge)
    stone = kit.principled(bpy, prefix + " Stone", STONE, roughness=0.9)
    tiles = kit.principled(bpy, prefix + " Terracotta Tiles", TERRACOTTA, roughness=0.75)
    timber = kit.principled(bpy, prefix + " Timber", TIMBER, roughness=0.7)
    glass = kit.principled(bpy, prefix + " Lit Glass", (0.9, 0.6, 0.3), roughness=0.2, emission=WARM_GLASS,
                           emission_strength=3.0)

    def make(name, geometry, material):
        obj = kit.mesh_object(bpy, name, *geometry, location=origin)
        kit.assign(obj, material)
        return obj
    make(prefix + " Walls", _walls(plan), stone)
    make(prefix + " Roof", _roof(plan), tiles)
    x, y, side, top = plan["chimney"]
    make(prefix + " Chimney", merged([kit.box_geometry((side, side, top - 2.0 - 0.1), (x, y, (top + 1.9) / 2)),
                                      kit.box_geometry((side + 0.15, side + 0.15, 0.1), (x, y, top - 0.05))]), stone)
    x, y, z, width, height = plan["door"]
    make(prefix + " Door", kit.box_geometry((width, 0.08, height), (x, y, z)), timber)
    for index, (x, y, z, outward, width, height) in enumerate(plan["windows"]):
        frame, pane = _window((x, y, z), outward, width, height)
        make("%s Window %02d" % (prefix, index + 1), frame, timber)
        make("%s Window %02d Glass" % (prefix, index + 1), pane, glass)
    if lantern:
        x, y, z = plan["lantern"]
        iron = kit.principled(bpy, "Lantern Iron", (0.03, 0.03, 0.035), roughness=0.5, metallic=1.0)
        glow = kit.principled(bpy, "Lantern Glass", (1.0, 0.7, 0.35), roughness=0.2, emission=(1.0, 0.6, 0.2),
                              emission_strength=6.0)
        body = kit.mesh_object(bpy, "Lantern", *merged([
            kit.box_geometry((0.05, 0.14, 0.05), (x, y + 0.07, z + 0.2)),       # The bracket to the wall.
            kit.box_geometry((0.2, 0.2, 0.03), (x, y, z + 0.16)), kit.box_geometry((0.2, 0.2, 0.03), (x, y, z - 0.16)),
            kit.box_geometry((0.14, 0.14, 0.29), (x, y, z))]), location=origin)
        kit.assign(body, iron, glow)
        for polygon in list(body.data.polygons)[-6:]:   # The last box is the glass.
            polygon.material_index = 1
        kit.point_light(bpy, "Lantern Light", _add(origin, (x, y - 0.02, z)), (1.0, 0.72, 0.4), 60.0, radius=0.05)
    return plan
