# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot test ``oneshot-roadster``: a small car with four matching wheels from one message (held out for
parallel agents).

Held out from the chess benchmark (``scenario_oneshot_chess``): a vehicle splits into a body, a wheel repeated four
times and its details; the brief says nothing about how. The stand-in is ``parallel_kits.build_roadster``. Rays at
each wheel's stated place find a wheel there (from the side, at hub height), the roof's height from above, and the
car's overall size; every part's name starts with 'Car', however it is made or copied.

Axes: the car is centred on the origin, its front toward +X, standing on z = 0.
"""

import oneshot_kit
import parallel_kits as car
import scenario_build_kit as kit

ID = "oneshot_roadster"
TITLE = "A small red car"
FRAME = 1
FINAL_VIEW = "hero"
FINAL_STILL = {"name": "roadster-final.png", "size": (1920, 1080)}
GROUND_TRUTH = "stand-in"
LIMITS = {"wall_s": 3600.0, "job_wait_s": 0.0, "cost_usd": 40.0}
APPROVABLE = ()
JOB_WAIT_S = 0.0

HERO = {"location": (5.6, -5.2, 2.2), "target": (0.0, 0.0, 0.6), "lens": 35.0}
CAMERAS = {
    "hero": dict(HERO, role="reference", label="Hero (the scene camera)"),
    "side": {"location": (0.0, -12.0, 0.7), "target": (0.0, 0.0, 0.7), "ortho_scale": 5.0, "role": "reference",
             "label": "Side elevation (orthographic, looking +Y)"},
    "front": {"location": (12.0, 0.0, 0.7), "target": (0.0, 0.0, 0.7), "ortho_scale": 2.6, "role": "reference",
              "label": "Front elevation (orthographic, looking -X)"},
    "top": {"location": (0.0, 0.0, 12.0), "target": (0.0, 0.0, 0.0), "ortho_scale": 5.0, "role": "reference",
            "label": "Plan (orthographic, +Y up)"},
    "closeup_wheel": {"location": (2.4, -2.4, 0.6), "target": (1.3, -0.78, 0.33), "lens": 40.0, "role": "closeup",
                      "label": "Close-up: the front left wheel"},
    "closeup_front": {"location": (4.6, -1.2, 1.0), "target": (2.1, 0.0, 0.65), "lens": 35.0, "role": "closeup",
                      "label": "Close-up: the front and its headlights"},
}
CAR, WHEEL = {"prefix": "Car", "word": True}, {"prefix": "Car Wheel", "word": True}
SUBJECT = (CAR,)

PROMPT = (
    "A small, rounded red two-door car standing on a plain grey ground. Build it. The two attached pictures show "
    "the car I have in mind: the hero shot and a side elevation. Take the proportions and the character from "
    "them and design the details yourself; where they and this brief disagree, the brief wins.\n\n"
    "Conventions. Metres, Z up. I find things by name: every part of the car has a name starting with 'Car' "
    "(for example 'Car Body'), and anything else you add gets a meaningful name of its own. Keep the file tidy: "
    "nothing from the default scene remains, no unused leftovers, and a material on everything that shows.\n\n"
    "The car. Centred on the origin with its front toward +X, standing on z = 0: 4.2 m long, 1.8 m wide and "
    "1.32 m to the top of its roof. Glossy, slightly metallic red paint; a glass cabin or windows; two round "
    "chrome headlights at the front. Four matching wheels, their names starting with 'Car Wheel', with black "
    "tyres and chrome hubs, 0.66 m across and 0.22 m wide, their centres at x = +1.3 and -1.3 and y = +0.78 and -0.78, 0.33 m up, touching the ground.\n\n"
    "Light and camera. A clear, slightly warm sun and a camera named 'Hero Camera' at (5.6, -5.2, 2.2) looking "
    "at (0, 0, 0.6) with a 35 mm lens, as the scene's camera.\n\n"
    "Before you call it done, look at the car from the hero camera next to the pictures and fix what is wrong.")


def checks():
    # From outside the car toward its middle at hub height: the first wheel surface is its outer face, about
    # half a wheel's width outside its centre (a hub cap or a tyre bulge a little further out).
    wheels = [{"kind": "probe", "origin": [x, 3.0 * side, car.WHEEL_RADIUS], "direction": [0, -side, 0],
               "target": WHEEL, "axis": "y", "distance": 4.0, "label": "a wheel at (%g, %g), 0.33 m up" % (x, y),
               "range": sorted([side * (abs(y) - 0.1), side * (abs(y) + car.WHEEL_WIDTH / 2 + 0.12)])}
              for x, y in car.WHEELS for side in [1 if y > 0 else -1]]
    return [
        {"kind": "hygiene", "absent": ["Cube", "Light", "Camera"], "max_default_named": 0, "max_without_material": 0,
         "max_unused": 0, "label": "tidy file: no default leftovers, named, materials"},
        {"kind": "bbox", "target": CAR, "size": [[3.9, 4.5], [1.65, 1.95], [1.2, 1.45]], "min": [None, None, 0.0],
         "tolerance": 0.06, "label": "a car 4.2 x 1.8 x 1.32 m standing on z = 0"},
        *wheels,
        {"kind": "probe", "origin": [-0.3, 0.0, 4.0], "direction": [0, 0, -1], "target": CAR, "axis": "z",
         "range": [1.15, 1.45], "distance": 5.0, "label": "the roof 1.32 m up"},
        {"kind": "probe", "origin": [0.0, -0.78, 4.0], "direction": [0, 0, -1], "target": CAR, "axis": "z",
         "range": [0.5, 1.45], "distance": 5.0, "label": "the body between the wheels"},
        {"kind": "surface", "target": CAR, "color": ["warm"], "label": "red paint"},
        {"kind": "camera", "target": {"name": "Hero Camera"}, "active": True, "location": list(HERO["location"]),
         "radius": 0.3, "look_at": list(HERO["target"]), "min_dot": 0.99, "lens": [33.0, 37.0],
         "label": "Hero Camera placed, aimed and active"},
    ]


GRADE_CLASSES = {
    "Body": {"views": ["hero", "side", "front"],
             "what": "The car's body: a small, rounded two-door car in glossy, slightly metallic red, with a glass "
                     "cabin or windows, 4.2 m long."},
    "Wheels": {"views": ["closeup_wheel", "side"], "what": "Four matching wheels: black tyres and chrome hubs."},
    "Front and headlights": {"views": ["closeup_front", "front"], "what": "The car's front with two round chrome "
                                                                          "headlights."},
}
CHECKS = checks()
TURNS = [{"id": "brief", "title": "The car", "references": ["hero", "side"], "prompt": PROMPT, "checks": CHECKS}]


def build_turn(bpy, turn_id):
    if turn_id != "brief":
        raise KeyError(turn_id)
    oneshot_kit.clear_default_scene(bpy)
    car.build_roadster(bpy)
    ground = kit.mesh_object(bpy, "Ground", *kit.box_geometry((30.0, 30.0, 0.1), (0.0, 0.0, -0.05)))
    kit.assign(ground, kit.principled(bpy, "Asphalt", (0.25, 0.25, 0.26), roughness=0.9))
    scene = bpy.context.scene
    kit.sun_object(bpy, "Sun", 40.0, -130.0, (1.0, 0.93, 0.82), 3.5, angle_deg=2.0)
    scene.world = kit.colour_world(bpy, "Sky", (0.6, 0.68, 0.78), strength=0.7)
    scene.camera = kit.camera_object(bpy, "Hero Camera", HERO["location"], HERO["target"], HERO["lens"])
    bpy.context.view_layer.update()


def build(bpy, upto=None):
    build_turn(bpy, "brief")
