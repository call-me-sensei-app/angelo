# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot test ``oneshot-high-street``: a high street of eight buildings, a square and its props (a long test).

A long test (LONG-TEST-SELECTION, 2026-10-25): the maintainer asked for a one-shot job that takes one chat 20+
minutes, to see whether parallel agents save time on it. It is larger than ``scenario_oneshot_apartment``: eight
distinct buildings with their own facades and roofs, a square with a fountain, trees, benches and a kiosk, and the
street's furniture and two cars, the kind of job an artist would split; the brief says nothing about how. Not part
of the registered parallel set (``ONESHOT_PARALLEL``).

The stand-in is ``long_kit_street.build_high_street``. The checks name only what the brief names: each building's
parts stay on its plot and front, a ray down its roof finds the stated height, and the named props are where and as
large as stated; rays down the café tables find their tops.

Axes: the street runs along X through the origin, north is +Y; the road's top is z = 0 and the pavements' 0.15 m.
"""

import long_kit_street as town
import oneshot_kit
import scenario_build_kit as kit

ID = "oneshot_high_street"
TITLE = "A high street with a square"
FRAME = 1
FINAL_VIEW = "hero"
FINAL_STILL = {"name": "high-street-final.png", "size": (1920, 1080)}
GROUND_TRUTH = "stand-in"
# The calibration pilots' cap (BENCH-PREREG Amendment I7): a pilot that reaches it counts as ">= 7,200 s", never a
# measured time. Paired trials set their own with --task-timeout (Amendment I3: twice the qualifying pilot's time).
LIMITS = {"wall_s": 7200.0, "job_wait_s": 0.0, "cost_usd": 80.0}
APPROVABLE = ()
JOB_WAIT_S = 0.0

HERO = {"location": (2.0, -36.0, 26.0), "target": (0.0, 2.0, 4.0), "lens": 30.0}


def _look(location, target, label, lens=28.0):
    return {"location": location, "target": target, "lens": lens, "role": "closeup", "label": "Close-up: " + label}


CAMERAS = {
    "hero": dict(HERO, role="reference", label="Hero (the scene camera)"),
    "top": {"location": (0.0, 0.0, 60.0), "target": (0.0, 0.0, 0.0), "ortho_scale": 60.0, "role": "reference",
            "label": "Plan (orthographic, +Y up)"},
    "front": {"location": (0.0, -60.0, 7.0), "target": (0.0, 0.0, 7.0), "ortho_scale": 44.0, "role": "reference",
              "label": "From the south (orthographic, looking +Y)"},
    "side": {"location": (60.0, 0.0, 7.0), "target": (0.0, 0.0, 7.0), "ortho_scale": 38.0, "role": "reference",
             "label": "From the east (orthographic, looking -X)"},
    "closeup_west": _look((-10.0, -3.0, 4.0), (-14.5, 8.0, 5.0), "the bakery and the bookshop"),
    "closeup_cafe": _look((-1.0, -2.5, 3.5), (-3.0, 7.0, 4.5), "the cafe and its terrace"),
    "closeup_east": _look((11.0, -2.5, 4.0), (10.0, 8.0, 5.0), "the pharmacy, the flower shop and the bank"),
    "closeup_square": _look((0.0, -2.0, 6.0), (0.0, -11.5, 1.0), "the square, its fountain, trees, stalls and kiosk"),
    "closeup_hotel": _look((-9.0, 2.5, 4.0), (-15.0, -9.0, 6.0), "the hotel and the phone box"),
    "closeup_town_hall": _look((9.0, 2.5, 4.0), (15.0, -9.0, 8.0),
                               "the town hall, its clock tower and the bus shelter"),
    "closeup_street": _look((-20.0, 0.5, 2.2), (0.0, 1.5, 1.0), "the street's lamps, cars and crossing", 35.0),
}
# Boxes measure the whole named thing (scenario_checks ``whole``: its parts and its children too).
ROAD, PAVEMENT, SQUARE = {"whole": "Street Road"}, {"prefix": "Street Pavement"}, {"whole": "Square Paving"}
SUBJECT = ({"prefix": ""},)

PROMPT = (
    "A small-town high street on a sunny afternoon: eight buildings, each its own design, a little square with a "
    "fountain, and everything a street has. Build it. The two attached pictures show the street I have in mind: the "
    "hero shot and a plan from above (+Y is north). Take the layout and the mood from them and design every building "
    "and prop yourself, each with real detail; where they and this brief disagree, the brief wins.\n\n"
    "Conventions. Metres, Z up. I find things by name, so name the road, pavements, square and props exactly as "
    "written here, give every part of a building a name that starts with that building's name as written below (for "
    "example 'Bakery Roof'), and give anything else a meaningful name of its own. Keep the file tidy: nothing from "
    "the default scene remains, no unused leftovers, and a material on everything that shows. Heights of buildings "
    "and props are measured from the pavement or square they stand on.\n\n"
    "The street. 'Street Road', asphalt with white road markings, 40 m along X by 7 m along Y, centred on the origin "
    "with its top at z = 0, and a zebra crossing across it at x = 8. Pavements of stone paving whose names start "
    "with 'Street Pavement', 3 m wide along both sides of the road for its full length, their tops at z = 0.15 with "
    "a kerb at the road's edge.\n\n"
    "The north side. Six buildings side by side, each 10 m deep, their fronts on y = 6.5 facing the street, west to "
    "east: 'Bakery' from x = -20 to -13, three storeys of cream render, a slate roof pitched along the street with "
    "its eaves at 9 m and its ridge 12 m up, chimneys at both ends of its ridge, a big shop window and a striped "
    "awning; 'Bookshop' from -13 to -7, two storeys of red brick with a dark green shopfront and a bay window, a "
    "flat roof at 7 m behind a parapet; 'Cafe' from -7 to 1, three storeys of pale blue render, a mansard roof with "
    "dormer windows rising from eaves at 9.5 m to a flat top at 12.5 m, a striped awning over its terrace; "
    "'Pharmacy' from 1 to 7, two storeys of white render, its gable facing the street, the ridge running north-south "
    "over x = 4 at 10 m with its eaves at 6.5 m, a green cross sign; 'Flower Shop' from 7 to 13, two storeys of pink "
    "render, a flat roof at 6.5 m behind a parapet, an awning and buckets of flowers out front; and 'Bank' from 13 "
    "to 20, three storeys of pale stone with columns either side of its door, a cornice and a flat roof at 13 m "
    "behind a balustrade. Every awning reaches 1.2 m over the pavement, and no part of any building on either side "
    "of the street reaches further; every building has windows on every storey, a front door and a sign, and every "
    "shop window holds a display of what the shop sells.\n\n"
    "The south side. 'Hotel' from x = -20 to -10 and 'Town Hall' from x = 10 to 20, each 10 m deep, their fronts on "
    "y = -6.5 facing the street. The hotel is four storeys of sandstone with a canopy over its entrance, balconies "
    "with railings and flower boxes on its upper storeys and a flat roof at 14 m. The town hall is two storeys of "
    "red brick with a flat roof at 9 m and a square clock tower 4 m across centred at (15, -9), with a clock face "
    "toward the street and a pointed spire whose tip, 20 m up, is the town hall's highest point.\n\n"
    "The square, between them from x = -10 to 10 and y = -16.5 to -6.5: 'Square Paving' with its top at z = 0.15; "
    "'Square Fountain', a round stone basin 5 m across centred at (0, -11.5) with its rim 0.6 m high, water in it "
    "and a central column 2.5 m tall, its highest point; four trees whose names start with 'Square Tree', 6 m tall, "
    "at (-6.5, -9.5), (6.5, -9.5), (-6.5, -14) and (6.5, -14); four wooden benches facing the fountain; three market "
    "stalls with striped canopies along the square's south edge at x = -3, 0 and 3, selling fruit, vegetables and "
    "cheese; and a green newspaper kiosk, 'Square Kiosk', 2.5 m along X by 2 m, 3 m tall, centred at (-8, -15).\n\n"
    "On the street. Eight matching lamp posts 4.5 m tall whose names start with 'Street Lamp', four on each pavement "
    "at y = 4 and y = -4, at x = -15, -5, 5 and 15; the cafe's terrace on the pavement in front of it: three round "
    "tables 0.7 m across whose names start with 'Terrace Table', at (-5, 4.8), (-3, 4.8) and (-1, 4.8), tops 0.75 m, "
    "each with two chairs whose names start with 'Terrace Chair'; a red post box; bins; traffic lights at the "
    "crossing; 'Bus Shelter', 3 m along X by 1.4 m, roof 2.6 m up, centred at (14, -5.3), open toward the road; "
    "'Phone Box', a red telephone box 1 m square and 2.5 m tall, centred at (-14, -4.4); and two cars parked on the "
    "road against the north kerb: 'Parked Hatchback', a blue hatchback 4 m long, 1.75 m wide and 1.5 m tall centred "
    "at (-9, 2.4), and 'Parked Van', a white delivery van 5.2 m long, 2 m wide and 2.4 m tall centred at (13, "
    "2.3).\n\n"
    "Light and camera. A warm afternoon sun from the south-west with soft shadows and a clear sky, and a camera "
    "named 'Hero Camera' at (2, -36, 26) looking at (0, 2, 4) with a 30 mm lens, as the scene's camera.\n\n"
    "Before you call it done, look at the street from the hero camera and building by building next to the pictures, "
    "and fix what is wrong.")

K = town.KERB


def _down(at, target, low, high, label, top=40.0):
    """A ray straight down at ``at``. Every ray is aimed at a face with margin (the roadster lesson, 2026-10-25):
    never along a symmetry axis, through a pole or on a ridge, seam or edge, and well inside what the brief's own
    sizes guarantee is there (``long_probe_margins`` proves each still hits the stand-in within range when moved
    5 cm in eight directions). Tips and tops a ray could only meet on a pole are boxes' heights instead."""
    return {"kind": "probe", "origin": [round(at[0], 3), round(at[1], 3), top], "direction": [0, 0, -1],
            "target": target, "axis": "z", "range": [low, high], "distance": top + 1.0, "label": label}


