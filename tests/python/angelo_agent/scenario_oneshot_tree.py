# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot test ``oneshot-tree``: one believable broadleaf tree, built and rendered from one message.

The brief attaches one picture: the broadleaf tree cut from the maintainer's
reference painting (``SOURCE_IMAGES``). It fixes what must be checkable (the
tree's size, a clear trunk, limbs that fork and spread, foliage that is a few
thousand separate leaves and not a ball, the bark and leaf colours, the sun,
the camera and the output) and leaves the making to the model: the brief
names no mechanism for the leaves, because the route is what is measured.

``build`` is a stand-in, not a target look (``GROUND_TRUTH``): the plainest
tree that follows the brief. The checks are derived from its constants and
hold for any build that follows the brief, however it is made.

Axes: the tree is rooted at the origin; Z is up; metres.
"""

import math

import oneshot_kit
import scenario_build_kit as kit

ID = "oneshot_tree"
TITLE = "A broadleaf tree on a grassy rise"
FRAME = 1
FINAL_VIEW = "hero"
FINAL_STILL = {"name": "tree-final.png", "size": (2560, 1440)}
GROUND_TRUTH = "stand-in"
LIMITS = {"wall_s": 2400.0, "job_wait_s": 900.0, "cost_usd": 25.0}
APPROVABLE = ("angelo_job_start",)
JOB_WAIT_S = LIMITS["job_wait_s"]

HEIGHT, SEED, LEAVES = 6.3, 7, 3200
RISE_ACROSS, RISE_DROP, GROUND_BOTTOM = 16.0, 1.1, -2.5
SUN_ELEVATION, SUN_AZIMUTH = 40.0, -135.0            # From the camera's left: the -X, -Y side.
HERO = {"location": (10.5, -10.5, 2.6), "target": (0.0, 0.0, 3.3), "lens": 35.0}

# The one picture the brief attaches: the tree left of the cottage in the maintainer's reference painting.
SOURCE_IMAGES = {"picture": {
    "file": "maintainer-reference-2026-10-11.png",
    "sha256": "7aa30417d806ee899c03ffc575a4e2f3220986888372fd365f613ba18604dc20",
    "box": (520, 235, 660, 365), "scale": 4, "label": "The kind of tree (cut from the reference painting)",
    "origin": "the maintainer's reference painting of a floating island, 2026-10-11",
}}
CAMERAS = {
    "hero": dict(HERO, role="reference", label="Hero (the scene camera)"),
    "front": {"location": (0.0, -30.0, 3.2), "target": (0.0, 0.0, 3.2), "ortho_scale": 9.0, "role": "reference",
              "label": "Front elevation (orthographic, looking +Y)"},
    "side": {"location": (-30.0, 0.0, 3.2), "target": (0.0, 0.0, 3.2), "ortho_scale": 9.0, "role": "reference",
             "label": "Side elevation (orthographic, looking +X)"},
    "top": {"location": (0.0, 0.0, 30.0), "target": (0.0, 0.0, 0.0), "ortho_scale": 9.0, "role": "reference",
            "label": "Plan (orthographic, +Y up)"},
    "closeup_crown": {"location": (4.6, -4.6, 3.0), "target": (0.0, 0.0, 4.4), "lens": 40.0, "role": "closeup",
                      "label": "Close-up: limbs and leaves"},
}
# The brief quotes 'Tree Trunk' and 'Tree Foliage' as names to use exactly (Blender's .001 siblings count as the
# same name); 'Tree' and 'Tree Branch' are what names start with.
GROUND, TREE = {"name": "Ground"}, {"prefix": "Tree"}
TRUNK, BRANCH, FOLIAGE = {"name": "Tree Trunk"}, {"prefix": "Tree Branch"}, {"name": "Tree Foliage"}
LIMB_OR_LEAF = {"any": [BRANCH, FOLIAGE]}
SUBJECT = (TREE,)

PROMPT = (
    "One believable broadleaf tree, standing alone on a grassy rise on a bright summer morning. Build it and "
    "render one still. The attached picture shows the kind of tree I mean: a slender trunk that forks into a "
    "few rising limbs, and an open, airy crown of leaf masses with sky showing between them. It is cut from a "
    "larger painting, so ignore what stands around the tree; where the picture and this brief disagree, the "
    "brief wins.\n\n"
    "Conventions. Metres, Z up. The tree is rooted at the origin and stands upright. I find things by name, so "
    "name them exactly as written here and give anything else you add a meaningful name of its own. Keep the "
    "file tidy: nothing from the default scene (its cube, light or camera) remains, no unused leftovers, and a "
    "material on everything that shows.\n\n"
    "The tree. Between 5.5 and 7 m tall with a crown 4 to 6 m across; every part's name starts with 'Tree'. "
    "'Tree Trunk' is 0.35 to 0.5 m thick at the base, tapers, is a little irregular, and stays clear of "
    "branches and leaves for the first 1.8 m. The limbs and branches, their names starting with 'Tree Branch': "
    "at least five main limbs rise and spread from the fork and divide into thinner branches that carry the "
    "leaves, and some of them show through the crown. 'Tree Foliage' is the leaves: a few thousand leaves or "
    "leaf clusters, gathered into masses at different heights with gaps between them, never one solid ball. "
    "Bark: brown to grey-brown, rough. Leaves: a fresh mid green.\n\n"
    "The ground. One mesh named 'Ground': a grassy rise about 16 m across whose top is at height 0 where the "
    "tree stands and which falls gently away from it.\n\n"
    "Light. A sun light named 'Sun', 35 to 45 degrees above the horizon, coming from the camera's left so that "
    "light and shade model the crown and the tree throws a clear shadow on the grass, under a clear blue "
    "sky.\n\n"
    "Camera. A camera named 'Hero Camera' at (10.5, -10.5, 2.6), looking at (0, 0, 3.3) with a 35 mm lens, as "
    "the scene's camera: the whole tree with some ground and sky around it.\n\n"
    "Output. EEVEE with ray tracing on, 2560 x 1440 at 100 %. Render the still from 'Hero Camera' and save it "
    "as a PNG named 'tree-final.png'; tell me where it is.\n\n"
    "Before you call it done, look at your render next to the picture and fix what is wrong.")


def _probe(origin, direction, target, axis=None, value_range=None, expect="hit", label="", distance=40.0):
    check = {"kind": "probe", "origin": list(origin), "direction": list(direction), "target": target,
             "expect": expect, "label": label, "distance": distance}
    if axis:
        check.update(axis=axis, range=list(value_range))
    return check


def _trunk_probes(height, low, high, label):
    """From 1 m away on four sides, the trunk's own surface is ``low`` to ``high`` m from the axis: the trunk is
    there and about that thick. Other parts named 'Tree' (a root flare, ivy) are looked through."""
    sides = (((-1, 0), (1, 0), "x", (-high, -low)), ((1, 0), (-1, 0), "x", (low, high)),
             ((0, -1), (0, 1), "y", (-high, -low)), ((0, 1), (0, -1), "y", (low, high)))
    return [_probe((x, y, height), (dx, dy, 0), TRUNK, axis, limits, label="%s (from %s%s)" % (
        label, "-" if x + y < 0 else "+", axis.upper()), distance=2.0) for (x, y), (dx, dy), axis, limits in sides]


def _clear_probes(height):
    """The brief's "stays clear of branches and leaves for the first 1.8 m": no branch or leaf within 1 m of the
    axis at this height, along X and along Y (each ray crosses the whole 2 m through the trunk)."""
    return [_probe(origin, direction, LIMB_OR_LEAF, expect="miss", distance=2.0,
                   label="no branch or leaf within 1 m of the trunk at %g m (along %s)" % (height, axis))
            for origin, direction, axis in (((-1, 0, height), (1, 0, 0), "X"), ((0, -1, height), (0, 1, 0), "Y"))]


CHECKS = [
    {"kind": "hygiene", "absent": ["Cube", "Light", "Camera"], "max_default_named": 0, "max_without_material": 0,
     "max_unused": 0, "label": "tidy file: no default leftovers, named, materials"},
    {"kind": "object", "target": TRUNK, "label": "Tree Trunk exists"},
    *_trunk_probes(0.4, 0.10, 0.42, "trunk 0.35 to 0.5 m thick near the base"),
    *_trunk_probes(1.2, 0.05, 0.36, "the trunk at 1.2 m"),
    *_trunk_probes(1.65, 0.04, 0.36, "the trunk at 1.65 m"),
    *[probe for height in (0.4, 1.2, 1.65) for probe in _clear_probes(height)],
    {"kind": "bbox", "target": TREE, "min": [None, None, 0.0], "max": [None, None, 6.25], "tolerance": 0.95,
     "label": "rooted at the ground, 5.5 to 7 m tall"},
    {"kind": "object", "target": BRANCH, "label": "limbs named 'Tree Branch'"},
    {"kind": "bbox", "target": BRANCH, "size": [[2.0, 7.0], [2.0, 7.0], [1.5, 6.5]],
     "label": "the limbs rise and spread"},
    # Rays run up a cylinder 0.45 m around the trunk's axis: a limb that spreads has to cross it, at whatever
    # height it leaves. Wood of either name counts (the fork may belong to the trunk object). Close to the
    # axis, so that a limb's own side branches, which leave it further out, are not counted as limbs. Two
    # limbs crossing at overlapping angles merge for the rays, so separate pieces of wood that grow out of
    # the trunk (start within its largest radius the brief allows, 0.25 m) and spread past it count too.
    {"kind": "ring", "targets": [TRUNK, BRANCH], "centre": [0.0, 0.0], "radii": [0.45], "from": 1.7, "to": 7.5,
     "rays": 180, "count": [5, 400], "core": 0.25, "label": "at least five limbs spread from the fork"},
    {"kind": "object", "target": FOLIAGE, "label": "Tree Foliage exists"},
    {"kind": "bbox", "target": FOLIAGE, "size": [[3.6, 6.6], [3.6, 6.6], [1.5, 7.5]],
     "label": "a crown 4 to 6 m across, at different heights"},
    # However they are made: leaf cards in one mesh, instances of a leaf, or leaf objects. Not a few blobs.
    {"kind": "pieces", "target": FOLIAGE, "count": [1500, 5000000],
     "label": "foliage of a few thousand separate leaves or clusters"},
    {"kind": "surface", "target": TRUNK, "color": ["warm", "grey"], "roughness": [0.5, 1.0],
     "label": "brown to grey-brown, rough bark"},
    {"kind": "surface", "target": FOLIAGE, "color": ["green"], "label": "green leaves"},
    {"kind": "object", "target": GROUND, "type": "MESH", "label": "Ground exists"},
    {"kind": "bbox", "target": GROUND, "size": [[12, 22], [12, 22], None], "label": "a rise about 16 m across"},
    # From under the crown: a ray through a dense crown would spend itself on leaves before it met the ground.
    _probe((1.2, 1.2, 1.5), (0, 0, -1), GROUND, "z", (-0.35, 0.25), label="the top is at height 0 by the tree",
           distance=6.0),
    _probe((5.0, 0, 10), (0, 0, -1), GROUND, "z", (-3.0, -0.05), label="the ground falls away (towards +X)"),
    _probe((-3.5, 3.5, 10), (0, 0, -1), GROUND, "z", (-3.0, -0.05), label="the ground falls away (towards -X, +Y)"),
    {"kind": "surface", "target": GROUND, "color": ["green"], "label": "grass"},
    {"kind": "light", "target": {"name": "Sun"}, "type": "SUN", "elevation_deg": [30, 50],
     "label": "a Sun 35 to 45 degrees up"},
    {"kind": "light_from", "target": {"name": "Sun"}, "azimuth_deg": 225.0, "within_deg": 60.0,
     "label": "the sun comes from the camera's left"},
    {"kind": "world", "sky_or_color": ["blue"], "sun_elevation_deg": [20, 60], "label": "a clear blue sky"},
    {"kind": "camera", "target": {"name": "Hero Camera"}, "active": True, "location": list(HERO["location"]),
     "radius": 0.5, "look_at": list(HERO["target"]), "min_dot": 0.99, "lens": [33.0, 37.0],
     "label": "Hero Camera placed, aimed and active"},
    {"kind": "scene", "values": {"render.engine": "BLENDER_EEVEE", "render.resolution_x": 2560,
                                 "render.resolution_y": 1440, "render.resolution_percentage": 100,
                                 "eevee.use_raytracing": True}, "label": "EEVEE, ray tracing, 2560 x 1440"},
]

TURNS = [{
    "id": "brief", "title": "The tree and its final still",
    "references": ["picture"], "prompt": PROMPT, "checks": CHECKS,
    "outputs": [{"name": FINAL_STILL["name"], "min_size": list(FINAL_STILL["size"]),
                 "label": "the final still was rendered and saved (2560 x 1440 or larger)"}],
    "job": {"kind": "RENDER_STILL", "output": "renders/" + FINAL_STILL["name"],
            "resolution": list(FINAL_STILL["size"]), "samples": 16, "format": "PNG", "minutes": 10},
}]


# ------------------------------------------------------------------ the stand-in

def rise_height(x, y):
    """Height 0 where the tree stands, falling smoothly to ``-RISE_DROP`` about 9 m out."""
    t = min(1.0, math.hypot(x, y) / 9.0)
    return -RISE_DROP * t * t * (3.0 - 2.0 * t)


def build_turn(bpy, turn_id):
    """The whole stand-in (the brief is one turn): the plainest scene that follows the brief."""
    if turn_id != "brief":
        raise KeyError(turn_id)
    oneshot_kit.clear_default_scene(bpy)
    scene = bpy.context.scene
    half = RISE_ACROSS / 2
    samples = [-half + index * 0.5 for index in range(int(RISE_ACROSS / 0.5) + 1)]
    ground = kit.heightfield_block(bpy, "Ground", samples, samples, rise_height, GROUND_BOTTOM)
    kit.assign(ground, kit.principled(bpy, "Ground Grass", (0.13, 0.26, 0.06), roughness=0.92))
    oneshot_kit.tree(bpy, "Tree", (0.0, 0.0, 0.0), HEIGHT, SEED, LEAVES)
    kit.sun_object(bpy, "Sun", SUN_ELEVATION, SUN_AZIMUTH, (1.0, 0.96, 0.9), 4.0, angle_deg=1.0)
    scene.world = kit.sky_world(bpy, "Morning Sky", SUN_ELEVATION, SUN_AZIMUTH, strength=0.5)
    scene.camera = kit.camera_object(bpy, "Hero Camera", HERO["location"], HERO["target"], HERO["lens"])
    render = scene.render
    render.engine = "BLENDER_EEVEE"
    render.resolution_x, render.resolution_y, render.resolution_percentage = *FINAL_STILL["size"], 100
    scene.eevee.use_raytracing = True
    bpy.context.view_layer.update()


def build(bpy, upto=None):
    build_turn(bpy, "brief")
