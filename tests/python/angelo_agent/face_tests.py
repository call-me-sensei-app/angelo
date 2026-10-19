# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the face and hair one-shot tests share: their cameras, checks, ground-truth scene and start file.

The tests (``scenario_oneshot_head_*``, ``scenario_oneshot_hair_*``) each
ask for one head or one hairstyle matched to orthographic reference pictures
whose scale the brief states, so a side can place every feature to the
millimetre. Their closed checks are ``head_checks`` (a side's readings
against the ground truth's, in the pictures' own views) plus the usual
tidy-file, light, camera, render and still checks.

* ``cameras``: front, side (the head's right, from -X), top and a
  three-quarter hero view, plus back for hair; front and side are the
  orthographic pictures the brief describes (1280 x 720).
* ``face_checks`` / ``hair_checks`` / ``scene_checks``: the checks, the
  tolerances the briefs state.
* ``build_scene``: lights, world, the hero camera and the render settings of
  a ground truth; ``make_start`` / ``setup``: a start file holding a head and
  its eyes, appended into both sides' factory scene before the message.
"""

import hashlib
import math
from pathlib import Path
import time

import numpy  # noqa: F401  (loaded with the scenario, before any turn: see setup)

# At import, never inside a function: Blender MCP's grading prelude puts this folder on sys.path only while it
# imports the scenario (bench_mcp_grading._scenario_prelude), so a later import from here would fail.
import head_designs
import head_kit
import head_sdf
import scenario_build_kit as kit

HERO = {"location": (0.62, -1.05, 0.10), "target": (0.0, 0.0, -0.02), "lens": 85.0}
HAIR_HERO = {"location": (0.72, -1.20, 0.16), "target": (0.0, 0.0, -0.05), "lens": 70.0}
STILL_SIZE = (1920, 1080)
START = "start.blend"
HEAD, EYE_L, EYE_R = {"name": "Head"}, {"name": "Head Eye L"}, {"name": "Head Eye R"}
FACE_PARTS = ({"prefix": "Head"},)


def cameras(scale, centre_z, hero, back=False):
    """The fixed views: orthographic front, side and top ``scale`` metres across, centred at ``centre_z``."""
    distance = 2.0
    found = {
        "front": {"location": (0.0, -distance, centre_z), "target": (0.0, 0.0, centre_z), "ortho_scale": scale,
                  "role": "reference", "label": "Front (orthographic, looking along +Y)"},
        "side": {"location": (-distance, 0.0, centre_z), "target": (0.0, 0.0, centre_z), "ortho_scale": scale,
                 "role": "reference", "label": "Right side (orthographic, looking along +X)"},
        "top": {"location": (0.0, 0.0, distance), "target": (0.0, 0.0, centre_z), "ortho_scale": scale,
                "role": "reference", "label": "Top (orthographic, +Y up)"},
        "hero": dict(hero, role="reference", label="Three-quarter hero (the scene camera)"),
    }
    if back:
        found["back"] = {"location": (0.0, distance, centre_z), "target": (0.0, 0.0, centre_z), "ortho_scale": scale,
                         "role": "reference", "label": "Back (orthographic, looking along -Y)"}
    return found


def face_checks(scenario, tolerance, eyes=True, mouth=True):
    """The head against the truth: profile, eyes, chin and jaw, mouth, both outlines and the relief."""
    checks = [
        {"kind": "object", "target": HEAD, "type": "MESH", "label": "a mesh named 'Head'"},
        {"kind": "face_outline", "scenario": scenario, "view": "front", "tolerance_mm": tolerance,
         "label": "the front outline within %g mm of the front picture" % tolerance},
        {"kind": "face_outline", "scenario": scenario, "view": "side", "tolerance_mm": tolerance,
         "label": "the side outline within %g mm of the side picture" % tolerance},
        {"kind": "face_landmarks", "scenario": scenario, "tolerance_mm": tolerance,
         "label": "the profile's brow, nose, lips and chin within %g mm" % tolerance},
        {"kind": "face_jaw", "scenario": scenario, "tolerance_mm": tolerance,
         "label": "the chin's bottom and the jaw line within %g mm" % tolerance},
        {"kind": "face_relief", "scenario": scenario, "tolerance_mm": tolerance,
         "label": "the face's relief within %g mm, region by region" % tolerance},
    ]
    if eyes:
        checks[1:1] = [{"kind": "object", "target": EYE_L, "type": "MESH", "label": "an eye named 'Head Eye L'"},
                       {"kind": "object", "target": EYE_R, "type": "MESH", "label": "an eye named 'Head Eye R'"},
                       {"kind": "face_eyes", "scenario": scenario, "tolerance_mm": tolerance,
                        "label": "both eyes within %g mm of the pictures" % tolerance}]
    if mouth:
        checks.append({"kind": "face_mouth", "scenario": scenario, "tolerance_mm": tolerance,
                       "label": "the mouth line's corners and middle within %g mm" % tolerance})
    return checks


def hair_checks(scenario, outline_mm, hairline_mm, length_mm, min_iou, min_strands):
    return [
        {"kind": "hair_strands", "target": {"prefix": "Hair"}, "head": "Head", "min_strands": min_strands,
         "rooted_mm": 3.0, "rooted_share": 0.9, "inside_share": 0.03,
         "label": "the hair is at least %d strands named 'Hair', rooted on the scalp, never inside the head"
                  % min_strands},
        {"kind": "hair_shape", "scenario": scenario, "views": ["front", "side", "back"], "min_iou": min_iou,
         "tolerance_mm": outline_mm,
         "label": "the hair covers what the pictures show (overlap %g) and its outline is within %g mm"
                  % (min_iou, outline_mm)},
        {"kind": "hairline", "scenario": scenario, "tolerance_mm": hairline_mm,
         "label": "the hairline over the forehead within %g mm" % hairline_mm},
        {"kind": "hair_length", "scenario": scenario, "tolerance_mm": length_mm,
         "label": "the hair's length at the back and the sides within %g mm" % length_mm},
        {"kind": "face_outline", "scenario": scenario, "view": "front", "tolerance_mm": 0.5,
         "label": "the head itself is unchanged (front outline within 0.5 mm)"},
        {"kind": "face_outline", "scenario": scenario, "view": "side", "tolerance_mm": 0.5,
         "label": "the head itself is unchanged (side outline within 0.5 mm)"},
    ]


def scene_checks(hero, still):
    """A tidy file, the key light, the hero camera and the render settings."""
    return [
        {"kind": "hygiene", "absent": ["Cube", "Light", "Camera"], "max_default_named": 0, "max_without_material": 0,
         "max_unused": 12, "label": "tidy file: no default leftovers, named, materials"},
        {"kind": "light", "target": {"name": "Key Light"}, "label": "a light named 'Key Light'"},
        {"kind": "camera", "target": {"name": "Hero Camera"}, "active": True, "location": list(hero["location"]),
         "radius": 0.02, "look_at": list(hero["target"]), "min_dot": 0.999,
         "lens": [hero["lens"] - 2.0, hero["lens"] + 2.0], "label": "Hero Camera placed, aimed and active"},
        {"kind": "scene", "values": {"render.engine": "BLENDER_EEVEE", "render.resolution_x": still[0],
                                     "render.resolution_y": still[1], "render.resolution_percentage": 100},
         "label": "EEVEE, %d x %d" % still},
    ]


def turn(prompt, references, checks, still_name, title):
    return [{"id": "brief", "title": title, "references": list(references), "prompt": prompt, "checks": checks,
             "outputs": [{"name": still_name, "min_size": list(STILL_SIZE),
                          "label": "the final still was rendered and saved (%d x %d or larger)" % STILL_SIZE}],
             "job": {"kind": "RENDER_STILL", "output": "renders/" + still_name, "resolution": list(STILL_SIZE),
                     "samples": 16, "format": "PNG", "minutes": 10}}]


# ---------------------------------------------------------------- the ground truth's scene


def build_scene(bpy, hero, target):
    """Lights, a neutral studio world, the hero camera and the render settings (the briefs' own)."""
    scene = bpy.context.scene
    head_kit.portrait_lights(bpy, target)
    scene.world = head_kit.studio_world(bpy)
    scene.camera = kit.camera_object(bpy, "Hero Camera", hero["location"], hero["target"], hero["lens"])
    render = scene.render
    render.engine = "BLENDER_EEVEE"
    render.resolution_x, render.resolution_y, render.resolution_percentage = *STILL_SIZE, 100
    bpy.context.view_layer.update()


def make_start(bpy, folder, design, cell):
    """Start job (``scenario_start_job``): a scene holding only the head ``design`` and its eyes, saved as
    ``START``; then, as a trial does, the factory scene with that file appended. Returns what the manifest keeps."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    head_kit.build_head(bpy, design, cell=cell)
    path = Path(folder) / START
    bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True, compress=False)
    data = path.read_bytes()
    saved = {"file": START, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "design": design,
             "cell_m": cell}
    bpy.ops.wm.read_factory_settings(use_empty=False)
    started = time.perf_counter()
    appended = setup(bpy, bpy.context.scene, path, saved["sha256"])
    return dict(saved, append=appended, append_total_s=round(time.perf_counter() - started, 3))


def setup(bpy, scene, path, sha256):
    """Both sides, before the first message: the start file's head and eyes appended into ``scene``.

    NumPy, which the graders use, is loaded with this module, before any turn, as ``scenario_heavy`` does: a
    scripted agent's code that imports it and then drops what it imported would otherwise leave a second NumPy.
    """
    return head_kit.append_start(bpy, scene, path, sha256)


def replace_head(bpy, design, cell=0.0012):
    """The perfect agent of a start-file test: the start head's mesh becomes the ground truth's, and the eyes move."""
    head = bpy.data.objects["Head"]
    data = head_designs.design(design)
    vertices, quads = head_sdf.surface_nets(data, data["low"], data["high"], cell)
    mesh = head_kit.mesh_from_quads(bpy, "Head", vertices, quads)
    for material in head.data.materials:
        mesh.materials.append(material)
    old, head.data = head.data, mesh
    bpy.data.meshes.remove(old)
    left, right = head_designs.eye_centres(data)
    bpy.data.objects["Head Eye L"].location = left
    bpy.data.objects["Head Eye R"].location = right


def pixel_mm(scale, width=1280):
    return scale / width * 1000.0


def frame_text(scale, centre_z):
    """How the brief states the orthographic pictures' scale and centre."""
    height = scale * 720.0 / 1280.0
    return ("Both orthographic pictures are %s m across and %s m tall (1280 x 720 pixels, so %s mm a pixel), "
            "centred on x = 0, z = %s in the front view and on y = 0, z = %s in the side view."
            % (_g(scale), _g(height), _g(pixel_mm(scale)), _g(centre_z), _g(centre_z)))


def _g(value):
    text = ("%.4f" % value).rstrip("0").rstrip(".")
    return text if text not in ("-0", "") else "0"


def distance(a, b):
    return math.dist(a, b)
