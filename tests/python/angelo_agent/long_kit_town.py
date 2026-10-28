# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The stand-in of ``scenario_oneshot_town_centre``: a small town centre, the plainest build of its brief.

The high street (``long_kit_street``) enlarged with more of the same kind of content: the high street runs along X
and two side streets, Church Lane and Market Street, cross it along Y; 23 distinct buildings front them, with a
square, a park, a covered market hall, a church, street furniture and five vehicles. Every size, place and height the
brief states is a constant here (the brief's building sentences are written from ``BUILDINGS``) and the checks are
derived from them; facades, roofs and props are placeholder shapes. A building is built in its own frame (front on
local y = 0 facing local -Y, width along local X, depth along +Y) and turned to face its street.
Axes: roads' tops at z = 0, pavements', the square's and the park's at z = 0.15 (``KERB``); north is +Y.
"""

import math

import long_kits as lk
import long_kit_street as street
import scenario_build_kit as kit

KERB, PAVEMENT = 0.15, 3.0
HIGH_STREET = (-42.0, 42.0)            # The high street's road runs along X, y = -3.5 to 3.5.
SIDE_STREETS = {"Church Lane": -24.0, "Market Street": 24.0}   # Roads along Y centred on these x, 7 m wide.
SIDE_LENGTH = (-36.0, 36.0)
ROAD_HALF = 3.5
FACINGS = {"S": -90.0, "N": 90.0, "E": 0.0, "W": 180.0}   # The world direction a front looks in.
# name: (x0, x1, y0, y1, facing, roof, height, eaves, storeys, wall material, look). Heights above the pavement.
BUILDINGS = {
    "Bakery": (-17.5, -10.5, 6.5, 16.5, "S", "ridge_x", 12.0, 9.0, 3, "cream",
               "cream render, a striped awning and loaves in its window"),
    "Bookshop": (-10.5, -3.5, 6.5, 16.5, "S", "flat", 7.0, None, 2, "brick",
                 "red brick with a dark green shopfront and a bay window"),
    "Cafe": (-3.5, 3.5, 6.5, 16.5, "S", "mansard", 12.5, 9.5, 3, "blue",
             "pale blue render with dormers, a striped awning and a terrace"),
    "Pharmacy": (3.5, 10.5, 6.5, 16.5, "S", "ridge_y", 10.0, 6.5, 2, "white",
                 "white render with a green cross sign"),
    "Bank": (10.5, 17.5, 6.5, 16.5, "S", "flat", 13.0, None, 3, "stone",
             "pale stone with columns either side of its door, a cornice and a balustrade"),
    "Hotel": (-17.5, -8.0, -16.5, -6.5, "N", "flat", 14.0, None, 4, "sand",
              "sandstone with balconies, flower boxes and a canopy over its entrance"),
    "Town Hall": (8.0, 17.5, -16.5, -6.5, "N", "flat", 9.0, None, 2, "brick",
                  "red brick with a clock tower"),
    "Pub": (-42.0, -30.5, 6.5, 16.5, "S", "ridge_x", 10.5, 7.0, 2, "black",
            "black timber and white plaster with a hanging painted sign and hanging baskets"),
    "Hardware Store": (-42.0, -30.5, -16.5, -6.5, "N", "flat", 6.0, None, 1, "green",
                       "green weatherboard with spades, rakes and ladders in its windows"),
    "Cinema": (30.5, 42.0, 6.5, 16.5, "S", "flat", 11.0, None, 2, "art",
               "cream art deco with a marquee over its doors and film posters"),
    "Post Office": (30.5, 42.0, -16.5, -6.5, "N", "ridge_y", 9.0, 6.0, 2, "brick",
                    "red brick with a red sign and a post box by its door"),
    "Parish Church": (-42.0, -30.5, 14.0, 34.0, "E", "ridge_y", 14.0, 8.0, 1, "stone",
               "grey stone with tall arched windows, a rose window and a tower"),
    "Library": (-17.5, -7.5, 18.0, 27.0, "W", "ridge_x", 11.0, 8.0, 2, "sand",
                "sandstone with a portico and tall windows"),
    "Bicycle Shop": (-17.5, -7.5, 27.0, 36.0, "W", "flat", 6.5, None, 2, "yellow",
                     "yellow render with bicycles in its window"),
    "Butcher": (-42.0, -30.5, -17.0, -8.0, "E", "ridge_x", 9.0, 6.5, 2, "white",
                "white tiles with a red-and-white striped awning"),
    "Tailor": (-42.0, -30.5, -26.0, -17.0, "E", "mansard", 11.0, 8.0, 3, "grey",
               "dark grey render with mannequins in its window"),
    "School": (-42.0, -30.5, -36.0, -26.0, "E", "ridge_x", 10.0, 7.0, 2, "brick",
               "red brick with a clock above its door"),
    "Market Hall": (30.5, 40.5, 12.0, 34.0, "W", "ridge_x", 13.0, 7.0, 1, "brick",
                    "brick with tall open arches along its front and glazed skylights in its roof"),
    "Toy Shop": (7.5, 17.5, 18.0, 27.0, "E", "ridge_y", 9.0, 6.0, 2, "pink",
                 "pink render with toys in its window"),
    "Ice Cream Parlour": (7.5, 17.5, 27.0, 36.0, "E", "flat", 6.0, None, 1, "mint",
                          "mint green render with a giant cone sign and a striped awning"),
    "Fire Station": (30.5, 40.5, -22.0, -8.0, "W", "flat", 9.0, None, 2, "red",
                     "red brick with two big red doors for its engines"),
    "Garage": (30.5, 40.5, -36.0, -22.0, "W", "ridge_x", 7.5, 5.0, 1, "grey",
               "grey cladding with a wide workshop door"),
    "Apartments": (7.5, 17.5, -36.0, -20.0, "E", "flat", 15.0, None, 5, "white",
                   "white render with balconies on every upper storey"),
}
AWNING_REACH = 1.2
SQUARE = (-8.0, 8.0, -16.5, -6.5)
FOUNTAIN_AT, FOUNTAIN_RADIUS, FOUNTAIN_TOP = (0.0, -11.5), 2.0, 2.5
TOWN_HALL_TOWER, TOWN_HALL_TIP = (12.75, -9.0), 20.0
CHURCH_TOWER, CHURCH_TIP = (-33.0, 24.0), 30.0
PARK = (-17.5, -2.0, -36.0, -20.0)
PARK_TREES = ((-15.0, -23.0), (-5.0, -23.0), (-15.0, -33.0), (-5.0, -33.0), (-10.0, -34.0), (-4.5, -28.0))
TREE_HEIGHT = 7.0
BANDSTAND_AT, BANDSTAND_RADIUS, BANDSTAND_TOP = (-10.0, -28.0), 2.5, 5.0
LAMPS_X, LAMP_Y, LAMP_HEIGHT = (-36.0, -12.0, 0.0, 12.0, 36.0), 4.0, 4.5
# name: ((centre x, y), (length, width, height), turned along Y): vehicles stand on the road.
VEHICLES = {"Parked Hatchback": ((-8.0, 2.4), (4.0, 1.75, 1.5), False),
            "Parked Van": ((14.0, 2.3), (5.2, 2.0, 2.4), False),
            "Town Bus": ((-6.0, -2.2), (10.0, 2.5, 3.0), False),
            "Taxi": ((-25.5, 14.0), (4.6, 1.8, 1.5), True),
            "Fire Engine": ((26.0, -15.0), (8.0, 2.5, 3.2), True)}
SHELTER = ((-6.0, -5.3), (3.0, 1.4, 2.6))
PHONE_BOX = ((-20.0, -4.6), (1.0, 1.0, 2.5))
CROSSINGS = ((-12.0, "X"), (12.0, "X"), (24.0, "Y"))   # On the high street at x, and Market Street at y = 24.

PALETTE = dict(street.PALETTE, **{
    "black": ("Black Timber", (0.05, 0.04, 0.04), 0.6), "green": ("Green Weatherboard", (0.20, 0.38, 0.25), 0.7),
    "art": ("Art Deco Cream", (0.93, 0.88, 0.74), 0.6), "yellow": ("Yellow Render", (0.90, 0.78, 0.35), 0.8),
    "grey": ("Grey Render", (0.40, 0.42, 0.44), 0.8), "mint": ("Mint Render", (0.62, 0.86, 0.72), 0.8),
    "red": ("Fire Station Red", (0.55, 0.12, 0.08), 0.7), "metal": ("Black Metal", (0.04, 0.04, 0.04), 0.4, 0.8),
    "taxi": ("Taxi Yellow", (0.95, 0.75, 0.10), 0.3, 0.4), "bus": ("Bus Red", (0.70, 0.08, 0.06), 0.35, 0.3),
    "grass": ("Grass", (0.20, 0.42, 0.14), 0.95),
})
WALL_KEY = {"cream": "cream", "brick": "brick", "blue": "blue", "white": "white", "stone": "stone", "sand": "sand",
            "black": "black", "green": "green", "art": "art", "yellow": "yellow", "grey": "grey", "pink": "pink",
            "mint": "mint", "red": "red"}


def frame(name):
    """(origin x, y), facing degrees, width along the front, depth: the building's own frame in the world."""
    x0, x1, y0, y1, facing = BUILDINGS[name][:5]
    if facing in "SN":
        return ((x0 + x1) / 2, y0 if facing == "S" else y1), FACINGS[facing], x1 - x0, y1 - y0
    return (x1 if facing == "E" else x0, (y0 + y1) / 2), FACINGS[facing], y1 - y0, x1 - x0


def to_world(name, local):
    """A point in a building's frame (x along its front, y back from it) in world x, y."""
    (ox, oy), facing, _w, _d = frame(name)
    angle = math.radians(facing + 90.0)
    lx, ly = local
    return (ox + lx * math.cos(angle) - ly * math.sin(angle), oy + lx * math.sin(angle) + ly * math.cos(angle))


def _placed(bpy, m, name, geometry, key, building, collection):
    (ox, oy), facing, _w, _d = frame(building)
    obj = kit.mesh_object(bpy, name, *geometry, location=(ox, oy, 0.0), collection=collection)
    obj.rotation_euler = (0.0, 0.0, math.radians(facing + 90.0))
    kit.assign(obj, m.get(key))
    return obj


def build_building(bpy, m, name, collection=None):
    _x0, _x1, _y0, _y1, _facing, roof, height, eaves, storeys, wall = BUILDINGS[name][:10]
    _origin, _facing_deg, width, depth = frame(name)
    top, walls_top = KERB + height, KERB + (eaves if eaves else height)
    hw = width / 2
    _placed(bpy, m, name + " Walls", lk.box((width, depth, walls_top - KERB), (0.0, depth / 2, (walls_top + KERB) / 2)),
            WALL_KEY[wall], name, collection)
    if roof == "ridge_x":
        parts, key = [street._prism_x(-hw, hw, 0.0, depth, walls_top, top)], "slate"
    elif roof == "ridge_y":
        parts, key = [street._prism_y(-hw, hw, 0.0, depth, walls_top, top)], "slate"
    elif roof == "mansard":
        parts, key = [street._frustum(-hw, hw, 0.0, depth, walls_top, top, 1.2)], "slate"
    else:   # Flat, at ``height``, behind a parapet 0.6 m tall.
        parts = [lk.box((width, 0.25, 0.6), (0.0, y, top + 0.3)) for y in (0.125, depth - 0.125)]
        parts += [lk.box((0.25, depth, 0.6), (x, depth / 2, top + 0.3)) for x in (-hw + 0.125, hw - 0.125)]
        key = WALL_KEY[wall]
    _placed(bpy, m, name + " Roof", lk.combine(parts), key, name, collection)
    panes = [lk.box((width * 0.7, 0.06, 2.2), (0.0, -0.03, KERB + 1.4))]
    for storey in range(1, storeys):
        z = KERB + storey * (walls_top - KERB) / storeys + 0.6
        columns = max(2, int(width // 1.8))
        panes += [lk.box((0.9, 0.06, 1.4), (-hw + (c + 0.5) * width / columns, -0.03, z + 0.7)) for c in range(columns)]
    _placed(bpy, m, name + " Windows", lk.combine(panes), "glass", name, collection)
    if "awning" in BUILDINGS[name][10] or "marquee" in BUILDINGS[name][10]:
        sheet = ([(-hw + 0.3, 0.0, 3.4), (hw - 0.3, 0.0, 3.4), (hw - 0.3, -AWNING_REACH, 2.7),
                  (-hw + 0.3, -AWNING_REACH, 2.7)], [(0, 1, 2, 3)])
        _placed(bpy, m, name + " Awning", sheet, "awning", name, collection)


def build_towers(bpy, m, c=None):
    for name, (tx, ty), tip, size, base in (("Town Hall Clock Tower", TOWN_HALL_TOWER, TOWN_HALL_TIP, 4.0, 15.0),
                                            ("Parish Church Tower", CHURCH_TOWER, CHURCH_TIP, 5.0, 20.0)):
        tower = kit.mesh_object(bpy, name, *lk.combine([
            lk.box((size, size, base - KERB), (tx, ty, (base + KERB) / 2)),
            street._pyramid(tx, ty, size / 2, base, KERB + tip)]), collection=c)
        kit.assign(tower, m.get("brick" if name.startswith("Town") else "stone"))


def build_ground(bpy, m, c=None):
    x0, x1 = HIGH_STREET
    roads = [("High Street Road", lk.box((x1 - x0, 2 * ROAD_HALF, 0.2), ((x0 + x1) / 2, 0.0, -0.1)))]
    walks = [lk.box((x1 - x0, PAVEMENT, 0.35), ((x0 + x1) / 2, s * (ROAD_HALF + PAVEMENT / 2), KERB - 0.175))
             for s in (-1, 1)]
    for name, x in SIDE_STREETS.items():
        for y0, y1 in ((SIDE_LENGTH[0], -ROAD_HALF), (ROAD_HALF, SIDE_LENGTH[1])):
            roads.append((name + " Road", lk.box((2 * ROAD_HALF, y1 - y0, 0.2), (x, (y0 + y1) / 2, -0.1))))
            lo, hi = (y0, -ROAD_HALF - PAVEMENT) if y1 < 0 else (ROAD_HALF + PAVEMENT, y1)
            walks += [lk.box((PAVEMENT, hi - lo, 0.35), (x + s * (ROAD_HALF + PAVEMENT / 2), (lo + hi) / 2,
                                                         KERB - 0.175)) for s in (-1, 1)]
    for name in ("High Street Road", "Church Lane Road", "Market Street Road"):
        parts = [g for n, g in roads if n == name]
        road = kit.mesh_object(bpy, name, *lk.combine(parts), collection=c)
        kit.assign(road, m.get("asphalt"))
    pavement = kit.mesh_object(bpy, "Town Pavement", *lk.combine(walks), collection=c)
    kit.assign(pavement, m.get("paving"))
    sx0, sx1, sy0, sy1 = SQUARE
    square = kit.mesh_object(bpy, "Square Paving", *lk.box((sx1 - sx0, sy1 - sy0, 0.35), (0.0, (sy0 + sy1) / 2,
                                                                                          KERB - 0.175)), collection=c)
    kit.assign(square, m.get("paving"))
    px0, px1, py0, py1 = PARK
    park = kit.mesh_object(bpy, "Park Lawn", *lk.box((px1 - px0, py1 - py0, 0.35), ((px0 + px1) / 2, (py0 + py1) / 2,
                                                                                      KERB - 0.175)), collection=c)
    kit.assign(park, m.get("grass"))
    marks = []
    for at, along in CROSSINGS:
        for d in (-1.5, -0.5, 0.5, 1.5):
            marks.append(lk.box((0.5, 2 * ROAD_HALF - 0.4, 0.006), (at + d, 0.0, 0.003)) if along == "X" else
                         lk.box((2 * ROAD_HALF - 0.4, 0.5, 0.006), (SIDE_STREETS["Market Street"], at + d, 0.003)))
    paint = kit.mesh_object(bpy, "Town Road Markings", *lk.combine(marks), collection=c)
    kit.assign(paint, m.get("stripe"))


def _vehicle(length, width, height):
    waist = 0.25 + height * 0.55
    body = lk.combine([lk.box((length, width, waist - 0.25), (0, 0, (waist + 0.25) / 2)),
                       lk.box((length * 0.6 if height < 2 else length, width * 0.9, height - waist),
                              (-length * 0.1 if height < 2 else 0.0, 0, (height + waist) / 2))])
    wheels = lk.combine([lk.cylinder(0.35, 0.22, (sx * length * 0.33, sy * (width / 2 - 0.12), 0.35), 16, "Y")
                         for sx in (-1, 1) for sy in (-1, 1)])
    return lk.multi([(0, body), (1, wheels)])


def build_props(bpy, m, c=None):
    p = lambda name, geometry, at, facing=-90.0, keys=("metal",), faces_of=None: lk.place(
        bpy, m, name, geometry, at, facing, keys, faces_of, collection=c)
    for x in LAMPS_X:
        for s in (-1, 1):
            p("Street Lamp", lk.combine([lk.cylinder(0.08, LAMP_HEIGHT, (0, 0, LAMP_HEIGHT / 2), 12),
                                        lk.box((0.3, 0.6, 0.25), (0, 0.25, LAMP_HEIGHT - 0.125))]),
              (x, s * LAMP_Y, KERB), facing=90.0 if s > 0 else -90.0)
    keys = {"Parked Hatchback": "carpaint", "Parked Van": "vanpaint", "Town Bus": "bus", "Taxi": "taxi",
            "Fire Engine": "red"}
    for name, ((x, y), size, along_y) in VEHICLES.items():
        geometry, faces_of = _vehicle(*size)
        p(name, geometry, (x, y), facing=0.0 if along_y else -90.0, keys=(keys[name], "metal"), faces_of=faces_of)
    (sx, sy), (sl, sd, sh) = SHELTER
    p("Bus Shelter", lk.combine([lk.box((sl, sd, 0.1), (0, 0, sh - 0.05)),
                                 lk.box((sl, 0.05, sh - 0.1), (0, sd / 2 - 0.05, (sh - 0.1) / 2))]),
      (sx, sy, KERB), facing=90.0, keys=("glass",))
    (px, py), size = PHONE_BOX
    p("Phone Box", lk.box(size, (0, 0, size[2] / 2)), (px, py, KERB), keys=("red",))
    fx, fy = FOUNTAIN_AT
    p("Square Fountain", lk.combine([lk.cylinder(FOUNTAIN_RADIUS, 0.6, (0, 0, 0.3), 40),
                                     lk.cylinder(0.3, FOUNTAIN_TOP, (0, 0, FOUNTAIN_TOP / 2), 16)]),
      (fx, fy, KERB), keys=("stone",))
    for x, y in PARK_TREES:
        geometry, faces_of = lk.multi([(0, lk.cylinder(0.25, 3.5, (0, 0, 1.75), 12)),
                                       (1, lk.sphere(2.0, (0, 0, TREE_HEIGHT - 2.0), 16, 8))])
        p("Park Tree", geometry, (x, y, KERB), keys=("bark", "leaf"), faces_of=faces_of)
    bx, by = BANDSTAND_AT
    p("Park Bandstand", lk.combine([lk.cylinder(BANDSTAND_RADIUS, 0.6, (0, 0, 0.3), 8),
                                    street._pyramid(0.0, 0.0, BANDSTAND_RADIUS * 0.75, 3.2, BANDSTAND_TOP)] +
                                   [lk.cylinder(0.08, 2.6, (BANDSTAND_RADIUS * 0.7 * math.cos(a * math.pi / 4),
                                                            BANDSTAND_RADIUS * 0.7 * math.sin(a * math.pi / 4), 1.9), 8)
                                    for a in range(8)]), (bx, by, KERB), keys=("white",))
    for i, x in enumerate((33.0, 35.5, 38.0)):   # Stalls inside the market hall.
        p("Market Hall Stall", lk.table(2.0, 1.0, 0.9, 0.06), (x, 23.0 + (i - 1) * 4.0, KERB), keys=("wood",))


def build_town_centre(bpy, collection=None):
    m = lk.Materials(bpy, PALETTE)
    build_ground(bpy, m, collection)
    for name in BUILDINGS:
        build_building(bpy, m, name, collection)
    build_towers(bpy, m, collection)
    build_props(bpy, m, collection)
