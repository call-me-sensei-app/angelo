# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot test ``oneshot-apartment``: a furnished seven-room apartment from one message (a long test).

A long test (LONG-TEST-SELECTION, 2026-10-25): the maintainer asked for a one-shot job that takes one chat 20+
minutes, to see whether parallel agents save time on it. It is about ten parlours (``scenario_oneshot_parlour``):
six furnished rooms and a hallway, about forty distinct designs plus their dressing, the kind of job an artist would
split; the brief says nothing about how. Not part of the registered parallel set (``ONESHOT_PARALLEL``).

The stand-in is ``long_kit_apartment.build_apartment``. The checks name only the floor, the walls and each room's
name prefix: rays straight down at each stated piece find its seat or top at the stated height, rays along the
hallway find its doorways open and its walls where stated, and every room's contents stay inside that room.

Axes: the floor is centred on the origin at z = 0, north is +Y; the hallway runs along X through the middle.
"""

import long_kit_apartment as flat
import oneshot_kit
import scenario_build_kit as kit

ID = "oneshot_apartment"
TITLE = "A furnished seven-room apartment"
FRAME = 1
FINAL_VIEW = "hero"
FINAL_STILL = {"name": "apartment-final.png", "size": (1920, 1080)}
GROUND_TRUTH = "stand-in"
# The calibration pilots' cap (BENCH-PREREG Amendment I7): a pilot that reaches it counts as ">= 7,200 s", never a
# measured time. Paired trials set their own with --task-timeout (Amendment I3: twice the qualifying pilot's time).
LIMITS = {"wall_s": 7200.0, "job_wait_s": 0.0, "cost_usd": 80.0}
APPROVABLE = ()
JOB_WAIT_S = 0.0


def _room_view(room):
    """From high above the hallway, looking down into ``room`` over its wall (there is no ceiling)."""
    x0, x1, y0, y1 = flat.ROOMS[room]
    cx, cy, width = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0
    edge = y0 - 1.2 if y0 > 0 else y1 + 1.2
    return {"location": (cx + 0.2 * width, edge, 6.0), "target": (cx, cy, 0.3), "lens": 24.0,
            "role": "closeup", "label": "Close-up: the %s from above the hallway wall" % room.lower()}


HERO = {"location": (7.0, -9.0, 15.0), "target": (0.0, 0.0, 0.0), "lens": 35.0}
CAMERAS = {
    "hero": dict(HERO, role="reference", label="Hero (the scene camera)"),
    "top": {"location": (0.0, 0.0, 20.0), "target": (0.0, 0.0, 0.0), "ortho_scale": 16.5, "role": "reference",
            "label": "Plan (orthographic, +Y up)"},
    "front": {"location": (0.0, -25.0, 1.3), "target": (0.0, 0.0, 1.3), "ortho_scale": 13.2, "role": "reference",
              "label": "South elevation (orthographic, looking +Y)"},
    "side": {"location": (25.0, 0.0, 1.3), "target": (0.0, 0.0, 1.3), "ortho_scale": 10.0, "role": "reference",
             "label": "East elevation (orthographic, looking -X)"},
    **{"closeup_" + room.lower().replace(" ", "_"): _room_view(room) for room in flat.ROOMS if room != "Hallway"},
    "closeup_hallway": {"location": (6.8, -1.6, 3.4), "target": (0.0, 0.0, 0.3), "lens": 24.0, "role": "closeup",
                        "label": "Close-up: the hallway from its east end"},
}
FLOOR, WALLS = {"whole": "Apartment Floor"}, {"prefix": "Apartment Wall"}   # Boxes measure the whole floor.
SUBJECT = ({"prefix": ""},)
FURNITURE = {"prefix": "", "except": [FLOOR, WALLS]}

PROMPT = (
    "A furnished apartment, seen from above with no ceiling: six rooms off one hallway, every room fully furnished "
    "and dressed. Build it. The two attached pictures show the apartment I have in mind: the hero shot and a plan "
    "from above (+Y is north). Take the layout and the mood from them and design every piece yourself, each with "
    "real detail; where they and this brief disagree, the brief wins.\n\n"
    "Conventions. Metres, Z up. I find the floor and walls by name and each room's things by their names, so name "
    "the floor and walls exactly as written here, give everything in a room a name that starts with that room's name "
    "as written in its paragraph (for example 'Kitchen Fridge'), and give anything else a meaningful name of its "
    "own. Keep the file tidy: nothing from the default scene remains, no unused leftovers, and a material on "
    "everything that shows. No ceiling.\n\n"
    "The shell. 'Apartment Floor', oak boards, 12 m along X by 9 m along Y, centred on the origin with its top at z "
    "= 0. Plaster walls 2.7 m high and 0.1 m thick whose names start with 'Apartment Wall': along all four outer "
    "edges; the two walls of a 1.2 m wide hallway running the full length east to west, centred on y = 0.6 and y = "
    "-0.6; and between the rooms, at x = -1.5 and x = 2 north of the hallway and at x = -2 and x = 1.5 south of it. "
    "North of the hallway are, west to east, the living room, the dining room and the kitchen; south of it the "
    "bedroom, the study and the bathroom. Each room opens onto the hallway through an open doorway 0.9 m wide and "
    "2.1 m high, centred on the hallway wall at x = -2.2 (living room), 0.25 (dining room), 2.6 (kitchen), -3 "
    "(bedroom), 0 (study) and 3.5 (bathroom). The front door is in the east wall at the end of the hallway. Every "
    "room but the hallway has a window with a frame and a sill in an outer wall.\n\n"
    "Each piece below stands on the floor centred where given (x, y), with its seat or top at the stated height.\n\n"
    "The living room ('Living Room'): a three-seat fabric sofa 2.2 m wide with its back to the north wall at (-3.75, "
    "4), seat 0.45 m, with cushions and a throw; a round wooden coffee table 0.9 m across at (-3.75, 2.9), top 0.4 "
    "m, on a large rug; a leather wingback armchair at (-5.2, 2.9) facing east, seat 0.45 m; a bookcase 1.2 m wide "
    "against the west wall at (-5.78, 1.5), 2 m tall and full of books; a low TV console 1.8 m long against the "
    "hallway wall at (-3.75, 0.8) with a flat television on it facing the sofa; an arc floor lamp, a tall potted "
    "plant in the north-east corner and two framed pictures on the walls.\n\n"
    "The dining room ('Dining Room'): an oak dining table 2 m along X by 1 m at (0.25, 2.7), top 0.75 m, laid with "
    "six place settings (plates, glasses and cutlery) and a vase of flowers in the middle; six matching dining "
    "chairs facing it, seats 0.45 m, two on each long side at (-0.2, 1.95), (0.7, 1.95), (-0.2, 3.45) and (0.7, "
    "3.45) and one at each end at (-1, 2.7) and (1.5, 2.7); a sideboard 1.6 m wide against the north wall at (0.25, "
    "4.22), top 0.85 m, with drawers and cupboard doors and a candlestick at each end; a glass-fronted display "
    "cabinet 1 m wide against the east wall at (1.72, 1.4), 1.9 m tall, with dishes on its shelves; and a large "
    "mirror on the west wall.\n\n"
    "The kitchen ('Kitchen'): a run of base cabinets with a stone worktop along the north wall from x = 2 to x = "
    "5.2, 0.6 m deep, worktop 0.9 m high, with a sink centred at x = 2.8 and a four-ring hob at x = 4.6 set into it, "
    "a cooker hood above the hob and wall cabinets 0.35 m deep above the run; a tall fridge-freezer at (5.6, 4.15), "
    "1.85 m tall; a kitchen island 1.8 m by 0.9 m at (4, 2.4), worktop 0.9 m; three high bar chairs along its south "
    "side at (3.4, 1.6), (4, 1.6) and (4.6, 1.6), seats 0.75 m; pans on the hob, a kettle and a fruit bowl on the "
    "worktop, and a rail of hanging utensils.\n\n"
    "The hallway ('Hallway'): a narrow console table against its north wall at (-1, 0.4), top 0.8 m, with a round "
    "mirror above it; a shoe bench against its south wall at (5, -0.38), seat 0.45 m; a coat stand with coats near "
    "the front door; a long runner rug down the middle; and the front door itself.\n\n"
    "The bedroom ('Bedroom'): a double bed 1.6 m by 2 m with its headboard against the south wall at (-4, -3.45), "
    "mattress top 0.55 m, made up with a duvet and pillows; a bedside table each side of it with a small lamp; a "
    "wardrobe 2 m wide against the west wall at (-5.68, -1.8), 2.1 m tall; a chest of drawers 1 m wide against the "
    "east wall at (-2.22, -2.2), top 0.9 m; a rug at the foot of the bed and curtains at the window.\n\n"
    "The study ('Study'): a desk 1.4 m by 0.7 m against the south wall at (-0.25, -4.1), top 0.75 m, with a monitor, "
    "a keyboard and a desk lamp; a swivel office chair at (-0.25, -3.35), seat 0.48 m; a bookcase 2.4 m wide against "
    "the west wall at (-1.78, -2.4), 2.2 m tall and full of books; a velvet lounge chair with a matching footrest at "
    "(0.8, -1.7), seat 0.42 m, a reading lamp beside it, and a plant.\n\n"
    "The bathroom ('Bathroom'): white tiles; a bathtub along the south wall from x = 1.6 to x = 3.3; a vanity unit "
    "with a basin against the south wall at (4.3, -4.25) and a mirror cabinet above it; a toilet at (5.5, -4.15); a "
    "glass shower enclosure 0.9 m square in the north-east corner; a towel rail with towels and a bath mat.\n\n"
    "Light and camera. Soft daylight from the south-east coming in through the windows, and a camera named 'Hero "
    "Camera' at (7, -9, 15) looking at (0, 0, 0) with a 35 mm lens, as the scene's camera.\n\n"
    "Before you call it done, look at the apartment from the hero camera and room by room next to the pictures, and "
    "fix what is wrong.")


def _down(at, low, high, label, off=(0.0, 0.0)):
    """A ray straight down onto a piece, ``off`` metres from its stated centre (``at``).

    Every ray is aimed at a face with margin (the roadster lesson, 2026-10-25): never along a symmetry axis, through a
    pole or on a seam or edge, so each offset is off both of a piece's centre lines and stays well inside the
    footprint the brief's own sizes guarantee (``long_probe_margins`` proves each ray still hits the stand-in within
    range when moved 5 cm in eight directions)."""
    return {"kind": "probe", "origin": [round(at[0] + off[0], 3), round(at[1] + off[1], 3), 4.0],
            "direction": [0, 0, -1], "target": FURNITURE, "axis": "z", "range": [low, high], "distance": 5.0,
            "label": label}


def _along(origin, direction, label, axis=None, span=None, distance=1.2):
    """A ray along the hallway or a room at 1.1 m: ``span`` set, it must meet a wall there; unset, no wall."""
    check = {"kind": "probe", "origin": list(origin), "direction": list(direction), "target": WALLS,
             "distance": distance, "label": label}
    if span is None:
        check["expect"] = "miss"
    else:
        check.update(axis=axis, range=list(span))
    return check


def _inside(room, axis, low, high):
    """Everything named after ``room`` lies between ``low`` and ``high`` on ``axis`` (25 cm of slack for frames and
    sills in its walls): a bbox whose min and max must both lie within half the room's span of its middle."""
    middle = [None, None, None]
    middle[axis] = (low + high) / 2
    return {"kind": "bbox", "target": {"prefix": room, "word": True}, "min": list(middle), "max": list(middle),
            "tolerance": (high - low) / 2 + 0.25,
            "label": "everything named '%s ...' inside it, %s = %g to %g" % (room, "xy"[axis], low, high)}


