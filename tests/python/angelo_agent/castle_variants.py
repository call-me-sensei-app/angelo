# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Castle variants for ``oneshot_variants``: other ways to follow the brief, and deliberate defects.

Each builder starts from the stand-in (``scenario_oneshot_castle.build``) and
either makes one thing another way (every check must still pass) or breaks
one thing the brief asks for (exactly the checks named in ``VARIANTS`` must
fail). Run through ``oneshot_variants.py -- oneshot_castle`` in a background
stock Blender; nothing is saved.
"""

import math

import castle_kit as castle
import scenario_build_kit as kit

ROUND = "a round tower %s with a pointed blue-slate roof"
CORNERS, GATES = ("NE", "NW", "SE", "SW"), ("Gate West", "Gate East")
ARCH = "an open round-arched gateway through the gatehouse"
ENCLOSED = "the wall, towers and gatehouse enclose the courtyard"
MERLONS, FLOATING = "battlements: at least thirty merlons on the wall", "nothing of the castle floats"
APART, CAMERA = "the towers and the keep stand apart", ("Hero Camera placed, aimed and active",
                                                        "the camera frames the whole castle")
KEEP = ("a keep about 14 x 9 m at (0, 6) on the north side", "the keep is the tallest part of the castle")
GLOW = "the windows glow warm"
HYGIENE = "tidy file: no default leftovers, named, materials"
WALL_OPEN = "the curtain wall has no see-through gaps"
ROOFS_OPEN = "the keep's and towers' roofs close their footprints above the eaves"


def _build(bpy):
    import scenario_oneshot_castle as test
    test.build(bpy)


def _replace(bpy, name, vertices, faces):
    """Give a named object new geometry, keeping its materials; the old mesh is removed, as a tidy build would."""
    obj = bpy.data.objects[name]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(v) for v in vertices], [], [tuple(f) for f in faces])
    mesh.update()
    for material in obj.data.materials:
        mesh.materials.append(material)
    old, obj.data = obj.data, mesh
    bpy.data.meshes.remove(old)
    return obj


def _remove(bpy, names):
    for name in names:
        obj = bpy.data.objects[name]
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data is not None and data.users == 0:
            bpy.data.meshes.remove(data)


def octagonal_towers(bpy):
    """Eight-sided towers still read as round."""
    _build(bpy)
    for tower_id, (centre, radius, top, _apex) in castle.TOWERS.items():
        _replace(bpy, "Castle Tower " + tower_id, *kit.cylinder_geometry(radius, top, 8, (*centre, top / 2)))


def battlements_in_the_walls(bpy):
    """Each wall run and its merlons as one object."""
    _build(bpy)
    for side, (size, centre, merlons) in castle.wall_runs().items():
        local = [(box, tuple(c - o for c, o in zip(middle, centre))) for box, middle in [(size, centre)] + merlons]
        _replace(bpy, "Castle Wall " + side, *kit.merged_boxes(local))   # The wall's origin is its centre.
        _remove(bpy, ["Castle Wall %s Battlements" % side])


def merlon_objects(bpy):
    """Every merlon its own object, sharing one mesh."""
    _build(bpy)
    shared = None
    for side, (_size, _centre, merlons) in castle.wall_runs().items():
        _remove(bpy, ["Castle Wall %s Battlements" % side])
        for index, (size, centre) in enumerate(merlons):
            if shared is None:
                shared = kit.mesh_object(bpy, "Castle Wall Merlon", *kit.box_geometry(castle.MERLON))
                kit.assign(shared, bpy.data.materials["Castle Stone"])
            obj = bpy.data.objects.new("Castle Wall Merlon %s %02d" % (side, index), shared.data)
            obj.location, obj.rotation_euler.z = centre, 0.0 if size[0] >= size[1] else math.pi / 2
            bpy.context.scene.collection.objects.link(obj)
    _remove(bpy, ["Castle Wall Merlon"])


def wall_through_the_gatehouse(bpy):
    """The south wall run on through the gatehouse and cut by the same arch: the two arch undersides coincide."""
    _build(bpy)
    end = castle.HALF_X - castle.CORNER_RADIUS + 0.5   # Where the stand-in's two south runs end at the towers.
    profile = [(-end if x == castle.GATE_X[0] else end if x == castle.GATE_X[1] else x,
                castle.WALL_TOP if z == castle.GATE_TOP else z) for x, z in castle.gate_profile()]
    _remove(bpy, ["Castle Wall South West", "Castle Wall South East"])
    half = castle.WALL_THICK / 2
    wall = kit.mesh_object(bpy, "Castle Wall South", *castle.prism_xz(profile, -castle.HALF_Y - half,
                                                                       -castle.HALF_Y + half))
    kit.assign(wall, bpy.data.materials["Castle Stone"])


def square_corner_towers(bpy):
    _build(bpy)
    for tower_id in CORNERS:
        (x, y), radius, top, _apex = castle.TOWERS[tower_id]
        _replace(bpy, "Castle Tower " + tower_id, *kit.box_geometry((2 * radius, 2 * radius, top), (x, y, top / 2)))


def flat_roofs(bpy):
    _build(bpy)
    for tower_id, (centre, radius, top, _apex) in castle.TOWERS.items():
        _replace(bpy, "Castle Tower %s Roof" % tower_id,
                 *kit.cylinder_geometry(radius + castle.ROOF_OVERHANG, 0.4, 32, (*centre, top + 0.2)))


def stone_roofs(bpy):
    _build(bpy)
    for tower_id in castle.TOWERS:
        bpy.data.objects["Castle Tower %s Roof" % tower_id].data.materials[0] = bpy.data.materials["Castle Stone"]


def solid_gatehouse(bpy):
    _build(bpy)
    low, high = castle.GATE_X
    _replace(bpy, "Castle Gatehouse", *castle.prism_xz([(low, 0.0), (high, 0.0), (high, castle.GATE_TOP),
                                                         (low, castle.GATE_TOP)], *castle.GATE_Y))


def closed_gateway(bpy):
    _build(bpy)
    door = kit.box_object(bpy, "Castle Gate Door", (2 * castle.PASSAGE_HALF, 0.2, castle.SPRINGING),
                          (0.0, castle.GATE_Y[0] + 0.3, castle.SPRINGING / 2))
    kit.assign(door, bpy.data.materials["Castle Stone"])


def gap_in_the_east_wall(bpy):
    _build(bpy)
    size, (_x, y, _z), merlons = castle.wall_runs()["East"]
    half = size[1] / 2   # Two lengths either side of a 4 m gap, about the wall's origin (its centre).
    _replace(bpy, "Castle Wall East", *kit.merged_boxes([((size[0], half - 2.0, size[2]), (0.0, -half / 2 - 1.0, 0.0)),
                                                         ((size[0], half - 2.0, size[2]), (0.0, half / 2 + 1.0, 0.0))]))
    kept = [box for box in merlons if abs(box[1][1] - y) > 2.0]
    _replace(bpy, "Castle Wall East Battlements", *kit.merged_boxes(kept))


def ten_merlons(bpy):
    _build(bpy)
    for side, (_size, _centre, merlons) in castle.wall_runs().items():
        _replace(bpy, "Castle Wall %s Battlements" % side, *kit.merged_boxes(merlons[:2]))


def floating_roof(bpy):
    _build(bpy)
    bpy.data.objects["Castle Tower NE Roof"].location.z += 2.0


def tower_in_the_keep(bpy):
    """A stair tower pushed into the keep's footprint."""
    _build(bpy)
    body = kit.mesh_object(bpy, "Castle Tower Stair", *kit.cylinder_geometry(2.0, 20.0, 32, (5.0, 8.0, 10.0)))
    kit.assign(body, bpy.data.materials["Castle Stone"])


