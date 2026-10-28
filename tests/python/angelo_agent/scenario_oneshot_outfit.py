# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot test ``oneshot-outfit``: a stylised character dressed in separate clothing parts, from one message
(held out for parallel agents).

Held out from the chess benchmark (``scenario_oneshot_chess``): a character splits into its body and its clothing
parts (a shirt, trousers, a shoe repeated twice, a hat), each its own object so it can be changed on its own; the
brief says nothing about how to split the work. The stand-in is ``parallel_kits.build_outfit``. The checks read each
named part's place on the figure and the figure's height, however the parts are modelled or copied.

Axes: the character stands at the origin facing -Y, its feet on z = 0.
"""

import oneshot_kit
import parallel_kits as figure
import scenario_build_kit as kit

ID = "oneshot_outfit"
TITLE = "A character in separate clothing parts"
FRAME = 1
FINAL_VIEW = "hero"
FINAL_STILL = {"name": "outfit-final.png", "size": (1920, 1080)}
GROUND_TRUTH = "stand-in"
LIMITS = {"wall_s": 3600.0, "job_wait_s": 0.0, "cost_usd": 40.0}
APPROVABLE = ()
JOB_WAIT_S = 0.0

HERO = {"location": (2.0, -4.4, 1.2), "target": (0.0, 0.0, 0.9), "lens": 50.0}
CAMERAS = {
    "hero": dict(HERO, role="reference", label="Hero (the scene camera)"),
    "front": {"location": (0.0, -10.0, 0.9), "target": (0.0, 0.0, 0.9), "ortho_scale": 3.4, "role": "reference",
              "label": "Front elevation (orthographic, looking +Y)"},
    "side": {"location": (-10.0, 0.0, 0.9), "target": (0.0, 0.0, 0.9), "ortho_scale": 3.4, "role": "reference",
             "label": "Side elevation (orthographic, looking +X)"},
    "top": {"location": (0.0, 0.0, 10.0), "target": (0.0, 0.0, 0.0), "ortho_scale": 1.6, "role": "reference",
            "label": "Plan (orthographic, +Y up)"},
    "closeup_top": {"location": (0.6, -1.5, 1.6), "target": (0.0, 0.0, 1.35), "lens": 50.0, "role": "closeup",
                    "label": "Close-up: head, hat and shirt"},
    "closeup_legs": {"location": (0.6, -1.6, 0.5), "target": (0.0, 0.0, 0.4), "lens": 50.0, "role": "closeup",
                     "label": "Close-up: trousers and shoes"},
}
BODY, SHIRT, TROUSERS = {"name": "Character Body"}, {"name": "Character Shirt"}, {"name": "Character Trousers"}
SHOE, HAT = {"prefix": "Character Shoe", "word": True}, {"name": "Character Hat"}
# Where a box measures a garment it measures the whole garment (its parts and children too); "its own object" and
# the colours stay on the exact names the brief gives each garment.
WHOLE_SHIRT, WHOLE_TROUSERS, WHOLE_HAT = ({"whole": target["name"]} for target in (SHIRT, TROUSERS, HAT))
SUBJECT = ({"prefix": "Character", "word": True},)

PROMPT = (
    "A friendly, stylised adult character standing upright, dressed in separate clothing parts I can change one "
    "at a time. Build it. The two attached pictures show the character I have in mind: the hero shot and a "
    "front elevation. Take the proportions and the look from them and design the details yourself; where they "
    "and this brief disagree, the brief wins.\n\n"
    "Conventions. Metres, Z up. I find the parts by name, so name them exactly as written here and give anything "
    "else you add a meaningful name of its own. Keep the file tidy: nothing from the default scene remains, no "
    "unused leftovers, and a material on everything that shows.\n\n"
    "The character. 1.75 m tall to the top of the hat, standing at the origin facing -Y with its feet on z = 0, "
    "arms relaxed at its sides. 'Character Body' is the skin: head, neck, arms and hands (what the clothes "
    "cover may be left out). Each clothing part is its own object, fitted over the body: 'Character Shirt', an "
    "orange cotton shirt from the waist (about 0.9 m up) to the neck; 'Character Trousers', blue denim from the "
    "waist down to the ankles; two matching leather shoes whose names start with 'Character Shoe', one on each "
    "foot, about 28 cm long; and 'Character Hat', a dark green felt hat with a brim.\n\n"
    "Light and camera. Soft studio light from the front left and a camera named 'Hero Camera' at "
    "(2.0, -4.4, 1.2) looking at (0, 0, 0.9) with a 50 mm lens, as the scene's camera.\n\n"
    "Before you call it done, look at the character from the hero camera next to the pictures and fix what is "
    "wrong.")


def checks():
    return [
        {"kind": "hygiene", "absent": ["Cube", "Light", "Camera"], "max_default_named": 0, "max_without_material": 0,
         "max_unused": 0, "label": "tidy file: no default leftovers, named, materials"},
        *[{"kind": "object", "target": target, "type": "MESH", "label": "%s is its own object" % label}
          for target, label in ((BODY, "the body"), (SHIRT, "the shirt"), (TROUSERS, "the trousers"),
                                (HAT, "the hat"))],
        {"kind": "count", "target": SHOE, "type": "MESH", "count": [2, 2], "label": "two shoes"},
        {"kind": "bbox", "target": {"prefix": "Character", "word": True}, "size": [[0.35, 1.2], [0.15, 0.8],
                                                                                   [1.65, 1.85]],
         "min": [None, None, 0.0], "tolerance": 0.04, "label": "1.75 m tall, feet on the ground"},
        {"kind": "bbox", "target": WHOLE_HAT, "max": [None, None, figure.HEIGHT], "tolerance": 0.06,
         "label": "the hat at the top"},
        {"kind": "bbox", "target": WHOLE_SHIRT, "min": [None, None, 0.9], "max": [None, None, 1.42], "tolerance": 0.12,
         "label": "the shirt from the waist to the neck"},
        {"kind": "bbox", "target": WHOLE_TROUSERS, "min": [None, None, 0.08], "max": [None, None, 1.0], "tolerance": 0.12,
         "label": "the trousers from the waist to the ankles"},
        {"kind": "bbox", "target": SHOE, "min": [None, None, 0.0], "max": [None, None, 0.1], "tolerance": 0.04,
         "size": [None, [0.22, 0.6], None], "label": "shoes on the ground, about 28 cm long"},
        {"kind": "surface", "target": TROUSERS, "color": ["blue"], "label": "blue trousers"},
        {"kind": "surface", "target": SHIRT, "color": ["warm"], "label": "an orange shirt"},
        {"kind": "camera", "target": {"name": "Hero Camera"}, "active": True, "location": list(HERO["location"]),
         "radius": 0.2, "look_at": list(HERO["target"]), "min_dot": 0.99, "lens": [48.0, 52.0],
         "label": "Hero Camera placed, aimed and active"},
    ]


GRADE_CLASSES = {
    "Body and head": {"views": ["front", "closeup_top"],
                      "what": "A friendly stylised adult's body: head, neck, arms and hands, 1.75 m with the hat."},
    "Shirt": {"views": ["closeup_top", "hero"], "what": "An orange cotton shirt fitted over the body, waist to neck."},
    "Trousers and shoes": {"views": ["closeup_legs", "front"],
                           "what": "Blue denim trousers to the ankles and two matching leather shoes."},
    "Hat": {"views": ["closeup_top", "side"], "what": "A dark green felt hat with a brim."},
}
CHECKS = checks()
TURNS = [{"id": "brief", "title": "The dressed character", "references": ["hero", "front"], "prompt": PROMPT,
          "checks": CHECKS}]


def build_turn(bpy, turn_id):
    if turn_id != "brief":
        raise KeyError(turn_id)
    oneshot_kit.clear_default_scene(bpy)
    figure.build_outfit(bpy)
    scene = bpy.context.scene
    kit.sun_object(bpy, "Key Light", 45.0, -120.0, (1.0, 0.95, 0.9), 3.0, angle_deg=10.0)
    scene.world = kit.colour_world(bpy, "Studio", (0.45, 0.45, 0.47), strength=0.7)
    scene.camera = kit.camera_object(bpy, "Hero Camera", HERO["location"], HERO["target"], HERO["lens"])
    bpy.context.view_layer.update()


def build(bpy, upto=None):
    build_turn(bpy, "brief")
