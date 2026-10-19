# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot test ``oneshot-cottage``: a small stone cottage at golden hour, built and rendered from one message.

The brief attaches one picture: the cottage cut from the maintainer's
reference painting (``SOURCE_IMAGES``). Where it departs from the picture it
says so: the painted roof is slate and the brief asks for terracotta tiles,
and it adds a door and a wall lantern the picture does not show. A side that
copies the picture instead of reading the brief fails those checks.

``build`` is a stand-in, not a target look (``GROUND_TRUTH``): the plainest
cottage that follows the brief. The checks are derived from its constants
and hold for any build that follows the brief, however it is made.

Axes: the cottage stands on the origin, its front wall facing -Y, its ridge along X; Z is up; metres.
"""

import oneshot_kit
import oneshot_opacity
import scenario_build_kit as kit

ID = "oneshot_cottage"
TITLE = "A stone cottage at golden hour"
FRAME = 1
FINAL_VIEW = "hero"
FINAL_STILL = {"name": "cottage-final.png", "size": (2560, 1440)}
GROUND_TRUTH = "stand-in"
LIMITS = {"wall_s": 3000.0, "job_wait_s": 900.0, "cost_usd": 30.0}
APPROVABLE = ("angelo_job_start",)
JOB_WAIT_S = LIMITS["job_wait_s"]

SIZE, EAVES, RIDGE, CHIMNEY_TOP = (8.0, 5.5), 2.6, 5.4, 6.5
HALF_X, HALF_Y = SIZE[0] / 2, SIZE[1] / 2
PLOT = 30.0
WINDOW_X, SILL = 2.4, 1.0
LANTERN = (1.0, -HALF_Y - 0.14, 1.9)
SUN_ELEVATION, SUN_AZIMUTH = 11.0, -150.0            # From the front left: the -X, -Y side.
HERO = {"location": (-9.0, -13.0, 2.2), "target": (0.0, 0.0, 2.6), "lens": 35.0}

# The one picture the brief attaches: the cottage in the maintainer's reference painting.
SOURCE_IMAGES = {"picture": {
    "file": "maintainer-reference-2026-10-11.png",
    "sha256": "7aa30417d806ee899c03ffc575a4e2f3220986888372fd365f613ba18604dc20",
    "box": (600, 205, 900, 395), "scale": 3, "label": "The kind of cottage (cut from the reference painting)",
    "origin": "the maintainer's reference painting of a floating island, 2026-10-11",
}}
CAMERAS = {
    "hero": dict(HERO, role="reference", label="Hero (the scene camera)"),
    "front": {"location": (0.0, -40.0, 3.2), "target": (0.0, 0.0, 3.2), "ortho_scale": 13.0, "role": "reference",
              "label": "Front elevation (orthographic, looking +Y)"},
    "side": {"location": (-40.0, 0.0, 3.2), "target": (0.0, 0.0, 3.2), "ortho_scale": 13.0, "role": "reference",
             "label": "The chimney end (orthographic, looking +X)"},
    "top": {"location": (0.0, 0.0, 40.0), "target": (0.0, 0.0, 0.0), "ortho_scale": 14.0, "role": "reference",
            "label": "Plan (orthographic, +Y up)"},
    "closeup_door": {"location": (2.6, -7.0, 1.7), "target": (0.6, -2.75, 1.5), "lens": 40.0, "role": "closeup",
                     "label": "Close-up: door, lantern and a front window"},
}
# Names as the brief states them: "name them exactly as written here and give anything else you add a meaningful
# name of its own". A quoted name is that object (and Blender's .001 siblings); a differently named part (a core,
# an underlay, a frame, a child) is something else. Windows and every part are named by how their names start.
GROUND, COTTAGE, LANTERN_PARTS = {"name": "Ground"}, {"prefix": "Cottage"}, {"name": "Lantern"}
WALLS, ROOF, CHIMNEY = {"name": "Cottage Walls"}, {"name": "Cottage Roof"}, {"name": "Cottage Chimney"}
DOOR, WINDOW = {"name": "Cottage Door"}, {"prefix": "Cottage Window"}
DOOR_LEAF = DOOR
# The wall face at a place: the walls, or a window or the door the brief sets in a wall (windows may be anywhere).
WALL_FACE = {"any": [WALLS, WINDOW, DOOR]}
ROOF_FACE = {"any": [ROOF, WINDOW]}   # The roof, or a roof window the brief allows anywhere.
SUBJECT = (COTTAGE, LANTERN_PARTS)

PROMPT = (
    "A small stone cottage at golden hour. Build it and render one still. The attached picture shows the kind "
    "of cottage I mean: rough stone walls, a steep tiled roof with a stone chimney, small-paned windows glowing "
    "warm from inside. It is cut from a larger painting, so ignore the trees and rocks around it; where the "
    "picture and this brief disagree, the brief wins. Design the details yourself; these are the things I need "
    "to find.\n\n"
    "Conventions. Metres, Z up. The cottage stands on the origin with its front facing -Y. I find things by "
    "name, so name them exactly as written here and give anything else you add a meaningful name of its own. "
    "Keep the file tidy: nothing from the default scene (its cube, light or camera) remains, no unused "
    "leftovers, and a material on everything that shows.\n\n"
    "The plot. One mesh named 'Ground': a grassy plot about 30 m by 30 m at height 0; a path to the door is "
    "welcome.\n\n"
    "The cottage. Every part's name starts with 'Cottage'. Its footprint is 8 m along X by 5.5 m along Y. "
    "'Cottage Walls': rough stone in a warm grey, 2.6 m high to the eaves, with gable ends at -X and +X. "
    "'Cottage Roof': a steep pitched roof whose ridge runs along X about 5.4 m above the ground, overhanging "
    "the walls a little, covered in terracotta tiles laid in courses: the picture's roof is slate; I want "
    "terracotta. 'Cottage Chimney': a stone chimney about 0.8 m square at the -X end, rising to about 6.5 m, "
    "clear above the ridge. 'Cottage Door': a timber door 0.9 m wide and 2 m tall in the middle of the front "
    "wall. Windows, their names starting with 'Cottage Window': at least four, with frames and glazing bars, "
    "two of them in the front wall, centred 2.4 m either side of the door with their sills 1 m above the "
    "ground. The glass glows warm from inside (emission).\n\n"
    "The lantern. 'Lantern': a wall lantern on the front wall to the right of the door as you face it (about "
    "x = 1, 1.9 m up), with glowing warm glass, and inside it a warm point light named 'Lantern Light'.\n\n"
    "Light. Golden hour: a sun light named 'Sun', 8 to 15 degrees above the horizon and warm orange, coming "
    "from the front left (-X, -Y) so that it rakes across the front wall and the chimney throws a long shadow, "
    "under a sky to match.\n\n"
    "Camera. A camera named 'Hero Camera' at (-9, -13, 2.2), looking at (0, 0, 2.6) with a 35 mm lens, as the "
    "scene's camera: the front and the left gable in a three-quarter view.\n\n"
    "Output. EEVEE with ray tracing on, 2560 x 1440 at 100 %. Render the still from 'Hero Camera' and save it "
    "as a PNG named 'cottage-final.png'; tell me where it is.\n\n"
    "Before you call it done, look at your render next to the picture and fix what is wrong.")


def _probe(origin, direction, target, axis=None, value_range=None, expect="hit", label="", distance=40.0):
    check = {"kind": "probe", "origin": list(origin), "direction": list(direction), "target": target,
             "expect": expect, "label": label, "distance": distance}
    if axis:
        check.update(axis=axis, range=list(value_range))
    return check


def _seen(x, z, target, label):
    """Just outside the front wall, the first thing a ray meets at (x, z) is the named part. The ray starts 0.6 m
    out: anything else the brief welcomes in front of the cottage (a path, planting) never stands in its way."""
    return {"kind": "visible", "origin": [x, -HALF_Y - 0.6, z], "direction": [0, 1, 0], "target": target,
            "distance": 2.0, "label": label}


CHECKS = [
    {"kind": "hygiene", "absent": ["Cube", "Light", "Camera"], "max_default_named": 0, "max_without_material": 0,
     "max_unused": 0, "label": "tidy file: no default leftovers, named, materials"},
    {"kind": "object", "target": GROUND, "type": "MESH", "label": "Ground exists"},
    {"kind": "bbox", "target": GROUND, "size": [[24, 38], [24, 38], None], "label": "a plot about 30 x 30 m"},
    _probe((10, -10, 10), (0, 0, -1), GROUND, "z", (-0.35, 0.35), label="the plot is at height 0"),
    {"kind": "surface", "target": GROUND, "color": ["green"], "label": "a grassy plot"},
    {"kind": "bbox", "target": WALLS, "min": [-HALF_X, -HALF_Y, 0.0], "max": [HALF_X, HALF_Y, None],
     "tolerance": 0.35, "size": [None, None, [2.4, 5.8]], "label": "walls on an 8 x 5.5 m footprint"},
    # Beside the windows, where the wall is plain: a ray from the front meets stone at the wall's face.
    _probe((3.6, -9, 1.3), (0, 1, 0), WALL_FACE, "y", (-HALF_Y - 0.35, -HALF_Y + 0.35), label="front wall (right end)"),
    _probe((-3.6, -9, 2.3), (0, 1, 0), WALL_FACE, "y", (-HALF_Y - 0.35, -HALF_Y + 0.35),
           label="front wall reaches 2.3 m (left end)"),
    _probe((3.0, 9, 1.3), (0, -1, 0), WALL_FACE, "y", (HALF_Y - 0.35, HALF_Y + 0.35), label="back wall"),
    {"kind": "surface", "target": WALLS, "color": ["grey", "warm"], "roughness": [0.5, 1.0],
     "label": "rough stone walls in a warm grey"},
    # Grader only, never shown to the model: walls and a roof are closed surfaces; any part of the cottage (a
    # window, the door, a core) may close them, so only an open gap counts (oneshot_opacity).
    {"kind": "opaque", "target": COTTAGE, "areas": oneshot_opacity.wall_areas(HALF_X, HALF_Y, EAVES, RIDGE),
     "label": "the walls have no see-through gaps"},
    {"kind": "bbox", "target": ROOF, "min": [-HALF_X - 0.3, -HALF_Y - 0.35, None],
     "max": [HALF_X + 0.3, HALF_Y + 0.35, RIDGE + 0.05], "tolerance": 0.45,
     "label": "the roof covers the walls with a small overhang, ridge about 5.4 m"},
    # Clear of the band the chimney may stand in (its check allows max x up to -2.7 + 1.5 = -1.2).
    _probe((-0.9, 0, 12), (0, 0, -1), ROOF, "z", (RIDGE - 0.5, RIDGE + 0.5), label="ridge along X (left)"),
    _probe((2.5, 0, 12), (0, 0, -1), ROOF, "z", (RIDGE - 0.5, RIDGE + 0.5), label="ridge along X (right)"),
    # Two metres from the ridge a 5.4 m ridge over 2.6 m eaves is at 3.4 m: a steep pitch, not a flat lid.
    _probe((0, -2.0, 12), (0, 0, -1), ROOF_FACE, "z", (2.9, 4.2), label="steep front slope"),
    _probe((0, 2.0, 12), (0, 0, -1), ROOF_FACE, "z", (2.9, 4.2), label="steep back slope"),
    {"kind": "opaque", "target": COTTAGE, "areas": oneshot_opacity.roof_areas(HALF_X, HALF_Y, EAVES),
     "label": "the roof closes the footprint above the eaves"},
    {"kind": "surface", "target": ROOF, "color": ["warm"], "roughness": [0.3, 1.0],
     "label": "terracotta tiles (the brief's, not the picture's slate)"},
    {"kind": "bbox", "target": CHIMNEY, "max": [None, None, CHIMNEY_TOP], "tolerance": 0.6,
     "size": [[0.5, 1.4], [0.5, 1.4], [1.0, 8.0]], "label": "a chimney about 0.8 m square, to about 6.5 m"},
    {"kind": "bbox", "target": CHIMNEY, "max": [-2.7, None, None], "tolerance": 1.5,
     "label": "the chimney is at the -X end"},
    {"kind": "bbox", "target": DOOR_LEAF, "min": [-0.45, None, 0.0], "max": [0.45, None, 2.0], "tolerance": 0.3,
     "size": [[0.8, 1.4], None, [1.85, 2.5]], "label": "a door 0.9 m wide and 2 m tall, mid front wall"},
    _probe((0, -9, 1.1), (0, 1, 0), DOOR, "y", (-HALF_Y - 0.55, -HALF_Y + 0.55), label="the door is in the front wall"),
    _seen(0.0, 1.1, DOOR, "the door can be seen from the front"),
    {"kind": "surface", "target": DOOR, "color": ["warm"], "label": "a timber door"},
    # A window is its frame, bars and glass together, whether that is one object or twenty.
    {"kind": "groups", "target": WINDOW, "gap": 0.25, "size": [0.4, 2.6], "count": [4, 60],
     "label": "at least four windows"},
    # Just above the stated sill height: the brief states the sills, not the windows' height.
    _seen(-WINDOW_X, SILL + 0.1, WINDOW, "front window left of the door"),
    _seen(WINDOW_X, SILL + 0.1, WINDOW, "front window right of the door"),
    _probe((-WINDOW_X, -9, 0.8), (0, 1, 0), WINDOW, expect="miss", label="left window's sill about 1 m up",
           distance=6.9),
    _probe((WINDOW_X, -9, 0.8), (0, 1, 0), WINDOW, expect="miss", label="right window's sill about 1 m up",
           distance=6.9),
    {"kind": "surface", "target": WINDOW, "emission_min": 0.3, "emission_color": ["warm"],
     "label": "the windows glow warm"},
    {"kind": "bbox", "target": LANTERN_PARTS, "min": [LANTERN[0] - 0.15, -HALF_Y - 0.3, LANTERN[2] - 0.2],
     "max": [LANTERN[0] + 0.15, -HALF_Y, LANTERN[2] + 0.2], "tolerance": 0.45,
     "label": "a lantern on the front wall right of the door, about 1.9 m up"},
    {"kind": "surface", "target": LANTERN_PARTS, "emission_min": 0.3, "emission_color": ["warm"],
     "label": "the lantern's glass glows warm"},
    {"kind": "light", "target": {"name": "Lantern Light"}, "type": "POINT", "color": ["warm"],
     "location": list(LANTERN), "radius": 0.6, "label": "a warm point light inside the lantern"},
    {"kind": "light", "target": {"name": "Sun"}, "type": "SUN", "elevation_deg": [5, 18], "color": ["warm"],
     "label": "a warm Sun 8 to 15 degrees up"},
    {"kind": "light_from", "target": {"name": "Sun"}, "azimuth_deg": 225.0, "within_deg": 50.0,
     "label": "the sun comes from the front left"},
    {"kind": "world", "sky_or_color": ["warm", "blue"], "sun_elevation_deg": [0, 22], "label": "a sky to match"},
    {"kind": "camera", "target": {"name": "Hero Camera"}, "active": True, "location": list(HERO["location"]),
     "radius": 0.5, "look_at": list(HERO["target"]), "min_dot": 0.99, "lens": [33.0, 37.0],
     "label": "Hero Camera placed, aimed and active"},
    {"kind": "scene", "values": {"render.engine": "BLENDER_EEVEE", "render.resolution_x": 2560,
                                 "render.resolution_y": 1440, "render.resolution_percentage": 100,
                                 "eevee.use_raytracing": True}, "label": "EEVEE, ray tracing, 2560 x 1440"},
]

TURNS = [{
    "id": "brief", "title": "The cottage and its final still",
    "references": ["picture"], "prompt": PROMPT, "checks": CHECKS,
    "outputs": [{"name": FINAL_STILL["name"], "min_size": list(FINAL_STILL["size"]),
                 "label": "the final still was rendered and saved (2560 x 1440 or larger)"}],
    "job": {"kind": "RENDER_STILL", "output": "renders/" + FINAL_STILL["name"],
            "resolution": list(FINAL_STILL["size"]), "samples": 16, "format": "PNG", "minutes": 10},
}]


# ------------------------------------------------------------------ the stand-in

def build_turn(bpy, turn_id):
    """The whole stand-in (the brief is one turn): the plainest scene that follows the brief."""
    if turn_id != "brief":
        raise KeyError(turn_id)
    oneshot_kit.clear_default_scene(bpy)
    scene = bpy.context.scene
    half = PLOT / 2
    ground = kit.mesh_object(bpy, "Ground", [(-half, -half, 0.0), (half, -half, 0.0), (half, half, 0.0),
                                             (-half, half, 0.0)], [(0, 1, 2, 3)])
    kit.assign(ground, kit.principled(bpy, "Ground Grass", (0.14, 0.25, 0.06), roughness=0.92))
    path = kit.mesh_object(bpy, "Path", *kit.box_geometry((1.3, half - HALF_Y - 0.1, 0.04),
                                                          (0.0, -(half + HALF_Y + 0.1) / 2, 0.02)))
    kit.assign(path, kit.principled(bpy, "Path Gravel", (0.30, 0.26, 0.20), roughness=0.95))
    oneshot_kit.cottage(bpy, "Cottage", (0.0, 0.0, 0.0), SIZE, EAVES, RIDGE)
    kit.sun_object(bpy, "Sun", SUN_ELEVATION, SUN_AZIMUTH, (1.0, 0.62, 0.32), 4.5, angle_deg=1.5)
    scene.world = kit.sky_world(bpy, "Golden Hour Sky", SUN_ELEVATION, SUN_AZIMUTH, strength=0.5, aerosol=2.0)
    scene.camera = kit.camera_object(bpy, "Hero Camera", HERO["location"], HERO["target"], HERO["lens"])
    render = scene.render
    render.engine = "BLENDER_EEVEE"
    render.resolution_x, render.resolution_y, render.resolution_percentage = *FINAL_STILL["size"], 100
    scene.eevee.use_raytracing = True
    bpy.context.view_layer.update()


def build(bpy, upto=None):
    build_turn(bpy, "brief")