def low_keep(bpy):
    """The keep and its roof at half height: lower than the towers, and its upper windows left in the air."""
    _build(bpy)
    (cx, cy), (sx, sy) = castle.KEEP_CENTRE, castle.KEEP_SIZE
    eaves, ridge = castle.KEEP_EAVES / 2, castle.KEEP_RIDGE / 2
    keep = _replace(bpy, "Castle Keep", *kit.box_geometry((sx, sy, eaves)))
    keep.location.z = eaves / 2
    half = sy / 2 + castle.ROOF_OVERHANG
    profile = [(cy - half, eaves - 0.3), (cy + half, eaves - 0.3), (cy, ridge)]
    _replace(bpy, "Castle Keep Roof", *castle.prism_yz(profile, cx - sx / 2 - 0.5, cx + sx / 2 + 0.5))


def inner_parapets(bpy):
    """A parapet along the inner edge of every wall-walk run, beside the battlements on its outer edge."""
    import scenario_oneshot_castle as scenario
    _build(bpy)
    stone = bpy.data.materials[0]
    for index, (x0, y0, x1, y1) in enumerate(scenario.CHECKS[[c["kind"] for c in scenario.CHECKS].index(
            "crenellations")]["lines"]):
        length = math.hypot(x1 - x0, y1 - y0)
        ux, uy = (x1 - x0) / length, (y1 - y0) / length
        mx, my = (x0 + x1) / 2 - uy * -0.8, (y0 + y1) / 2 + ux * -0.8   # 0.8 m in from the centreline.
        size = (length if abs(ux) > 0.5 else 0.3, length if abs(uy) > 0.5 else 0.3, 1.0)
        kit.assign(kit.box_object(bpy, "Castle Wall Parapet %d" % index, size,
                                  (mx, my, castle.WALL_TOP + 0.5)), stone)


