# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The stand-in of ``scenario_oneshot_grand_apartment``: a large apartment of twelve rooms, a hall and a balcony.

The plainest build that follows the brief: the shell (floor, walls with doorways, windows, the two doors, the
balcony) and every piece of ``long_kit_flat_north.PIECES`` + ``long_kit_flat_south.PIECES`` at its stated place,
size and height, built from a few placeholder shapes (``shape``) that carry what the checks measure: open space
under seats and tops, knobs or pulls standing proud of drawer and door fronts, hollow basins, frames on walls.
One table drives the brief, this stand-in and the checks. Axes: the floor is centred on the origin with its top at
z = 0, north is +Y; a 1.5 m entrance hall runs along X through the middle, rooms north and south of it.
"""

import math

import long_kit_apartment as base   # Its wall builder (same 2.7 m height and 0.1 m thickness).
import long_kits as lk
import scenario_build_kit as kit

FLOOR = (22.0, 14.0)
WALL_HEIGHT, WALL_THICKNESS = base.WALL_HEIGHT, base.WALL_THICKNESS
DOOR_WIDTH, DOOR_HEIGHT = 0.9, 2.1
HALL = 0.75   # The hall's walls are centred on y = +0.75 and y = -0.75.
ROOMS = {
    "Entrance Hall": (-11.0, 11.0, -HALL, HALL),
    "Living Room": (-11.0, -4.0, HALL, 7.0), "Dining Room": (-4.0, 0.5, HALL, 7.0), "Kitchen": (0.5, 5.5, HALL, 7.0),
    "Pantry": (5.5, 7.5, HALL, 7.0), "Laundry Room": (7.5, 11.0, HALL, 7.0),
    "Master Bedroom": (-11.0, -5.0, -7.0, -HALL), "Walk-in Wardrobe": (-5.0, -2.5, -3.75, -HALL),
    "En-suite": (-5.0, -2.5, -7.0, -3.75), "Kids Room": (-2.5, 1.5, -7.0, -HALL),
    "Guest Bedroom": (1.5, 5.0, -7.0, -HALL), "Bathroom": (5.0, 7.5, -7.0, -HALL), "Study": (7.5, 11.0, -7.0, -HALL),
    "Balcony": (-11.0, -4.0, 7.0, 9.0),
}
# Open doorways 0.9 m wide and 2.1 m high, centred on the hall's walls at these x.
HALL_DOORWAYS = {"Living Room": -5.0, "Dining Room": -1.75, "Kitchen": 1.4, "Pantry": 6.5, "Laundry Room": 8.6,
                 "Master Bedroom": -6.2, "Kids Room": -0.5, "Guest Bedroom": 3.2, "Bathroom": 6.25, "Study": 8.5}
# The wardrobe and the en-suite open off the master bedroom, through its east wall at x = -5, centred at these y.
SIDE_WALL_X = -5.0
SIDE_DOORWAYS = {"Walk-in Wardrobe": -2.25, "En-suite": -5.4}
SPLIT_Y = -3.75   # The wall between the wardrobe and the en-suite.
NORTH_PARTITIONS, SOUTH_PARTITIONS = (-4.0, 0.5, 5.5, 7.5), (-5.0, -2.5, 1.5, 5.0, 7.5)
BALCONY_DOOR = (-7.5, 1.8, 2.2)   # Centre x, width, height in the north wall.
BALUSTRADE = 1.1
FRONT_DOOR = (0.0, 1.0)   # Centre y and width in the east wall.
# (room, wall, centre along the wall, width, sill, head).
WINDOWS = (("Living Room", "N", -9.6, 1.6, 0.9, 2.1), ("Dining Room", "N", -1.75, 1.8, 0.9, 2.1),
           ("Kitchen", "N", 2.6, 1.6, 1.1, 2.1), ("Laundry Room", "N", 9.3, 1.0, 0.9, 2.1),
           ("Master Bedroom", "S", -8.0, 1.8, 0.9, 2.1), ("En-suite", "S", -3.75, 0.8, 1.4, 2.1),
           ("Kids Room", "S", -0.5, 1.4, 0.9, 2.1), ("Guest Bedroom", "S", 3.25, 1.4, 0.9, 2.1),
           ("Bathroom", "S", 6.25, 0.8, 1.4, 2.1), ("Study", "S", 9.25, 1.8, 0.9, 2.1),
           ("Living Room", "W", 4.0, 1.6, 0.9, 2.1), ("Master Bedroom", "W", -4.0, 1.4, 0.9, 2.1),
           ("Laundry Room", "E", 4.5, 0.9, 0.9, 2.1), ("Study", "E", -3.5, 1.2, 0.9, 2.1))
WALL_WORD = {"N": "north", "S": "south", "W": "west", "E": "east"}

PALETTE = {
    "oak": ("Oak", (0.52, 0.36, 0.2), 0.55), "plaster": ("Plaster", (0.88, 0.86, 0.82), 0.9),
    "glass": ("Glass", (0.62, 0.74, 0.8), 0.05), "frame": ("Window Frame Paint", (0.92, 0.92, 0.9), 0.4),
    "navy": ("Navy Paint", (0.05, 0.09, 0.22), 0.4), "walnut": ("Walnut", (0.26, 0.15, 0.08), 0.4),
    "white": ("White Lacquer", (0.9, 0.9, 0.88), 0.3), "marble": ("White Marble", (0.86, 0.86, 0.84), 0.2),
    "brass": ("Brass", (0.8, 0.6, 0.25), 0.3, 1.0), "steel": ("Brushed Steel", (0.7, 0.7, 0.72), 0.3, 1.0),
    "black": ("Black Metal", (0.04, 0.04, 0.04), 0.4, 0.8), "green": ("Bottle Green Velvet", (0.06, 0.25, 0.12), 0.8),
    "cognac": ("Cognac Leather", (0.5, 0.25, 0.1), 0.45), "mustard": ("Mustard Boucle", (0.75, 0.55, 0.12), 0.9),
    "rust": ("Rust Velvet", (0.55, 0.2, 0.08), 0.8), "slate": ("Slate Blue Paint", (0.24, 0.33, 0.45), 0.5),
    "teak": ("Teak", (0.45, 0.28, 0.14), 0.5), "travertine": ("Travertine", (0.78, 0.7, 0.58), 0.6),
    "rug": ("Persian Wool", (0.45, 0.12, 0.1), 0.95), "books": ("Book Covers", (0.55, 0.2, 0.16), 0.7),
    "leaf": ("Leaves", (0.12, 0.35, 0.12), 0.7), "pot": ("Terracotta", (0.62, 0.32, 0.2), 0.8),
    "shade": ("Lamp Linen", (0.95, 0.9, 0.78), 0.8), "screen": ("Screen", (0.02, 0.02, 0.03), 0.15),
    "ceramic": ("White Ceramic", (0.95, 0.95, 0.95), 0.1), "sage": ("Sage Green Paint", (0.45, 0.58, 0.45), 0.5),
    "pink": ("Dusty Pink Velvet", (0.75, 0.45, 0.48), 0.8), "chrome": ("Chrome", (0.85, 0.85, 0.88), 0.1, 1.0),
    "oxblood": ("Oxblood Leather", (0.35, 0.05, 0.05), 0.45), "duckegg": ("Duck Egg Paint", (0.55, 0.72, 0.72), 0.5),
    "terracotta": ("Terracotta Wool", (0.62, 0.36, 0.24), 0.95), "cream": ("Cream Enamel", (0.9, 0.85, 0.7), 0.3),
    "mint": ("Mint Enamel", (0.6, 0.85, 0.72), 0.3), "red": ("Cherry Red Paint", (0.7, 0.06, 0.05), 0.5),
    "butcher": ("Butcher Block", (0.6, 0.42, 0.24), 0.6), "pine": ("Pine", (0.75, 0.6, 0.4), 0.6),
    "grey": ("Pale Grey Paint", (0.62, 0.63, 0.64), 0.5), "graphite": ("Graphite Enamel", (0.18, 0.19, 0.2), 0.4),
    "charcoal": ("Charcoal Linen", (0.15, 0.15, 0.16), 0.9), "linen": ("White Linen", (0.92, 0.9, 0.86), 0.8),
    "wool": ("Grey Wool", (0.45, 0.45, 0.46), 0.9), "cerused": ("Cerused Oak", (0.7, 0.62, 0.5), 0.6),
    "emerald": ("Emerald Velvet", (0.03, 0.4, 0.2), 0.8), "black_enamel": ("Black Enamel", (0.03, 0.03, 0.03), 0.2),
    "yellow": ("Sunflower Plastic", (0.95, 0.7, 0.05), 0.3), "mintpaint": ("Mint Paint", (0.5, 0.8, 0.62), 0.5),
    "paleblue": ("Pale Blue Paint", (0.55, 0.7, 0.88), 0.5), "coral": ("Coral Paint", (0.9, 0.42, 0.32), 0.5),
    "seagreen": ("Sea Green Tile", (0.3, 0.6, 0.5), 0.2), "mahogany": ("Mahogany", (0.35, 0.12, 0.06), 0.4),
    "deepgreen": ("Deep Green Paint", (0.05, 0.22, 0.14), 0.5), "brown_leather": ("Brown Leather", (0.32, 0.17, 0.08), 0.5),
    "canvas": ("Canvas", (0.85, 0.8, 0.68), 0.9), "jute": ("Jute", (0.68, 0.55, 0.36), 0.95),
    "acacia": ("Acacia", (0.55, 0.35, 0.18), 0.6), "cedar": ("Cedar", (0.6, 0.38, 0.22), 0.6),
    "concrete": ("Concrete", (0.6, 0.6, 0.58), 0.8), "decking": ("Teak Decking", (0.5, 0.33, 0.18), 0.7),
    "cobalt": ("Cobalt Glaze", (0.1, 0.2, 0.6), 0.2), "gilt": ("Gilt", (0.75, 0.55, 0.2), 0.35, 1.0),
    "paint_art": ("Ochre And Teal Oil", (0.7, 0.5, 0.2), 0.7), "rattan": ("Rattan", (0.7, 0.55, 0.32), 0.7),
    "zellige": ("Green Zellige", (0.1, 0.38, 0.28), 0.15), "stone": ("Matte Stone", (0.85, 0.84, 0.8), 0.6),
}


def cone(radius, height, segments=12):
    vertices = [(radius * math.cos(2 * math.pi * i / segments), radius * math.sin(2 * math.pi * i / segments), 0.0)
                for i in range(segments)] + [(0.0, 0.0, height)]
    faces = [(i, (i + 1) % segments, segments) for i in range(segments)] + [tuple(reversed(range(segments)))]
    return vertices, faces


def legs(w, d, length, size=0.045, count=4, inset=0.03):
    """Separate legs (each its own piece) under a seat or top: four at the corners, or three round the middle."""
    if count == 3:
        r = min(w, d) / 2 - inset - size
        return [lk.box((size, size, length), (r * math.cos(a), r * math.sin(a), length / 2))
                for a in (math.pi / 2, math.pi * 7 / 6, math.pi * 11 / 6)]
    return [lk.box((size, size, length), (sx * (w / 2 - inset - size / 2), sy * (d / 2 - inset - size / 2), length / 2))
            for sx in (-1, 1) for sy in (-1, 1)]


def knobs(w, d, h, top, n):
    """``n`` knobs or pulls standing 3 cm proud of the front (-Y), in a grid over it."""
    if not n:
        return []
    cols = 1 if n == 1 else (3 if n % 3 == 0 and n > 6 else 2 if n % 2 == 0 else 1)
    rows = int(math.ceil(n / cols))
    low, high = 0.18 * min(h, top), min(h, top) - 0.1
    parts = []
    for i in range(n):
        r, c = divmod(i, cols)
        x = (c - (cols - 1) / 2) * (w / cols)
        z = high if rows == 1 else high - r * (high - low) / (rows - 1)
        parts.append(lk.box((0.06, 0.03, 0.03), (x, -d / 2 - 0.015, z)))
    return parts


def basin(w, d, rim, depth, wall=0.03):
    """A hollow basin: four walls up to ``rim`` and a floor ``depth`` below it."""
    z0 = rim - depth
    return [lk.box((w, d, 0.02), (0, 0, z0 + 0.01)),
            lk.box((w, wall, depth), (0, -d / 2 + wall / 2, z0 + depth / 2)),
            lk.box((w, wall, depth), (0, d / 2 - wall / 2, z0 + depth / 2)),
            lk.box((wall, d, depth), (-w / 2 + wall / 2, 0, z0 + depth / 2)),
            lk.box((wall, d, depth), (w / 2 - wall / 2, 0, z0 + depth / 2))]


def _seat(w, d, h, top, arms=False, leg=None):
    """A chair or upholstered seat on separate legs: seat block, back at +Y, optional arms."""
    leg = top - 0.06 if leg is None else leg
    parts = legs(w, d, leg)
    parts.append(lk.box((w, d, top - leg), (0, 0, (top + leg) / 2)))
    parts.append(lk.box((w, 0.08 if not arms else 0.2, h - top), (0, d / 2 - (0.04 if not arms else 0.1), (h + top) / 2)))
    if arms:
        arm_top = min(h, top + 0.22)
        parts += [lk.box((0.14, d, arm_top - top), (sx * (w / 2 - 0.07), 0, (arm_top + top) / 2)) for sx in (-1, 1)]
    return parts


def shape(kind, w, d, h, top, n):
    """Geometry ``(vertices, faces)`` of a piece of ``kind``, built round its own origin, front toward -Y."""
    top = h if top is None else top
    if kind == "box":
        return lk.box((w, d, h), (0, 0, h / 2))
    if kind == "cyl":
        return lk.cylinder(w / 2, h, (0, 0, h / 2), 24)
    if kind == "rug":
        return lk.box((w, d, max(h, 0.01)), (0, 0, max(h, 0.01) / 2))
    if kind == "wall":
        return lk.box((w, d, h), (0, 0, h / 2))
    if kind == "table":
        return lk.combine(legs(w, d, top - 0.04) + [lk.box((w, d, 0.04), (0, 0, top - 0.02))])
    if kind == "round":
        return lk.combine([lk.cylinder(w / 2, 0.04, (0, 0, top - 0.02), 32), lk.cylinder(0.04, top - 0.04, (0, 0, (top - 0.04) / 2), 12),
                           lk.cylinder(w * 0.3, 0.03, (0, 0, 0.015), 24)])
    if kind in ("chair", "carver"):
        return lk.combine(_seat(w, d, h, top, arms=kind == "carver", leg=top - 0.05))
    if kind == "arm":
        return lk.combine(_seat(w, d, h, top, arms=True, leg=0.12))
    if kind == "sofa":
        return lk.combine(_seat(w, d, h, top, arms=True, leg=0.12))
    if kind == "bench":
        return lk.combine(legs(w, d, top - 0.06) + [lk.box((w, d, 0.06), (0, 0, top - 0.03))])
    if kind == "cantilever":
        return lk.combine([lk.box((w, d, 0.04), (0, 0, top - 0.02)), lk.box((w, 0.04, h - top), (0, d / 2 - 0.02, (h + top) / 2))]
                          + [lk.box((0.025, d, 0.025), (sx * (w / 2 - 0.02), 0, 0.0125)) for sx in (-1, 1)]
                          + [lk.box((0.025, 0.025, top), (sx * (w / 2 - 0.02), -d / 2 + 0.03, top / 2)) for sx in (-1, 1)])
    if kind in ("stool", "stool3", "table3"):
        parts = [lk.cylinder(w / 2, 0.05, (0, 0, top - 0.025), 24)] + legs(w, d, top - 0.05, 0.035, 4 if kind == "stool" else 3, 0.05)
        if h > top + 0.1:
            parts.append(lk.box((w * 0.8, 0.04, h - top), (0, d / 2 - 0.05, (h + top) / 2)))
        return lk.combine(parts)
    if kind in ("bed", "housebed"):
        parts = legs(w, d, 0.15) + [lk.box((w, d, top - 0.35), (0, 0, (top - 0.05) / 2)),
                                    lk.box((w - 0.04, d - 0.04, 0.2), (0, 0, top - 0.1))]
        if kind == "bed":
            parts.append(lk.box((w, 0.08, h), (0, d / 2 + 0.04, h / 2)))
        else:
            post = h - 0.4
            parts += [lk.box((0.05, 0.05, post), (sx * (w / 2 + 0.03), sy * (d / 2 + 0.03), post / 2)) for sx in (-1, 1) for sy in (-1, 1)]
            parts.append(lk.box((0.05, d + 0.1, 0.05), (0, 0, h - 0.025)))
            for sy in (-1, 1):
                parts += [lk.box((0.04, 0.04, 0.5), (sx * w / 4, sy * (d / 2 + 0.03), post + 0.2)) for sx in (-1, 1)]
        return lk.combine(parts)
    if kind in ("cab", "cablegs", "shelf", "desk", "island", "washer", "fireplace"):
        foot = 0.15 if kind == "cablegs" else 0.0
        parts = legs(w, d, foot) if foot else []
        if kind == "shelf":
            parts.append(lk.bookcase(w, d, h, 6))
        elif kind == "desk":
            parts += [lk.box((0.42, d - 0.04, top - 0.04), (sx * (w / 2 - 0.23), 0, (top - 0.04) / 2)) for sx in (-1, 1)]
            parts.append(lk.box((w, d, 0.04), (0, 0, top - 0.02)))
        elif kind == "island":
            parts += [lk.box((w - 0.08, d - 0.08, top - 0.04), (0, 0, (top - 0.04) / 2)), lk.box((w, d, 0.04), (0, 0, top - 0.02))]
        elif kind == "fireplace":
            parts += [lk.box((w - 0.1, d - 0.06, top - 0.05), (0, 0.03, (top - 0.05) / 2)), lk.box((w, d, 0.05), (0, 0, top - 0.025))]
        else:
            parts.append(lk.box((w, d, h - foot), (0, 0, (h + foot) / 2)))
        if kind == "washer":
            parts.append(lk.cylinder(0.2, 0.04, (0, -d / 2 - 0.02, 0.45), 24, axis="Y"))
        return lk.combine(parts + knobs(w, d, h, top, n))
    if kind == "sled":
        frames = [lk.box((0.03, d, 0.03), (sx * (w / 2 - 0.05), 0, 0.015)) for sx in (-1, 1)]
        frames += [lk.box((0.03, 0.03, top - 0.03), (sx * (w / 2 - 0.05), sy * (d / 2 - 0.05), (top - 0.03) / 2)) for sx in (-1, 1) for sy in (-1, 1)]
        return lk.combine(frames + [lk.box((w, d, 0.03), (0, 0, top - 0.015))])
    if kind in ("sink", "basin", "usink", "bath", "tub"):
        depth = {"sink": 0.22, "basin": 0.15, "usink": 0.3, "bath": 0.42, "tub": 0.4}[kind]
        parts = basin(w, d, top, depth)
        if kind == "basin":
            parts.append(lk.cylinder(0.09, top - depth, (0, 0, (top - depth) / 2), 16))
        elif kind == "usink":
            parts += legs(w, d, top - depth)
        elif kind == "bath":
            parts += legs(w, d, top - depth, 0.08, 4, 0.12)
        elif kind == "tub":
            parts.append(lk.box((w, d, top - depth), (0, 0, (top - depth) / 2)))
        return lk.combine(parts)
    if kind == "lounger":
        return lk.combine(legs(w, d, top - 0.06) + [lk.box((w, d, 0.06), (0, 0, top - 0.03)),
                                                     lk.box((w, 0.06, h - top), (0, d / 2 - 0.3, (h + top) / 2))])
    if kind == "swivel":
        return lk.combine([lk.cylinder(w * 0.4, 0.03, (0, 0, 0.015), 20), lk.cylinder(0.03, top - 0.08, (0, 0, (top - 0.08) / 2), 10),
                           lk.box((w, d, 0.08), (0, 0, top - 0.04)), lk.box((w, 0.08, h - top), (0, d / 2 - 0.04, (h + top) / 2))])
    if kind == "peacock":
        return lk.combine([lk.cylinder(0.3, 0.06, (0, 0, top - 0.03), 24), cone(0.28, top - 0.06),
                           lk.box((w, 0.06, h - top), (0, d / 2 - 0.03, (h + top) / 2))])
    if kind == "teepee":
        return cone(w / 2, h, 12)
    if kind == "curtains":
        return lk.combine([lk.box((0.3, d, h), (sx * (w / 2 - 0.15), 0, h / 2)) for sx in (-1, 1)])
    if kind == "screen":
        return lk.box((w, 0.02, h), (0, 0, h / 2))
    raise KeyError(kind)


def _opening_wall(line, axis, low, high, openings):
    return base._wall_x(line, low, high, openings) if axis == "x" else base._wall_y(line, low, high, openings)


def build_shell(bpy, m, collection=None):
    hx, hy = FLOOR[0] / 2, FLOOR[1] / 2
    floor = kit.mesh_object(bpy, "Apartment Floor", *lk.box((FLOOR[0], FLOOR[1], 0.05), (0, 0, -0.025)),
                            collection=collection)
    kit.assign(floor, m.get("oak"))
    win = {side: [(c, w, sill, head) for _room, s, c, w, sill, head in WINDOWS if s == side] for side in "NSWE"}
    parts = _opening_wall(hy, "x", -hx - 0.05, hx + 0.05, win["N"] + [(BALCONY_DOOR[0], BALCONY_DOOR[1], 0.0, BALCONY_DOOR[2])])
    parts += _opening_wall(-hy, "x", -hx - 0.05, hx + 0.05, win["S"])
    parts += _opening_wall(-hx, "y", -hy + 0.05, hy - 0.05, win["W"])
    parts += _opening_wall(hx, "y", -hy + 0.05, hy - 0.05, win["E"] + [(FRONT_DOOR[0], FRONT_DOOR[1], 0.0, DOOR_HEIGHT)])
    for sign in (1, -1):
        openings = [(x, DOOR_WIDTH, 0.0, DOOR_HEIGHT) for room, x in HALL_DOORWAYS.items() if ROOMS[room][2] * sign > 0]
        parts += _opening_wall(sign * HALL, "x", -hx + 0.05, hx - 0.05, openings)
    for x in NORTH_PARTITIONS:
        parts += _opening_wall(x, "y", HALL + 0.05, hy - 0.05, [])
    for x in SOUTH_PARTITIONS:
        side = [(y, DOOR_WIDTH, 0.0, DOOR_HEIGHT) for y in SIDE_DOORWAYS.values()] if x == SIDE_WALL_X else []
        parts += _opening_wall(x, "y", -hy + 0.05, -HALL - 0.05, side)
    parts += _opening_wall(SPLIT_Y, "x", SIDE_WALL_X + 0.05, -2.55, [])
    walls = kit.mesh_object(bpy, "Apartment Wall", *lk.combine(parts), collection=collection)
    kit.assign(walls, m.get("plaster"))
    counts = {}
    for room, side, c, w, sill, head in WINDOWS:
        counts[room] = counts.get(room, 0) + 1
        name = "Apartment Window %s %s" % (room, WALL_WORD[side].title())
        tall = head - sill
        frame = [lk.box((w, 0.08, 0.05), (0, 0, 0.025)), lk.box((w, 0.08, 0.05), (0, 0, tall - 0.025)),
                 lk.box((0.05, 0.08, tall), (-w / 2 + 0.025, 0, tall / 2)), lk.box((0.05, 0.08, tall), (w / 2 - 0.025, 0, tall / 2)),
                 lk.box((0.04, 0.06, tall), (0, 0, tall / 2)), lk.box((w + 0.1, 0.16, 0.04), (0, -0.06, -0.02))]
        geometry, faces_of = lk.multi([(0, lk.combine(frame)), (1, lk.box((w - 0.1, 0.01, tall - 0.1), (0, 0, tall / 2)))])
        at = {"N": (c, hy, sill), "S": (c, -hy, sill), "W": (-hx, c, sill), "E": (hx, c, sill)}[side]
        facing = {"N": -90.0, "S": 90.0, "W": 0.0, "E": 180.0}[side]
        lk.place(bpy, m, name, geometry, at, facing, ("frame", "glass"), faces_of, collection=collection)
    lk.place(bpy, m, "Entrance Hall Front Door", lk.combine([lk.box((FRONT_DOOR[1], 0.05, DOOR_HEIGHT), (0, 0, DOOR_HEIGHT / 2)),
                                                             lk.box((0.12, 0.05, 0.03), (0.35, -0.05, 1.0))]),
             (hx - 0.03, FRONT_DOOR[0]), 180.0, ("navy",), collection=collection)
    x, width, height = BALCONY_DOOR
    door, faces_of = lk.multi([(0, lk.combine([lk.box((width, 0.06, 0.05), (0, 0, height - 0.025)),
                                               lk.box((0.05, 0.06, height), (-width / 2 + 0.025, 0, height / 2)),
                                               lk.box((0.05, 0.06, height), (width / 2 - 0.025, 0, height / 2)),
                                               lk.box((0.05, 0.1, height), (0.05, 0, height / 2))])),
                               (1, lk.combine([lk.box((width / 2, 0.01, height - 0.05), (sx * width / 4, sx * 0.02, height / 2))
                                               for sx in (-1, 1)]))])
    lk.place(bpy, m, "Balcony Door", door, (x, hy), -90.0, ("black", "glass"), faces_of, collection=collection)
    bx0, bx1, by0, by1 = ROOMS["Balcony"]
    deck = kit.mesh_object(bpy, "Balcony Deck", *lk.box((bx1 - bx0, by1 - by0 - 0.05, 0.05), ((bx0 + bx1) / 2, (by0 + by1 + 0.05) / 2, -0.025)),
                           collection=collection)
    kit.assign(deck, m.get("decking"))
    rails, faces = [], []
    for (a, b), line, along_x in ((((bx0, bx1), by1 - 0.02, True)), (((by0 + 0.05, by1), bx0 + 0.02, False)), (((by0 + 0.05, by1), bx1 - 0.02, False))):
        size = (b - a, 0.02, BALUSTRADE - 0.05) if along_x else (0.02, b - a, BALUSTRADE - 0.05)
        middle = ((a + b) / 2, line) if along_x else (line, (a + b) / 2)
        faces.append(lk.box(size, (middle[0], middle[1], (BALUSTRADE - 0.05) / 2)))
        rail = (b - a, 0.05, 0.05) if along_x else (0.05, b - a, 0.05)
        rails.append(lk.box(rail, (middle[0], middle[1], BALUSTRADE - 0.025)))
    geometry, faces_of = lk.multi([(0, lk.combine(rails)), (1, lk.combine(faces))])
    lk.place(bpy, m, "Balcony Balustrade", geometry, (0.0, 0.0), -90.0, ("steel", "glass"), faces_of, collection=collection)


N, S, E, W = 90.0, -90.0, 0.0, 180.0   # The world direction a piece's front looks in.


def P(room, suffix, kind, at, size, text, face=S, mat="oak", top=None, checks="", n=0, col=None, z=None):
    """One row of the table: ``room`` + ``suffix`` is its name; ``text`` is its brief phrase ({w} {d} {h} {top} {at}
    {x} {y} {z} are filled from these numbers); ``checks`` letters say what the checks measure (see the scenario)."""
    return {"room": room, "name": "%s %s" % (room, suffix), "kind": kind, "at": tuple(at), "size": tuple(size),
            "text": text, "facing": face, "mat": (mat,) if isinstance(mat, str) else tuple(mat), "top": top,
            "checks": checks, "n": n, "col": col, "z": z}


def place_piece(bpy, m, piece, collection=None):
    """One piece at its place: wall pieces hang with their backs on the wall face ``at``, centred ``z`` up."""
    w, d, h = piece["size"]
    at, facing, kind = piece["at"], piece["facing"], piece["kind"]
    if kind == "wall":
        a = math.radians(facing)
        at = (at[0] + math.cos(a) * d / 2, at[1] + math.sin(a) * d / 2, piece["z"] - h / 2)
    keys, faces_of = piece["mat"], None
    if kind == "lamp":
        geometry, faces_of = lk.lamp(h, w / 2)
        keys = keys if len(keys) > 1 else keys + ("shade",)
    elif kind == "plant":
        geometry, faces_of = lk.plant(h, min(w, 0.6) / 3)
        keys = keys if len(keys) > 1 else ("pot", "leaf")
    elif kind == "books":
        geometry = lk.books(w, d, h, 6)
    else:
        top = piece["top"] - (at[2] if len(at) > 2 else 0.0) if piece["top"] is not None else None
        geometry = shape(kind, w, d, h, top, piece["n"])
    return lk.place(bpy, m, piece["name"], geometry, at, facing, keys, faces_of, collection=collection)


def build_flat(bpy, pieces, collection=None):
    m = lk.Materials(bpy, PALETTE)
    build_shell(bpy, m, collection)
    for piece in pieces:
        place_piece(bpy, m, piece, collection)