def _where(at):
    return "(%g, %g)" % at


SEAT = (0.38, 0.6)
# The doorway rays run 12 cm east of each doorway's centre line, inside its 0.9 m width.
DOOR_OFFSET = 0.12


def checks():
    hx, hy = flat.FLOOR[0] / 2, flat.FLOOR[1] / 2
    doorways = [_along((x + DOOR_OFFSET, 0.07, 1.1), (0, 1 if flat.ROOMS[room][2] > 0 else -1, 0),
                       "the %s doorway open at x = %g" % (room.lower(), x)) for room, x in flat.DOORWAYS.items()]
    walls = [_along((-4.53, 0.07, 1.1), (0, 1, 0), "the hallway's north wall at y = 0.6", "y", (0.45, 0.75)),
             _along((-4.53, -0.07, 1.1), (0, -1, 0), "the hallway's south wall at y = -0.6", "y", (-0.75, -0.45))]
    walls += [_along((x0 + 0.5, cy, 1.1), (1, 0, 0), "a wall between rooms at x = %g" % x, "x", (x - 0.15, x + 0.15),
                     distance=8.0)
              for (x0, cy, x) in ((-5.5, 2.33, -1.5), (0.0, 2.33, 2.0), (-5.5, -2.33, -2.0), (-1.5, -2.33, 1.5))]
    rooms = [_inside(room, axis, low, high) for room, (x0, x1, y0, y1) in flat.ROOMS.items()
             for axis, low, high in ((0, x0, x1), (1, y0, y1))]
    pieces = [
        # The middle seat cushion, off its centre and in front of the back.
        _down(flat.SOFA_AT, *SEAT, "the sofa seat 0.45 m at " + _where(flat.SOFA_AT), (0.12, -0.08)),
        _down(flat.COFFEE_AT, 0.36, 0.48, "the coffee table top 0.4 m at " + _where(flat.COFFEE_AT), (0.15, 0.1)),
        _down(flat.ARMCHAIR_AT, *SEAT, "the armchair seat 0.45 m at " + _where(flat.ARMCHAIR_AT), (0.1, 0.07)),
        _down((-5.78, 1.5), 1.85, 2.15, "the living-room bookcase 2 m tall at (-5.78, 1.5)", (0.04, 0.2)),
        # Toward its west end, clear of the vase in the middle and of the place settings at the sides and the end.
        _down(flat.DINING_TABLE_AT, 0.7, 0.82, "the dining table top 0.75 m at (0.25, 2.7)", (-0.6, 0.08)),
        *[_down(at, 0.4, 0.55, "a dining chair seat 0.45 m at " + _where(at), (0.06, 0.05))
          for at in flat.DINING_CHAIRS],
        # Off its middle, short of the candlestick at each end.
        _down((0.25, 4.22), 0.8, 0.9, "the sideboard top 0.85 m at (0.25, 4.22)", (-0.35, -0.06)),
        _down((1.72, 1.4), 1.8, 2.0, "the display cabinet 1.9 m tall at (1.72, 1.4)", (-0.06, 0.2)),
        # The worktop between the sink and the hob, in front of the wall cabinets above it.
        _down((3.7, 4.0), 0.86, 0.95, "the kitchen worktop 0.9 m between the sink and the hob", (0.0, -0.02)),
        _down(flat.FRIDGE_AT, 1.75, 1.95, "the fridge-freezer 1.85 m tall at " + _where(flat.FRIDGE_AT), (-0.12, -0.1)),
        _down(flat.ISLAND_AT, 0.86, 0.95, "the island worktop 0.9 m at " + _where(flat.ISLAND_AT), (-0.45, 0.12)),
        *[_down(at, 0.68, 0.85, "a bar chair seat 0.75 m at " + _where(at), (0.06, 0.05)) for at in flat.STOOLS],
        _down(flat.CONSOLE_AT, 0.76, 0.86, "the hallway console table top 0.8 m at " + _where(flat.CONSOLE_AT),
              (-0.25, -0.04)),
        _down((5.0, -0.38), 0.4, 0.55, "the shoe bench seat 0.45 m at (5, -0.38)", (0.25, 0.04)),
        # Toward the foot of the bed, off its middle line: the duvet over the mattress, not a pillow.
        _down(flat.BED_AT, 0.48, 0.75, "the bed's mattress top 0.55 m at " + _where(flat.BED_AT), (0.3, 0.35)),
        _down((-5.68, -1.8), 2.0, 2.2, "the wardrobe 2.1 m tall at (-5.68, -1.8)", (0.08, 0.4)),
        _down((-2.22, -2.2), 0.85, 0.95, "the chest of drawers top 0.9 m at (-2.22, -2.2)", (-0.06, 0.25)),
        # The desk's front half beside the middle, where a keyboard may lie but the monitor does not stand.
        _down(flat.DESK_AT, 0.7, 0.82, "the desk top 0.75 m at (-0.25, -4.1)", (0.3, 0.18)),
        _down(flat.OFFICE_CHAIR_AT, 0.4, 0.6, "the office chair seat 0.48 m at " + _where(flat.OFFICE_CHAIR_AT),
              (0.06, 0.05)),
        _down((-1.78, -2.4), 2.05, 2.35, "the study bookcase 2.2 m tall at (-1.78, -2.4)", (0.04, 0.5)),
        _down(flat.LOUNGE_AT, 0.35, 0.58, "the lounge chair seat 0.42 m at " + _where(flat.LOUNGE_AT), (0.07, -0.05)),
    ]
    return [
        {"kind": "hygiene", "absent": ["Cube", "Light", "Camera"], "max_default_named": 0, "max_without_material": 0,
         "max_unused": 0, "label": "tidy file: no default leftovers, named, materials"},
        {"kind": "bbox", "target": FLOOR, "size": [[11.6, 12.4], [8.6, 9.4], None], "max": [None, None, 0.0],
         "tolerance": 0.05, "label": "a 12 x 9 m floor, its top at z = 0"},
        {"kind": "bbox", "target": WALLS, "min": [-hx, -hy, 0.0], "max": [hx, hy, flat.WALL_HEIGHT], "tolerance": 0.15,
         "label": "walls around the 12 x 9 m floor, 2.7 m high"},
        *walls, *doorways, *rooms, *pieces,
        {"kind": "camera", "target": {"name": "Hero Camera"}, "active": True, "location": list(HERO["location"]),
         "radius": 0.3, "look_at": list(HERO["target"]), "min_dot": 0.99, "lens": [33.0, 37.0],
         "label": "Hero Camera placed, aimed and active"},
    ]