# Roof rays land 0.7 m east and 0.6 m off the middle of the plot (toward the back): off the ridge of a pitched roof,
# on its slope, where the brief's eaves and ridge heights give the height; on the flat top of a mansard or flat roof.
ROOF_OFFSET = (0.7, 0.6)


def _building(name):
    x0, x1, front, roof, height = town.BUILDINGS[name]
    target = {"prefix": name, "word": True}
    north = front > 0
    back = front + town.DEPTH * (1 if north else -1)
    reach = town.AWNING_REACH / 2   # A front between the facade and the awnings' edge.
    plot = {"kind": "bbox", "target": target, "min": [x0, None if north else back, None],
            "max": [x1, back if north else None, None], "tolerance": 0.5,
            "label": "%s on its plot, x = %g to %g, 10 m deep" % (name, x0, x1)}
    face = {"kind": "bbox", "target": target, "min": [None, front - reach, None] if north else None,
            "max": None if north else [None, front + reach, None], "tolerance": reach + 0.05,
            "label": "%s's front on y = %g (awnings up to 1.2 m over the pavement)" % (name, front)}
    away = 1 if north else -1
    at = ((x0 + x1) / 2 + ROOF_OFFSET[0], front + away * (town.DEPTH / 2 + ROOF_OFFSET[1]))
    if name == "Town Hall":   # Its flat roof clear of the tower (x = 13 to 17, y = -11 to -7).
        at = (12.0, -12.5)
    expected = height
    if roof == "ridge_x":     # Down the slope from the ridge toward the back eaves.
        expected = height - ROOF_OFFSET[1] * (height - town.EAVES[name]) / (town.DEPTH / 2)
    elif roof == "ridge_y":   # Down the slope from the ridge (along Y over the plot's middle) toward the east eaves.
        expected = height - ROOF_OFFSET[0] * (height - town.EAVES[name]) / ((x1 - x0) / 2)
    low, high = (0.35, 0.35) if roof.startswith("ridge") else (0.2, 0.3)
    roof_check = _down(at, target, round(K + expected - low, 3), round(K + expected + high, 3),
                       "%s's %s %g m up" % (name, "ridge" if roof.startswith("ridge") else "roof", height))
    return [plot, face, roof_check]


