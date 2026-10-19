# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the mini one-shot tests share (``scenario_oneshot_mini_<name>.py``).

A mini is the smallest useful one-shot test: one short everyday request, no
reference image, a few closed checks, expected to take well under two
minutes. It probes how the agent goes about ordinary work: typed tools or a
script for each change, how much it looks up first, what it costs and how
the turn ends. The path metrics are the point; the checks only say whether
the request was met.

Two things differ from the scene tests:

* **The factory scene is kept.** A mini starts from Blender's factory scene
  and its message does not ask for it to be cleared, so nothing here demands
  that the cube, the light or the camera be removed; each check set says
  what it expects of them.
* **No still is asked for** (``FINAL_STILL`` has no name), so there is no
  saved-still check and no pixel check. The harness still renders the result
  from ``FINAL_VIEW`` at ``FINAL_STILL``'s size for a person to look at.

Ground truths are plain stand-ins that satisfy the checks; nothing is shown
to the model.
"""

import math

import scenario_build_kit as kit

# One turn of five minutes at most (it should need far less), no render job to wait for, a small cost cap.
LIMITS = {"wall_s": 300.0, "job_wait_s": 0.0, "cost_usd": 3.0}
FINAL_STILL = {"name": None, "size": (1920, 1080)}   # No file is asked for; the size is the review render's.
FINAL_VIEW = "hero"
GROUND_TRUTH = "stand-in"
FRAME = 1
SUBJECT = ({"prefix": ""},)
# The factory camera's own view as the hero; the other three for the fixed-view captures.
FACTORY_CAMERA = (7.3589, -6.9258, 4.9583)
CAMERAS = {
    "front": {"location": (0.0, -30.0, 0.5), "target": (0.0, 0.0, 0.5), "ortho_scale": 9.0, "role": "reference",
              "label": "Front (orthographic, looking +Y)"},
    "side": {"location": (30.0, 0.0, 0.5), "target": (0.0, 0.0, 0.5), "ortho_scale": 9.0, "role": "reference",
             "label": "Side (orthographic, looking -X)"},
    "top": {"location": (0.0, 0.0, 30.0), "target": (0.0, 0.0, 0.0), "ortho_scale": 9.0, "role": "reference",
            "label": "Top (orthographic, +Y up)"},
    "hero": {"location": FACTORY_CAMERA, "target": (0.0, 0.0, 0.0), "lens": 50.0, "role": "reference",
             "label": "The factory camera's view"},
}
CUBE = {"name": "Cube"}
EVERYTHING = {"prefix": ""}   # A mini names nothing: checks that need a target look at every object of a type.
# The factory cube is still where and as large as it was (a bevel or a subdivision may round it a little): only
# for a message that edits the cube itself (oneshot-mini-cube), not one that merely aims or lights at it.
CUBE_KEPT = [
    {"kind": "object", "target": CUBE, "type": "MESH",
     "label": "the default cube is still there (the factory scene is kept)"},
    {"kind": "bbox", "target": CUBE, "min": [-1.0, -1.0, -1.0], "max": [1.0, 1.0, 1.0], "tolerance": 0.2,
     "label": "the cube is where and as large as it was"},
]


def turn(title, prompt, checks):
    """The one turn of a mini: its message, no reference image, no still."""
    return [{"id": "brief", "title": title, "references": [], "prompt": prompt, "checks": checks}]


def stone(bpy):
    return bpy.data.materials.get("Grey Stone") or kit.principled(bpy, "Grey Stone", (0.42, 0.42, 0.41), roughness=0.85)


def arched_strip(length, width, thickness, base, rise, y=0.0, segments=16):
    """Vertices and faces of a strip along X whose top follows ``base + rise * (1 - (2x/length)^2)``."""
    vertices, faces = [], []
    for index in range(segments + 1):
        x = (index / segments - 0.5) * length
        top = base + rise * (1.0 - (2.0 * x / length) ** 2)
        for side in (-1, 1):
            for z in (top - thickness, top):
                vertices.append((x, y + side * width / 2, z))
    # Per station: (-y, low)=4i, (-y, top)=4i+1, (+y, low)=4i+2, (+y, top)=4i+3.
    for index in range(segments):
        a, b = 4 * index, 4 * index + 4
        faces += [(a + 1, a + 3, b + 3, b + 1), (a, b, b + 2, a + 2), (a, a + 1, b + 1, b),
                  (a + 2, b + 2, b + 3, a + 3)]
    last = 4 * segments
    faces += [(0, 2, 3, 1), (last, last + 1, last + 3, last + 2)]
    return vertices, faces


def footbridge(bpy, rise=0.0):
    """A stone footbridge at the origin: a 4 x 1.2 m deck, four square posts, two rails (``rise``: the arch)."""
    material = stone(bpy)
    length, width, thickness, base = 4.0, 1.2, 0.15, 0.15
    end_top = base + rise * (1.0 - (2.0 * 1.88 / length) ** 2)
    parts = [kit.mesh_object(bpy, "Footbridge Deck", *arched_strip(length, width, thickness, base, rise))]
    for number, (x, y) in enumerate(((-1.88, -0.52), (-1.88, 0.52), (1.88, -0.52), (1.88, 0.52)), 1):
        parts.append(kit.box_object(bpy, "Footbridge Post %d" % number, (0.12, 0.12, 1.0), (x, y, end_top + 0.5)))
    for number, y in enumerate((-0.52, 0.52), 1):
        parts.append(kit.mesh_object(bpy, "Footbridge Rail %d" % number,
                                     *arched_strip(length, 0.08, 0.08, base + 0.95, rise, y=y)))
    for obj in parts:
        kit.assign(obj, material)
    bpy.context.view_layer.update()


FOOTBRIDGE_REQUEST = ("a deck 4 m long and 1.2 m wide, four square posts and two rails, in a grey stone material.")


def footbridge_checks(rise, rail_height):
    """The checks of both footbridge minis; ``rise``: allowed arch of the deck, ``rail_height``: of a rail piece.

    The default cube sits at the origin too. The message does not say what to do with it, so it may stay or
    go: an untouched cube is looked past, and neither choice is checked.
    """
    built = {"built": True}
    shortest = 3.7   # The plan check's shortest footbridge: the deck is sampled within it, and every ray must meet it.
    return [
        {"kind": "plan", "long": [shortest, 4.6], "short": [1.1, 1.8], "centre": [0.0, 0.0], "radius": 0.4,
         "label": "a footbridge about 4 x 1.2 m at the origin"},
        {"kind": "slab", "size": [4.0, 1.2], "centre": [0.0, 0.0], "span": shortest - 0.24, "rise": list(rise),
         "label": "a deck 4 m long and 1.2 m wide, %s" % ("flat" if rise[1] < 0.08 else "slightly arched")},
        {"kind": "members", "shape": "post", "count": [4, 4], "label": "four square posts"},
        {"kind": "members", "shape": "rail", "count": [2, 2], "max_height": rail_height, "label": "two rails"},
        {"kind": "surface", "target": built, "all": True, "saturation": [0.0, 0.25], "value": [0.05, 0.7],
         "roughness": [0.35, 1.0], "label": "a grey stone material on all of it"},
    ]


def area_light(bpy, name, location, energy, size):
    data = bpy.data.lights.new(name, "AREA")
    data.energy, data.size = energy, size
    obj = bpy.data.objects.new(name, data)
    obj.location = location
    obj.rotation_euler = kit.look_at_rotation(location, (0.0, 0.0, 0.0))
    return kit.link(bpy, obj)


def camera_cosine(location):
    """Cosine of the angle at the origin between a point and the factory camera (a rim light's is well below 0)."""
    return (sum(a * b for a, b in zip(location, FACTORY_CAMERA))
            / (math.dist(location, (0, 0, 0)) * math.dist(FACTORY_CAMERA, (0, 0, 0))))
