# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bridge scenario (the pilot): a stone arch bridge over a river valley at golden hour.

Nine turns touch terrain modelling, an open-spandrel arch, arrays and mirrors,
emissive and glossy materials, scattering, a Sun and physical sky, a camera,
keyframed animation and render settings. ``build(bpy, upto)`` is the ground
truth: the references are rendered from it and every check below is derived
from the same constants (``scenario_specs.selfcheck`` proves each turn's checks
fail before that turn and pass after it).

Axes: the river runs along Y; the bridge spans X; Z is up; metres.
"""

import math

import oneshot_kit
import scenario_build_kit as kit

ID = "bridge"
TITLE = "Stone arch bridge at golden hour"
FRAME = 60   # References and end-of-session captures show this frame (the car mid-bridge).

# Terrain: flat banks for |x| >= BANK_X at height 0, a riverbed at BED_Z for |x| <= BED_X.
VALLEY_SIZE, VALLEY_BOTTOM, HILL_HEIGHT = (120.0, 80.0), -8.0, 5.0
BED_X, BANK_X, BED_Z = 5.0, 9.0, -4.0
WATER_Z, WATER_WIDTH = -3.0, 15.0
DECK_TOP, DECK_THICKNESS, DECK_LENGTH, DECK_WIDTH = 1.0, 0.6, 24.0, 5.0
ARCH_SPRING_Z, ARCH_SPAN_OUT, ARCH_SPAN_IN, ARCH_RISE_OUT, ARCH_RISE_IN, ARCH_WIDTH = -2.5, 8.0, 7.2, 2.9, 2.1, 4.4
PIER_X = (4.0, 5.5, 7.0)
RAIL_Y, RAIL_HEIGHT, POST_SPACING = 2.4, 1.0, 1.5
LAMP_X, LAMP_Y, LAMP_TOP = (-9.0, -4.5, 0.0, 4.5, 9.0), 2.05, 4.4
CAR_Y, CAR_START, CAR_END, CAR_FRAMES = -1.0, -10.5, 10.5, (1, 120)
SUN_ELEVATION, SUN_AZIMUTH = 5.0, -35.0
DECK_CAMERA = {"location": (-11.0, 1.4, 2.6), "target": (11.0, -0.6, 1.6), "lens": 24.0}

CAMERAS = {
    "front": {"location": (0.0, -60.0, -1.0), "target": (0.0, 0.0, -1.0), "ortho_scale": 34.0, "role": "reference",
              "label": "Front (orthographic, looking +Y)"},
    # The valley's hills hide the bridge from a level view along X, so the side view looks down from upstream.
    "side": {"location": (14.0, 22.0, 14.0), "target": (-1.0, 0.0, -1.5), "lens": 28.0, "role": "reference",
             "label": "Side (perspective, from upstream)"},
    "top": {"location": (0.0, 0.0, 60.0), "target": (0.0, 0.0, 0.0), "ortho_scale": 64.0, "role": "reference",
            "label": "Top (orthographic, +Y up)"},
    "hero": {"location": (-8.6, -26.0, 0.6), "target": (1.5, 0.0, 0.4), "lens": 24.0, "role": "reference",
             "label": "Hero (perspective)"},
    "closeup_railing": {"location": (-6.5, -8.0, 3.4), "target": (-2.0, -2.2, 2.1), "lens": 32.0, "role": "closeup",
                        "label": "Close-up: railing and lamps"},
    "closeup_river": {"location": (2.5, -16.0, -1.8), "target": (6.0, -6.0, -2.8), "lens": 30.0, "role": "closeup",
                      "label": "Close-up: water and rocks"},
    "closeup_car": {"location": (3.5, -8.5, 4.4), "target": (-0.5, -1.0, 1.6), "lens": 40.0, "role": "closeup",
                    "label": "Close-up: the car on the deck (frame 60)"},
}
SUBJECT = ({"prefix": "Bridge"}, {"prefix": "Railing"}, {"prefix": "Lamp"}, {"prefix": "Car"})

ROCKS = [  # (x, y, radius): along both water edges, deterministic.
    (-6.1, -16.5, 0.55), (-5.6, -11.0, 0.38), (-6.6, -7.2, 0.72), (-5.4, -3.8, 0.42), (-6.3, 5.5, 0.6),
    (-5.8, 9.8, 0.46), (-6.8, 14.2, 0.78), (-5.5, 17.6, 0.36), (6.0, -17.8, 0.5), (5.7, -13.2, 0.7),
    (6.5, -8.6, 0.44), (5.6, -5.0, 0.62), (6.2, 4.2, 0.4), (5.5, 8.4, 0.75), (6.6, 12.6, 0.48), (5.9, 16.8, 0.58),
]


def smoothstep(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


def terrain_height(x, y):
    """Valley height: a flat riverbed, smooth slopes, banks at 0 that roll up into low hills further out."""
    distance = abs(x)
    if distance <= BED_X:
        base = BED_Z
    elif distance >= BANK_X:
        base = 0.0
    else:
        base = BED_Z * (1.0 - smoothstep((distance - BED_X) / (BANK_X - BED_X)))
    hills = smoothstep((distance - 10.0) / 35.0) * (
        HILL_HEIGHT * (0.5 + 0.5 * math.sin(0.085 * x + 0.9)) * (0.6 + 0.4 * math.cos(0.061 * y + 0.4))
        + 0.5 * math.sin(0.19 * x + 0.6) * math.cos(0.15 * y))
    return base + hills


def arch_extrados(x):
    """Height of the arch's top surface at ``x`` (None outside the span)."""
    if abs(x) >= ARCH_SPAN_OUT:
        return None
    return ARCH_SPRING_Z + ARCH_RISE_OUT * math.sqrt(1.0 - (x / ARCH_SPAN_OUT) ** 2)