def checks():
    buildings = [check for name in town.BUILDINGS for check in _building(name)]
    fx, fy = town.FOUNTAIN_AT
    (hx, hy), (hl, hw, hh) = town.HATCHBACK
    (vx, vy), (vl, vw, vh) = town.VAN
    (sx, sy), (sl, sd, sh) = town.SHELTER
    (px, py), (pw, pd, ph) = town.PHONE_BOX
    kx, ky = town.KIOSK_AT
    kl, kd, kh = town.KIOSK
    lamp_x = max(town.LAMPS_X)
    radius = town.CAFE_TABLE_RADIUS
    terrace = (min(x for x, _ in town.CAFE_TABLES), town.CAFE_TABLES[0][1], max(x for x, _ in town.CAFE_TABLES))
    return [
        {"kind": "hygiene", "absent": ["Cube", "Light", "Camera"], "max_default_named": 0, "max_without_material": 0,
         "max_unused": 0, "label": "tidy file: no default leftovers, named, materials"},
        {"kind": "bbox", "target": ROAD, "size": [[39.0, 41.0], [6.6, 7.4], None], "max": [None, None, 0.0],
         "tolerance": 0.05, "label": "a 40 x 7 m road, its top at z = 0"},
        {"kind": "bbox", "target": PAVEMENT, "min": [-20.0, -6.5, None], "max": [20.0, 6.5, K], "tolerance": 0.15,
         "label": "pavements 3 m wide along both sides, tops at 0.15 m"},
        _down((0.37, 5.1), PAVEMENT, 0.1, 0.2, "the north pavement's top at 0.15 m"),
        _down((0.37, -5.1), PAVEMENT, 0.1, 0.2, "the south pavement's top at 0.15 m"),
        *buildings,
        {"kind": "bbox", "target": {"prefix": "Town Hall", "word": True}, "max": [None, None, K + town.TOWER_TIP],
         "tolerance": 0.4, "label": "the town hall's spire tip 20 m up, its highest point"},
        {"kind": "bbox", "target": SQUARE, "min": [-10.0, -16.5, None], "max": [10.0, -6.5, K], "tolerance": 0.15,
         "label": "the square from x = -10 to 10 and y = -16.5 to -6.5, top at 0.15 m"},
        {"kind": "bbox", "target": {"prefix": "Square Fountain", "word": True},
         "min": [fx - town.FOUNTAIN_RADIUS, fy - town.FOUNTAIN_RADIUS, None],
         "max": [fx + town.FOUNTAIN_RADIUS, fy + town.FOUNTAIN_RADIUS, None], "tolerance": 0.25,
         "label": "the fountain's basin 5 m across at (0, -11.5)"},
        {"kind": "bbox", "target": {"prefix": "Square Fountain", "word": True},
         "max": [None, None, K + town.FOUNTAIN_TOP],
         "tolerance": 0.3, "label": "the fountain's column 2.5 m tall, its highest point"},
        # Into each crown, off the trunk's axis: a tree stands there; the box gives the trees' height.
        *[_down((at[0] + 0.15, at[1] + 0.1), {"prefix": "Square Tree", "word": True}, K + 2.0,
                K + town.TREE_HEIGHT + 0.6, "a tree at (%g, %g)" % at) for at in town.TREES],
        {"kind": "bbox", "target": {"prefix": "Square Tree", "word": True}, "min": [None, None, K],
         "max": [None, None, K + town.TREE_HEIGHT], "tolerance": 0.5, "label": "the trees 6 m tall, on the paving"},
        {"kind": "bbox", "target": {"whole": "Square Kiosk"}, "min": [kx - kl / 2, ky - kd / 2, K],
         "max": [kx + kl / 2, ky + kd / 2, K + kh], "tolerance": 0.4,
         "label": "the kiosk 2.5 x 2 m, 3 m tall at (-8, -15)"},
        {"kind": "bbox", "target": {"prefix": "Street Lamp", "word": True},
         "min": [-lamp_x, -town.LAMP_Y, None], "max": [lamp_x, town.LAMP_Y, K + town.LAMP_HEIGHT], "tolerance": 0.6,
         "label": "lamp posts 4.5 m tall along both pavements, x = -15 to 15, y = +/-4"},
        {"kind": "bbox", "target": {"prefix": "Terrace Table", "word": True},
         "min": [terrace[0] - radius, terrace[1] - radius, None],
         "max": [terrace[2] + radius, terrace[1] + radius, K + town.CAFE_TABLE_TOP], "tolerance": 0.12,
         "label": "three terrace tables 0.7 m across at y = 4.8, x = -5 to -1, tops 0.75 m"},
        {"kind": "bbox", "target": {"whole": "Parked Hatchback"}, "min": [hx - hl / 2, hy - hw / 2, 0.0],
         "max": [hx + hl / 2, hy + hw / 2, hh], "tolerance": 0.3,
         "label": "the hatchback 4 x 1.75 x 1.5 m at (-9, 2.4)"},
        {"kind": "bbox", "target": {"whole": "Parked Van"}, "min": [vx - vl / 2, vy - vw / 2, 0.0],
         "max": [vx + vl / 2, vy + vw / 2, vh], "tolerance": 0.3, "label": "the van 5.2 x 2 x 2.4 m at (13, 2.3)"},
        {"kind": "bbox", "target": {"whole": "Bus Shelter"}, "min": [sx - sl / 2, sy - sd / 2, K],
         "max": [sx + sl / 2, sy + sd / 2, K + sh], "tolerance": 0.3,
         "label": "the bus shelter, roof 2.6 m up at (14, -5.3)"},
        {"kind": "bbox", "target": {"whole": "Phone Box"}, "min": [px - pw / 2, py - pd / 2, K],
         "max": [px + pw / 2, py + pd / 2, K + ph], "tolerance": 0.25, "label": "the phone box 1 m square, 2.5 m tall"},
        {"kind": "camera", "target": {"name": "Hero Camera"}, "active": True, "location": list(HERO["location"]),
         "radius": 0.4, "look_at": list(HERO["target"]), "min_dot": 0.99, "lens": [28.0, 32.0],
         "label": "Hero Camera placed, aimed and active"},
    ]


