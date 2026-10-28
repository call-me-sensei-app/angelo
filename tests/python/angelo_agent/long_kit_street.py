# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The stand-in of ``scenario_oneshot_high_street``: a high street of eight buildings and a square, plainest build.

Every size, place and height the brief states is a constant here and the checks are derived from them; facades,
roofs and props are simple placeholder shapes so the reference pictures show the layout and mood.
Axes: the street runs along X through the origin; the road's top is z = 0 and the pavements' and the square's
top is z = 0.15 (``KERB``). Six buildings face it from the north (fronts on y = 6.5), two from the south (fronts
on y = -6.5) either side of the square. Every stated height of a building or prop is above the pavement or the
square it stands on (cars stand on the road).
"""

import long_kits as lk
import scenario_build_kit as kit

LENGTH, ROAD_HALF, PAVEMENT_OUT, KERB = 40.0, 3.5, 6.5, 0.15
DEPTH = 10.0   # Every building runs 10 m back from its front.
# name: (x0, x1, front y, roof kind, height): the ridge or roof-top height above the pavement.
BUILDINGS = {
    "Bakery": (-20.0, -13.0, 6.5, "ridge_x", 12.0), "Bookshop": (-13.0, -7.0, 6.5, "flat", 7.0),
    "Cafe": (-7.0, 1.0, 6.5, "mansard", 12.5), "Pharmacy": (1.0, 7.0, 6.5, "ridge_y", 10.0),
    "Flower Shop": (7.0, 13.0, 6.5, "flat", 6.5), "Bank": (13.0, 20.0, 6.5, "flat", 13.0),
    "Hotel": (-20.0, -10.0, -6.5, "flat", 14.0), "Town Hall": (10.0, 20.0, -6.5, "flat", 9.0),
}
EAVES = {"Bakery": 9.0, "Cafe": 9.5, "Pharmacy": 6.5}
AWNINGS = ("Bakery", "Cafe", "Flower Shop")   # Each reaches 1.2 m over the pavement.
AWNING_REACH = 1.2
TOWER_AT, TOWER_SIZE, TOWER_TIP = (15.0, -9.0), 4.0, 20.0
SQUARE = (-10.0, 10.0, -16.5, -6.5)
FOUNTAIN_AT, FOUNTAIN_RADIUS, FOUNTAIN_RIM, FOUNTAIN_TOP = (0.0, -11.5), 2.5, 0.6, 2.5   # Above the paving.
TREES = ((-6.5, -9.5), (6.5, -9.5), (-6.5, -14.0), (6.5, -14.0))
TREE_HEIGHT = 6.0
SQUARE_BENCHES = ((0.0, -8.3, -90.0), (0.0, -14.7, 90.0), (-4.2, -11.5, 0.0), (4.2, -11.5, 180.0))
KIOSK_AT, KIOSK = (-8.0, -15.0), (2.5, 2.0, 3.0)
STALLS_X, STALL_Y = (-3.0, 0.0, 3.0), -15.8
LAMPS_X, LAMP_Y, LAMP_HEIGHT = (-15.0, -5.0, 5.0, 15.0), 4.0, 4.5
CAFE_TABLES = ((-5.0, 4.8), (-3.0, 4.8), (-1.0, 4.8))   # Clear of the cafe's awning (y 5.3 to 6.5).
CAFE_TABLE_TOP, CAFE_TABLE_RADIUS = 0.75, 0.35
HATCHBACK = ((-9.0, 2.4), (4.0, 1.75, 1.5))   # Centre (x, y), size (length, width, height).
VAN = ((13.0, 2.3), (5.2, 2.0, 2.4))
SHELTER = ((14.0, -5.3), (3.0, 1.4, 2.6))
PHONE_BOX = ((-14.0, -4.4), (1.0, 1.0, 2.5))
POST_BOX_AT, CROSSING_X = (-12.0, 4.2), 8.0

PALETTE = {
    "asphalt": ("Asphalt", (0.10, 0.10, 0.11), 0.9), "paving": ("Paving Stone", (0.55, 0.53, 0.50), 0.85),
    "stripe": ("Road Paint", (0.92, 0.92, 0.90), 0.6), "cream": ("Cream Render", (0.90, 0.84, 0.70), 0.85),
    "brick": ("Red Brick", (0.45, 0.20, 0.14), 0.9), "green": ("Shopfront Green", (0.08, 0.25, 0.16), 0.4),
    "blue": ("Blue Render", (0.42, 0.55, 0.66), 0.85), "white": ("White Render", (0.90, 0.90, 0.88), 0.85),
    "pink": ("Pink Render", (0.85, 0.62, 0.62), 0.85), "stone": ("Portland Stone", (0.78, 0.76, 0.70), 0.75),
    "sand": ("Sandstone", (0.72, 0.60, 0.42), 0.8), "slate": ("Slate Roof", (0.20, 0.22, 0.25), 0.7),
    "glass": ("Window Glass", (0.10, 0.14, 0.18), 0.08), "awning": ("Awning Canvas", (0.70, 0.15, 0.12), 0.8),
    "black": ("Black Metal", (0.04, 0.04, 0.04), 0.4, 0.8), "wood": ("Painted Wood", (0.30, 0.18, 0.10), 0.5),
    "leaf": ("Leaves", (0.14, 0.34, 0.12), 0.75), "bark": ("Bark", (0.25, 0.17, 0.10), 0.9),
    "water": ("Water", (0.15, 0.30, 0.35), 0.05), "red": ("Pillar-Box Red", (0.70, 0.04, 0.03), 0.35),
    "carpaint": ("Car Paint", (0.10, 0.25, 0.55), 0.25, 0.6), "vanpaint": ("Van White", (0.92, 0.92, 0.92), 0.3),
    "gold": ("Clock Gold", (0.80, 0.62, 0.20), 0.3, 1.0),
}
WALLS = {"Bakery": "cream", "Bookshop": "brick", "Cafe": "blue", "Pharmacy": "white", "Flower Shop": "pink",
         "Bank": "stone", "Hotel": "sand", "Town Hall": "brick"}


def _prism_x(x0, x1, y0, y1, z0, z1):
    """A roof running along X: a triangle in Y-Z from y0 to y1 rising to z1 at the middle."""
    mid = (y0 + y1) / 2
    v = [(x0, y0, z0), (x0, y1, z0), (x0, mid, z1), (x1, y0, z0), (x1, y1, z0), (x1, mid, z1)]
    return v, [(0, 2, 1), (3, 4, 5), (0, 3, 5, 2), (1, 2, 5, 4), (0, 1, 4, 3)]


def _prism_y(x0, x1, y0, y1, z0, z1):
    v, f = _prism_x(y0, y1, x0, x1, z0, z1)
    return [(b, a, c) for a, b, c in v], [tuple(reversed(face)) for face in f]


def _frustum(x0, x1, y0, y1, z0, z1, inset):
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0 + inset, y0 + inset, z1), (x1 - inset, y0 + inset, z1), (x1 - inset, y1 - inset, z1),
         (x0 + inset, y1 - inset, z1)]
    return v, [(0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7), (4, 5, 6, 7), (3, 2, 1, 0)]


def _pyramid(cx, cy, half, z0, z1):
    v = [(cx - half, cy - half, z0), (cx + half, cy - half, z0), (cx + half, cy + half, z0), (cx - half, cy + half, z0),
         (cx, cy, z1)]
    return v, [(0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4), (3, 2, 1, 0)]


def build_building(bpy, m, name, collection=None):
    x0, x1, front, roof, height = BUILDINGS[name]
    height = KERB + height
    toward = -1 if front > 0 else 1                       # The street side, as a sign on Y.
    back = front + DEPTH * (1 if front > 0 else -1)
    y0, y1 = min(front, back), max(front, back)
    cx, cy, width = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0
    walls_top = KERB + EAVES[name] if name in EAVES else height
    body = [lk.box((width, DEPTH, walls_top - KERB), (cx, cy, (walls_top + KERB) / 2))]
    roof_parts = []
    if roof == "ridge_x":
        roof_parts.append(_prism_x(x0, x1, y0, y1, walls_top, height))
    elif roof == "ridge_y":
        roof_parts.append(_prism_y(x0, x1, y0, y1, walls_top, height))
    elif roof == "mansard":
        roof_parts.append(_frustum(x0, x1, y0, y1, walls_top, height, 1.2))
    else:   # A flat roof at ``height`` with a parapet 0.6 m tall round its edge.
        roof_parts += [lk.box((width, 0.25, 0.6), (cx, y, height + 0.3)) for y in (y0 + 0.125, y1 - 0.125)]
        roof_parts += [lk.box((0.25, DEPTH, 0.6), (x, cy, height + 0.3)) for x in (x0 + 0.125, x1 - 0.125)]
    shell = kit.mesh_object(bpy, name + " Walls", *lk.combine(body), collection=collection)
    kit.assign(shell, m.get(WALLS[name]))
    if roof_parts:
        top = kit.mesh_object(bpy, name + " Roof", *lk.combine(roof_parts), collection=collection)
        kit.assign(top, m.get("slate" if roof != "flat" else WALLS[name]))
    storeys = max(1, int((walls_top - KERB) // 3.2))
    columns = max(2, int(width // 1.8))
    panes = []
    for storey in range(1, storeys):
        z = KERB + storey * (walls_top - KERB) / storeys + 0.6
        for column in range(columns):
            x = x0 + (column + 0.5) * width / columns
            panes.append(lk.box((0.9, 0.06, 1.4), (x, front + toward * 0.03, z + 0.7)))
    panes.append(lk.box((width * 0.7, 0.06, 2.2), (cx, front + toward * 0.03, KERB + 1.4)))   # The shopfront.
    glass = kit.mesh_object(bpy, name + " Windows", *lk.combine(panes), collection=collection)
    kit.assign(glass, m.get("glass"))
    if name in AWNINGS:
        sloped = ([(x0 + 0.3, front, 3.4), (x1 - 0.3, front, 3.4), (x1 - 0.3, front + toward * AWNING_REACH, 2.7),
                   (x0 + 0.3, front + toward * AWNING_REACH, 2.7)], [(0, 1, 2, 3)])
        awning = kit.mesh_object(bpy, name + " Awning", *sloped, collection=collection)
        kit.assign(awning, m.get("awning"))
    if name == "Town Hall":
        half = TOWER_SIZE / 2
        tx, ty = TOWER_AT
        tower = kit.mesh_object(bpy, "Town Hall Clock Tower", *lk.combine([
            lk.box((TOWER_SIZE, TOWER_SIZE, 15.0 - KERB), (tx, ty, (15.0 + KERB) / 2)),
            _pyramid(tx, ty, half, 15.0, KERB + TOWER_TIP)]), collection=collection)
        kit.assign(tower, m.get("brick"))
        face = kit.mesh_object(bpy, "Town Hall Clock", *lk.cylinder(1.1, 0.1, (tx, ty + toward * half, 12.5), 24,
                                                                     axis="Y"), collection=collection)
        kit.assign(face, m.get("gold"))


def build_ground(bpy, m, c=None):
    road = kit.mesh_object(bpy, "Street Road", *lk.box((LENGTH, 2 * ROAD_HALF, 0.2), (0, 0, -0.1)), collection=c)
    kit.assign(road, m.get("asphalt"))
    width = PAVEMENT_OUT - ROAD_HALF
    pavement = kit.mesh_object(bpy, "Street Pavement", *lk.combine([
        lk.box((LENGTH, width, 0.35), (0, s * (ROAD_HALF + width / 2), KERB - 0.175)) for s in (-1, 1)]), collection=c)
    kit.assign(pavement, m.get("paving"))
    x0, x1, y0, y1 = SQUARE
    square = kit.mesh_object(bpy, "Square Paving", *lk.box((x1 - x0, y1 - y0, 0.35), ((x0 + x1) / 2, (y0 + y1) / 2,
                                                                                       KERB - 0.175)), collection=c)
    kit.assign(square, m.get("paving"))
    marks = [lk.box((2.0, 0.12, 0.005), (x, 0, 0.0025)) for x in range(-18, 19, 4)]
    marks += [lk.box((0.5, 2 * ROAD_HALF - 0.4, 0.006), (CROSSING_X + dx, 0, 0.003)) for dx in (-1.5, -0.5, 0.5, 1.5)]
    paint = kit.mesh_object(bpy, "Street Markings", *lk.combine(marks), collection=c)
    kit.assign(paint, m.get("stripe"))


def _box_prop(bpy, m, name, centre, size, key, c=None, z=0.0):
    obj = kit.mesh_object(bpy, name, *lk.box(size, (centre[0], centre[1], z + size[2] / 2)), collection=c)
    kit.assign(obj, m.get(key))
    return obj


def build_props(bpy, m, c=None):
    p = lambda name, geometry, at, facing=-90.0, keys=("black",), faces_of=None: lk.place(
        bpy, m, name, geometry, at, facing, keys, faces_of, collection=c)
    for x in LAMPS_X:
        for s in (-1, 1):
            p("Street Lamp", lk.combine([lk.cylinder(0.08, LAMP_HEIGHT, (0, 0, LAMP_HEIGHT / 2), 12),
                                        lk.box((0.3, 0.6, 0.25), (0, 0.25, LAMP_HEIGHT - 0.125))]),
              (x, s * LAMP_Y, KERB), facing=90.0 if s > 0 else -90.0)
    for at in CAFE_TABLES:
        p("Terrace Table", lk.round_table(CAFE_TABLE_RADIUS, CAFE_TABLE_TOP), (at[0], at[1], KERB), keys=("black",))
        for dx in (-0.6, 0.6):
            p("Terrace Chair", lk.chair(0.45, 0.42, 0.42, 0.85), (at[0] + dx, at[1], KERB),
              facing=0.0 if dx < 0 else 180.0, keys=("wood",))
    p("Street Post Box", lk.cylinder(0.25, 1.1, (0, 0, 0.55), 20), (POST_BOX_AT[0], POST_BOX_AT[1], KERB), keys=("red",))
    for (x, y), size, key, name in ((HATCHBACK[0], HATCHBACK[1], "carpaint", "Parked Hatchback"),
                                    (VAN[0], VAN[1], "vanpaint", "Parked Van")):
        length, width, height = size
        waist = 0.25 + height * 0.55   # The body from 0.25 m to its waist, the cabin from there to the roof.
        body = lk.combine([lk.box((length, width, waist - 0.25), (0, 0, (waist + 0.25) / 2)),
                           lk.box((length * 0.6, width * 0.9, height - waist), (-length * 0.1, 0, (height + waist) / 2))])
        wheels = lk.combine([lk.cylinder(0.32, 0.22, (sx * length * 0.33, sy * (width / 2 - 0.12), 0.32), 16, "Y")
                             for sx in (-1, 1) for sy in (-1, 1)])
        geometry, faces_of = lk.multi([(0, body), (1, wheels)])
        p(name, geometry, (x, y), facing=-90.0, keys=(key, "black"), faces_of=faces_of)
    (sx, sy), (sl, sd, sh) = SHELTER
    p("Bus Shelter", lk.combine([lk.box((sl, sd, 0.1), (0, 0, sh - 0.05)),
                                 lk.box((sl, 0.05, sh - 0.1), (0, sd / 2 - 0.05, (sh - 0.1) / 2)),
                                 lk.box((0.05, sd, sh - 0.1), (-sl / 2 + 0.03, 0, (sh - 0.1) / 2))]),
      (sx, sy, KERB), facing=90.0, keys=("glass",))
    (px, py), size = PHONE_BOX
    _box_prop(bpy, m, "Phone Box", (px, py), size, "red", c, z=KERB)
    fx, fy = FOUNTAIN_AT
    p("Square Fountain", lk.combine([lk.cylinder(FOUNTAIN_RADIUS, FOUNTAIN_RIM, (0, 0, FOUNTAIN_RIM / 2), 40),
                                     lk.cylinder(0.3, FOUNTAIN_TOP, (0, 0, FOUNTAIN_TOP / 2), 16)]),
      (fx, fy, KERB), keys=("stone",))
    p("Square Fountain Water", lk.cylinder(FOUNTAIN_RADIUS - 0.2, 0.02, (0, 0, FOUNTAIN_RIM - 0.1), 40), (fx, fy, KERB),
      keys=("water",))
    for x, y in TREES:
        geometry, faces_of = lk.multi([(0, lk.cylinder(0.2, 3.0, (0, 0, 1.5), 12)),
                                       (1, lk.sphere(1.8, (0, 0, TREE_HEIGHT - 1.8), 16, 8))])
        p("Square Tree", geometry, (x, y, KERB), keys=("bark", "leaf"), faces_of=faces_of)
    for x, y, facing in SQUARE_BENCHES:
        p("Square Bench", lk.sofa(1.8, 0.5, 0.45, 0.85, 0.06), (x, y, KERB), facing=facing, keys=("wood",))
    _box_prop(bpy, m, "Square Kiosk", KIOSK_AT, KIOSK, "green", c, z=KERB)
    for x in STALLS_X:
        geometry, faces_of = lk.multi([(0, lk.table(2.2, 1.0, 0.9, 0.06)),
                                       (1, lk.box((2.4, 1.3, 0.05), (0.0, 0.0, 2.3))),
                                       (0, lk.combine([lk.box((0.05, 0.05, 1.4), (sx * 1.05, sy * 0.45, 1.6))
                                                       for sx in (-1, 1) for sy in (-1, 1)]))])
        p("Square Market Stall", geometry, (x, STALL_Y, KERB), facing=90.0, keys=("wood", "awning"),
          faces_of=faces_of)


def build_high_street(bpy, collection=None):
    m = lk.Materials(bpy, PALETTE)
    build_ground(bpy, m, collection)
    for name in BUILDINGS:
        build_building(bpy, m, name, collection)
    build_props(bpy, m, collection)