# ------------------------------------------------------------------ prompts and checks

def _probe(origin, direction, target, axis=None, value_range=None, expect="hit", label="", distance=100.0):
    check = {"kind": "probe", "origin": list(origin), "direction": list(direction), "target": target,
             "expect": expect, "label": label, "distance": distance}
    if axis:
        check.update(axis=axis, range=list(value_range))
    return check


# Names the prompts quote as names are matched exactly (Blender's .001 siblings count as the same name); names they
# say parts start with are prefixes.
VALLEY, RIVER = {"name": "Valley"}, {"name": "River"}
DECK, ARCH = {"name": "Bridge Deck"}, {"name": "Bridge Arch"}
PIERS, ABUTMENTS = {"name": "Bridge Piers"}, {"name": "Bridge Abutments"}
# "Delete the default Cube": the factory object named 'Cube' itself, not a later 'Cube.001'.
DEFAULT_CUBE = {"name": "Cube", "except": [{"prefix": "Cube."}]}
BRIDGE, RAILING, LAMP, ROCK, CAR = ({"prefix": p} for p in ("Bridge", "Railing", "Lamp", "Rock", "Car"))
ROCKS_A_SIDE = 8   # "Sixteen rocks ... eight a side" (the one-shot brief); "16 rocks ... eight per side" (step 5).

