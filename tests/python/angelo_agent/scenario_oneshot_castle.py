# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot test ``oneshot-castle``: a whole fairytale castle, built and rendered from one message.

The heavy one-shot: a generic romantic hilltop castle in the spirit of the
Bavarian revival castles (a real public building is the inspiration; no
brand's castle is). It stresses what big builds need: repetition (six towers,
dozens of merlons, a dozen windows), an arch, a closed ring of walls and many
objects, so its output is long.

The brief attaches one picture, the hero shot rendered from ``build`` (the
stand-in, ``castle_kit``): the plainest castle that follows the brief. The
checks are derived from ``castle_kit``'s constants and hold for any build
that follows the brief, however it is made (``castle_variants`` proves both
directions in stock Blender).

Axes: the courtyard is centred on the origin, the gate faces -Y, Z is up, metres.
"""

import castle_kit as castle
import oneshot_opacity
import oneshot_kit
import scenario_build_kit as kit

ID = "oneshot_castle"
TITLE = "A fairytale castle on a meadow"
FRAME = 1
FINAL_VIEW = "hero"
FINAL_STILL = {"name": "castle-final.png", "size": (2560, 1440)}
GROUND_TRUTH = "reference"
LIMITS = {"wall_s": 3600.0, "job_wait_s": 900.0, "cost_usd": 40.0}
APPROVABLE = ("angelo_job_start",)
JOB_WAIT_S = LIMITS["job_wait_s"]

MEADOW = 160.0
SUN_ELEVATION, SUN_AZIMUTH = 28.0, -135.0            # From the south-west: the -X, -Y side.
HERO = {"location": (42.0, -74.0, 16.0), "target": (0.0, 0.0, 12.0), "lens": 35.0}
CAMERAS = {
    "hero": dict(HERO, role="reference", label="Hero (the scene camera)"),
    "front": {"location": (0.0, -120.0, 14.0), "target": (0.0, 0.0, 14.0), "ortho_scale": 60.0, "role": "reference",
              "label": "South front (orthographic, looking +Y)"},
    "side": {"location": (-120.0, 0.0, 14.0), "target": (0.0, 0.0, 14.0), "ortho_scale": 60.0, "role": "reference",
             "label": "West side (orthographic, looking +X)"},
    "top": {"location": (0.0, 0.0, 120.0), "target": (0.0, 0.0, 0.0), "ortho_scale": 60.0, "role": "reference",
            "label": "Plan (orthographic, +Y up)"},
    "closeup_gate": {"location": (9.0, -36.0, 4.0), "target": (0.0, -16.0, 5.0), "lens": 35.0, "role": "closeup",
                     "label": "Close-up: the gatehouse and its arch"},
}
# "the start of the name says which part … Any other detail of the castle starts with 'Castle' followed by a word
# of its own": prefixes that end a word ('Castle Wall 2', never 'Castle Wallflower').
GROUND, CASTLE = {"name": "Ground"}, {"prefix": "Castle", "word": True}
WALL, TOWER, GATE = ({"prefix": p, "word": True} for p in ("Castle Wall", "Castle Tower", "Castle Gatehouse"))
KEEP, WINDOW = {"prefix": "Castle Keep", "word": True}, {"prefix": "Castle Window", "word": True}
GATEHOUSE_BLOCK = [castle.GATE_X[0], castle.GATE_Y[0], castle.GATE_X[1], castle.GATE_Y[1]]
SUBJECT = (CASTLE,)

PROMPT = (
    "A fairytale castle on a green meadow on a clear late afternoon. Build it and render one still. The attached "
    "picture is the hero shot of the castle I have in mind: a castle in the spirit of the romantic Bavarian castles "
    "such as Neuschwanstein, pale stone, slender round towers under steep blue-slate cones, a tall keep and "
    "battlements all round. Take the layout and the mood from it and design the details yourself; where the "
    "picture and this brief disagree, the brief wins.\n\n"
    "Conventions. Metres, Z up. The courtyard is centred on the origin and the gate faces -Y. Keep the file "
    "tidy: nothing from the default scene (its cube, light or camera) remains, no unused leftovers, and a "
    "material on everything that shows. Nothing floats: every part rests on the ground or on another part, and "
    "the towers and the keep stand apart rather than inside one another.\n\n"
    "Names. I find things by name, so name them exactly as written here. Every part of the castle has a name "
    "starting with 'Castle', and the start of the name says which part of the castle it belongs to: the curtain "
    "wall and its battlements start with 'Castle Wall'; each tower, the two beside the gate included, and its "
    "roof start with 'Castle Tower'; the gatehouse, its battlements and its arch start with 'Castle Gatehouse'; "
    "the keep and its roof start with 'Castle Keep'; and every window, wherever it is, starts with 'Castle "
    "Window'. Any other detail of the castle starts with 'Castle' followed by a word of its own, never one of "
    "those. Nothing that is not part of the castle (the meadow, a path, the sun, the camera) has a name starting "
    "with 'Castle'; give it a meaningful name of its own.\n\n"
    "The meadow. One mesh named 'Ground': a grassy plain about 160 m across at height 0; a path to the gate is "
    "welcome.\n\n"
    "The curtain wall. Pale stone, 2 m thick, its wall-walk 9 m above the ground, running round a rectangular "
    "courtyard on the lines x = -20, x = 20, y = -15 and y = 15 with no gap anywhere. Its parts' names, its "
    "battlements' included, start with 'Castle Wall'. Battlements along the whole outer edge of the wall-walk: "
    "merlons about 1 m wide and 1.2 m tall with gaps of about 0.8 m between them.\n\n"
    "The towers. Six round towers, their names starting with 'Castle Tower': one centred on each corner of the "
    "curtain wall, about 3.5 m in radius and 17 m high to the eaves, and two flanking the gate, centred at "
    "(-6.5, -17.5) and (6.5, -17.5), about 2.4 m in radius and 16 m high. Each has a steep conical roof of "
    "blue-grey slate, clearly bluer than the stone, rising to about 25 m on the corner towers and 23 m on the "
    "gate towers; a roof's name starts with 'Castle Tower' like its tower's.\n\n"
    "The gatehouse. 'Castle Gatehouse': a block 10 m wide (x = -5 to 5) and about 7 m deep set across the "
    "middle of the south wall (y = -15), 13 m high with its own battlements (named 'Castle Gatehouse ...', not "
    "'Castle Wall ...'), with a round-arched gateway 4 m "
    "wide through it, the arch springing 3.5 m up with its crown about 5.5 m above the ground. The gateway is "
    "open: no door or portcullis closes it.\n\n"
    "The keep. 'Castle Keep': a tall rectangular hall-tower on the north side of the courtyard, about 14 m "
    "along X and 9 m along Y, centred at (0, 6), 25 m high to its eaves under a steep slate roof whose ridge "
    "runs along X about 33 m up (its name starting with 'Castle Keep'): the tallest part of the castle.\n\n"
    "Windows. At least twelve, their names starting with 'Castle Window', each with a stone frame and glass "
    "that glows with a warm light from inside: eight in two rows on the keep's courtyard side and one high on "
    "the outer side of each tower.\n\n"
    "Light. A sun light named 'Sun', 20 to 35 degrees above the horizon and warm in colour, coming from the "
    "south-west (-X, -Y), under a clear sky to match.\n\n"
    "Camera. A camera named 'Hero Camera' at (42, -74, 16), looking at (0, 0, 12) with a 35 mm lens, as the "
    "scene's camera, with the whole castle in frame. That is the view in the picture.\n\n"
    "Output. EEVEE with ray tracing on, 2560 x 1440 at 100 %. Render the still from 'Hero Camera' and save it "
    "as a PNG named 'castle-final.png'; tell me where it is.\n\n"
    "Before you call it done, look at your render next to the picture and fix what is wrong.")


def _round(tower_id):
    (x, y), radius, top, apex = castle.TOWERS[tower_id]
    # A tower's own window counts as the tower ("one high on the outer side of each tower"); a gate tower's
    # bearings into the gatehouse block, which the brief's coordinates set across it, are not asked.
    return {"kind": "round_tower", "targets": [TOWER], "set_in": [WINDOW], "centre": [x, y], "height": top - 4.0,
            "nominal": radius, "skip": [GATEHOUSE_BLOCK] if tower_id.startswith("Gate") else [],
            "radius": [radius * 0.65, radius * 1.4], "roundness": 0.2, "apex": [apex - 3.0, apex + 4.0],
            "pointed": 2.0, "roof_color": ["blue"],
            "label": "a round tower %s with a pointed blue-slate roof" % tower_id}


# The wall-walk lines between the towers and the gatehouse, each run so that +across points out of the courtyard:
# "Battlements along the whole outer edge of the wall-walk" are counted on the outer half of the 2 m wall only.
_EDGE = castle.HALF_X - castle.CORNER_RADIUS - 0.6
_RUNS = [[-_EDGE, castle.HALF_Y, _EDGE, castle.HALF_Y], [castle.HALF_X, 11.0, castle.HALF_X, -11.0],
         [-castle.HALF_X, -11.0, -castle.HALF_X, 11.0], [-5.6, -castle.HALF_Y, -_EDGE, -castle.HALF_Y],
         [_EDGE, -castle.HALF_Y, 5.6, -castle.HALF_Y]]



def _opacity():
    """Grader only, never shown to the model: the curtain wall ("no gap anywhere"), the keep's and each tower's
    slate roof have no see-through gaps; any part of the castle (a window, the gatehouse) may close them."""
    half, thick, top = castle.HALF_X, castle.WALL_THICK, castle.WALL_TOP
    low_z, high_z = 0.5, top - 0.5
    walls = []
    for name, (x0, x1) in (("north", (-16.0, 16.0)),):
        walls.append(oneshot_opacity.box_areas("curtain wall " + name, (x0, castle.HALF_Y - thick / 2, low_z),
                                               (x1, castle.HALF_Y + thick / 2, high_z), 1, -1, 2.0, 5.0, edge=0.0))
    for name, (x0, x1) in (("south-west", (-16.0, -9.4)), ("south-east", (9.4, 16.0))):
        walls.append(oneshot_opacity.box_areas("curtain wall " + name, (x0, -castle.HALF_Y - thick / 2, low_z),
                                               (x1, -castle.HALF_Y + thick / 2, high_z), 1, 1, 2.0, 5.0, edge=0.0))
    for name, x, sign in (("east", half, -1), ("west", -half, 1)):
        walls.append(oneshot_opacity.box_areas("curtain wall " + name, (x - thick / 2, -11.0, low_z),
                                               (x + thick / 2, 11.0, high_z), 0, sign, 2.0, 5.0, edge=0.0))
    (kx, ky), (sx, sy) = castle.KEEP_CENTRE, castle.KEEP_SIZE
    roofs = [oneshot_opacity.box_areas("keep roof", (kx - sx / 2, ky - sy / 2, castle.KEEP_EAVES - 0.2),
                                       (kx + sx / 2, ky + sy / 2, 60.0), 2, -1, 0.0, 60.0 - castle.KEEP_EAVES + 0.2,
                                       edge=0.6)]
    for tower_id, ((x, y), radius, eaves, _apex) in castle.TOWERS.items():
        inner = radius * 0.6
        roofs.append(oneshot_opacity.box_areas("tower %s roof" % tower_id, (x - inner, y - inner, eaves - 0.2),
                                               (x + inner, y + inner, 60.0), 2, -1, 0.0, 60.0 - eaves + 0.2,
                                               edge=0.0))
    return [{"kind": "opaque", "target": CASTLE, "areas": walls, "label": "the curtain wall has no see-through gaps"},
            {"kind": "opaque", "target": CASTLE, "areas": roofs, "grid": [12, 12],
             "label": "the keep's and towers' roofs close their footprints above the eaves"}]


CHECKS = [
    # Unused data is judged as a save writes it: zero-user data a save drops never counts (scenario_hygiene).
    {"kind": "hygiene", "absent": ["Cube", "Light", "Camera"], "max_default_named": 0, "max_without_material": 0,
     "max_unused": 0, "unused_counted": "saved", "label": "tidy file: no default leftovers, named, materials"},
    {"kind": "object", "target": GROUND, "type": "MESH", "label": "Ground exists"},
    {"kind": "bbox", "target": GROUND, "size": [[110, 240], [110, 240], None], "label": "a meadow about 160 m across"},
    {"kind": "probe", "origin": [40, 40, 30], "direction": [0, 0, -1], "target": GROUND, "axis": "z",
     "range": [-0.35, 0.35], "distance": 60.0, "expect": "hit", "label": "the meadow is at height 0"},
    {"kind": "surface", "target": GROUND, "color": ["green"], "label": "a grassy meadow"},
    {"kind": "groups", "target": TOWER, "gap": 0.3, "size": [10.0, 45.0], "count": [6, 30],
     "label": "at least six towers"},
    *[_round(tower_id) for tower_id in castle.TOWERS],
    {"kind": "bbox", "target": WALL, "min": [-castle.HALF_X - 1.0, -castle.HALF_Y - 1.0, 0.0],
     "max": [castle.HALF_X + 1.0, castle.HALF_Y + 1.0, None], "tolerance": 1.0, "size": [None, None, [8.5, 12.0]],
     "label": "a curtain wall on the 40 x 30 m courtyard, its walk 9 m up"},
    {"kind": "encloses", "targets": [WALL, TOWER, GATE, WINDOW], "centre": [0.0, -4.0, 7.0], "distance": 45.0, "rays": 72,
     "label": "the wall, towers and gatehouse enclose the courtyard"},
    {"kind": "crenellations", "targets": [WALL], "lines": _RUNS, "across": [0.2, 0.4, 0.6, 0.8],
     "step": 0.1, "rise": 0.5, "count": [30, 300], "label": "battlements: at least thirty merlons on the wall"},
    {"kind": "surface", "target": WALL, "color": ["grey", "warm"], "roughness": [0.5, 1.0],
     "label": "pale stone walls"},
    {"kind": "bbox", "target": GATE, "min": [castle.GATE_X[0], castle.GATE_Y[0], 0.0],
     "max": [castle.GATE_X[1], castle.GATE_Y[1], None], "tolerance": 1.6, "size": [None, None, [11.0, 16.0]],
     "label": "a gatehouse 10 m wide across the south wall, 13 m high"},
    {"kind": "arch", "targets": [GATE], "x": [-1.6, -1.0, 0.0, 1.0, 1.6], "y": -castle.HALF_Y, "crown": [4.6, 7.0],
     "rise": 0.4, "side_from": 1.4,
     # Through the passage only: "The gateway is open: no door or portcullis closes it."
     "clear": {"origin": [0.0, castle.GATE_Y[0] - 1.0, 1.5], "direction": [0.0, 1.0, 0.0],
               "distance": castle.GATE_Y[1] - castle.GATE_Y[0] + 2.0,
               "targets": [CASTLE]},
     "label": "an open round-arched gateway through the gatehouse"},
    {"kind": "bbox", "target": KEEP, "min": [-7.0, 1.5, 0.0], "max": [7.0, 10.5, None], "tolerance": 1.6,
     "size": [None, None, [28.0, 40.0]], "label": "a keep about 14 x 9 m at (0, 6) on the north side"},
    {"kind": "tallest", "target": KEEP, "others": [dict(CASTLE, **{"except": [KEEP]})], "margin": 0.001,
     "label": "the keep is the tallest part of the castle"},
    {"kind": "groups", "target": WINDOW, "gap": 0.2, "size": [0.6, 4.0], "count": [12, 400],
     "label": "at least twelve windows"},
    {"kind": "surface", "target": WINDOW, "emission_min": 0.3, "emission_color": ["warm"],
     "label": "the windows glow warm"},
    *_opacity(),
    {"kind": "supported", "target": CASTLE, "ground_target": GROUND, "label": "nothing of the castle floats"},
    {"kind": "apart", "targets": [TOWER, KEEP], "gap": 0.3, "max_overlap": 0.1,
     "label": "the towers and the keep stand apart"},
    {"kind": "light", "target": {"name": "Sun"}, "type": "SUN", "elevation_deg": [17, 38], "color": ["warm"],
     "label": "a warm Sun 20 to 35 degrees up"},
    {"kind": "light_from", "target": {"name": "Sun"}, "azimuth_deg": 225.0, "within_deg": 50.0,
     "label": "the sun comes from the south-west"},
    {"kind": "world", "sky_or_color": ["warm", "blue"], "sun_elevation_deg": [10, 45], "label": "a sky to match"},
    {"kind": "camera", "target": {"name": "Hero Camera"}, "active": True, "location": list(HERO["location"]),
     "radius": 0.5, "look_at": list(HERO["target"]), "min_dot": 0.99, "lens": [33.0, 37.0],
     "label": "Hero Camera placed, aimed and active"},
    {"kind": "frames", "target": CASTLE, "margin": 0.02, "fill": [0.0, 1.0],
     "label": "the camera frames the whole castle"},
    {"kind": "scene", "values": {"render.engine": "BLENDER_EEVEE", "render.resolution_x": 2560,
                                 "render.resolution_y": 1440, "render.resolution_percentage": 100,
                                 "eevee.use_raytracing": True}, "label": "EEVEE, ray tracing, 2560 x 1440"},
]

TURNS = [{
    "id": "brief", "title": "The castle and its final still",
    "references": ["hero"], "prompt": PROMPT, "checks": CHECKS,
    "outputs": [{"name": FINAL_STILL["name"], "min_size": list(FINAL_STILL["size"]),
                 "label": "the final still was rendered and saved (2560 x 1440 or larger)"}],
    "job": {"kind": "RENDER_STILL", "output": "renders/" + FINAL_STILL["name"],
            "resolution": list(FINAL_STILL["size"]), "samples": 16, "format": "PNG", "minutes": 10},
}]


# ------------------------------------------------------------------ the stand-in

def build_turn(bpy, turn_id):
    """The whole stand-in (the brief is one turn): the plainest castle that follows the brief."""
    if turn_id != "brief":
        raise KeyError(turn_id)
    oneshot_kit.clear_default_scene(bpy)
    scene = bpy.context.scene
    half = MEADOW / 2
    ground = kit.mesh_object(bpy, "Ground", [(-half, -half, 0.0), (half, -half, 0.0), (half, half, 0.0),
                                             (-half, half, 0.0)], [(0, 1, 2, 3)])
    kit.assign(ground, kit.principled(bpy, "Meadow Grass", (0.12, 0.26, 0.05), roughness=0.9))
    path = kit.mesh_object(bpy, "Gate Path", *kit.box_geometry((4.0, half + castle.GATE_Y[0] - 0.1, 0.04),
                                                               (0.0, (castle.GATE_Y[0] - half) / 2, 0.02)))
    kit.assign(path, kit.principled(bpy, "Path Gravel", (0.32, 0.28, 0.22), roughness=0.95))
    # The meadow runs on to the horizon a little below the plain, so the camera never sees under the sky.
    far = kit.mesh_object(bpy, "Far Meadow", [(-900.0, -900.0, -0.03), (900.0, -900.0, -0.03), (900.0, 900.0, -0.03),
                                              (-900.0, 900.0, -0.03)], [(0, 1, 2, 3)])
    kit.assign(far, bpy.data.materials["Meadow Grass"])
    castle.castle(bpy)
    kit.sun_object(bpy, "Sun", SUN_ELEVATION, SUN_AZIMUTH, (1.0, 0.78, 0.55), 6.0, angle_deg=1.0)
    scene.world = kit.sky_world(bpy, "Afternoon Sky", SUN_ELEVATION, SUN_AZIMUTH, strength=0.3, aerosol=1.2)
    scene.camera = kit.camera_object(bpy, "Hero Camera", HERO["location"], HERO["target"], HERO["lens"])
    render = scene.render
    render.engine = "BLENDER_EEVEE"
    render.resolution_x, render.resolution_y, render.resolution_percentage = *FINAL_STILL["size"], 100
    scene.eevee.use_raytracing = True
    bpy.context.view_layer.update()


def build(bpy, upto=None):
    build_turn(bpy, "brief")
