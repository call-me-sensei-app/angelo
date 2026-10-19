# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mini one-shot test ``oneshot-mini-cube``: three edits to the default cube in one request (``oneshot_minis``).

The factory scene is kept: the cube is the subject and must still be there.
The bevel may be a Bevel modifier or bevelled geometry; a person sees the same.
"""

import oneshot_minis as minis
from oneshot_minis import CAMERAS, FINAL_STILL, FINAL_VIEW, FRAME, GROUND_TRUTH, LIMITS, SUBJECT  # noqa: F401

ID = "oneshot_mini_cube"
TITLE = "Bevel, subdivide and paint the default cube"
BEVEL, SEGMENTS, LEVELS = 0.02, 3, 2
COPPER = (0.955, 0.637, 0.538)
PROMPT = ("Bevel the cube's edges by 2 cm with 3 segments, add a Subdivision modifier at level 2, and give it a "
          "brushed-copper material.")
CHECKS = minis.CUBE_KEPT + [
    {"kind": "bevel", "target": minis.CUBE, "width": BEVEL, "segments": SEGMENTS,
     "label": "edges bevelled 2 cm, 3 segments"},
    {"kind": "modifier_settings", "target": minis.CUBE, "type": "SUBSURF", "values": {"levels": LEVELS},
     "label": "a Subdivision modifier at level 2"},
    # Copper: a metal with a warm base colour; brushed: neither a mirror nor matt.
    {"kind": "surface", "target": minis.CUBE, "color": ["warm"], "saturation": [0.2, 1.0], "metallic": [0.8, 1.0],
     "roughness": [0.15, 0.65], "label": "a brushed-copper material"},
]
TURNS = minis.turn(TITLE, PROMPT, CHECKS)


def build_turn(bpy, turn_id):
    import scenario_build_kit as kit
    if turn_id != "brief":
        raise KeyError(turn_id)
    cube = bpy.data.objects["Cube"]
    bevel = cube.modifiers.new("Bevel", "BEVEL")
    bevel.width, bevel.segments = BEVEL, SEGMENTS
    subdivision = cube.modifiers.new("Subdivision", "SUBSURF")
    subdivision.levels = subdivision.render_levels = LEVELS
    copper = kit.principled(bpy, "Brushed Copper", COPPER, roughness=0.35, metallic=1.0)
    if cube.material_slots:
        cube.material_slots[0].material = copper
    else:
        kit.assign(cube, copper)
    bpy.context.view_layer.update()


def build(bpy, upto=None):
    build_turn(bpy, "brief")