TURNS = [
    {"id": "valley", "title": "Valley banks", "references": ["front", "side", "top", "hero"],
     "prompt": "We are going to build the scene in the four attached reference images of the finished scene (an "
               "orthographic front view looking along +Y, a view from upstream, an orthographic top view with +Y up, "
               "and a hero shot) over several messages. Match the references as closely as you can. The river runs "
               "along Y, the bridge will span X, Z is up and units are metres.\n\n"
               "Step 1: delete the default Cube, Camera and Light. Then model the valley as one solid mesh object "
               "named 'Valley', like a diorama block: 120 m along X by 80 m along Y, with flat earth sides going down "
               "to a flat base at z = -8 m. Its top surface has a flat riverbed at z = -4 m where |x| <= 5 m, smooth "
               "earthy slopes, grassy banks at height 0 from |x| = 9 m that roll up into low hills (up to about 5 m) "
               "towards the outer edges. Give it a grass-and-earth material.",
     "checks": [
         {"kind": "object", "target": DEFAULT_CUBE, "exists": False, "label": "default cube removed"},
         {"kind": "object", "target": VALLEY, "type": "MESH", "label": "Valley exists"},
         {"kind": "bbox", "target": VALLEY, "size": [[112, 128], [74, 86], [9.0, 16.0]],
          "label": "Valley block 120 x 80 m"},
         {"kind": "bbox", "target": VALLEY, "min": [None, None, VALLEY_BOTTOM], "tolerance": 0.6,
          "label": "flat base at -8 m"},
         _probe((0, 0, 20), (0, 0, -1), VALLEY, "z", (BED_Z - 0.6, BED_Z + 0.6), label="riverbed at -4 m"),
         _probe((-16, 6, 20), (0, 0, -1), VALLEY, "z", (-0.7, 0.7), label="left bank near 0 m"),
         _probe((-52, -20, 30), (0, 0, -1), VALLEY, "z", (1.0, 7.0), label="hills towards the outer edge"),
         _probe((17, -8, 20), (0, 0, -1), VALLEY, "z", (-0.7, 0.7), label="right bank near 0 m"),
         _probe((7, 0, 20), (0, 0, -1), VALLEY, "z", (-3.6, -0.4), label="slope between bed and bank"),
     ]},
    {"id": "bridge", "title": "Arch bridge", "references": [],
     "prompt": "Step 2: build the stone bridge. Name every bridge part starting with 'Bridge'. 'Bridge Deck' is a "
               "box 24 m long (X, from -12 to 12), 5 m wide (Y) and 0.6 m thick whose top is at z = 1 m. 'Bridge Arch' "
               "is a solid half-elliptical stone ring under the deck, 4.4 m wide in Y, springing from z = -2.5 m: its "
               "outer curve spans x = -8 to 8 m and rises to the deck's underside (z = 0.4 m), its inner opening spans "
               "x = -7.2 to 7.2 m and rises to z = -0.4 m. Add 'Bridge Piers': thin stone walls (0.5 m thick in X, "
               "4.4 m in Y) standing on the arch and reaching the deck at x = +/-4, +/-5.5 and +/-7 m, and "
               "'Bridge Abutments': stone blocks under both deck ends from |x| = 9 to 12 m, from z = -1 m up to the "
               "deck. Use a warm grey stone material.",
     "checks": [
         {"kind": "bbox", "target": DECK, "min": [-12, -2.5, DECK_TOP - DECK_THICKNESS], "max": [12, 2.5, DECK_TOP],
          "tolerance": 0.3, "label": "deck 24 x 5 x 0.6 m, top at 1 m"},
         {"kind": "bbox", "target": ARCH, "min": [-8, -2.2, ARCH_SPRING_Z], "max": [8, 2.2, 0.4], "tolerance": 0.45,
          "label": "arch spans -8..8, springs at -2.5, crown at 0.4"},
         _probe((0, -20, -1.2), (0, 1, 0), BRIDGE, expect="miss", label="arch opening clear at x=0, z=-1.2"),
         _probe((0, 0, 10), (0, 0, -1), DECK, "z", (0.7, 1.15), label="deck top above the crown"),
         _probe((0, 0, 0.2), (0, 0, -1), ARCH, "z", (-0.75, 0.05), label="arch crown (intrados) under the deck"),
         _probe((-6.0, -20, -0.9), (0, 1, 0), ARCH, "y", (-2.6, -1.9), label="arch ring at x=-6"),
         _probe((5.5, -20, 0.0), (0, 1, 0), PIERS, "y", (-2.6, -1.9), label="pier at x=5.5"),
         _probe((10.5, -20, -0.5), (0, 1, 0), ABUTMENTS, "y", (-2.8, -1.9), label="abutment under the deck end"),
     ]},
    {"id": "railings", "title": "Railings", "references": ["closeup_railing"],
     "prompt": "Step 3: add iron railings along both long edges of the deck (see the attached close-up). Name them "
               "starting with 'Railing'. Square posts 0.1 m thick and 1 m tall stand on the deck at y = +/-2.4 m, "
               "every 1.5 m from x = -12 to 12 m (17 per side), with a top rail at 1 m above the deck and a middle rail "
               "at 0.5 m above the deck, both running the full 24 m. Use a dark iron material.",
     "checks": [
         _probe((-3.0, -10, DECK_TOP + 0.97), (0, 1, 0), RAILING, "y", (-2.55, -2.25), label="top rail, near side"),
         _probe((7.0, 10, DECK_TOP + 0.97), (0, -1, 0), RAILING, "y", (2.25, 2.55), label="top rail, far side"),
         _probe((-7.5, -10, DECK_TOP + 0.25), (0, 1, 0), RAILING, "y", (-2.55, -2.25), label="post at x=-7.5"),
         _probe((-6.75, -10, DECK_TOP + 0.25), (0, 1, 0), RAILING, expect="miss", label="gap between posts"),
         _probe((12.0, 10, DECK_TOP + 0.25), (0, -1, 0), RAILING, "y", (2.25, 2.55), label="end post at x=12"),
         _probe((2.25, -10, DECK_TOP + 0.5), (0, 1, 0), RAILING, "y", (-2.55, -2.25), label="middle rail"),
         {"kind": "bbox", "target": RAILING, "min": [-12.1, -2.5, DECK_TOP], "max": [12.1, 2.5, DECK_TOP + 1.0],
          "tolerance": 0.2, "label": "railings span the deck"},
     ]},
    {"id": "lamps", "title": "Lamp array", "references": [],
     "prompt": "Step 4: line the deck with street lamps, named starting with 'Lamp'. Each lamp is a slim post 0.16 m "
               "across rising from the deck to z = 4.4 m, topped by a glowing sphere lantern 0.4 m across. Place them at "
               "y = +/-2.05 m, just inside the railings, at x = -9, -4.5, 0, 4.5 and 9 m (10 lamps). The lanterns "
               "glow warm orange (an emission material, strength about 4).",
     "checks": [
         *[_probe((x, 10, 3.0), (0, -1, 0), LAMP, "y", (1.8, 2.3), label="lamp post x=%g (+Y side)" % x)
           for x in LAMP_X],
         _probe((-4.5, -10, 3.0), (0, 1, 0), LAMP, "y", (-2.3, -1.8), label="lamp post x=-4.5 (-Y side)"),
         _probe((6.75, 10, 3.0), (0, -1, 0), LAMP, expect="miss", label="no lamp between x=4.5 and 9"),
         _probe((0, 2.05, 10), (0, 0, -1), LAMP, "z", (4.4, 4.95), label="lantern on top at x=0"),
         {"kind": "material", "target": LAMP, "emission_min": 2.0, "emission_color": ["warm"],
          "label": "warm emissive lanterns"},
     ]},
    {"id": "water_rocks", "title": "Water and rocks", "references": ["closeup_river"],
     "prompt": "Step 5: fill the river. Add a flat water surface named 'River' at z = -3 m, 15 m wide in X (centred) "
               "and 40 m long in Y, with a dark teal, glossy material (roughness 0.1 or less). Then scatter 16 rocks "
               "named starting with 'Rock' along both water edges, eight per side, 4.5 to 8 m from the river's centre "
               "line in X and spread from y = -18 to 18 m, each 0.7 to 1.6 m across and partly sunk into the bank "
               "(see the close-up).",
     "checks": [
         {"kind": "bbox", "target": RIVER, "min": [-7.5, -20, WATER_Z], "max": [7.5, 20, WATER_Z], "tolerance": 1.0,
          "label": "river 15 x 40 m at -3 m"},
         _probe((0, 0, 10), (0, 0, -1), RIVER, "z", (WATER_Z - 0.15, WATER_Z + 0.15), label="water level -3 m"),
         {"kind": "material", "target": RIVER, "color": ["blue", "green"], "roughness_max": 0.15,
          "label": "teal glossy water"},
         {"kind": "parts", "target": ROCK, "region_min": [-8.6, -20, -5], "region_max": [-4.0, 20, 1.5],
          "count": [ROCKS_A_SIDE, ROCKS_A_SIDE], "size": [0.4, 2.2], "label": "rocks along the left edge"},
         {"kind": "parts", "target": ROCK, "region_min": [4.0, -20, -5], "region_max": [8.6, 20, 1.5],
          "count": [ROCKS_A_SIDE, ROCKS_A_SIDE], "size": [0.4, 2.2], "label": "rocks along the right edge"},
     ]},
    {"id": "golden_hour", "title": "Golden-hour light", "references": [],
     "prompt": "Step 6: light it for golden hour. Add a Sun light named 'Sun' low in the sky, about 5 degrees above "
               "the horizon, shining from the +X and -Y side (azimuth about -35 degrees, measured from +X towards +Y), "
               "with a warm orange colour and strength around 7, so it lights the bridge's downstream face and casts "
               "long shadows. Give the world a matching hazy physical sky (or a warm sky colour) so the scene glows "
               "like the hero reference.",
     "checks": [
         {"kind": "light", "target": {"name": "Sun"}, "type": "SUN", "elevation_deg": [4, 18], "color": ["warm"],
          "energy": [1.0, 12.0], "label": "low warm Sun named 'Sun'"},
         {"kind": "world", "sky_or_color": ["warm"], "sun_elevation_deg": [0, 25], "label": "golden sky"},
     ]},
    {"id": "deck_camera", "title": "Deck camera", "references": [],
     "prompt": "Step 7: add a camera named 'Deck Camera' standing on the deck at (-11, 1.4, 2.6), looking along the "
               "bridge towards (11, -0.6, 1.6), with a 24 mm lens, and make it the scene's active camera.",
     "checks": [
         {"kind": "camera", "target": {"name": "Deck Camera"}, "active": True, "location": list(DECK_CAMERA["location"]),
          "radius": 0.5, "look_at": list(DECK_CAMERA["target"]), "min_dot": 0.98, "lens": [23.0, 25.0],
          "label": "Deck Camera placed, aimed and active"},
     ]},
    {"id": "car_animation", "title": "Car crossing", "references": ["closeup_car"],
     "prompt": "Step 8: add a small red car and animate it crossing the bridge. Name its parts starting with 'Car' "
               "(body, cabin and four wheels; the wheels touch the deck). It is about 4 m long, 1.8 m wide and 1.7 m "
               "tall and drives in the lane at y = -1 m along +X: keyframe it from x = -10.5 m at frame 1 to "
               "x = 10.5 m at frame 120 so it is centred on the bridge around frame 60. Set the scene's frame range to "
               "1-120.",
     "checks": [
         {"kind": "motion", "target": CAR, "tolerance": 0.7, "z_tolerance": 0.2,
          "frames": {1: {"centre": [CAR_START, CAR_Y, None], "min_z": DECK_TOP},
                     60: {"centre": [0.0, CAR_Y, None], "min_z": DECK_TOP},
                     120: {"centre": [CAR_END, CAR_Y, None], "min_z": DECK_TOP}},
          "label": "car drives -10.5 -> 10.5 m on the deck"},
         {"kind": "bbox", "target": CAR, "size": [[3.3, 4.8], [1.4, 2.2], [1.2, 2.2]], "label": "car about 4 x 1.8 x 1.7 m"},
         {"kind": "material", "target": CAR, "color": ["warm"], "label": "red car"},
         {"kind": "scene", "values": {"frame_start": 1, "frame_end": 120}, "label": "frame range 1-120"},
     ]},
    {"id": "render_setup", "title": "1080p render setup", "references": [],
     "prompt": "Step 9: set up the final render: 1920 x 1080 at 100 %, 24 fps, frames 1-120, the EEVEE engine with "
               "128 render samples, ray tracing and motion blur on, the AgX view transform, and 'Deck Camera' as the "
               "render camera.",
     "checks": [
         {"kind": "scene", "values": {"render.resolution_x": 1920, "render.resolution_y": 1080,
                                      "render.resolution_percentage": 100, "render.fps": 24, "frame_start": 1,
                                      "frame_end": 120, "render.engine": "BLENDER_EEVEE",
                                      "eevee.taa_render_samples": 128, "eevee.use_raytracing": True,
                                      "render.use_motion_blur": True, "view_settings.view_transform": "AgX",
                                      "camera": "Deck Camera"}, "label": "1080p EEVEE setup"},
     ]},
]