def flag_over_the_keep(bpy):
    """A flagpole on a gate tower rising above the keep's ridge: the keep is no longer the tallest part."""
    _build(bpy)
    (x, y), _radius, _top, apex = castle.TOWERS["Gate West"]
    kit.assign(kit.box_object(bpy, "Castle Flag", (0.1, 0.1, castle.KEEP_RIDGE + 1.0 - apex),
                              (x, y, (apex + castle.KEEP_RIDGE + 1.0) / 2)), bpy.data.materials[0])


def camera_aimed_away(bpy):
    _build(bpy)
    bpy.data.objects["Hero Camera"].rotation_euler.z += math.radians(60.0)


def dark_windows(bpy):
    _build(bpy)
    glass = bpy.data.materials["Castle Window Glass"]
    next(n for n in glass.node_tree.nodes if n.type == "BSDF_PRINCIPLED").inputs["Emission Strength"].default_value = 0


def zero_user_leftovers(bpy):
    """Forty data-blocks nothing uses, left alone: a save drops them, so the saved file is tidy."""
    _build(bpy)
    for index in range(30):
        bpy.data.materials.new("Castle Trial Material %02d" % index)
    for index in range(10):
        bpy.data.meshes.new("Castle Trial Mesh %02d" % index)


def leftovers_a_save_keeps(bpy):
    """Thirteen stones made and deleted again: their meshes go with them, but each mesh's own material is
    written by a save and reopens unused."""
    _build(bpy)
    for index in range(13):
        stone = kit.box_object(bpy, "Castle Spare Stone %02d" % index, (0.5, 0.5, 0.5), (30.0 + index, 30.0, 0.25))
        kit.assign(stone, kit.principled(bpy, "Castle Spare Stone %02d" % index, (0.5, 0.5, 0.5)))
        bpy.data.objects.remove(stone, do_unlink=True)


VARIANTS = (
    ("eight-sided towers", octagonal_towers, ()),
    ("each wall run and its merlons as one object", battlements_in_the_walls, ()),
    ("every merlon its own object, one shared mesh", merlon_objects, ()),
    ("the south wall run on through the gatehouse, cut by the same arch", wall_through_the_gatehouse, ()),
    ("square corner towers", square_corner_towers, tuple(ROUND % tower_id for tower_id in CORNERS)),
    ("flat tower roofs", flat_roofs, tuple(ROUND % tower_id for tower_id in CORNERS + GATES)),
    ("stone roofs, not slate", stone_roofs, tuple(ROUND % tower_id for tower_id in CORNERS + GATES)),
    ("a solid gatehouse with no gateway", solid_gatehouse, (ARCH,)),
    ("a door closing the gateway", closed_gateway, (ARCH,)),
    ("a 4 m gap in the east wall", gap_in_the_east_wall, (ENCLOSED, WALL_OPEN)),
    ("ten merlons in all", ten_merlons, (MERLONS,)),
    # A roof lifted off its tower is also a second tower group overlapping the first in plan.
    ("a roof floating 2 m above its tower", floating_roof, (FLOATING, APART)),
    ("a tower pushed into the keep", tower_in_the_keep, (APART,)),
    ("a parapet along the inner edge of every wall-walk run", inner_parapets, ()),
    ("a flag on a gate tower above the keep", flag_over_the_keep, ("the keep is the tallest part of the castle",)),
    ("a keep half as tall", low_keep, KEEP + (FLOATING, ROOFS_OPEN)),
    ("the camera aimed 60 degrees away", camera_aimed_away, CAMERA),
    ("windows that do not glow", dark_windows, (GLOW,)),
    # Unused data is judged as a save writes it: data a save drops passes, data it keeps unused fails.
    ("forty zero-user data-blocks left alone", zero_user_leftovers, ()),
    ("thirteen materials a save keeps unused", leftovers_a_save_keeps, (HYGIENE,)),
)
