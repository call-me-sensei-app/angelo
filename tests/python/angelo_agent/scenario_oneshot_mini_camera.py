# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mini one-shot test ``oneshot-mini-camera``: a new scene camera, the old one deleted (``oneshot_minis``).

The factory scene is kept except for what the message asks: exactly one
camera remains. The message says nothing else about the cube, so nothing
else about it is checked. The message names no camera, so the checks look at
whichever camera the scene has.
"""

import oneshot_minis as minis
from oneshot_minis import CAMERAS, FINAL_STILL, FINAL_VIEW, FRAME, GROUND_TRUTH, LIMITS, SUBJECT  # noqa: F401

ID = "oneshot_mini_camera"
TITLE = "A new scene camera aimed at the cube"
LOCATION, AIM, LENS = (6.0, -8.0, 4.0), (0.0, 0.0, 0.0), 50.0
PROMPT = ("Add a camera at (6, -8, 4) aimed at the cube with a 50 mm lens, make it the scene camera, then delete "
          "the old camera.")
CHECKS = [
    {"kind": "count", "target": minis.EVERYTHING, "type": "CAMERA", "count": [1, 1],
     "label": "one camera remains: the old one is gone"},
    {"kind": "camera", "target": minis.EVERYTHING, "active": True, "location": list(LOCATION), "radius": 0.1,
     "look_at": list(AIM), "min_dot": 0.995, "lens": [LENS - 0.5, LENS + 0.5],
     "label": "the scene camera is at (6, -8, 4), aimed at the cube, 50 mm"},
]
TURNS = minis.turn(TITLE, PROMPT, CHECKS)


def build_turn(bpy, turn_id):
    import scenario_build_kit as kit
    if turn_id != "brief":
        raise KeyError(turn_id)
    old = bpy.data.objects.get("Camera")
    bpy.context.scene.camera = kit.camera_object(bpy, "View Camera", LOCATION, AIM, LENS)
    if old is not None:
        bpy.data.objects.remove(old, do_unlink=True)
    bpy.context.view_layer.update()


def build(bpy, upto=None):
    build_turn(bpy, "brief")
