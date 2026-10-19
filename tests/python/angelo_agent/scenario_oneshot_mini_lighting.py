# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mini one-shot test ``oneshot-mini-lighting``: a three-point rig, a physical sky and Cycles (``oneshot_minis``).

The factory scene is kept: the cube is the subject, and the default point
light and camera may stay (only area lights are counted). The message says
nothing about keeping the cube as it was, so that is not checked. It names
no light, so the rig is read from the lights themselves: three area lights
aimed at the cube, one of them behind it as the scene camera sees it, and
the stronger of the other two as the key.
"""

import oneshot_minis as minis
from oneshot_minis import CAMERAS, FINAL_STILL, FINAL_VIEW, FRAME, GROUND_TRUTH, LIMITS, SUBJECT  # noqa: F401

ID = "oneshot_mini_lighting"
TITLE = "Three-point area lighting, a physical sky and Cycles"
SUN_ELEVATION, SAMPLES = 5.0, 128
# (name, location, watts, size): the rim stands behind the cube as the factory camera sees it.
RIG = (("Key", (4.0, -4.0, 5.0), 1000.0, 2.0), ("Fill", (-2.0, -6.0, 2.5), 250.0, 3.0),
       ("Rim", (-5.0, 5.0, 3.5), 600.0, 1.5))
PROMPT = ("Light the cube with a key, a fill and a rim area light, set the world to a physical sky with the sun "
          "5 degrees up, and switch to Cycles at 128 samples with denoising.")
CHECKS = [
    {"kind": "light_rig", "type": "AREA", "count": [3, 3], "aim": [0.0, 0.0, 0.0], "min_dot": 0.9, "behind": [1, 1],
     "key_over_fill": 1.2, "label": "three area lights on the cube: key, fill and a rim behind it"},
    {"kind": "world", "sky_or_color": [], "sun_elevation_deg": [SUN_ELEVATION - 1.0, SUN_ELEVATION + 1.0],
     "label": "a physical sky with the sun 5 degrees up"},
    {"kind": "scene", "values": {"render.engine": "CYCLES", "cycles.samples": SAMPLES, "cycles.use_denoising": True},
     "label": "Cycles, 128 samples, denoising"},
]
TURNS = minis.turn(TITLE, PROMPT, CHECKS)


def build_turn(bpy, turn_id):
    import scenario_build_kit as kit
    if turn_id != "brief":
        raise KeyError(turn_id)
    scene = bpy.context.scene
    for name, location, energy, size in RIG:
        minis.area_light(bpy, name, location, energy, size)
    scene.world = kit.sky_world(bpy, "Physical Sky", SUN_ELEVATION, 35.0, strength=1.0)
    scene.render.engine = "CYCLES"
    scene.cycles.samples, scene.cycles.use_denoising = SAMPLES, True
    bpy.context.view_layer.update()


def build(bpy, upto=None):
    build_turn(bpy, "brief")
