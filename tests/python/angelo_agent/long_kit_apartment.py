# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The stand-in of ``scenario_oneshot_apartment``: a furnished seven-room apartment, the plainest build of its brief.

Every size, place and height the brief states is a constant here and the checks are derived from them; the rest
(dressing, windows, the bathroom) are simple placeholder shapes so the reference pictures show the layout and mood.
Axes: the floor is centred on the origin with its top at z = 0, north is +Y; a 1.2 m hallway runs along X through
the middle, three rooms north of it and three south.
"""

import long_kits as lk
import scenario_build_kit as kit

FLOOR = (12.0, 9.0)
WALL_HEIGHT, WALL_THICKNESS = 2.7, 0.1
DOOR_WIDTH, DOOR_HEIGHT = 0.9, 2.1
HALL = 0.6   # The hallway's walls are centred on y = +0.6 and y = -0.6.
ROOMS = {"Living Room": (-6.0, -1.5, HALL, 4.5), "Dining Room": (-1.5, 2.0, HALL, 4.5),
         "Kitchen": (2.0, 6.0, HALL, 4.5), "Hallway": (-6.0, 6.0, -HALL, HALL),
         "Bedroom": (-6.0, -2.0, -4.5, -HALL), "Study": (-2.0, 1.5, -4.5, -HALL), "Bathroom": (1.5, 6.0, -4.5, -HALL)}
DOORWAYS = {"Living Room": -2.2, "Dining Room": 0.25, "Kitchen": 2.6, "Bedroom": -3.0, "Study": 0.0, "Bathroom": 3.5}
NORTH_PARTITIONS, SOUTH_PARTITIONS = (-1.5, 2.0), (-2.0, 1.5)

SOFA_AT, COFFEE_AT, ARMCHAIR_AT, LIVING_SHELF_AT, TV_CONSOLE_AT = (-3.75, 4.0), (-3.75, 2.9), (-5.2, 2.9), \
    (-5.775, 1.5), (-3.75, 0.8)
DINING_TABLE_AT = (0.25, 2.7)
DINING_CHAIRS = ((-0.2, 1.95), (0.7, 1.95), (-0.2, 3.45), (0.7, 3.45), (-1.0, 2.7), (1.5, 2.7))
SIDEBOARD_AT, DISPLAY_AT = (0.25, 4.225), (1.725, 1.4)
COUNTER = (2.0, 5.2)   # The base-cabinet run's x extent along the north wall, 0.6 m deep.
SINK_X, HOB_X, FRIDGE_AT, ISLAND_AT = 2.8, 4.6, (5.6, 4.15), (4.0, 2.4)
STOOLS = ((3.4, 1.6), (4.0, 1.6), (4.6, 1.6))
CONSOLE_AT, SHOE_BENCH_AT, COAT_STAND_AT = (-1.0, 0.4), (5.0, -0.375), (5.6, 0.3)
BED_AT, BEDSIDES, WARDROBE_AT, CHEST_AT = (-4.0, -3.45), ((-5.15, -4.25), (-2.85, -4.25)), (-5.675, -1.8), (-2.225, -2.2)
DESK_AT, OFFICE_CHAIR_AT, STUDY_SHELF_AT, LOUNGE_AT = (-0.25, -4.1), (-0.25, -3.35), (-1.775, -2.4), (0.8, -1.7)
TUB_AT, VANITY_AT, TOILET_AT, SHOWER_AT = (2.45, -4.075), (4.3, -4.25), (5.5, -4.15), (5.5, -1.1)

PALETTE = {
    "oak": ("Oak Boards", (0.50, 0.34, 0.19), 0.55), "plaster": ("Plaster", (0.88, 0.86, 0.82), 0.9),
    "glass": ("Window Glass", (0.62, 0.74, 0.80), 0.05), "door": ("Front Door Paint", (0.12, 0.22, 0.30), 0.4),
    "fabric": ("Sofa Linen", (0.42, 0.50, 0.46), 0.85), "leather": ("Tan Leather", (0.45, 0.24, 0.12), 0.45),
    "walnut": ("Walnut", (0.26, 0.15, 0.08), 0.4), "white": ("White Lacquer", (0.90, 0.90, 0.88), 0.3),
    "stone": ("Worktop Stone", (0.72, 0.72, 0.70), 0.25), "steel": ("Brushed Steel", (0.70, 0.70, 0.72), 0.3, 1.0),
    "black": ("Black Metal", (0.04, 0.04, 0.04), 0.4, 0.8), "books": ("Book Covers", (0.55, 0.20, 0.16), 0.7),
    "linen": ("Bed Linen", (0.92, 0.90, 0.86), 0.8), "rug": ("Wool Rug", (0.62, 0.48, 0.36), 0.95),
    "leaf": ("Leaves", (0.12, 0.35, 0.12), 0.7), "pot": ("Terracotta", (0.62, 0.32, 0.20), 0.8),
    "shade": ("Lamp Linen", (0.95, 0.90, 0.78), 0.8), "screen": ("Screen", (0.02, 0.02, 0.03), 0.15),
    "ceramic": ("White Ceramic", (0.95, 0.95, 0.95), 0.1), "tile": ("Bathroom Tile", (0.80, 0.86, 0.88), 0.2),
    "velvet": ("Green Velvet", (0.10, 0.28, 0.20), 0.8),
}


def _wall_x(y, x0, x1, openings):
    """Wall segments along X at ``y`` from ``x0`` to ``x1``; ``openings`` [(centre x, width, bottom, top)]."""
    parts, cursor = [], x0
    for centre, width, bottom, top in sorted(openings):
        left, right = centre - width / 2, centre + width / 2
        if left > cursor:
            parts.append(lk.box((left - cursor, WALL_THICKNESS, WALL_HEIGHT), ((cursor + left) / 2, y, WALL_HEIGHT / 2)))
        if bottom > 0:
            parts.append(lk.box((width, WALL_THICKNESS, bottom), (centre, y, bottom / 2)))
        parts.append(lk.box((width, WALL_THICKNESS, WALL_HEIGHT - top), (centre, y, (top + WALL_HEIGHT) / 2)))
        cursor = right
    if x1 > cursor:
        parts.append(lk.box((x1 - cursor, WALL_THICKNESS, WALL_HEIGHT), ((cursor + x1) / 2, y, WALL_HEIGHT / 2)))
    return parts


def _wall_y(x, y0, y1, openings):
    """The same along Y at ``x`` (built along X, then its coordinates swapped)."""
    swapped = []
    for vertices, faces in _wall_x(x, y0, y1, openings):
        swapped.append(([(vx_y[1], vx_y[0], vx_y[2]) for vx_y in vertices], [tuple(reversed(f)) for f in faces]))
    return swapped


WINDOW = (0.9, 2.1)   # Sill and head heights of every window.
NORTH_WINDOWS = ((-3.75, 1.6), (0.25, 1.4))
SOUTH_WINDOWS = ((-0.25, 1.2),)
WEST_WINDOWS = ((-3.7, 1.2),)
EAST_WINDOWS = ((2.4, 1.4), (-2.6, 0.9))
FRONT_DOOR = (0.0, 1.0)


def build_shell(bpy, m, collection=None):
    floor = kit.mesh_object(bpy, "Apartment Floor", *lk.box((FLOOR[0], FLOOR[1], 0.05), (0, 0, -0.025)),
                            collection=collection)
    kit.assign(floor, m.get("oak"))
    hx, hy = FLOOR[0] / 2, FLOOR[1] / 2
    win = lambda specs: [(c, w, WINDOW[0], WINDOW[1]) for c, w in specs]
    parts = _wall_x(hy, -hx - 0.05, hx + 0.05, win(NORTH_WINDOWS)) + _wall_x(-hy, -hx - 0.05, hx + 0.05, win(SOUTH_WINDOWS))
    parts += _wall_y(-hx, -hy + 0.05, hy - 0.05, win(WEST_WINDOWS))
    parts += _wall_y(hx, -hy + 0.05, hy - 0.05, win(EAST_WINDOWS) + [(FRONT_DOOR[0], FRONT_DOOR[1], 0.0, DOOR_HEIGHT)])
    north = [(x, DOOR_WIDTH, 0.0, DOOR_HEIGHT) for room, x in DOORWAYS.items() if ROOMS[room][2] > 0]
    south = [(x, DOOR_WIDTH, 0.0, DOOR_HEIGHT) for room, x in DOORWAYS.items() if ROOMS[room][2] < 0]
    parts += _wall_x(HALL, -hx + 0.05, hx - 0.05, north) + _wall_x(-HALL, -hx + 0.05, hx - 0.05, south)
    parts += [part for x in NORTH_PARTITIONS for part in _wall_y(x, HALL + 0.05, hy - 0.05, [])]
    parts += [part for x in SOUTH_PARTITIONS for part in _wall_y(x, -hy + 0.05, -HALL - 0.05, [])]
    walls = kit.mesh_object(bpy, "Apartment Wall", *lk.combine(parts), collection=collection)
    kit.assign(walls, m.get("plaster"))
    panes = [lk.box((w, 0.02, WINDOW[1] - WINDOW[0]), (c, s * hy, sum(WINDOW) / 2))
             for s, specs in ((1, NORTH_WINDOWS), (-1, SOUTH_WINDOWS)) for c, w in specs]
    panes += [lk.box((0.02, w, WINDOW[1] - WINDOW[0]), (s * hx, c, sum(WINDOW) / 2))
              for s, specs in ((-1, WEST_WINDOWS), (1, EAST_WINDOWS)) for c, w in specs]
    glass = kit.mesh_object(bpy, "Apartment Windows", *lk.combine(panes), collection=collection)
    kit.assign(glass, m.get("glass"))
    lk.place(bpy, m, "Hallway Front Door", lk.box((FRONT_DOOR[1], 0.05, DOOR_HEIGHT), (0, 0, DOOR_HEIGHT / 2)),
             (hx - 0.03, FRONT_DOOR[0]), facing=180.0, keys=("door",), collection=collection)


def build_rooms(bpy, m, c=None):
    p = lambda name, geometry, at, facing=-90.0, keys=("walnut",), faces_of=None: lk.place(
        bpy, m, name, geometry, at, facing, keys, faces_of, collection=c)
    # Living room.
    p("Living Room Sofa", lk.sofa(2.2, 0.9, 0.45), SOFA_AT, keys=("fabric",))
    p("Living Room Coffee Table", lk.round_table(0.45, 0.4), COFFEE_AT)
    p("Living Room Armchair", lk.sofa(0.85, 0.85, 0.45, 1.05, 0.15), ARMCHAIR_AT, facing=0.0, keys=("leather",))
    p("Living Room Bookcase", lk.bookcase(1.2, 0.35, 2.0), LIVING_SHELF_AT, facing=0.0)
    p("Living Room Books", lk.books(1.2, 0.35, 2.0), LIVING_SHELF_AT, facing=0.0, keys=("books",))
    p("Living Room TV Console", lk.box((1.8, 0.4, 0.5), (0, 0, 0.25)), TV_CONSOLE_AT, facing=90.0)
    p("Living Room Television", lk.box((1.2, 0.05, 0.7), (0, 0.05, 0.9)), TV_CONSOLE_AT, facing=90.0,
      keys=("screen",))
    p("Living Room Rug", lk.rug(2.4, 1.7), COFFEE_AT, keys=("rug",))
    p("Living Room Floor Lamp", *lk.lamp(1.7)[:1], (-5.6, 3.8), keys=("black", "shade"), faces_of=lk.lamp(1.7)[1])
    p("Living Room Plant", *lk.plant(1.4)[:1], (-1.95, 4.1), keys=("pot", "leaf"), faces_of=lk.plant(1.4)[1])
    # Dining room.
    p("Dining Room Table", lk.table(2.0, 1.0, 0.75, 0.07), DINING_TABLE_AT, keys=("oak",))
    for at in DINING_CHAIRS:
        dx, dy = DINING_TABLE_AT[0] - at[0], DINING_TABLE_AT[1] - at[1]
        facing = (0.0 if dx > 0 else 180.0) if abs(dx) > abs(dy) else (90.0 if dy > 0 else -90.0)
        p("Dining Room Chair", lk.chair(0.45), at, facing=facing, keys=("oak",))
    p("Dining Room Plates", lk.combine([lk.cylinder(0.13, 0.015, (x, y, 0.758), 20) for x, y in (
        (-0.45, -0.3), (0.45, -0.3), (-0.45, 0.3), (0.45, 0.3), (-0.85, 0.0), (0.85, 0.0))]), DINING_TABLE_AT,
      keys=("ceramic",))
    p("Dining Room Vase", lk.cylinder(0.07, 0.3, (0, 0, 0.9), 16), DINING_TABLE_AT, keys=("ceramic",))
    p("Dining Room Sideboard", lk.box((1.6, 0.45, 0.85), (0, 0, 0.425)), SIDEBOARD_AT)
    p("Dining Room Display Cabinet", lk.box((1.0, 0.45, 1.9), (0, 0, 0.95)), DISPLAY_AT, facing=180.0,
      keys=("white",))
    # Kitchen.
    run = COUNTER[1] - COUNTER[0]
    counter_at = ((COUNTER[0] + COUNTER[1]) / 2, 4.2)
    p("Kitchen Base Cabinets", lk.box((run, 0.6, 0.86), (0, 0, 0.43)), counter_at, keys=("white",))
    p("Kitchen Worktop", lk.box((run, 0.62, 0.04), (0, 0, 0.88)), counter_at, keys=("stone",))
    p("Kitchen Wall Cabinets", lk.box((run, 0.35, 0.7), (0, 0.125, 1.85)), counter_at, keys=("white",))
    p("Kitchen Sink", lk.box((0.6, 0.42, 0.02), (0, 0, 0.905)), (SINK_X, 4.2), keys=("steel",))
    p("Kitchen Hob", lk.box((0.6, 0.52, 0.02), (0, 0, 0.905)), (HOB_X, 4.2), keys=("black",))
    p("Kitchen Fridge", lk.box((0.7, 0.7, 1.85), (0, 0, 0.925)), FRIDGE_AT, keys=("steel",))
    p("Kitchen Island", lk.combine([lk.box((1.8, 0.9, 0.86), (0, 0, 0.43)), lk.box((1.9, 1.0, 0.04), (0, 0, 0.88))]),
      ISLAND_AT, keys=("white",))
    for at in STOOLS:
        p("Kitchen Bar Chair", lk.stool(0.75), at, facing=90.0, keys=("black",))
    # Hallway.
    p("Hallway Console Table", lk.table(1.0, 0.3, 0.8, 0.04), CONSOLE_AT, facing=-90.0)
    p("Hallway Shoe Bench", lk.box((1.0, 0.35, 0.45), (0, 0, 0.225)), SHOE_BENCH_AT, facing=90.0, keys=("oak",))
    p("Hallway Coat Stand", lk.lamp(1.8, 0.06)[0], COAT_STAND_AT, keys=("black",))
    p("Hallway Runner", lk.rug(7.0, 0.7), (-1.0, 0.0), keys=("rug",))
    # Bedroom.
    p("Bedroom Bed", lk.combine([lk.box((1.6, 2.0, 0.55), (0, 0, 0.275)), lk.box((1.6, 0.08, 1.1), (0, 1.0, 0.55))]),
      BED_AT, facing=90.0, keys=("linen",))
    for at in BEDSIDES:
        p("Bedroom Bedside Table", lk.box((0.45, 0.4, 0.55), (0, 0, 0.275)), at, facing=90.0)
        p("Bedroom Bedside Lamp", *lk.lamp(0.45, 0.1)[:1], (at[0], at[1], 0.55), keys=("black", "shade"),
          faces_of=lk.lamp(0.45, 0.1)[1])
    p("Bedroom Wardrobe", lk.box((2.0, 0.55, 2.1), (0, 0, 1.05)), WARDROBE_AT, facing=0.0, keys=("white",))
    p("Bedroom Chest of Drawers", lk.box((1.0, 0.45, 0.9), (0, 0, 0.45)), CHEST_AT, facing=180.0)
    p("Bedroom Rug", lk.rug(2.2, 1.0), (-4.0, -1.9), keys=("rug",))
    # Study.
    p("Study Desk", lk.table(1.4, 0.7, 0.75, 0.05), DESK_AT, facing=90.0, keys=("oak",))
    p("Study Monitor", lk.box((0.6, 0.04, 0.38), (0, 0.2, 1.0)), DESK_AT, facing=90.0, keys=("screen",))
    p("Study Office Chair", lk.combine([lk.stool(0.48, 0.24), lk.box((0.45, 0.06, 0.5), (0, 0.22, 0.75))]),
      OFFICE_CHAIR_AT, facing=-90.0, keys=("black",))
    p("Study Bookcase", lk.bookcase(2.4, 0.35, 2.2, 6), STUDY_SHELF_AT, facing=0.0)
    p("Study Books", lk.books(2.4, 0.35, 2.2, 6), STUDY_SHELF_AT, facing=0.0, keys=("books",))
    p("Study Lounge Chair", lk.sofa(0.8, 0.8, 0.42, 0.95, 0.12), LOUNGE_AT, facing=-120.0, keys=("velvet",))
    p("Study Plant", *lk.plant(1.2)[:1], (1.2, -4.2), keys=("pot", "leaf"), faces_of=lk.plant(1.2)[1])
    # Bathroom.
    p("Bathroom Bathtub", lk.box((1.7, 0.75, 0.6), (0, 0, 0.3)), TUB_AT, facing=90.0, keys=("ceramic",))
    p("Bathroom Vanity", lk.box((1.0, 0.5, 0.85), (0, 0, 0.425)), VANITY_AT, facing=90.0, keys=("oak",))
    p("Bathroom Toilet", lk.combine([lk.cylinder(0.2, 0.4, (0, -0.05, 0.2), 20), lk.box((0.4, 0.18, 0.4), (0, 0.18, 0.6))]),
      TOILET_AT, facing=90.0, keys=("ceramic",))
    p("Bathroom Shower", lk.combine([lk.box((0.9, 0.02, 2.0), (0, -0.45, 1.0)), lk.box((0.02, 0.9, 2.0), (-0.45, 0, 1.0))]),
      SHOWER_AT, facing=-90.0, keys=("glass",))
    p("Bathroom Floor Tiles", lk.rug(4.4, 3.8), (3.75, -2.55), keys=("tile",))


def build_apartment(bpy, collection=None):
    m = lk.Materials(bpy, PALETTE)
    build_shell(bpy, m, collection)
    build_rooms(bpy, m, collection)