# ------------------------------------------------------------------ ground truth

def _valley(bpy, data):
    oneshot_kit.clear_default_scene(bpy)
    sx, sy = VALLEY_SIZE
    xs = kit.graded_samples(-sx / 2, sx / 2, -14.0, 14.0, 0.5, 2.0)
    ys = kit.graded_samples(-sy / 2, sy / 2, 0.0, 0.0, 1.0, 1.0)
    valley = kit.heightfield_block(bpy, "Valley", xs, ys, terrain_height, VALLEY_BOTTOM)
    kit.assign(valley, kit.height_ramp_material(bpy, "Valley Grass", [
        (VALLEY_BOTTOM, (0.16, 0.10, 0.06)), (-4.2, (0.11, 0.08, 0.055)), (-3.0, (0.2, 0.13, 0.075)),
        (-1.3, (0.27, 0.18, 0.1)), (-0.4, (0.19, 0.22, 0.07)), (0.8, (0.22, 0.28, 0.08)),
        (5.0, (0.3, 0.32, 0.12))], roughness=0.92))


def _arch_ring(segments=48):
    vertices, faces = [], []
    half = ARCH_WIDTH / 2
    for i in range(segments + 1):
        theta = math.pi * i / segments
        for a, b in ((ARCH_SPAN_OUT, ARCH_RISE_OUT), (ARCH_SPAN_IN, ARCH_RISE_IN)):
            for y in (-half, half):
                vertices.append((a * math.cos(theta), y, ARCH_SPRING_Z + b * math.sin(theta)))
    # Per step i: outer(-y)=4i, outer(+y)=4i+1, inner(-y)=4i+2, inner(+y)=4i+3.
    for i in range(segments):
        o0, o1, n0, n1 = 4 * i, 4 * i + 1, 4 * i + 2, 4 * i + 3
        p0, p1, q0, q1 = o0 + 4, o1 + 4, n0 + 4, n1 + 4
        faces += [(o0, p0, p1, o1), (n0, n1, q1, q0), (o0, n0, q0, p0), (o1, p1, q1, n1)]
    last = 4 * segments
    faces += [(0, 1, 3, 2), (last, last + 2, last + 3, last + 1)]
    return vertices, faces


