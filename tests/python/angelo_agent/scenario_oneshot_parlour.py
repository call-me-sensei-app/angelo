# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot test ``oneshot-parlour``: a furnished living room from one message (held out for parallel agents).

Held out from the chess benchmark (``scenario_oneshot_chess``): the same single-chat and parallel arms, to show a
gain is not chess's. A room's furniture is split by unique design (a sofa, an armchair repeated twice, a coffee
table, a dining table, a dining chair repeated four times, a lamp, a bookshelf); the brief says nothing about how.
The stand-in is ``parallel_kits.build_parlour``. The checks name only the floor: rays straight down at each piece's
stated place find its seat or top at the stated height, whatever it is named or however it was copied.

Axes: the floor is centred on the origin at z = 0; the back wall runs along +Y, the left wall along -X.
"""

import oneshot_kit
import parallel_kits as room
import scenario_build_kit as kit

ID = "oneshot_parlour"
TITLE = "A furnished living room"
FRAME = 1
FINAL_VIEW = "hero"
FINAL_STILL = {"name": "parlour-final.png", "size": (1920, 1080)}
GROUND_TRUTH = "stand-in"
LIMITS = {"wall_s": 3600.0, "job_wait_s": 0.0, "cost_usd": 40.0}
APPROVABLE = ()
JOB_WAIT_S = 0.0

HERO = {"location": (3.6, -4.4, 2.4), "target": (0.0, 0.3, 0.6), "lens": 28.0}
CAMERAS = {
    "hero": dict(HERO, role="reference", label="Hero (the scene camera)"),
    "top": {"location": (0.0, 0.0, 12.0), "target": (0.0, 0.0, 0.0), "ortho_scale": 9.6, "role": "reference",
            "label": "Plan (orthographic, +Y up)"},
    "front": {"location": (0.0, -12.0, 1.3), "target": (0.0, 0.0, 1.3), "ortho_scale": 6.6, "role": "reference",
              "label": "Front elevation (orthographic, looking +Y)"},
    "side": {"location": (12.0, 0.0, 1.3), "target": (0.0, 0.0, 1.3), "ortho_scale": 5.6, "role": "reference",
             "label": "Side elevation (orthographic, looking -X)"},
    "closeup_sofa": {"location": (0.9, -0.6, 1.1), "target": (0.0, 1.8, 0.5), "lens": 35.0, "role": "closeup",
                     "label": "Close-up: the sofa, coffee table and armchairs"},
    "closeup_dining": {"location": (2.0, -2.9, 1.5), "target": (0.0, -1.35, 0.6), "lens": 35.0, "role": "closeup",
                       "label": "Close-up: the dining table and its chairs"},
    "closeup_corner": {"location": (0.4, 0.2, 1.4), "target": (-2.3, 2.2, 1.0), "lens": 35.0, "role": "closeup",
                       "label": "Close-up: the bookshelf and the lamp"},
}
FLOOR = {"whole": "Room Floor"}   # The whole floor: its parts and children too.
SUBJECT = ({"prefix": ""},)
FURNITURE = {"prefix": "", "except": [FLOOR, {"prefix": "Room Wall"}]}

PROMPT = (
    "A furnished living room with a dining corner. Build it. The two attached pictures show the room I have in "
    "mind: the hero shot and a plan from above (+Y up). Take the layout and the mood from them and design the "
    "furniture yourself; where they and this brief disagree, the brief wins.\n\n"
    "Conventions. Metres, Z up. I find the floor and walls by name, so name them exactly as written here and "
    "give everything else a meaningful name of its own. Keep the file tidy: nothing from the default scene "
    "remains, no unused leftovers, and a material on everything that shows. No ceiling.\n\n"
    "The room. 'Room Floor', oak boards, 6 m along X by 5 m along Y, centred on the origin with its top at "
    "z = 0. Two plaster walls 2.7 m high whose names start with 'Room Wall': one along the back (+Y) edge and "
    "one along the left (-X) edge.\n\n"
    "The furniture, each standing on the floor where given (x, y), seat or top heights as stated: a three-seat "
    "fabric sofa centred at (0, 1.85), 2 m wide, its back to the back wall, seat 0.45 m high; a wooden coffee "
    "table in front of it at (0, 0.85), top 0.42 m; two matching upholstered armchairs at (-1.55, 0.85) and "
    "(1.55, 0.85), seats 0.45 m, facing each other across the coffee table; a wooden dining table at "
    "(0, -1.35), 1.6 m by 0.9 m, top 0.75 m; four matching dining chairs, two each side of it, at "
    "(-0.45, -0.7), (0.45, -0.7), (-0.45, -2.0) and (0.45, -2.0), seats 0.45 m, facing the table; a floor lamp "
    "with a fabric shade at (2.55, 2.05), 1.6 m tall; and a wooden bookshelf against the back wall at "
    "(-2.35, 2.25), 1 m wide and 1.8 m tall, with shelves.\n\n"
    "Light and camera. Soft daylight from the room's open front, and a camera named 'Hero Camera' at "
    "(3.6, -4.4, 2.4) looking at (0, 0.3, 0.6) with a 28 mm lens, as the scene's camera.\n\n"
    "Before you call it done, look at the room from the hero camera next to the pictures and fix what is wrong.")


def _down(at, low, high, label):
    return {"kind": "probe", "origin": [at[0], at[1], 4.0], "direction": [0, 0, -1], "target": FURNITURE,
            "axis": "z", "range": [low, high], "distance": 5.0, "label": label}


def checks():
    return [
        {"kind": "hygiene", "absent": ["Cube", "Light", "Camera"], "max_default_named": 0, "max_without_material": 0,
         "max_unused": 0, "label": "tidy file: no default leftovers, named, materials"},
        {"kind": "bbox", "target": FLOOR, "size": [[5.6, 6.4], [4.6, 5.4], None], "max": [None, None, 0.0],
         "tolerance": 0.05, "label": "a 6 x 5 m floor, its top at z = 0"},
        {"kind": "bbox", "target": {"prefix": "Room Wall"}, "max": [None, 2.5, 2.7], "min": [-3.0, None, None],
         "tolerance": 0.25, "label": "walls along the back and left edges, 2.7 m high"},
        _down(room.SOFA_AT, 0.38, 0.6, "the sofa seat 0.45 m at (0, 1.85)"),
        _down(room.COFFEE_AT, 0.36, 0.48, "the coffee table top 0.42 m at (0, 0.85)"),
        *[_down(at, 0.38, 0.6, "an armchair seat 0.45 m at (%g, %g)" % at) for at in room.ARMCHAIRS],
        _down(room.DINING_AT, 0.7, 0.8, "the dining table top 0.75 m at (0, -1.35)"),
        *[_down(at, 0.4, 0.55, "a dining chair seat 0.45 m at (%g, %g)" % at) for at in room.DINING_CHAIRS],
        _down(room.LAMP_AT, 1.4, 1.8, "the floor lamp 1.6 m tall at (2.55, 2.05)"),
        _down(room.SHELF_AT, 1.65, 1.95, "the bookshelf 1.8 m tall at (-2.35, 2.25)"),
        {"kind": "probe", "origin": [0.0, -0.05, 4.0], "direction": [0, 0, -1], "target": FURNITURE, "expect": "miss",
         "distance": 4.01, "label": "the floor clear between the sitting and dining areas"},
        {"kind": "camera", "target": {"name": "Hero Camera"}, "active": True, "location": list(HERO["location"]),
         "radius": 0.3, "look_at": list(HERO["target"]), "min_dot": 0.99, "lens": [26.0, 30.0],
         "label": "Hero Camera placed, aimed and active"},
    ]


GRADE_CLASSES = {
    "Sofa and armchairs": {"views": ["closeup_sofa", "hero"],
                           "what": "A three-seat fabric sofa and two matching upholstered armchairs facing each other."},
    "Coffee and dining tables": {"views": ["closeup_dining", "closeup_sofa"],
                                 "what": "A wooden coffee table and a 1.6 x 0.9 m wooden dining table."},
    "Dining chairs": {"views": ["closeup_dining", "top"], "what": "Four matching wooden dining chairs."},
    "Bookshelf and lamp": {"views": ["closeup_corner", "hero"],
                           "what": "A wooden bookshelf with shelves, 1.8 m, and a floor lamp with a fabric shade."},
    "Room": {"views": ["top", "hero"], "what": "The oak floor and two plaster walls of a living room.",
             "layout": "Sofa at the back wall, coffee table before it, armchairs either side facing each other, the "
                       "dining table with two chairs on each side facing it, lamp and bookshelf in the back corners."},
}
CHECKS = checks()
TURNS = [{"id": "brief", "title": "The living room", "references": ["hero", "top"], "prompt": PROMPT,
          "checks": CHECKS}]


def build_turn(bpy, turn_id):
    if turn_id != "brief":
        raise KeyError(turn_id)
    oneshot_kit.clear_default_scene(bpy)
    room.build_parlour(bpy)
    scene = bpy.context.scene
    kit.sun_object(bpy, "Daylight", 35.0, -60.0, (1.0, 0.95, 0.88), 3.0, angle_deg=6.0)
    scene.world = kit.colour_world(bpy, "Daylight Sky", (0.55, 0.62, 0.7), strength=0.8)
    scene.camera = kit.camera_object(bpy, "Hero Camera", HERO["location"], HERO["target"], HERO["lens"])
    bpy.context.view_layer.update()


def build(bpy, upto=None):
    build_turn(bpy, "brief")
