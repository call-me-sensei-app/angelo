# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot test ``oneshot-bridge``: the bridge scenario's whole build as one brief, in one message.

The same scene as ``scenario_bridge`` (its ground truth, its fixed cameras and
every one of its 46 closed checks, unchanged), but asked for once: one
message with the four overview references, then nothing. The brief adds what
a one-shot test needs: a tidy file, and the finished still rendered by the
agent itself (frame 60 from the brief's camera) and saved by name.

``scenario_bridge`` stays the multi-turn scenario; this module only reads it.
"""

import math

import oneshot_opacity
import scenario_bridge as bridge

ID = "oneshot_bridge"
TITLE = "Stone arch bridge at golden hour, in one message"
FRAME = bridge.FRAME
SUBJECT = bridge.SUBJECT
# The brief's own render camera as a fixed view too, so the harness's final still has a reference beside it.
FINAL_VIEW = "deck"
CAMERAS = dict(bridge.CAMERAS, deck=dict(bridge.DECK_CAMERA, role="closeup",
                                         label="The brief's render camera (frame 60)"))
FINAL_STILL = {"name": "bridge-final.png", "size": (1920, 1080)}
# The reference is a render of this ground truth: its view scores say how close a side came to it.
GROUND_TRUTH = "reference"
# One message; the agent's turn may run this long, a render job it started this much longer, and
# the side is stopped once its model usage passes this many dollars at list prices.
LIMITS = {"wall_s": 3600.0, "job_wait_s": 900.0, "cost_usd": 50.0}
APPROVABLE = ("angelo_job_start",)
JOB_WAIT_S = LIMITS["job_wait_s"]

PROMPT = (
    "Build the scene in the four attached pictures and render one still from it. The pictures show the finished "
    "scene: an orthographic front view looking along +Y, a view from upstream, an orthographic top view with +Y "
    "up, and a hero shot. It is a stone arch bridge over a river valley at golden hour, with a small red car "
    "crossing. Match the pictures as closely as you can; where they and this brief disagree, the brief wins.\n\n"
    "Conventions. Metres, Z up. The river runs along Y and the bridge spans X, centred on the origin. I find "
    "things by name, so name them exactly as written here and give anything else you add a meaningful name of "
    "its own. Keep the file tidy: nothing from the default scene (its cube, light or camera) remains, no unused "
    "leftovers, and a material on everything that shows.\n\n"
    "The valley. One solid mesh named 'Valley', like a diorama block: 120 m along X by 80 m along Y, with flat "
    "earth sides going down to a flat base at z = -8 m. Its top has a flat riverbed at z = -4 m where "
    "|x| <= 5 m, smooth earthy slopes, and grassy banks at height 0 from |x| = 9 m that roll up into low hills "
    "(to about 5 m) towards the outer edges. A grass-and-earth material.\n\n"
    "The bridge. Warm grey stone; every part's name starts with 'Bridge'. 'Bridge Deck' is a slab 24 m long "
    "(x = -12 to 12), 5 m wide and 0.6 m thick, its top at z = 1 m. 'Bridge Arch' is a solid half-elliptical "
    "ring under it, 4.4 m wide in Y, springing from z = -2.5 m: its outer curve spans x = -8 to 8 m and rises to "
    "the deck's underside (z = 0.4 m), its opening spans x = -7.2 to 7.2 m and rises to z = -0.4 m. "
    "'Bridge Piers' are thin stone walls (0.5 m thick in X, 4.4 m in Y) standing on the arch and reaching the "
    "deck at x = +/-4, +/-5.5 and +/-7 m. 'Bridge Abutments' are stone blocks under both deck ends, from "
    "|x| = 9 to 12 m and from z = -1 m up to the deck.\n\n"
    "On the deck. Dark iron railings along both long edges, their names starting with 'Railing': square posts "
    "0.1 m thick and 1 m tall at y = +/-2.4 m, every 1.5 m from x = -12 to 12 m (17 a side; the end posts are "
    "centred on the deck's ends at x = -12 and 12 m, even though half of each then reaches past the deck end), "
    "with a top rail 1 m above the deck and a middle rail 0.5 m above it, both running the full 24 m. Street "
    "lamps, their names "
    "starting with 'Lamp': slim posts 0.16 m across rising from the deck to z = 4.4 m, each topped by a glowing "
    "sphere lantern 0.4 m across, at y = +/-2.05 m and x = -9, -4.5, 0, 4.5 and 9 m (ten lamps). The lanterns "
    "glow warm orange (emission, strength about 4).\n\n"
    "The river. A flat water surface named 'River' at z = -3 m, 15 m wide in X (centred) and 40 m long in Y, "
    "dark teal and glossy (roughness 0.1 or less). Sixteen rocks, their names starting with 'Rock', along the "
    "two water edges, eight a side: 4.5 to 8 m from the river's centre line, spread from y = -18 to 18 m, each "
    "0.7 to 1.6 m across and partly sunk into the bank.\n\n"
    "Light. Golden hour. A sun light named 'Sun', about 5 degrees above the horizon, shining from the +X, -Y "
    "side (azimuth about -35 degrees, measured from +X towards +Y), warm orange, strength around 7, so that it "
    "lights the bridge's downstream face and throws long shadows. A matching hazy sky (a physical sky or a warm "
    "sky colour), so the scene glows as in the hero picture.\n\n"
    "The car. A small red car, its parts named starting with 'Car' (body, cabin and four wheels that touch the "
    "deck), about 4 m long, 1.8 m wide and 1.7 m tall. It drives along +X in the lane at y = -1 m, keyframed "
    "from x = -10.5 m at frame 1 to x = 10.5 m at frame 120, so it is mid-bridge around frame 60. The scene's "
    "frame range is 1 to 120.\n\n"
    "Camera and output. A camera named 'Deck Camera' stands on the deck at (-11, 1.4, 2.6), looks along the "
    "bridge towards (11, -0.6, 1.6) with a 24 mm lens, and is the scene's camera. Render settings: EEVEE, "
    "1920 x 1080 at 100 %, 24 fps, 128 render samples, ray tracing and motion blur on, the AgX view transform. "
    "Render frame 60 from 'Deck Camera' with those settings and save the still as a PNG named "
    "'bridge-final.png'; tell me where it is.\n\n"
    "Before you call it done, look at your result next to the four pictures and fix what does not match.")

HYGIENE = {"kind": "hygiene", "absent": ["Cube", "Light", "Camera"], "max_default_named": 0,
           "max_without_material": 0, "max_unused": 0, "label": "tidy file: no default leftovers, named, materials"}



def _ring_points(samples=24, layers=(0.25, 0.5, 0.75)):
    """Origins in front of the arch ring's face (y = -4): between its opening (7.2 x 2.1 m) and outer curve
    (8 x 2.9 m) half-ellipses from the springing at z = -2.5, so each ray runs through the stated ring."""
    points = []
    for index in range(samples):
        angle = math.pi * (index + 0.5) / samples
        for share in layers:
            a, b = 7.2 + share * 0.8, 2.1 + share * 0.8
            points.append([a * math.cos(angle), -4.0, -2.5 + b * math.sin(angle)])
    return points


# Grader only, never shown to the model: the brief's slab, solid ring and solid block have no see-through gaps.
OPACITY = [
    {"kind": "opaque", "target": bridge.BRIDGE, "label": "the deck slab has no see-through gaps",
     "areas": [oneshot_opacity.box_areas("deck", (-12.0, -2.5, 0.4), (12.0, 2.5, 1.0), 2, -1, 9.0, 9.55,
                                         edge=0.5)]},
    {"kind": "opaque", "target": bridge.BRIDGE, "label": "the arch ring has no see-through gaps",
     "areas": [{"name": "arch ring", "points": _ring_points(), "direction": [0.0, 1.0, 0.0], "depth": 8.0}]},
    {"kind": "opaque", "target": bridge.VALLEY, "label": "the valley block's top has no see-through gaps",
     "areas": [oneshot_opacity.box_areas("valley top", (-60.0, -40.0, -8.0), (60.0, 40.0, 5.0), 2, -1, 15.0,
                                         28.0, edge=5.0)]},
]

TURNS = [{
    "id": "brief", "title": "The whole bridge scene and its final still",
    "references": ["front", "side", "top", "hero"],
    "prompt": PROMPT,
    "checks": [dict(check, label="%s: %s" % (turn["id"], check["label"]))
               for turn in bridge.TURNS for check in turn["checks"]] + [HYGIENE] + OPACITY,
    "outputs": [{"name": FINAL_STILL["name"], "min_size": list(FINAL_STILL["size"]),
                 "label": "the final still was rendered and saved (1920 x 1080 or larger)"}],
    # What a perfect agent starts after building: the dry run's scripted models play it.
    "job": {"kind": "RENDER_STILL", "output": "renders/" + FINAL_STILL["name"], "frame_start": FRAME,
            "resolution": list(FINAL_STILL["size"]), "samples": 16, "format": "PNG", "minutes": 10},
}]


def build_turn(bpy, turn_id):
    """The whole ground truth at once (the brief is one turn), then frame 60, the frame the still shows."""
    if turn_id != "brief":
        raise KeyError(turn_id)
    bridge.build(bpy)
    bpy.context.scene.frame_set(FRAME)
    bpy.context.view_layer.update()


def build(bpy, upto=None):
    build_turn(bpy, "brief")