def _bridge(bpy, data):
    stone = kit.principled(bpy, "Bridge Stone", (0.42, 0.37, 0.31), roughness=0.82)
    deck = kit.box_object(bpy, "Bridge Deck", (DECK_LENGTH, DECK_WIDTH, DECK_THICKNESS),
                          (0.0, 0.0, DECK_TOP - DECK_THICKNESS / 2))
    bevel = deck.modifiers.new("Bevel", "BEVEL")
    bevel.width, bevel.segments = 0.04, 2
    arch = kit.mesh_object(bpy, "Bridge Arch", *_arch_ring())
    piers = []
    for x in PIER_X:
        bottom = arch_extrados(x + 0.25) - 0.15
        top = DECK_TOP - DECK_THICKNESS
        piers.append(((0.5, ARCH_WIDTH, top - bottom), (x, 0.0, (top + bottom) / 2)))
    pier = kit.mesh_object(bpy, "Bridge Piers", *kit.merged_boxes(piers))
    mirror = pier.modifiers.new("Mirror", "MIRROR")
    mirror.use_axis = (True, False, False)
    abutment = kit.mesh_object(bpy, "Bridge Abutments", *kit.merged_boxes(
        [((3.0, DECK_WIDTH - 0.2, DECK_TOP - DECK_THICKNESS + 1.0), (10.5, 0.0, (DECK_TOP - DECK_THICKNESS - 1.0) / 2))]))
    abutment.modifiers.new("Mirror", "MIRROR").use_axis = (True, False, False)
    for obj in (deck, arch, pier, abutment):
        kit.assign(obj, stone)


