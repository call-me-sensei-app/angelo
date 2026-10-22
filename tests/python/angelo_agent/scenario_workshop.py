# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scenario ``scenario-workshop``: four everyday edits in a 60-object garage workshop (``many_object_scenes``).

As ``scenario_shop``: most objects each turn edits are neither listed with a revision nor named exactly in the
message, one turn continues a numbered row that is already in use, and every turn checks that every start
object no turn names is exactly as it was. Held out from the run that exposed the many-object class.

Axes: the garage door is at -Y, the workbench, pegboard and tool racks along the back wall at +Y; Z is up;
metres.
"""

import many_object_scenes as scenes

ID = "workshop"
TITLE = "Four everyday edits in a crowded workshop"
FRAME = 1
START = "start.blend"
SETUP_TIMEOUT_S = 300.0
SHELF_LIGHTS, RACKS = {"prefix": "Shelf Light", "word": True}, {"prefix": "Tool Rack", "word": True}
CRATES, LAMP = {"prefix": "Crate", "word": True}, {"name": "Ceiling Lamp"}
TOOLBOX, CANS = {"name": "Toolbox"}, {"prefix": "Oil Can", "word": True}
NAMED = ([SHELF_LIGHTS], [RACKS], [CRATES], [LAMP, TOOLBOX, CANS])


def unchanged(upto):
    """Everything no turn names, as in the heavy scene: the same after every turn and again at the end."""
    exclude = [target for group in NAMED for target in group]
    return {"kind": "unchanged", "scene": ID, "exclude": exclude,
            "label": "every start object no turn names is exactly as it was"}


CAMERAS = {
    "front": {"location": (0.0, -12.0, 1.7), "target": (0.0, 0.0, 1.7), "ortho_scale": 11.0, "role": "reference",
              "label": "Front (orthographic, looking +Y)"},
    "side": {"location": (-12.0, 0.0, 1.7), "target": (0.0, 0.0, 1.7), "ortho_scale": 9.0, "role": "reference",
             "label": "Side (orthographic, looking +X)"},
    "hero": {"location": (0.0, -7.5, 2.2), "target": (0.0, 1.0, 1.0), "lens": 24.0, "role": "reference",
             "label": "Hero (the workshop camera)"},
    "top": {"location": (0.0, 0.0, 14.0), "target": (0.0, 0.0, 0.0), "ortho_scale": 11.0, "role": "reference",
            "label": "Plan (orthographic, +Y up)"},
    "closeup_bench": {"location": (-1.5, 0.0, 2.0), "target": (-2.5, 3.0, 1.0), "lens": 35.0, "role": "closeup",
                      "label": "Close-up: the workbench and the racks"},
}
SUBJECT = ({"prefix": ""},)

TURNS = [
    {"id": "lights", "title": "Warmer shelf lights", "references": [],
     "prompt": "The shelf lights look cold and dim. Make every one of them a warm white at 60 W. Change nothing else.",
     "checks": [{"kind": "light", "target": {"name": "Shelf Light %d" % n}, "energy": [54.0, 66.0],
                 "color": ["warm", "grey"], "label": "Shelf Light %d warm white at 60 W" % n} for n in range(1, 6)]
               + [unchanged(0)]},
    {"id": "racks", "title": "More tool racks", "references": [],
     "prompt": "Add two more tool racks beside the ones on the back wall, continuing the row at the same spacing. "
               "Change nothing else.",
     "checks": [{"kind": "row", "scene": ID, "target": RACKS, "count": 5, "axis": 0,
                 "label": "five tool racks in one evenly spaced row"}, unchanged(1)]},
    {"id": "crates", "title": "Crates away from the door", "references": [],
     "prompt": "Move all twelve crates 1 m farther from the garage door. Change nothing else.",
     "checks": [{"kind": "offset_all", "scene": ID, "target": CRATES, "offset": [0.0, 1.0, 0.0],
                 "label": "every crate 1 m farther from the door"}, unchanged(2)]},
    {"id": "shoot", "title": "Before the shoot", "references": [],
     "prompt": "Before the shoot: lower the ceiling lamp by 40 cm, turn the toolbox 90 degrees about its vertical "
               "axis, and make each oil can 50 % bigger where it stands. Change nothing else.",
     "checks": [{"kind": "offset_all", "scene": ID, "target": LAMP, "offset": [0.0, 0.0, -0.4],
                 "label": "the ceiling lamp 40 cm lower"},
                {"kind": "turned", "scene": ID, "target": TOOLBOX, "degrees": 90.0, "label": "the toolbox turned 90 degrees"},
                {"kind": "scaled_all", "scene": ID, "target": CANS, "factor": 1.5, "label": "every oil can 50 % bigger"},
                unchanged(3)]},
]


def make_start(bpy, folder):
    return scenes.make_start(bpy, ID, folder, START)


def setup(bpy, scene, path, sha256):
    """Both sides, before the first message: the start file appended into ``scene`` (SHA-256 checked)."""
    return scenes.append_start(bpy, ID, scene, path, sha256)


def build_turn(bpy, turn_id):
    """The perfect agent (dry run, self-check)."""
    objects = bpy.context.scene.objects
    if turn_id == "lights":
        for n in range(1, 6):
            light = objects["Shelf Light %d" % n].data
            light.energy, light.color = 60.0, (1.0, 0.85, 0.7)
    elif turn_id == "racks":
        source = objects["Tool Rack 3"]
        for n in (4, 5):
            copy = source.copy()
            copy.name, copy.location.x = "Tool Rack %d" % n, source.location.x + 1.5 * (n - 3)
            source.users_collection[0].objects.link(copy)
    elif turn_id == "crates":
        for n in range(1, 13):
            objects["Crate %02d" % n].location.y += 1.0
    elif turn_id == "shoot":
        objects["Ceiling Lamp"].location.z -= 0.4
        objects["Toolbox"].rotation_euler.z += 1.5707963
        for n in range(1, 9):
            objects["Oil Can %d" % n].scale = (1.5, 1.5, 1.5)
    else:
        raise KeyError(turn_id)
    bpy.context.view_layer.update()


def build(bpy, upto=None):
    for turn in TURNS:
        build_turn(bpy, turn["id"])
        if turn["id"] == upto:
            break
