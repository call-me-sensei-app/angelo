# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot test ``oneshot-footbridge``: a timber footbridge over a stream, built and rendered from one message.

The brief fixes what must be checkable (scale, the named ingredients, the
materials' character, the sun, the camera and the output) and leaves the
design to the model. ``build`` is the ground truth the four reference
pictures are rendered from; every check below is derived from its constants
and holds for any build that follows the brief, however it is made.

Axes: the stream runs along Y through the origin; the bridge spans X; Z is up; metres.
"""

import math

import oneshot_kit
import scenario_build_kit as kit

ID = "oneshot_footbridge"
TITLE = "Timber footbridge over a stream"
FRAME = 1
FINAL_VIEW = "hero"
FINAL_STILL = {"name": "footbridge-final.png", "size": (2560, 1440)}
GROUND_TRUTH = "reference"
LIMITS = {"wall_s": 2400.0, "job_wait_s": 900.0, "cost_usd": 25.0}
APPROVABLE = ("angelo_job_start",)
JOB_WAIT_S = LIMITS["job_wait_s"]

GROUND_SIZE, GROUND_BOTTOM = (48.0, 40.0), -2.0
BED_HALF, BANK_HALF, BED_Z, WATER_Z = 1.2, 2.0, -0.9, -0.45
DECK_LENGTH, DECK_WIDTH, DECK_TOP, PLANKS = 6.0, 1.4, 0.3, 40
RAIL_TOP, RAIL_Y, POST_X = 1.3, 0.655, (-2.85, -1.425, 0.0, 1.425, 2.85)
SUN_ELEVATION, SUN_AZIMUTH = 25.0, -125.0
HERO = {"location": (4.2, -5.6, 3.6), "target": (0.0, 0.0, 0.3), "lens": 35.0}
STONES = [  # (x, y, radius)
    (-1.45, -4.6, 0.45), (1.35, -3.1, 0.32), (-0.4, -2.3, 0.26), (1.6, 2.4, 0.4), (-1.55, 3.6, 0.3),
    (0.5, 5.2, 0.36), (-0.9, 6.3, 0.24), (1.5, -6.4, 0.38), (-1.3, 8.2, 0.33),
]

CAMERAS = {
    "front": {"location": (0.0, -30.0, 0.4), "target": (0.0, 0.0, 0.4), "ortho_scale": 11.0, "role": "reference",
              "label": "Front elevation (orthographic, looking +Y)"},
    "side": {"location": (-9.0, -5.0, 3.2), "target": (0.0, 0.5, 0.3), "lens": 35.0, "role": "reference",
             "label": "From the left bank (perspective)"},
    "top": {"location": (0.0, 0.0, 30.0), "target": (0.0, 0.0, 0.0), "ortho_scale": 13.0, "role": "reference",
            "label": "Plan (orthographic, +Y up)"},
    "hero": dict(HERO, role="reference", label="Hero (the scene camera)"),
    "closeup_railing": {"location": (2.3, -3.1, 1.75), "target": (-0.6, 0.0, 0.75), "lens": 40.0, "role": "closeup",
                        "label": "Close-up: railing and deck"},
}
# The brief quotes 'Footbridge Deck' as a name to use exactly: one planked object, or planks that are Blender's
# copies of it ('Footbridge Deck.001' on).
GROUND, STREAM, DECK = {"name": "Ground"}, {"name": "Stream"}, {"name": "Footbridge Deck"}
FOOTBRIDGE, RAILING, STONE = {"prefix": "Footbridge"}, {"prefix": "Railing"}, {"prefix": "Stone"}
SUBJECT = (FOOTBRIDGE, RAILING)

PROMPT = (
    "A timber footbridge over a stream, late on a summer afternoon. Build it and render one still. The four "
    "attached pictures show the scene I have in mind: the hero shot, a front elevation, a plan from above "
    "(+Y up) and a close-up of the railing. Take the proportions and the mood from them and design the details "
    "yourself; where they and this brief disagree, the brief wins.\n\n"
    "Conventions. Metres, Z up. The stream runs along Y through the origin and the bridge crosses it along X. "
    "I find things by name, so name them exactly as written here and give anything else you add a meaningful "
    "name of its own. Keep the file tidy: nothing from the default scene (its cube, light or camera) remains, "
    "no unused leftovers, and a material on everything that shows.\n\n"
    "The ground. One mesh named 'Ground', about 48 m along X by 40 m along Y: grassy banks at height 0, a "
    "little uneven, cut by a stream channel about 4 m wide whose bed lies about 0.9 m below the banks. Grass on "
    "the banks, earth or gravel in the channel.\n\n"
    "The water. A surface named 'Stream' that fills the channel along its whole length, about 0.45 m below the "
    "banks: dark, clear and glossy (roughness 0.15 or less).\n\n"
    "The footbridge. Weathered timber in a warm brown, rough to the touch; every part's name starts with "
    "'Footbridge', except the railings, whose part names start with 'Railing'. It is 6 m long (x = -3 to 3) and "
    "1.4 m wide. Its walking surface, 'Footbridge Deck', is "
    "made of real cross planks with small gaps between them, and its top is 0.3 m above the banks "
    "(z = 0.3 m). Carry the deck on beams that rest on the banks and clear the water; how you frame it is up "
    "to you.\n\n"
    "The railings. A handrail on each side with its top 1 m above the deck (z = 1.3 m), on posts roughly every "
    "1.5 m (at least four a side, each post its own piece), with a lower rail or balusters as you see fit. "
    "Every railing part's name starts with 'Railing'. The walkway between the railings stays clear.\n\n"
    "Dressing. At least five rounded stones in and beside the stream, their names starting with 'Stone', each "
    "0.3 to 1 m across. Reeds, grass tufts or anything else that helps the picture is up to you.\n\n"
    "Light. A sun light named 'Sun', 20 to 30 degrees above the horizon and clearly warm in colour, raking "
    "across the bridge so the railings throw shadows on the deck, under a clear sky to match.\n\n"
    "Camera. A camera named 'Hero Camera' at (4.2, -5.6, 3.6), looking at (0, 0, 0.3) with a 35 mm lens, as the "
    "scene's camera. That is the hero shot in the first picture.\n\n"
    "Output. EEVEE with ray tracing on, 2560 x 1440 at 100 %. Render the still from 'Hero Camera' and save it "
    "as a PNG named 'footbridge-final.png'; tell me where it is.\n\n"
    "Before you call it done, look at your render next to the pictures and fix what is wrong.")


def _probe(origin, direction, target, axis=None, value_range=None, expect="hit", label="", distance=40.0):
    check = {"kind": "probe", "origin": list(origin), "direction": list(direction), "target": target,
             "expect": expect, "label": label, "distance": distance}
    if axis:
        check.update(axis=axis, range=list(value_range))
    return check


# The middle of a handrail piece lies 1.05 to 1.45 m up: its top at 1.3 m within the 0.15 m the check allowed
# before, less half the depth of a handrail up to 0.2 m deep. Pieces shorter than half the stated post spacing
# (1.5 m) are caps or finials, not a handrail; a post's middle is lower.
HANDRAIL_MIDDLE, HANDRAIL_SHORTEST = (RAIL_TOP - 0.25, RAIL_TOP + 0.15), 0.75
# Water about 0.45 m below the banks: the bridge clears it when nothing of it comes within this of that level.
WATER_CLEARANCE = 0.02


def _side(sign, check, z_low, z_high):
    """``check`` for one side's railing (``sign`` -1: the -Y side): the brief asks for each side on its own."""
    low, high = sorted((sign * 0.2, sign * 1.1))
    return dict(check, region_min=[-3.4, low, z_low], region_max=[3.4, high, z_high],
                label="%s (%sY side)" % (check["label"], "-" if sign < 0 else "+"))


def checks(railing):
    """The checks, with ``railing`` the target the brief's railing names give (``RAILING`` here)."""
    walkway = (-DECK_LENGTH / 2, 0, DECK_TOP + 0.7)   # Along the walkway between the railings, end to end.
    return [
        {"kind": "hygiene", "absent": ["Cube", "Light", "Camera"], "max_default_named": 0, "max_without_material": 0,
         "max_unused": 0, "label": "tidy file: no default leftovers, named, materials"},
        {"kind": "object", "target": GROUND, "type": "MESH", "label": "Ground exists"},
        {"kind": "bbox", "target": GROUND, "size": [[40, 56], [33, 47], None], "label": "Ground about 48 x 40 m"},
        _probe((-7, 3, 10), (0, 0, -1), GROUND, "z", (-0.45, 0.45), label="banks near height 0"),
        _probe((0, 5, 10), (0, 0, -1), GROUND, "z", (BED_Z - 0.6, BED_Z + 0.35), label="stream bed about 0.9 m down"),
        {"kind": "surface", "target": GROUND, "color": ["green", "warm"], "label": "grass-and-earth ground"},
        _probe((0, 5, 10), (0, 0, -1), STREAM, "z", (WATER_Z - 0.3, WATER_Z + 0.25), label="water about 0.45 m down"),
        {"kind": "bbox", "target": STREAM, "size": [[1.5, 7.0], [30, 50], None], "label": "the stream runs the length"},
        {"kind": "surface", "target": STREAM, "roughness": [0.0, 0.2], "label": "glossy water"},
        {"kind": "bbox", "target": DECK, "min": [-DECK_LENGTH / 2, None, None], "max": [DECK_LENGTH / 2, None, DECK_TOP],
         "tolerance": 0.2, "size": [None, [1.2, 1.7], [0.02, 0.4]], "label": "deck 6 x 1.4 m, top at 0.3 m"},
        # No ray is cast at the planks: it could fall through a gap between two of them.
        {"kind": "bbox", "target": DECK, "max": [None, None, DECK_TOP], "tolerance": 0.1,
         "label": "walking surface 0.3 m above the banks"},
        {"kind": "parts", "target": DECK, "region_min": [-3.3, -1.0, 0.0], "region_max": [3.3, 1.0, 0.6],
         "count": [12, 400], "size": [1.1, 1.8], "label": "a deck of separate cross planks"},
        # Its lowest point is between 0.6 m below the banks (footings) and the deck: no pile reaches the bed at -0.9 m.
        {"kind": "bbox", "target": FOOTBRIDGE, "min": [None, None, -0.15], "tolerance": 0.45,
         "label": "the bridge rests on the banks, not in the stream bed"},
        _probe((0, -6, WATER_Z + WATER_CLEARANCE), (0, 1, 0), FOOTBRIDGE, expect="miss",
               label="the bridge clears the water", distance=12.0),
        {"kind": "bbox", "target": railing, "min": [-DECK_LENGTH / 2, -0.7, None], "max": [DECK_LENGTH / 2, 0.7, None],
         "tolerance": 0.3, "label": "railings along both sides, the bridge's length"},
        # The brief fixes the handrail's top, not the posts': a post may rise past it to a cap.
        *[_side(sign, {"kind": "parts", "target": railing, "count": [1, 400], "size": [HANDRAIL_SHORTEST, 100.0],
                       "label": "handrail top 1 m above the deck"}, *HANDRAIL_MIDDLE) for sign in (-1, 1)],
        *[_side(sign, {"kind": "parts", "target": railing, "count": [4, 400], "size": [0.7, 1.9],
                       "label": "at least four posts a side"}, -0.2, 1.05) for sign in (-1, 1)],
        _probe(walkway, (1, 0, 0), railing, expect="miss", label="walkway clear of railings", distance=DECK_LENGTH),
        _probe(walkway, (1, 0, 0), FOOTBRIDGE, expect="miss", label="walkway clear of the bridge",
               distance=DECK_LENGTH),
        {"kind": "surface", "target": FOOTBRIDGE, "color": ["warm"], "roughness": [0.4, 1.0],
         "label": "warm brown, rough timber"},
        {"kind": "parts", "target": STONE, "region_min": [-24, -20, -2], "region_max": [24, 20, 2], "count": [5, 400],
         "size": [0.25, 1.3], "label": "five or more stones 0.3 to 1 m across"},
        {"kind": "light", "target": {"name": "Sun"}, "type": "SUN", "elevation_deg": [15, 35], "color": ["warm"],
         "label": "a warm Sun 20 to 30 degrees up"},
        {"kind": "world", "sky_or_color": ["blue", "warm"], "sun_elevation_deg": [8, 42], "label": "a clear sky"},
        {"kind": "camera", "target": {"name": "Hero Camera"}, "active": True, "location": list(HERO["location"]),
         "radius": 0.4, "look_at": list(HERO["target"]), "min_dot": 0.99, "lens": [33.0, 37.0],
         "label": "Hero Camera placed, aimed and active"},
        {"kind": "scene", "values": {"render.engine": "BLENDER_EEVEE", "render.resolution_x": 2560,
                                     "render.resolution_y": 1440, "render.resolution_percentage": 100,
                                     "eevee.use_raytracing": True}, "label": "EEVEE, ray tracing, 2560 x 1440"},
    ]


CHECKS = checks(RAILING)

TURNS = [{
    "id": "brief", "title": "The footbridge and its final still",
    "references": ["hero", "front", "top", "closeup_railing"], "prompt": PROMPT, "checks": CHECKS,
    "outputs": [{"name": FINAL_STILL["name"], "min_size": list(FINAL_STILL["size"]),
                 "label": "the final still was rendered and saved (2560 x 1440 or larger)"}],
    "job": {"kind": "RENDER_STILL", "output": "renders/" + FINAL_STILL["name"],
            "resolution": list(FINAL_STILL["size"]), "samples": 16, "format": "PNG", "minutes": 10},
}]


# ------------------------------------------------------------------ ground truth

def smoothstep(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3.0 - 2.0 * t)


def terrain_height(x, y):
    """Banks at 0 with a little unevenness away from the bridge, a flat stream bed, smooth slopes between."""
    distance = abs(x)
    if distance <= BED_HALF:
        base = BED_Z
    elif distance >= BANK_HALF:
        base = 0.0
    else:
        base = BED_Z * (1.0 - smoothstep((distance - BED_HALF) / (BANK_HALF - BED_HALF)))
    uneven = smoothstep((distance - 3.4) / 3.0) * (0.14 * math.sin(0.55 * x + 0.3) * math.cos(0.45 * y)
                                                 + 0.06 * math.sin(1.3 * x) * math.sin(1.1 * y + 0.7))
    return base + uneven


def _ground(bpy):
    sx, sy = GROUND_SIZE
    xs = kit.graded_samples(-sx / 2, sx / 2, -3.0, 3.0, 0.2, 0.6)
    ys = kit.graded_samples(-sy / 2, sy / 2, -8.0, 8.0, 0.5, 1.0)
    ground = kit.heightfield_block(bpy, "Ground", xs, ys, terrain_height, GROUND_BOTTOM)
    kit.assign(ground, kit.height_ramp_material(bpy, "Ground Grass", [
        (BED_Z, (0.12, 0.10, 0.07)), (-0.4, (0.17, 0.15, 0.08)), (-0.1, (0.13, 0.22, 0.06)),
        (0.0, (0.14, 0.25, 0.06)), (0.35, (0.19, 0.30, 0.08))], roughness=0.92))
    half = BANK_HALF + 0.1
    water = kit.mesh_object(bpy, "Stream", [(-half, -sy / 2, 0), (half, -sy / 2, 0), (half, sy / 2, 0),
                                            (-half, sy / 2, 0)], [(0, 1, 2, 3)], location=(0.0, 0.0, WATER_Z))
    kit.assign(water, kit.principled(bpy, "Stream Water", (0.02, 0.07, 0.06), roughness=0.05, specular=0.6))
    stone = kit.principled(bpy, "Stone Grey", (0.17, 0.165, 0.15), roughness=0.85)
    for index, (x, y, radius) in enumerate(STONES):
        rock = kit.mesh_object(bpy, "Stone %02d" % (index + 1), *kit.rock_geometry(radius, seed=index + 31),
                               location=(x, y, max(terrain_height(x, y), WATER_Z - 0.15) + radius * 0.2), smooth=True)
        rock.rotation_euler = (0.0, 0.0, 0.9 * index)
        kit.assign(rock, stone)


def _footbridge(bpy):
    timber = kit.principled(bpy, "Footbridge Timber", (0.22, 0.115, 0.05), roughness=0.72)
    pitch = DECK_LENGTH / PLANKS
    plank = kit.mesh_object(bpy, "Footbridge Deck", *kit.box_geometry(
        (pitch - 0.015, DECK_WIDTH, 0.05), (-DECK_LENGTH / 2 + pitch / 2, 0.0, DECK_TOP - 0.025)))
    array = plank.modifiers.new("Planks", "ARRAY")
    array.count, array.use_relative_offset, array.use_constant_offset = PLANKS, False, True
    array.constant_offset_displace = (pitch, 0.0, 0.0)
    beams = kit.mesh_object(bpy, "Footbridge Beams", *kit.merged_boxes(
        [((DECK_LENGTH, 0.12, 0.15), (0.0, side * 0.45, 0.175)) for side in (-1, 1)]))
    sills = kit.mesh_object(bpy, "Footbridge Sills", *kit.merged_boxes(
        [((0.25, DECK_WIDTH + 0.3, 0.10), (side * 2.8, 0.0, 0.05)) for side in (-1, 1)]))
    posts = kit.mesh_object(bpy, "Railing Posts", *kit.merged_boxes(
        [((0.09, 0.09, RAIL_TOP - 0.1), (x, side * RAIL_Y, (RAIL_TOP + 0.1) / 2)) for side in (-1, 1) for x in POST_X]))
    handrails = kit.mesh_object(bpy, "Railing Handrails", *kit.merged_boxes(
        [((DECK_LENGTH + 0.1, 0.11, 0.06), (0.0, side * RAIL_Y, RAIL_TOP - 0.03)) for side in (-1, 1)]))
    midrails = kit.mesh_object(bpy, "Railing Midrails", *kit.merged_boxes(
        [((DECK_LENGTH, 0.05, 0.08), (0.0, side * (RAIL_Y - 0.06), 0.8)) for side in (-1, 1)]))
    for obj in (plank, beams, sills, posts, handrails, midrails):
        kit.assign(obj, timber)
    for obj in (posts, handrails):
        bevel = obj.modifiers.new("Bevel", "BEVEL")
        bevel.width, bevel.segments = 0.008, 2


def _light_camera_output(bpy):
    scene = bpy.context.scene
    kit.sun_object(bpy, "Sun", SUN_ELEVATION, SUN_AZIMUTH, (1.0, 0.78, 0.55), 3.5, angle_deg=1.5)
    scene.world = kit.sky_world(bpy, "Afternoon Sky", SUN_ELEVATION, SUN_AZIMUTH, strength=0.4, aerosol=1.5)
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.camera = kit.camera_object(bpy, "Hero Camera", HERO["location"], HERO["target"], HERO["lens"])
    render = scene.render
    render.engine = "BLENDER_EEVEE"
    render.resolution_x, render.resolution_y, render.resolution_percentage = *FINAL_STILL["size"], 100
    scene.eevee.use_raytracing = True


def build_turn(bpy, turn_id):
    """The whole ground truth (the brief is one turn): what a perfect agent would leave behind."""
    if turn_id != "brief":
        raise KeyError(turn_id)
    oneshot_kit.clear_default_scene(bpy)
    _ground(bpy)
    _footbridge(bpy)
    _light_camera_output(bpy)
    bpy.context.view_layer.update()


def build(bpy, upto=None):
    build_turn(bpy, "brief")