def _railings(bpy, data):
    iron = kit.principled(bpy, "Railing Iron", (0.03, 0.03, 0.032), roughness=0.4, metallic=0.85)
    # Origins stay at the world origin so the Mirror modifiers reflect across the deck's centre line.
    post = kit.mesh_object(bpy, "Railing Posts", *kit.box_geometry((0.1, 0.1, RAIL_HEIGHT),
                                                                    (-12.0, -RAIL_Y, DECK_TOP + RAIL_HEIGHT / 2)))
    array = post.modifiers.new("Array", "ARRAY")
    array.count, array.use_relative_offset, array.use_constant_offset = 17, False, True
    array.constant_offset_displace = (POST_SPACING, 0.0, 0.0)
    post.modifiers.new("Mirror", "MIRROR").use_axis = (False, True, False)
    rails = kit.mesh_object(bpy, "Railing Rails", *kit.merged_boxes([
        ((24.1, 0.08, 0.06), (0.0, -RAIL_Y, DECK_TOP + RAIL_HEIGHT - 0.03)),
        ((24.1, 0.06, 0.05), (0.0, -RAIL_Y, DECK_TOP + RAIL_HEIGHT / 2))]))
    rails.modifiers.new("Mirror", "MIRROR").use_axis = (False, True, False)
    for obj in (post, rails):
        kit.assign(obj, iron)