_STREET = ("The street's layout: the road, pavements and crossing, eight buildings and the square where the brief "
           "puts them.")
GRADE_CLASSES = {
    "Bakery and bookshop": {"views": ["closeup_west", "hero"],
                            "what": "A three-storey cream bakery with a pitched slate roof, chimneys, a shop window and "
                                    "a striped awning; a two-storey brick bookshop with a green shopfront, a bay window "
                                    "and a parapet."},
    "Cafe": {"views": ["closeup_cafe", "hero"],
             "what": "A three-storey pale blue cafe with a mansard roof and dormers, a striped awning, and its terrace "
                     "of three round tables with two chairs each."},
    "Pharmacy, flower shop and bank": {"views": ["closeup_east", "hero"],
                                       "what": "A white gabled pharmacy with a green cross sign; a pink flower shop with "
                                               "an awning and buckets of flowers; a pale stone bank with columns, a "
                                               "cornice and a balustrade."},
    "Hotel": {"views": ["closeup_hotel", "front"],
              "what": "A four-storey sandstone hotel with an entrance canopy, balconies with railings and flower "
                      "boxes, a sign and a flat roof; a red phone box."},
    "Town hall": {"views": ["closeup_town_hall", "front"],
                  "what": "A two-storey brick town hall with a square clock tower, a clock face and a pointed spire; "
                          "a bus shelter open toward the road."},
    "Square": {"views": ["closeup_square", "top"],
               "what": "Stone paving, a round fountain with water and a central column, four trees, four wooden "
                       "benches facing the fountain, three market stalls with striped canopies selling fruit, "
                       "vegetables and cheese, and a green newspaper kiosk."},
    "Street furniture and cars": {"views": ["closeup_street", "hero"],
                                  "what": "Eight matching lamp posts, a red post box, bins, traffic lights at a zebra "
                                          "crossing, road markings, a blue hatchback and a white delivery van."},
    "Street": {"views": ["top", "hero"], "what": "A sunny small-town high street.", "layout": _STREET},
}
CHECKS = checks()
TURNS = [{"id": "brief", "title": "The high street", "references": ["hero", "top"], "prompt": PROMPT, "checks": CHECKS}]


def build_turn(bpy, turn_id):
    if turn_id != "brief":
        raise KeyError(turn_id)
    oneshot_kit.clear_default_scene(bpy)
    town.build_high_street(bpy)
    scene = bpy.context.scene
    kit.sun_object(bpy, "Afternoon Sun", 38.0, -135.0, (1.0, 0.9, 0.76), 3.5, angle_deg=2.0)
    scene.world = kit.colour_world(bpy, "Clear Sky", (0.52, 0.66, 0.85), strength=0.9)
    scene.camera = kit.camera_object(bpy, "Hero Camera", HERO["location"], HERO["target"], HERO["lens"])
    bpy.context.view_layer.update()


def build(bpy, upto=None):
    build_turn(bpy, "brief")
