# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every object of the heavy scene (``heavy_scene``): name, place, material, parent and how its mesh is made.

``plan()`` is cheap (no geometry): a list of ``Entry`` in creation order.
``geometry(entry)`` makes an entry's mesh arrays once per process and keeps
them, keyed by the mesh's name, so objects that share a mesh (the merlons)
share one array set. No bpy: the host tests, the generator and the graders
all read the same plan.
"""

import math
from collections import namedtuple

import castle_kit
import heavy_scene as scene

Entry = namedtuple("Entry", "name collection mesh location rotation materials parent kind")
# kind: "mesh", "camera" or "sun". mesh: the mesh's name (shared meshes share it). rotation: Z only, radians.

FLAG, WATCHTOWER, WATCHTOWER_ROOF = "Flag", "Watchtower", "Watchtower Roof"
STATUE, RELIEF, TERRAIN = "Courtyard Statue", "Keep Relief", "Terrain"
CAMERA, SUN = "Castle Camera", "Sun"
CAMERA_AT, CAMERA_TARGET, SUN_ELEVATION, SUN_AZIMUTH = (120.0, -170.0, 60.0), (0.0, 0.0, 8.0), 32.0, -135.0
_MAKERS = {}
_CACHE = {}


def _entry(name, collection, mesh, location=(0.0, 0.0, 0.0), rotation=0.0, materials=("Castle Stone",), parent=None,
           kind="mesh", make=None):
    if make is not None:
        _MAKERS.setdefault(mesh, make)
    location = tuple(float(v) for v in scene.q(location))
    return Entry(name, collection, mesh, location, round(float(rotation), 6), tuple(materials), parent, kind)


def _castle():
    entries = []
    for side, ((x0, y0), (x1, y1), flipped) in scene.WALLS.items():
        length = math.hypot(x1 - x0, y1 - y0)
        entries.append(_entry("Castle Wall " + side, "Castle", "Castle Wall " + side, (x0, y0, 0.0),
                              math.atan2(y1 - y0, x1 - x0), make=lambda length=length, flipped=flipped:
                              scene.wall(length, flipped)))
    boxes = [box for _, _, merlons in castle_kit.wall_runs().values() for box in merlons]
    front, back = castle_kit.GATE_Y[0] + castle_kit.MERLON[1] / 2, castle_kit.GATE_Y[1] - castle_kit.MERLON[1] / 2
    boxes += castle_kit.merlon_row(*castle_kit.GATE_X, True, front, castle_kit.GATE_TOP)
    boxes += castle_kit.merlon_row(*castle_kit.GATE_X, True, back, castle_kit.GATE_TOP)
    merlon_centre = (0.0, 0.0, castle_kit.MERLON[2] / 2)
    merlon = lambda: scene.from_kit(*castle_kit.oriented_box(castle_kit.MERLON, merlon_centre, 0.0))
    for index, (size, (x, y, z)) in enumerate(boxes, 1):
        entries.append(_entry("Castle Merlon %03d" % index, "Castle", "Merlon", (x, y, z - size[2] / 2),
                              0.0 if size[0] >= size[1] else math.pi / 2, make=merlon))
    for tower_id, ((x, y), radius, top, apex) in castle_kit.TOWERS.items():
        name = "Castle Tower " + tower_id
        entries.append(_entry(name, "Castle", name, (x, y, 0.0), make=lambda r=radius, t=top: scene.cylinder(
            r, t, scene.TOWER_SEGMENTS, scene.TOWER_STEP, jointed=True)))
        entries.append(_entry(name + " Roof", "Castle", name + " Roof", (x, y, 0.0), materials=("Castle Slate",),
                              make=lambda r=radius, t=top, a=apex: scene.cone(r + castle_kit.ROOF_OVERHANG, t, a,
                                                                               scene.TOWER_SEGMENTS, 40)))
    entries.append(_entry("Castle Gatehouse", "Castle", "Castle Gatehouse", make=lambda: scene.from_kit(
        *castle_kit.prism_xz(castle_kit.gate_profile(), *castle_kit.GATE_Y))))
    (cx, cy), (sx, sy) = castle_kit.KEEP_CENTRE, castle_kit.KEEP_SIZE
    entries.append(_entry("Castle Keep", "Castle", "Castle Keep", (cx, cy, 0.0), make=lambda: scene.from_kit(
        *castle_kit.oriented_box((sx, sy, castle_kit.KEEP_EAVES), (0.0, 0.0, castle_kit.KEEP_EAVES / 2), 0.0))))
    half = sy / 2 + castle_kit.ROOF_OVERHANG
    profile = [(-half, castle_kit.KEEP_EAVES - 0.3), (half, castle_kit.KEEP_EAVES - 0.3), (0.0, castle_kit.KEEP_RIDGE)]
    entries.append(_entry("Castle Keep Roof", "Castle", "Castle Keep Roof", (cx, cy, 0.0), materials=("Castle Slate",),
                          make=lambda: scene.from_kit(*castle_kit.prism_yz(profile, -sx / 2 - 0.5, sx / 2 + 0.5))))
    for index, (centre, yaw) in enumerate(castle_kit.window_spots(), 1):
        name = "Castle Window %02d" % index
        entries.append(_entry(name, "Castle", name, materials=("Window Stone", "Window Glass"),
                              make=lambda c=centre, y=yaw: _window(c, y)))
    pedestal = lambda: scene.from_kit(*castle_kit.oriented_box((2.0, 2.0, 1.2), (0.0, 0.0, 0.6), 0.0))
    entries.append(_entry("Statue Pedestal", "Castle", "Statue Pedestal", (-9.0, -4.0, 0.0), make=pedestal))
    entries.append(_entry(STATUE, "Castle", STATUE, (-9.0, -4.0, 1.2), materials=("Statue Bronze",), make=scene.statue))
    entries.append(_entry(RELIEF, "Castle", RELIEF, (0.0, cy - sy / 2 - 0.02, 15.5), make=scene.relief))
    entries.append(_entry(FLAG, "Castle", FLAG, (*scene.FLAG_AT, 0.0), materials=("Timber", "Flag Cloth"),
                          make=_flag))
    return entries


def _window(centre, yaw):
    frame, glass = castle_kit.window_parts(centre, yaw)
    positions, sizes, corners = scene.from_kit(*castle_kit.merged([frame, glass]))
    indices = [0] * len(frame[1]) + [1] * len(glass[1])
    return positions, sizes, corners, indices


def _flag():
    pole = scene.cylinder(0.06, 6.0, 12, 1.0)
    import numpy
    xs, zs = numpy.linspace(0.06, 1.56, 16), numpy.linspace(4.9, 5.9, 11)
    x, z = numpy.meshgrid(xs, zs)
    cloth = (scene.q(numpy.stack([x.ravel(), 0.02 * numpy.sin(4 * x.ravel()), z.ravel()], axis=1)),) + \
        scene.grid_faces(16, 11)
    merged = scene.merge(pole, cloth)
    return merged + ([0] * len(pole[1]) + [1] * len(cloth[1]),)


def _ring_point(stream, low, high, avoid=()):
    while True:
        radius, angle = stream.uniform(low, high), stream.uniform(0.0, 2 * math.pi)
        x, y = radius * math.cos(angle), radius * math.sin(angle)
        if all(math.hypot(x - ax, y - ay) > clearance for ax, ay, clearance in avoid):
            return x, y


def _land():
    entries, stream = [], scene.Stream(scene.SEED)
    entries.append(_entry(TERRAIN, "Terrain", TERRAIN, materials=("Meadow Grass",), make=scene.terrain))
    avoid = ((scene.WATCHTOWER_AT[0] + 5.0, scene.WATCHTOWER_AT[1], 14.0),)   # Its place and where it moves to.
    order = sorted(range(scene.TREES), key=lambda index: stream.uniform())
    error = {index: "float" for index in order[:scene.FLOATING]}
    error.update({index: "sunk" for index in order[scene.FLOATING:scene.FLOATING + scene.SUNK]})
    for index in range(scene.TREES):
        x, y = _ring_point(stream, 90.0, 190.0, avoid)
        size, turn = stream.uniform(0.8, 1.5), stream.uniform(0.0, 2 * math.pi)
        offset = {"float": stream.uniform(0.5, 2.5), "sunk": -stream.uniform(0.5, 1.5)}.get(error.get(index), 0.0)
        name = "Tree %03d" % (index + 1)
        entries.append(_entry(name, "Forest", name, (x, y, scene.surface(x, y) + offset), turn, ("Bark", "Leaves"),
                              make=lambda s=size, seed=index: _tree(s, seed)))
    for index in range(scene.ROCKS):
        x, y = _ring_point(stream, 30.0, 195.0, avoid + ((0.0, 0.0, 26.0),))
        size, turn = stream.uniform(0.3, 1.6), stream.uniform(0.0, 2 * math.pi)
        name = "Rock %04d" % (index + 1)
        entries.append(_entry(name, "Rocks", name, (x, y, scene.surface(x, y) - 0.25 * size), turn, ("Rock",),
                              make=lambda s=size, seed=index: scene.sphere(s / 2, 12, 8, scale=(1.0, 0.85, 0.6),
                                                                         lumps=scene.Stream(seed + 7))))
    return entries


def _tree(size, seed):
    positions, sizes, corners = scene.tree(size, scene.Stream(seed + 1))
    trunk_faces = 12 * (int(round(3.0 * size / (0.75 * size)))) + 2
    return positions, sizes, corners, [0] * trunk_faces + [1] * (len(sizes) - trunk_faces)


def _village():
    entries, stream = [], scene.Stream(scene.SEED + 1)
    house = lambda: scene.from_kit(*castle_kit.oriented_box((6.0, 5.0, 4.0), (0.0, 0.0, 2.0), 0.0))
    roof = lambda: scene.from_kit(*castle_kit.prism_yz([(-2.9, 3.8), (2.9, 3.8), (0.0, 6.4)], -3.3, 3.3))
    for index in range(scene.HOUSES):
        # Two rows round the south-east of the plain, 8 degrees apart: no two houses touch.
        angle = math.radians(-165.0 + 8.0 * (index // 2) + 4.0 * (index % 2))
        radius = (50.0 if index % 2 else 66.0) + stream.uniform(-1.5, 1.5)
        x, y, turn = radius * math.cos(angle), radius * math.sin(angle), stream.uniform(-0.4, 0.4)
        name = "House %02d" % (index + 1)
        entries.append(_entry(name, "Village", "House", (x, y, 0.0), turn, ("Plaster",), make=house))
        entries.append(_entry(name + " Roof", "Village", "House Roof", (x, y, 0.0), turn, ("Roof Tiles",), make=roof))
    post = lambda: scene.from_kit(*castle_kit.oriented_box((0.15, 0.15, 1.2), (0.0, 0.0, 0.6), 0.0))
    for index in range(scene.POSTS):
        x, y = (-3.0 if index % 2 else 3.0), -22.0 - 0.5 * (index // 2)
        entries.append(_entry("Fence Post %03d" % (index + 1), "Village", "Fence Post", (x, y, scene.surface(x, y)),
                              materials=("Timber",), make=post))
    crate = lambda: scene.from_kit(*castle_kit.oriented_box((0.8, 0.8, 0.8), (0.0, 0.0, 0.4), 0.0))
    for index in range(scene.CRATES):
        entries.append(_entry("Crate %03d" % (index + 1), "Props", "Crate", (-17.0 + (index % 10) * 0.9,
                                                                             -12.0 + (index // 10) * 0.9, 0.0),
                              stream.uniform(0.0, 0.5), ("Timber",), make=crate))
    barrel = lambda: scene.cylinder(0.35, 0.9, 16, 0.3)
    for index in range(scene.BARRELS):
        entries.append(_entry("Barrel %03d" % (index + 1), "Props", "Barrel", (11.0 + (index % 8) * 0.8,
                                                                               -12.0 + (index // 8) * 0.8, 0.0),
                              materials=("Timber",), make=barrel))
    x, y = scene.WATCHTOWER_AT
    entries.append(_entry(WATCHTOWER, "Village", WATCHTOWER, (x, y, 0.0), materials=("Castle Stone",),
                          make=lambda: scene.cylinder(2.4, 12.0, 8, 0.5)))
    entries.append(_entry(WATCHTOWER_ROOF, "Village", WATCHTOWER_ROOF, (0.0, 0.0, 12.0), materials=("Castle Slate",),
                          parent=WATCHTOWER, make=lambda: scene.cone(3.0, 0.0, 5.0, 8, 4)))
    entries.append(_entry(CAMERA, "Lighting", None, CAMERA_AT, kind="camera", materials=()))
    entries.append(_entry(SUN, "Lighting", None, (0.0, 0.0, 60.0), kind="sun", materials=()))
    return entries


def plan():
    """Every object, in creation order (parents before children)."""
    if "plan" not in _CACHE:
        _CACHE["plan"] = _land() + _castle() + _village()
    return _CACHE["plan"]


def entry(name):
    return next(item for item in plan() if item.name == name)


def geometry(item):
    """``(positions, sizes, corners, material indices or None)`` of an entry's mesh, made once per process."""
    plan()
    key = ("mesh", item.mesh)
    if key not in _CACHE:
        made = _MAKERS[item.mesh]()
        _CACHE[key] = tuple(made) if len(made) == 4 else tuple(made) + (None,)
    return _CACHE[key]


def counts():
    """``{"objects", "meshes", "vertices" (unique meshes), "scene_vertices" (every object), "faces"}``."""
    seen, totals = {}, {"objects": len(plan()), "meshes": 0, "vertices": 0, "scene_vertices": 0, "faces": 0}
    for item in plan():
        if item.kind != "mesh":
            continue
        positions, sizes, _, _ = geometry(item)
        totals["scene_vertices"] += len(positions)
        if item.mesh not in seen:
            seen[item.mesh] = True
            totals["meshes"] += 1
            totals["vertices"] += len(positions)
            totals["faces"] += len(sizes)
    return totals
