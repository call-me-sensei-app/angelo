# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scenario ``scenario-shop``: four everyday edits in a 70-object bookshop (``many_object_scenes``).

Most objects each turn edits are neither among the eight rows a turn observation lists nor named exactly in
the message, so an agent finds them first, and one turn continues a numbered row that is already in use. The
turns are one continuing chat. Every turn also checks that every start object no turn names is exactly as
it was (``unchanged``). Held out: nothing here comes from the run that exposed the many-object class
(doc/angelo/agent_integration.rst, inferred bindings).

Axes: the front window is at +X, the door at -X, the bookcases along the back wall at +Y; Z is up; metres.
"""

import many_object_scenes as scenes

ID = "shop"
TITLE = "Four everyday edits in a crowded bookshop"
FRAME = 1
START = "start.blend"
SETUP_TIMEOUT_S = 300.0
CEILING, WINDOW, GLOW = {"prefix": "Ceiling Light", "word": True}, {"name": "Window Light"}, {"name": "Desk Lamp Glow"}
STOOLS, LANTERNS = {"prefix": "Stool", "word": True}, {"prefix": "Lantern", "word": True}
RUG, FILL, REGISTER = {"name": "Rug"}, {"name": "Fill Light"}, {"name": "Cash Register"}
POTS = {"prefix": "Plant Pot", "word": True}
NAMED = ([CEILING, WINDOW], [STOOLS], [LANTERNS], [RUG, FILL, POTS, REGISTER])


def unchanged(upto):
    """Everything no turn names, as in the heavy scene: the same after every turn and again at the end."""
    exclude = [target for group in NAMED for target in group]
    return {"kind": "unchanged", "scene": ID, "exclude": exclude,
            "label": "every start object no turn names is exactly as it was"}


def _light(name, low, high, **rest):
    return dict({"kind": "light", "target": {"name": name}, "energy": [low, high],
                 "label": "%s at %g to %g W" % (name, low, high)}, **rest)


CAMERAS = {
    "front": {"location": (0.0, -12.0, 1.5), "target": (0.0, 0.0, 1.5), "ortho_scale": 10.0, "role": "reference",
              "label": "Front (orthographic, looking +Y)"},
    "side": {"location": (-12.0, 0.0, 1.5), "target": (0.0, 0.0, 1.5), "ortho_scale": 8.0, "role": "reference",
             "label": "Side (orthographic, looking +X)"},
    "hero": {"location": (0.0, -6.5, 1.7), "target": (0.0, 0.5, 1.2), "lens": 28.0, "role": "reference",
             "label": "Hero (the shop camera)"},
    "top": {"location": (0.0, 0.0, 12.0), "target": (0.0, 0.0, 0.0), "ortho_scale": 10.0, "role": "reference",
            "label": "Plan (orthographic, +Y up)"},
    "closeup_counter": {"location": (-1.0, -4.2, 1.6), "target": (-1.0, -1.4, 0.7), "lens": 35.0, "role": "closeup",
                        "label": "Close-up: the counter and its stools"},
}
SUBJECT = ({"prefix": ""},)

TURNS = [
    {"id": "evening", "title": "Evening light", "references": [],
     "prompt": "Make it evening in the shop: dim each of the three ceiling lights to a quarter of its current "
               "power, turn the window light into a cool blue moonlight of about 20 W, and add a warm point light "
               "named 'Desk Lamp Glow' inside the shade of the lamp on the desk. Change nothing else.",
     "checks": [_light("Ceiling Light %d" % n, 33.75, 41.25) for n in (1, 2, 3)]
               + [_light("Window Light", 15.0, 25.0, color=["blue"]),
                  {"kind": "light", "target": GLOW, "type": "POINT", "color": ["warm"], "location": (2.95, 2.5, 1.0),
                   "radius": 0.25, "label": "a warm point light inside the desk lamp's shade"},
                  unchanged(0)]},
    {"id": "stools", "title": "More stools at the counter", "references": [],
     "prompt": "Add three more stools to the row along the counter, continuing it at the same spacing. "
               "Change nothing else.",
     "checks": [{"kind": "row", "scene": ID, "target": STOOLS, "count": 7, "axis": 0,
                 "label": "seven stools in one evenly spaced row"}, unchanged(1)]},
    {"id": "lanterns", "title": "Raise the lanterns", "references": [],
     "prompt": "Raise every hanging lantern by 30 cm. Change nothing else.",
     "checks": [{"kind": "offset_all", "scene": ID, "target": LANTERNS, "offset": [0.0, 0.0, 0.3],
                 "label": "all six lanterns 30 cm higher"}, unchanged(2)]},
    {"id": "photo", "title": "Ready for a photo", "references": [],
     "prompt": "Get the shop ready for a photo: move the rug 50 cm toward the door, set the fill light to 25 W, "
               "make every plant pot 20 % bigger where it stands, and turn the cash register 90 degrees about its "
               "vertical axis. Change nothing else.",
     "checks": [{"kind": "toward", "scene": ID, "target": RUG, "point": [-4.4, -1.5, 0.0], "distance": 0.5,
                 "label": "the rug 50 cm toward the door"},
                _light("Fill Light", 22.5, 27.5),
                {"kind": "scaled_all", "scene": ID, "target": POTS, "factor": 1.2,
                 "label": "the plant pots 20 % bigger"},
                {"kind": "turned", "scene": ID, "target": REGISTER, "degrees": 90.0,
                 "label": "the cash register turned 90 degrees"}, unchanged(3)]},
]


def make_start(bpy, folder):
    return scenes.make_start(bpy, ID, folder, START)


def setup(bpy, scene, path, sha256):
    """Both sides, before the first message: the start file appended into ``scene`` (SHA-256 checked)."""
    return scenes.append_start(bpy, ID, scene, path, sha256)


def build_turn(bpy, turn_id):
    """The perfect agent (dry run, self-check)."""
    objects = bpy.context.scene.objects
    if turn_id == "evening":
        for n in (1, 2, 3):
            objects["Ceiling Light %d" % n].data.energy /= 4.0
        objects["Window Light"].data.energy, objects["Window Light"].data.color = 20.0, (0.45, 0.6, 1.0)
        glow = bpy.data.objects.new("Desk Lamp Glow", bpy.data.lights.new("Desk Lamp Glow", "POINT"))
        glow.data.color, glow.location = (1.0, 0.7, 0.4), (2.95, 2.5, 1.0)
        objects["Desk Lamp"].users_collection[0].objects.link(glow)
    elif turn_id == "stools":
        source = objects["Stool 4"]
        for n in (5, 6, 7):
            copy = source.copy()
            copy.name, copy.location.x = "Stool %d" % n, source.location.x + 0.6 * (n - 4)
            source.users_collection[0].objects.link(copy)
    elif turn_id == "lanterns":
        for n in range(1, 7):
            objects["Lantern %02d" % n].location.z += 0.3
    elif turn_id == "photo":
        objects["Rug"].location.x -= 0.5
        objects["Fill Light"].data.energy = 25.0
        for n in (1, 2, 3):
            objects["Plant Pot %d" % n].scale = (1.2, 1.2, 1.2)
        objects["Cash Register"].rotation_euler.z += 1.5707963
    else:
        raise KeyError(turn_id)
    bpy.context.view_layer.update()


def build(bpy, upto=None):
    for turn in TURNS:
        build_turn(bpy, turn["id"])
        if turn["id"] == upto:
            break