def _lamps(bpy, data):
    iron = bpy.data.materials.get("Railing Iron") or kit.principled(bpy, "Lamp Iron", (0.03, 0.03, 0.032), 0.4, 0.85)
    glow = kit.principled(bpy, "Lamp Glow", (1.0, 0.75, 0.45), roughness=0.3, emission=(1.0, 0.55, 0.22),
                          emission_strength=4.0)
    height = LAMP_TOP - DECK_TOP
    vertices, faces = kit.cylinder_geometry(0.08, height, 12, centre=(LAMP_X[0], -LAMP_Y, DECK_TOP + height / 2))
    cap, cap_faces = kit.cylinder_geometry(0.13, 0.08, 12, centre=(LAMP_X[0], -LAMP_Y, LAMP_TOP), offset=len(vertices))
    post = kit.mesh_object(bpy, "Lamp Posts", vertices + cap, faces + cap_faces)
    head = kit.mesh_object(bpy, "Lamp Lanterns", *kit.uv_sphere_geometry(0.2, 16, 8, centre=(LAMP_X[0], -LAMP_Y,
                                                                                               LAMP_TOP + 0.2)), smooth=True)
    for obj, material in ((post, iron), (head, glow)):
        kit.assign(obj, material)
        array = obj.modifiers.new("Array", "ARRAY")
        array.count, array.use_relative_offset, array.use_constant_offset = len(LAMP_X), False, True
        array.constant_offset_displace = (LAMP_X[1] - LAMP_X[0], 0.0, 0.0)
        obj.modifiers.new("Mirror", "MIRROR").use_axis = (False, True, False)


def _water_rocks(bpy, data):
    water = kit.mesh_object(bpy, "River", [(-WATER_WIDTH / 2, -20, 0), (WATER_WIDTH / 2, -20, 0),
                                           (WATER_WIDTH / 2, 20, 0), (-WATER_WIDTH / 2, 20, 0)], [(0, 1, 2, 3)],
                            location=(0.0, 0.0, WATER_Z))
    kit.assign(water, kit.principled(bpy, "River Water", (0.012, 0.06, 0.065), roughness=0.04, specular=0.6))
    stone = kit.principled(bpy, "Rock Stone", (0.24, 0.22, 0.2), roughness=0.88)
    for index, (x, y, radius) in enumerate(ROCKS):
        vertices, faces = kit.rock_geometry(radius, seed=index + 7)
        ground = terrain_height(x, y)
        rock = kit.mesh_object(bpy, "Rock %02d" % (index + 1), vertices, faces,
                               location=(x, y, max(ground, WATER_Z - 0.2) + radius * 0.25), smooth=True)
        rock.rotation_euler = (0.0, 0.0, 0.7 * index)
        kit.assign(rock, stone)


