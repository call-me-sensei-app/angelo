# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot test ``oneshot-town-centre``: a small town centre from one message (long candidate 3).

LONG-TEST-SELECTION rule 3: candidate 2 (``scenario_oneshot_high_street``) took one Sol chat 358 s in its pilot, under
the 20 minutes a long test needs, so it is enlarged with more of the same kind of content as a new candidate: the
high street crossed by two side streets, 23 distinct buildings (each its own walls, roof, shopfront and sign), a
church with a tower, a covered market hall with stalls, a square, a park with a bandstand, street furniture and five
distinct vehicles. Candidates 1 and 2 are unchanged. The brief says nothing about how to build or split the work.

The stand-in is ``long_kit_town.build_town_centre``; the brief's building sentences are written from its
``BUILDINGS`` table, so the brief, the stand-in and the checks share one source of every number. The checks name
only what the brief names: each building's parts on its plot and front, a ray down its roof at the stated height,
the towers' tips and the fountain's top as box heights, the named props and vehicles as boxes, the park's trees by
rays into their crowns. Every ray is aimed at a face with margin; ``long_probe_check`` proves it on the stand-in.

Axes: roads' tops at z = 0, pavements', the square's and the park's at z = 0.15; north is +Y.
"""

import long_kit_town as town
import oneshot_kit
import scenario_build_kit as kit

ID = "oneshot_town_centre"
TITLE = "A small town centre"
FRAME = 1
FINAL_VIEW = "hero"
FINAL_STILL = {"name": "town-centre-final.png", "size": (1920, 1080)}
GROUND_TRUTH = "stand-in"
# The calibration pilots' cap (BENCH-PREREG Amendment I7): a pilot that reaches it counts as ">= 7,200 s", never a
# measured time. Paired trials set their own with --task-timeout (Amendment I3: twice the qualifying pilot's time).
LIMITS = {"wall_s": 7200.0, "job_wait_s": 0.0, "cost_usd": 80.0}
APPROVABLE = ()
JOB_WAIT_S = 0.0

HERO = {"location": (24.0, -62.0, 70.0), "target": (0.0, 0.0, 0.0), "lens": 24.0}


def _look(location, target, label, lens=28.0):
    return {"location": location, "target": target, "lens": lens, "role": "closeup", "label": "Close-up: " + label}


CAMERAS = {
    "hero": dict(HERO, role="reference", label="Hero (the scene camera)"),
    "top": {"location": (0.0, 0.0, 90.0), "target": (0.0, 0.0, 0.0), "ortho_scale": 140.0, "role": "reference",
            "label": "Plan (orthographic, +Y up)"},
    "front": {"location": (0.0, -90.0, 8.0), "target": (0.0, 0.0, 8.0), "ortho_scale": 90.0, "role": "reference",
              "label": "From the south (orthographic, looking +Y)"},
    "side": {"location": (90.0, 0.0, 8.0), "target": (0.0, 0.0, 8.0), "ortho_scale": 78.0, "role": "reference",
             "label": "From the east (orthographic, looking -X)"},
    "closeup_high_street": _look((0.0, -3.0, 5.0), (0.0, 11.0, 5.0), "the high street's north side", 24.0),
    "closeup_square": _look((0.0, 3.0, 9.0), (0.0, -12.0, 3.0), "the square, the hotel and the town hall", 24.0),
    "closeup_church_lane": _look((-24.0, -2.0, 7.0), (-24.0, 22.0, 4.0), "Church Lane, the church and the library"),
    "closeup_church": _look((-20.0, 18.0, 8.0), (-35.0, 24.0, 9.0), "the church and its tower"),
    "closeup_market_street": _look((24.0, -2.0, 7.0), (24.0, 22.0, 4.0), "Market Street, the market hall and shops"),
    "closeup_market_hall": _look((20.0, 16.0, 8.0), (35.0, 23.0, 4.0), "the market hall and its stalls"),
    "closeup_south": _look((24.0, 2.0, 7.0), (28.0, -24.0, 3.0), "the fire station, the garage and the apartments"),
    "closeup_park": _look((-24.0, -18.0, 8.0), (-10.0, -28.0, 1.5), "the park, its bandstand and trees, the shops"),
    "closeup_west_end": _look((-30.0, 2.0, 5.0), (-36.0, -8.0, 4.0), "the pub, the hardware store and the butcher"),
}
# Boxes measure the whole named thing (scenario_checks ``whole``: its parts and its children too); the pavement's
# rays find it by its own name.
ROADS = {name: {"whole": name} for name in ("High Street Road", "Church Lane Road", "Market Street Road")}
PAVEMENT, SQUARE, LAWN = {"name": "Town Pavement"}, {"whole": "Square Paving"}, {"whole": "Park Lawn"}
SUBJECT = ({"prefix": ""},)
K = town.KERB
WORDS = {1: "one storey", 2: "two storeys", 3: "three storeys", 4: "four storeys", 5: "five storeys"}


def _g(value):
    return "%g" % value


def _roof(name):
    roof, height, eaves = town.BUILDINGS[name][5:8]
    if roof == "ridge_x":
        return "a roof pitched along the street, eaves at %s m and ridge at %s m" % (_g(eaves), _g(height))
    if roof == "ridge_y":
        return "its gable to the street, eaves at %s m and the ridge, running back from the street over the " \
               "middle of its front, at %s m" % (_g(eaves), _g(height))
    if roof == "mansard":
        return "a mansard roof from eaves at %s m to a flat top at %s m" % (_g(eaves), _g(height))
    return "a flat roof at %s m behind a parapet" % _g(height)


def _sentence(name):
    x0, x1, y0, y1, facing, _roof_kind, _h, _e, storeys, _wall, look = town.BUILDINGS[name]
    front = {"S": "y = %s" % _g(y0), "N": "y = %s" % _g(y1), "E": "x = %s" % _g(x1), "W": "x = %s" % _g(x0)}[facing]
    return "'%s', from x = %s to %s and y = %s to %s with its front on %s: %s of %s, %s" % (
        name, _g(x0), _g(x1), _g(y0), _g(y1), front, WORDS[storeys], look, _roof(name))


def _row(names):
    return "; ".join(_sentence(name) for name in names) + "."


HIGH_NORTH = ("Pub", "Bakery", "Bookshop", "Cafe", "Pharmacy", "Bank", "Cinema")
HIGH_SOUTH = ("Hardware Store", "Hotel", "Town Hall", "Post Office")
CHURCH_LANE = ("Parish Church", "Library", "Bicycle Shop", "Butcher", "Tailor", "School")
MARKET_STREET = ("Market Hall", "Toy Shop", "Ice Cream Parlour", "Fire Station", "Garage", "Apartments")
assert sorted(HIGH_NORTH + HIGH_SOUTH + CHURCH_LANE + MARKET_STREET) == sorted(town.BUILDINGS)


def _vehicle_text(name, look):
    (x, y), (length, width, height), _along_y = town.VEHICLES[name]
    return "'%s', %s %s m long, %s m wide and %s m tall, centred at (%s, %s)" % (
        name, look, _g(length), _g(width), _g(height), _g(x), _g(y))


PROMPT = (
    "A small town centre on a sunny afternoon: a high street crossed by two side streets, twenty-three buildings, "
    "each its own design, a church, a covered market hall, a square, a park, and everything streets have. Build "
    "it. The two attached pictures show the town I have in mind: the hero shot and a plan from above (+Y is "
    "north). Take the layout and the mood from them and design every building and prop yourself, each with real "
    "detail; where they and this brief disagree, the brief wins.\n\n"
    "Conventions. Metres, Z up. I find things by name, so name the roads, pavements, square, lawn and props "
    "exactly as written here, give every part of a building a name that starts with that building's name as "
    "written below (for example 'Bakery Roof'), and give anything else a meaningful name of its own. Keep the "
    "file tidy: nothing from the default scene remains, no unused leftovers, and a material on everything that "
    "shows. Heights of buildings and props are measured from the pavement, square or lawn they stand on.\n\n"
    "The streets. 'High Street Road', asphalt with white markings, from x = -42 to 42 and y = -3.5 to 3.5. "
    "Two side streets cross it, each 7 m wide and running from y = -36 to 36: 'Church Lane Road' centred on "
    "x = -24 and 'Market Street Road' centred on x = 24. All three road tops are at z = 0. 'Town Pavement', stone "
    "paving 3 m wide along both sides of every road (the high street's from x = -42 to 42), its top at z = 0.15 "
    "with kerbs at the roads' edges. Zebra crossings over the high street at x = -12 and x = 12 and over Market "
    "Street at y = 24.\n\n"
    "The buildings. Every awning, sign, balcony, canopy or marquee reaches at most 1.2 m over the pavement and no "
    "part of a building reaches further; every building has windows on every storey, a front door and its own "
    "sign, and every shop window holds a display of what the shop sells. Along the north side of the high street, "
    "west to east: " + _row(HIGH_NORTH) + " Along its south side: " + _row(HIGH_SOUTH) + " The town hall's "
    "clock tower is 4 m square, centred at (12.75, -9), with a clock face toward the street and a pointed spire "
    "whose tip, 20 m up, is the town hall's highest point.\n\n"
    "Along Church Lane: " + _row(CHURCH_LANE) + " The parish church's tower is 5 m square, centred at (-33, 24) at the "
    "front of the church, with a belfry and a pointed spire whose tip, 30 m up, is the parish church's highest point.\n\n"
    "Along Market Street: " + _row(MARKET_STREET) + " Inside the market hall, at least six stalls, each its own "
    "design, selling fruit, vegetables, fish, cheese, bread and flowers.\n\n"
    "The square, between the hotel and the town hall from x = -8 to 8 and y = -16.5 to -6.5: 'Square Paving', its "
    "top at z = 0.15; 'Square Fountain', a round stone basin 4 m across centred at (0, -11.5) with water in it "
    "and a central column 2.5 m tall, its highest point; benches; and a newspaper kiosk.\n\n"
    "The park, from x = -17.5 to -2 and y = -36 to -20: 'Park Lawn', its top at z = 0.15, with paths; six trees "
    "whose names start with 'Park Tree', 7 m tall, at (-15, -23), (-5, -23), (-15, -33), (-5, -33), (-10, -34) and "
    "(-4.5, -28); 'Park Bandstand', an open octagonal bandstand 5 m across centred at (-10, -28) with a roof whose "
    "top is 5 m up; a small playground with swings and a slide; benches and a low railing round it.\n\n"
    "On the streets. Ten matching lamp posts 4.5 m tall whose names start with 'Street Lamp', on both high-street "
    "pavements at y = 4 and y = -4, at x = -36, -12, 0, 12 and 36; 'Bus Shelter', 3 m along X by 1.4 m, roof "
    "2.6 m up, centred at (-6, -5.3), open toward the road; 'Phone Box', a red telephone box 1 m square and 2.5 m "
    "tall, centred at (-20, -4.6); post boxes, bins, bicycle racks and traffic lights at the crossings. Five "
    "vehicles on the roads: " + _vehicle_text("Parked Hatchback", "a blue hatchback") + "; "
    + _vehicle_text("Parked Van", "a white delivery van") + "; " + _vehicle_text("Town Bus", "a red single-deck bus")
    + " at the shelter; " + _vehicle_text("Taxi", "a yellow taxi") + ", along Church Lane; and "
    + _vehicle_text("Fire Engine", "a red fire engine") + ", along Market Street.\n\n"
    "Light and camera. A warm afternoon sun from the south-west with soft shadows and a clear sky, and a camera "
    "named 'Hero Camera' at (24, -62, 70) looking at (0, 0, 0) with a 24 mm lens, as the scene's camera.\n\n"
    "Before you call it done, look at the town from the hero camera and street by street next to the pictures, "
    "and fix what is wrong.")


def _down(at, target, low, high, label, top=60.0):
    """A ray straight down at ``at``, aimed at a face with margin (never a symmetry axis, a pole, a ridge or an edge;
    ``long_probe_check`` proves each still passes moved 5 cm in eight directions on the stand-in)."""
    return {"kind": "probe", "origin": [round(at[0], 3), round(at[1], 3), top], "direction": [0, 0, -1],
            "target": target, "axis": "z", "range": [round(low, 3), round(high, 3)], "distance": top + 1.0,
            "label": label}


# Roof rays land 0.7 m along the front from its middle and 0.6 m behind the middle of the depth: off a ridge, on the
# slope where the brief's eaves and ridge give the height; on the flat top of a mansard or flat roof.
ROOF_OFFSET = (0.7, 0.6)
# Rays that would land on a tower are moved to this local point instead (well clear of the tower's stated square).
ROOF_CLEAR_OF_TOWER = {"Town Hall": (-3.0, 7.5), "Parish Church": (4.0, 7.0)}


def _building(name):
    x0, x1, y0, y1, facing, roof, height, eaves = town.BUILDINGS[name][:8]
    target = {"prefix": name, "word": True}
    reach = town.AWNING_REACH / 2
    lo, hi = [x0, y0, None], [x1, y1, None]
    axis, side = {"S": (1, "min"), "N": (1, "max"), "E": (0, "max"), "W": (0, "min")}[facing]
    (lo if side == "min" else hi)[axis] = None
    plot = {"kind": "bbox", "target": target, "min": lo, "max": hi, "tolerance": 0.5,
            "label": "%s on its plot, x = %s to %s, y = %s to %s" % (name, _g(x0), _g(x1), _g(y0), _g(y1))}
    front = {"S": y0, "N": y1, "E": x1, "W": x0}[facing]
    toward = -1 if facing in "SW" else 1   # Toward the street, on the front's axis.
    bound = [None, None, None]
    bound[axis] = front + toward * reach
    face = {"kind": "bbox", "target": target, "min" if side == "min" else "max": bound, "tolerance": reach + 0.05,
            "label": "%s's front on %s = %s (at most 1.2 m over the pavement)" % (name, "xy"[axis], _g(front))}
    _origin, _facing, width, depth = town.frame(name)
    local = ROOF_CLEAR_OF_TOWER.get(name, (ROOF_OFFSET[0], depth / 2 + ROOF_OFFSET[1]))
    expected = height
    if roof == "ridge_x":
        expected = height - abs(local[1] - depth / 2) * (height - eaves) / (depth / 2)
    elif roof == "ridge_y":
        expected = height - abs(local[0]) * (height - eaves) / (width / 2)
    low, high = (0.35, 0.35) if roof.startswith("ridge") else (0.2, 0.3)
    roof_check = _down(town.to_world(name, local), target, K + expected - low, K + expected + high,
                       "%s's %s %s m up" % (name, "ridge" if roof.startswith("ridge") else "roof", _g(height)))
    return [plot, face, roof_check]


def _box(name, centre, size, tolerance, label, z0=K):
    """The whole named thing (its parts and children too), its footprint, standing on its ground (z0) and its stated
    height tall: a lone roof slab cannot pass."""
    (x, y), (sx, sy, sz) = centre, size
    return {"kind": "bbox", "target": {"whole": name}, "min": [x - sx / 2, y - sy / 2, z0],
            "max": [x + sx / 2, y + sy / 2, z0 + sz], "tolerance": tolerance, "label": label}


def checks():
    roads = [{"kind": "bbox", "target": ROADS["High Street Road"], "min": [-42.0, -3.5, None], "max": [42.0, 3.5, 0.0],
              "tolerance": 0.1, "label": "the high street's road, x = -42 to 42, y = -3.5 to 3.5, top at z = 0"}]
    roads += [{"kind": "bbox", "target": ROADS[name + " Road"], "min": [x - 3.5, -36.0, None], "max": [x + 3.5, 36.0, 0.0],
               "tolerance": 0.1, "label": "%s's road, 7 m wide at x = %s, y = -36 to 36, top at z = 0" % (name, _g(x))}
              for name, x in town.SIDE_STREETS.items()]
    pavements = [_down(at, PAVEMENT, 0.1, 0.2, "the pavement's top at 0.15 m at (%s, %s)" % at)
                 for at in ((2.37, 5.1), (-5.63, -5.1), (-19.1, 30.37), (28.9, -29.63))]
    vehicles = [_box(name, centre, size if not along_y else (size[1], size[0], size[2]), 0.3,
                     "%s, %s x %s x %s m at (%s, %s)" % (name, _g(size[0]), _g(size[1]), _g(size[2]), _g(centre[0]),
                                                         _g(centre[1])), z0=0.0)
                for name, (centre, size, along_y) in town.VEHICLES.items()]
    fx, fy = town.FOUNTAIN_AT
    bx, by = town.BANDSTAND_AT
    lamp_x = max(town.LAMPS_X)
    (sx, sy), shelter = town.SHELTER
    (px, py), phone = town.PHONE_BOX
    return [
        {"kind": "hygiene", "absent": ["Cube", "Light", "Camera"], "max_default_named": 0, "max_without_material": 0,
         "max_unused": 0, "label": "tidy file: no default leftovers, named, materials"},
        *roads, *pavements,
        *[check for name in town.BUILDINGS for check in _building(name)],
        {"kind": "bbox", "target": {"prefix": "Town Hall", "word": True}, "max": [None, None, K + town.TOWN_HALL_TIP],
         "tolerance": 0.4, "label": "the town hall's spire tip 20 m up, its highest point"},
        {"kind": "bbox", "target": {"prefix": "Parish Church", "word": True}, "max": [None, None, K + town.CHURCH_TIP],
         "tolerance": 0.5, "label": "the church's spire tip 30 m up, its highest point"},
        {"kind": "bbox", "target": SQUARE, "min": [-8.0, -16.5, None], "max": [8.0, -6.5, K], "tolerance": 0.15,
         "label": "the square from x = -8 to 8 and y = -16.5 to -6.5, top at 0.15 m"},
        {"kind": "bbox", "target": {"prefix": "Square Fountain", "word": True},
         "min": [fx - town.FOUNTAIN_RADIUS, fy - town.FOUNTAIN_RADIUS, None],
         "max": [fx + town.FOUNTAIN_RADIUS, fy + town.FOUNTAIN_RADIUS, K + town.FOUNTAIN_TOP], "tolerance": 0.3,
         "label": "the fountain 4 m across at (0, -11.5), its column 2.5 m tall"},
        {"kind": "bbox", "target": LAWN, "min": [-17.5, -36.0, None], "max": [-2.0, -20.0, K], "tolerance": 0.15,
         "label": "the park's lawn from x = -17.5 to -2 and y = -36 to -20, top at 0.15 m"},
        # Into each crown, off the trunk's axis: a tree stands there.
        *[_down((x + 0.15, y + 0.1), {"prefix": "Park Tree", "word": True}, K + 2.0, K + town.TREE_HEIGHT + 0.6,
                "a park tree at (%s, %s)" % (_g(x), _g(y))) for x, y in town.PARK_TREES],
        {"kind": "bbox", "target": {"prefix": "Park Tree", "word": True}, "min": [None, None, K],
         "max": [None, None, K + town.TREE_HEIGHT], "tolerance": 0.6, "label": "the park trees 7 m tall, on the lawn"},
        {"kind": "bbox", "target": {"prefix": "Park Bandstand", "word": True},
         "min": [bx - town.BANDSTAND_RADIUS, by - town.BANDSTAND_RADIUS, None],
         "max": [bx + town.BANDSTAND_RADIUS, by + town.BANDSTAND_RADIUS, K + town.BANDSTAND_TOP], "tolerance": 0.35,
         "label": "the bandstand 5 m across at (-10, -28), its roof top 5 m up"},
        {"kind": "bbox", "target": {"prefix": "Street Lamp", "word": True}, "min": [-lamp_x, -town.LAMP_Y, None],
         "max": [lamp_x, town.LAMP_Y, K + town.LAMP_HEIGHT], "tolerance": 0.6,
         "label": "lamp posts 4.5 m tall on both high-street pavements, x = -36 to 36, y = +/-4"},
        _box("Bus Shelter", (sx, sy), shelter, 0.3, "the bus shelter, roof 2.6 m up at (-6, -5.3)"),
        _box("Phone Box", (px, py), phone, 0.25, "the phone box 1 m square, 2.5 m tall at (-20, -4.6)"),
        *vehicles,
        {"kind": "camera", "target": {"name": "Hero Camera"}, "active": True, "location": list(HERO["location"]),
         "radius": 0.5, "look_at": list(HERO["target"]), "min_dot": 0.99, "lens": [22.0, 26.0],
         "label": "Hero Camera placed, aimed and active"},
    ]


def _group(names):
    return " ".join("%s: %s." % (name, town.BUILDINGS[name][10]) for name in names)


GRADE_CLASSES = {
    "High street, west": {"views": ["closeup_high_street", "closeup_west_end"],
                          "what": _group(("Pub", "Bakery", "Bookshop", "Cafe", "Hardware Store"))},
    "High street, east": {"views": ["closeup_high_street", "hero"],
                          "what": _group(("Pharmacy", "Bank", "Cinema", "Post Office"))},
    "Square, hotel and town hall": {"views": ["closeup_square", "front"],
                                    "what": _group(("Hotel", "Town Hall")) + " A clock tower with a spire; the square "
                                            "with a round fountain, benches and a newspaper kiosk; a bus shelter "
                                            "and a red phone box."},
    "Church": {"views": ["closeup_church", "closeup_church_lane"],
               "what": _group(("Parish Church",)) + " A 5 m square tower with a belfry and a pointed spire."},
    "Church Lane": {"views": ["closeup_church_lane", "closeup_west_end"],
                    "what": _group(("Library", "Bicycle Shop", "Butcher", "Tailor", "School"))},
    "Market hall": {"views": ["closeup_market_hall", "closeup_market_street"],
                    "what": _group(("Market Hall",)) + " At least six distinct stalls selling fruit, vegetables, fish, "
                                                       "cheese, bread and flowers."},
    "Market Street": {"views": ["closeup_market_street", "closeup_south"],
                      "what": _group(("Toy Shop", "Ice Cream Parlour", "Fire Station", "Garage", "Apartments"))},
    "Park": {"views": ["closeup_park", "top"],
             "what": "A lawn with paths, six trees, an open octagonal bandstand, a playground with swings and a "
                     "slide, benches and a low railing."},
    "Streets and vehicles": {"views": ["hero", "top"],
                             "what": "Roads with markings and three zebra crossings, stone pavements with kerbs, ten "
                                     "matching lamp posts, post boxes, bins, bicycle racks, traffic lights, and five "
                                     "distinct vehicles: a blue hatchback, a white van, a red bus, a yellow taxi and "
                                     "a red fire engine.",
                             "layout": "The high street along X crossed by Church Lane and Market Street, every "
                                       "building, the square and the park where the brief puts them."},
}
CHECKS = checks()
TURNS = [{"id": "brief", "title": "The town centre", "references": ["hero", "top"], "prompt": PROMPT,
          "checks": CHECKS}]


def build_turn(bpy, turn_id):
    if turn_id != "brief":
        raise KeyError(turn_id)
    oneshot_kit.clear_default_scene(bpy)
    town.build_town_centre(bpy)
    scene = bpy.context.scene
    kit.sun_object(bpy, "Afternoon Sun", 38.0, -135.0, (1.0, 0.9, 0.76), 3.5, angle_deg=2.0)
    scene.world = kit.colour_world(bpy, "Clear Sky", (0.52, 0.66, 0.85), strength=0.9)
    scene.camera = kit.camera_object(bpy, "Hero Camera", HERO["location"], HERO["target"], HERO["lens"], clip_end=1000.0)
    bpy.context.view_layer.update()


def build(bpy, upto=None):
    build_turn(bpy, "brief")