GRADE_CLASSES = {
    "Living room": {"views": ["closeup_living_room", "hero"],
                    "what": "A fabric three-seat sofa with cushions, a round wooden coffee table on a rug, a leather "
                            "wingback armchair, a full bookcase, a TV console with a television, an arc lamp, a "
                            "plant and pictures."},
    "Dining room": {"views": ["closeup_dining_room", "top"],
                    "what": "An oak table laid for six with a vase of flowers, six matching chairs, a sideboard with "
                            "candlesticks, a glass-fronted display cabinet with dishes, a mirror."},
    "Kitchen": {"views": ["closeup_kitchen", "hero"],
                "what": "Base and wall cabinets with a stone worktop, a sink, a hob with a hood, a fridge-freezer, "
                        "an island with three high bar chairs, pans, a kettle, a fruit bowl and hanging utensils."},
    "Hallway": {"views": ["closeup_hallway", "top"],
                "what": "A console table with a round mirror, a shoe bench, a coat stand with coats, a runner rug "
                        "and the front door."},
    "Bedroom": {"views": ["closeup_bedroom", "hero"],
                "what": "A made-up double bed with a headboard, two bedside tables with lamps, a wardrobe, a chest "
                        "of drawers, a rug and curtains."},
    "Study": {"views": ["closeup_study", "hero"],
              "what": "A desk with a monitor, keyboard and lamp, a swivel office chair, a full bookcase, a velvet "
                      "lounge chair with a footrest and a reading lamp, a plant."},
    "Bathroom": {"views": ["closeup_bathroom", "top"],
                 "what": "White tiles, a bathtub, a vanity with a basin and mirror cabinet, a toilet, a glass "
                         "shower, a towel rail with towels and a bath mat."},
    "Apartment": {"views": ["top", "hero"], "what": "The oak floor, plaster walls, open doorways, windows with "
                                                    "frames and sills, and the front door of a six-room apartment.",
                  "layout": "A hallway along the middle; living room, dining room and kitchen north of it from west "
                            "to east; bedroom, study and bathroom south of it; each room's furniture where the brief "
                            "puts it."},
}
CHECKS = checks()
TURNS = [{"id": "brief", "title": "The apartment", "references": ["hero", "top"], "prompt": PROMPT, "checks": CHECKS}]


def build_turn(bpy, turn_id):
    if turn_id != "brief":
        raise KeyError(turn_id)
    oneshot_kit.clear_default_scene(bpy)
    flat.build_apartment(bpy)
    scene = bpy.context.scene
    kit.sun_object(bpy, "Daylight", 40.0, -45.0, (1.0, 0.95, 0.88), 3.0, angle_deg=4.0)
    scene.world = kit.colour_world(bpy, "Daylight Sky", (0.55, 0.62, 0.7), strength=0.8)
    scene.camera = kit.camera_object(bpy, "Hero Camera", HERO["location"], HERO["target"], HERO["lens"])
    bpy.context.view_layer.update()


def build(bpy, upto=None):
    build_turn(bpy, "brief")