def _golden_hour(bpy, data):
    kit.sun_object(bpy, "Sun", SUN_ELEVATION, SUN_AZIMUTH, (1.0, 0.42, 0.16), 7.0, angle_deg=1.2)
    bpy.context.scene.world = kit.sky_world(bpy, "Golden Sky", SUN_ELEVATION, SUN_AZIMUTH, strength=0.3, aerosol=6.0)


def _deck_camera(bpy, data):
    camera = kit.camera_object(bpy, "Deck Camera", DECK_CAMERA["location"], DECK_CAMERA["target"], DECK_CAMERA["lens"])
    bpy.context.scene.camera = camera


def _car_animation(bpy, data):
    scene = bpy.context.scene
    paint = kit.principled(bpy, "Car Paint", (0.55, 0.03, 0.02), roughness=0.25, metallic=0.5)
    glass = kit.principled(bpy, "Car Glass", (0.02, 0.025, 0.03), roughness=0.05)
    rubber = kit.principled(bpy, "Car Rubber", (0.015, 0.015, 0.015), roughness=0.7)
    wheel_radius, base = 0.34, DECK_TOP
    body = kit.mesh_object(bpy, "Car", *kit.box_geometry((4.0, 1.8, 0.62), (0.0, 0.0, wheel_radius + 0.36)),
                           location=(CAR_START, CAR_Y, base))
    body.modifiers.new("Bevel", "BEVEL").width = 0.08
    kit.assign(body, paint)
    cabin = kit.mesh_object(bpy, "Car Cabin", *kit.box_geometry((2.1, 1.6, 0.62), (-0.25, 0.0, wheel_radius + 0.98)))
    cabin.modifiers.new("Bevel", "BEVEL").width = 0.1
    kit.assign(cabin, glass)
    cabin.parent = body
    for label, x, y in (("FL", 1.3, 0.82), ("FR", 1.3, -0.82), ("RL", -1.3, 0.82), ("RR", -1.3, -0.82)):
        wheel = kit.mesh_object(bpy, "Car Wheel " + label,
                                *kit.cylinder_geometry(wheel_radius, 0.24, 16, centre=(x, y, wheel_radius), axis="Y"))
        kit.assign(wheel, rubber)
        wheel.parent = body
    kit.keyframes(body, "location", {CAR_FRAMES[0]: (CAR_START, CAR_Y, base), CAR_FRAMES[1]: (CAR_END, CAR_Y, base)},
                  interpolation="LINEAR")
    scene.frame_start, scene.frame_end = CAR_FRAMES


def _render_setup(bpy, data):
    scene = bpy.context.scene
    render = scene.render
    render.resolution_x, render.resolution_y, render.resolution_percentage = 1920, 1080, 100
    render.fps, render.fps_base = 24, 1.0
    render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = 128
    scene.eevee.use_raytracing = True
    render.use_motion_blur = True
    scene.frame_start, scene.frame_end = CAR_FRAMES
    scene.view_settings.view_transform = "AgX"
    scene.camera = bpy.data.objects["Deck Camera"]


BUILDERS = {"valley": _valley, "bridge": _bridge, "railings": _railings, "lamps": _lamps,
            "water_rocks": _water_rocks, "golden_hour": _golden_hour, "deck_camera": _deck_camera,
            "car_animation": _car_animation, "render_setup": _render_setup}


def build_turn(bpy, turn_id):
    """Apply one turn of the ground truth to the current scene (what a perfect agent would do)."""
    BUILDERS[turn_id](bpy, None)
    bpy.context.view_layer.update()


def build(bpy, upto=None):
    """The ground-truth scene up to and including turn ``upto`` (all turns by default)."""
    for turn in TURNS:
        build_turn(bpy, turn["id"])
        if turn["id"] == upto:
            break
