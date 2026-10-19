# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""angelo_view's reference comparisons (OVERLAY, OUTLINE) and MATCAP and CURVATURE looks in the compiled app.

Run by ``run_native_suites.py --gui --include test_blender_view_outline`` (offscreen drawing needs a window).
Checked against Blender itself, never against this code's own reading:

* A reference made by drawing a head through a free camera, compared by OUTLINE from that camera, matches
  itself (orthographic and perspective): overlap and distances within a pixel.
* Moving the head by a known amount, or scaling it, gives the centre offset Blender's own projection
  (``world_to_camera_view``) gives, and the size ratio it was scaled by.
* The targets' silhouette (``view_silhouette``) holds every point of a hair curves object Blender projects,
  and nothing farther from them than a strand's drawn width.
* A cut-out (transparent) reference is outlined by its transparency; a busy background is said to be a guess.
* OVERLAY is the image and the view averaged; MATCAP and CURVATURE draw and differ from SOLID.
* Wrong compare arguments are VALIDATION refusals, and every call leaves the document, the viewports and the
  data-blocks unchanged (snapshot, SceneCheck, depsgraph updates). No provider, account or network.
Inspect results.json; a zero exit status alone is not proof.
"""

import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

import bpy
import numpy as np
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Matrix, Vector

assert "--factory-startup" in sys.argv, "Use an isolated factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-view-outline-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path[:0] = [str(ROOT / "scripts/addons_core"), str(Path(__file__).resolve().parent)]
from angelo_agent import scene_queries, view_cache, view_silhouette  # noqa: E402
from angelo_agent.action_camera_light_spec import look_at_euler  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.refusal_codes import refusal_code  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402
from angelo_agent.view_pixels import encode_rgba_png  # noqa: E402
from view_outline_kit import Wrapped, decode  # noqa: E402

report = {"checks": {}, "measurements": {}, "binary": bpy.app.binary_path}
ctx = {}
SIZE = 512
FRONT = {"location": [0.0, -1.5, 0.0], "look_at": [0.0, 0.0, 0.0], "ortho_scale": 0.6}
PERSPECTIVE = {"location": [0.9, -1.25, 0.3], "look_at": [0.0, 0.0, 0.0], "lens": 85.0}
SHIFT = 0.03   # World units the head moves along +X.
SCALE = 1.05


def check(name, condition):
    report["checks"][name] = bool(condition)


def view(args, *, label):
    """One angelo_view call, checked to leave the document, viewports and data unchanged (view_outline_kit)."""
    return ctx["wrapped"].view(args, label=label)


def refused(args):
    """``(error type, refusal code, message)`` of a call that must be refused (None when it succeeded)."""
    try:
        ctx["executor"].execute("angelo_view", args)
    except Exception as error:  # noqa: BLE001 - the type, code and message are what is checked.
        return type(error).__name__, refusal_code(error), str(error)
    return None


# ---- Fixture ------------------------------------------------------------------------------------------------------

def _hair(count=1500, points=8):
    rng = np.random.default_rng(7)
    theta, lift = rng.uniform(0.0, 2.0 * np.pi, count), rng.uniform(0.05, 0.17, count)
    steps = np.arange(points)[None, :]
    positions = np.zeros((count, points, 3), np.float32)
    positions[..., 0] = np.cos(theta)[:, None] * (0.11 + 0.012 * steps)
    positions[..., 1] = np.sin(theta)[:, None] * (0.11 + 0.012 * steps)
    positions[..., 2] = lift[:, None] - 0.025 * steps
    curves = bpy.data.hair_curves.new("Hair")
    curves.add_curves([points] * count)
    curves.position_data.foreach_set("vector", positions.ravel())
    radius = curves.attributes.get("radius") or curves.attributes.new("radius", "FLOAT", "POINT")
    radius.data.foreach_set("value", np.full(count * points, 0.0008, np.float32))
    obj = bpy.data.objects.new("Hair", curves)
    ctx["window"].scene.collection.objects.link(obj)
    return obj, positions


def setup():
    window = bpy.context.window_manager.windows[0]
    ctx.update(window=window, area=next(a for a in window.screen.areas if a.type == "VIEW_3D"))
    check("checkout_modules_loaded", str(ROOT) in scene_queries.__file__)
    scene = window.scene
    scene.render.resolution_x = scene.render.resolution_y = SIZE   # Free cameras draw at the render aspect.
    bpy.data.objects.remove(scene.objects["Cube"])
    with bpy.context.temp_override(window=window, area=ctx["area"]):
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.12, segments=64, ring_count=32, location=(0, 0, 0))
    head = ctx["head"] = bpy.context.active_object
    head.name, head.scale = "Head", (1.0, 1.1, 1.3)
    ctx["hair"], ctx["strands"] = _hair()
    ctx["hair"].hide_viewport = True    # Shown only for the hair step.
    window.view_layer.update()
    ctx["refs"] = ReferenceStore(OUT, "3b8f3f5e-21a1-4d0e-9a51-3f8b7f0e2c61")
    ctx["executor"] = BlenderExecutor(window)
    ctx["executor"].observation_store = ctx["refs"]
    ctx["wrapped"] = Wrapped(window, ctx["executor"])
    ctx["head_handle"] = scene_queries.object_handle(head)
    ctx["hair_handle"] = scene_queries.object_handle(ctx["hair"])


def _attach(name, rows):
    source = OUT / "sources" / name
    source.parent.mkdir(exist_ok=True)
    source.write_bytes(encode_rgba_png(rows))
    return ctx["refs"].import_images([str(source)])[0]["id"]


def _reference(camera, label):
    """The head drawn through ``camera`` as a reference picture: its id and its RGB rows."""
    view_cache.clear()
    drawn = view({"views": [dict(camera)], "targets": [ctx["head_handle"]]}, label=label)
    rows = decode(drawn["views"][0]["path"])
    rgba = np.concatenate([rows, np.full(rows.shape[:2] + (1,), 255, np.int16)], axis=2).astype(np.uint8)
    return _attach(label + ".png", rgba), rows


def _outline(camera, reference, label, targets=None):
    result = view({"views": [dict(camera, reference=reference, compare="OUTLINE")],
                   "targets": targets or [ctx["head_handle"]]}, label=label)
    return result, result["views"][0]


def _centre_px(obj, camera_object):
    """Where Blender projects an object's bounds centre, in pixels of the square view (right, down)."""
    centre = obj.matrix_world @ (sum((Vector(corner) for corner in obj.bound_box), Vector()) / 8.0)
    x, y, _depth = world_to_camera_view(ctx["window"].scene, camera_object, centre)
    return np.array([x * SIZE, (1.0 - y) * SIZE])


def _off_surface(obj, point):
    """How far a world point lies from an object's surface, by Blender's own closest-point query."""
    inverse = obj.matrix_world.inverted()
    found, nearest, _normal, _index = obj.closest_point_on_mesh(inverse @ Vector(point))
    return (obj.matrix_world @ nearest - Vector(point)).length if found else float("inf")


def _camera_object(values):
    data = bpy.data.cameras.new("Check")
    if "ortho_scale" in values:
        data.type, data.ortho_scale = "ORTHO", values["ortho_scale"]
    else:
        data.lens = values["lens"]
    obj = bpy.data.objects.new("Check", data)
    obj.location = values["location"]
    obj.rotation_euler = look_at_euler(values["location"], values["look_at"])
    ctx["window"].scene.collection.objects.link(obj)
    ctx["window"].view_layer.update()
    return obj


# ---- Steps --------------------------------------------------------------------------------------------------------

def outline_matches_itself():
    ctx["references"] = {}
    for name, camera in (("orthographic", FRONT), ("perspective", PERSPECTIVE)):
        reference, rows = _reference(camera, "head-" + name)
        ctx["references"][name] = (reference, rows)
        result, record = _outline(camera, reference, "outline itself " + name)
        found = record["outline"]
        report["measurements"]["itself_" + name] = found
        # Within a pixel: the subject's antialiased edge is the only difference (overlap then depends on size).
        check(name + "_outline_matches_itself", found["compared"] and found["p95_px"] <= 1.0
              and found["max_px"] <= 2.0 and max(map(abs, found["centre_offset_px"])) <= 0.5
              and abs(found["size_ratio"] - 1.0) <= 0.01 and found["iou"] >= 0.98)
        check(name + "_reference_subject_from_plain_background",
              found["reference"]["subject_from"] == "PLAIN_BACKGROUND" and not found["reference"]["touches_border"])
        picture = decode(record["path"])
        cyan = (picture[..., 0] < 40) & (picture[..., 1] > 180) & (picture[..., 2] > 220)
        check(name + "_outline_picture_is_the_view_with_outlines",
              (record["width"], record["height"]) == (SIZE, SIZE) == picture.shape[1::-1]
              and record["layout"] == "OUTLINES_ON_VIEW" and cyan.sum() > 200)
        check(name + "_note_states_the_outline", "An OUTLINE is the view" in result["note"])
    check("orthographic_pixel_world_is_ortho_scale_over_size",
          abs(report["measurements"]["itself_orthographic"]["pixel_world"] - FRONT["ortho_scale"] / SIZE) < 1e-6)


def outline_measures_a_change():
    """A known move and scale of the head: the offset Blender's projection gives, the ratio it was scaled by."""
    head, window = ctx["head"], ctx["window"]
    for name, camera in (("orthographic", FRONT), ("perspective", PERSPECTIVE)):
        checker = _camera_object(camera)
        checker_axes = [tuple(checker.matrix_world.col[axis][:3]) for axis in (0, 1)]   # Its right and up.
        before = _centre_px(head, checker)
        head.location.x += SHIFT
        window.view_layer.update()
        expected = _centre_px(head, checker) - before
        _result, record = _outline(camera, ctx["references"][name][0], "moved " + name)
        found = record["outline"]
        on_surface = [_off_surface(head, gap["surface_world"]) for gap in found.get("gaps", ())]
        head.location.x -= SHIFT
        head.scale = tuple(value * SCALE for value in head.scale)
        window.view_layer.update()
        _result, scaled = _outline(camera, ctx["references"][name][0], "scaled " + name)
        head.scale = (1.0, 1.1, 1.3)
        bpy.data.objects.remove(checker)
        window.view_layer.update()
        report["measurements"]["moved_" + name] = dict(found, expected_offset_px=expected.round(3).tolist(),
                                                       gaps_off_surface=on_surface)
        gaps = found.get("gaps") or []
        # The move as Blender projects it: the largest gap is that many pixels, and every offset points back
        # against it in the view's plane (from the targets' edge to the picture's).
        right, up = (Vector(checker_axes[0]), Vector(checker_axes[1]))
        back = [Vector(gap["offset_world"]).dot(right) * expected[0] - Vector(gap["offset_world"]).dot(up) * expected[1]
                for gap in gaps]
        check(name + "_gaps_point_back_along_the_move", gaps
              and abs(gaps[0]["gap_px"] - float(np.linalg.norm(expected))) <= 2.0 and all(value < 0 for value in back)
              and {"OUT", "IN"} <= {gap["direction"] for gap in gaps})
        check(name + "_gap_points_lie_on_the_moved_surface", on_surface and max(on_surface) <= found["pixel_world"])
        report["measurements"]["scaled_" + name] = scaled["outline"]
        check(name + "_move_gives_blenders_projected_offset",
              np.abs(np.array(found["centre_offset_px"]) - expected).max() <= 1.0 and found["iou"] < 0.95)
        check(name + "_scale_gives_its_ratio", abs(scaled["outline"]["size_ratio"] - SCALE) <= 0.01)
        if "ortho_scale" in camera:
            check("orthographic_move_in_world_units",
                  abs(found["centre_offset_px"][0] * found["pixel_world"] - SHIFT) <= found["pixel_world"])


def hair_silhouette():
    """The hair's silhouette holds every strand point Blender projects, and reaches no farther than it should."""
    hair, window = ctx["hair"], ctx["window"]
    hair.hide_viewport = False
    window.view_layer.update()
    checker = _camera_object(FRONT)
    camera = {"view_matrix": [list(row) for row in checker.matrix_world.inverted()],
              "projection_matrix": [list(row) for row in checker.calc_matrix_camera(
                  window.view_layer.depsgraph, x=SIZE, y=SIZE)], "details": {"ortho_scale": FRONT["ortho_scale"]}}
    items, facts = view_silhouette.geometry(window, [hair])
    mask, _depth, drawn = view_silhouette.draw(items, camera, SIZE, SIZE)
    strands = ctx["strands"]
    dense = (strands[:, :-1, None] + (strands[:, 1:] - strands[:, :-1])[:, :, None]
             * np.linspace(0.0, 1.0, 12)[None, None, :, None]).reshape(-1, 3)
    pixels = np.array([world_to_camera_view(window.scene, checker, Vector(p))[:2] for p in dense])
    cols, rows = (pixels[:, 0] * SIZE).astype(int), ((1.0 - pixels[:, 1]) * SIZE).astype(int)
    inside = (cols >= 0) & (cols < SIZE) & (rows >= 0) & (rows < SIZE)
    near = np.zeros_like(mask)
    near[rows[inside], cols[inside]] = True
    reach = 2   # A strand drawn one sample wide touches a pixel either side; one more of rounding.
    grown = np.zeros_like(mask)
    for dy in range(-reach, reach + 1):
        for dx in range(-reach, reach + 1):
            grown |= np.roll(np.roll(near, dy, axis=0), dx, axis=1)
    padded = np.pad(mask, 1)
    held = np.zeros_like(mask)   # Within a pixel in any direction: Blender's projection and the rasterizer round apart.
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            held |= padded[dy:dy + SIZE, dx:dx + SIZE]
    report["measurements"]["hair_silhouette"] = dict(facts, **drawn, points=int(inside.sum()),
                                                      not_held=int((~held[rows[inside], cols[inside]]).sum()),
                                                      beyond=float((mask & ~grown).sum() / max(mask.sum(), 1)))
    found = report["measurements"]["hair_silhouette"]
    # A strand thinner than a pixel and seen end on covers no pixel centre, so the rasterizer (Blender's own
    # draw too) shows nothing of it: at most one projected point in ten thousand may fall there.
    check("hair_silhouette_holds_the_projected_points", found["points"] > 100000
          and found["not_held"] <= found["points"] // 10000)
    check("hair_silhouette_reaches_no_farther_than_its_strands", found["beyond"] <= 0.001)
    check("hair_strands_are_drawn_as_strands", drawn.get("strands") is True)
    reference, _rows = ctx["references"]["orthographic"]
    _result, record = _outline(FRONT, reference, "hair and head", [ctx["head_handle"], ctx["hair_handle"]])
    check("hair_and_head_outline_compares", record["outline"]["compared"]
          and record["outline"]["size_ratio"] > 1.05)   # The hair stands outside the head's outline.
    bpy.data.objects.remove(checker)
    hair.hide_viewport = True
    window.view_layer.update()


def cut_out_and_busy_references():
    _reference_id, rows = ctx["references"]["orthographic"]
    subject = np.abs(rows - rows[0, 0]).max(axis=2) > 3   # The drawn background is one flat colour.
    noise = np.random.default_rng(3).integers(0, 256, rows.shape[:2] + (3,))
    cut_out = np.concatenate([noise, np.where(subject, 255, 0)[..., None]], axis=2).astype(np.uint8)
    result, record = _outline(FRONT, _attach("cut-out.png", cut_out), "cut-out reference")
    found = record["outline"]
    report["measurements"]["cut_out"] = found
    check("cut_out_is_outlined_by_its_transparency", found["reference"]["subject_from"] == "TRANSPARENCY"
          and found["iou"] >= 0.99 and not result["uncertain"])
    busy = np.where(subject[..., None], rows, noise).astype(np.uint8)
    busy = np.concatenate([busy, np.full(busy.shape[:2] + (1,), 255, np.uint8)], axis=2)
    result, record = _outline(FRONT, _attach("busy.png", busy), "busy reference")
    codes = {note["code"] for note in result["uncertainty"]}
    report["measurements"]["busy"] = record["outline"]
    check("busy_background_is_stated", record["outline"]["reference"]["subject_from"] == "BUSY_BACKGROUND"
          and "reference_background_busy" in codes)


def overlay():
    reference, rows = ctx["references"]["orthographic"]
    view_cache.clear()
    plain = decode(view({"views": [dict(FRONT)], "targets": [ctx["head_handle"]]}, label="plain")["views"][0]["path"])
    result = view({"views": [dict(FRONT, reference=reference, compare="OVERLAY")],
                   "targets": [ctx["head_handle"]]}, label="overlay")
    record = result["views"][0]
    picture = decode(record["path"])
    report["measurements"]["overlay_error"] = float(np.abs(picture - (plain + rows) // 2).max())
    check("overlay_is_image_and_view_averaged", picture.shape[1::-1] == (SIZE, SIZE)
          and report["measurements"]["overlay_error"] <= 1 and record["layout"] == "REFERENCE_OVER_VIEW")


def looks():
    pictures = {}
    for shading in ("SOLID", "MATCAP", "CURVATURE"):
        view_cache.clear()
        result = view({"views": ["FRONT"], "targets": [ctx["head_handle"]], "shading": shading}, label=shading)
        pictures[shading] = decode(result["views"][0]["path"])
        check(shading.lower() + "_draws_without_a_missing_light",
              result["shading"] == shading and "studio_light_unavailable" not in
              {note["code"] for note in result["uncertainty"]})
    differ = {"%s_%s" % pair: round(float(np.abs(pictures[pair[0]] - pictures[pair[1]]).mean()), 3)
              for pair in (("SOLID", "MATCAP"), ("SOLID", "CURVATURE"), ("MATCAP", "CURVATURE"))}
    report["measurements"]["look_differences"] = differ
    check("matcap_and_curvature_are_their_own_looks", min(differ.values()) >= 2.0)


def refusals():
    """Invalid calls are refused before anything is drawn, naming the field (VALIDATION once admitted, as G09's)."""
    reference = ctx["references"]["orthographic"][0]
    cases = {"compare_without_reference": ({"views": [dict(FRONT, compare="OVERLAY")]}, "views[0].compare"),
             "outline_without_targets": ({"views": [dict(FRONT, reference=reference, compare="OUTLINE")]},
                                         "outlines the targets"),
             "unknown_compare": ({"views": [dict(FRONT, reference=reference, compare="SIDE")]}, "views[0].compare")}
    found = {}
    for name, (args, words) in cases.items():
        found[name] = refused(args)
        check(name + "_is_refused_naming_the_field", found[name] is not None
              and (found[name][0] == "ValueError" or found[name][1] == "VALIDATION") and words in found[name][2])
    report["measurements"]["refusals"] = found


def timing():
    reference = ctx["references"]["orthographic"][0]
    walls = {}
    for compare in ("PAIR", "OUTLINE"):
        result = view({"views": [dict(FRONT, reference=reference, compare=compare)], "targets": [ctx["head_handle"]],
                       "resolution": 1024}, label="timing " + compare)
        walls[compare] = result["timing"]["capture_wall_ms"]
    report["measurements"]["capture_wall_ms_1024"] = walls
    check("outline_adds_under_a_second_at_1024", walls["OUTLINE"] - walls["PAIR"] < 1000)


def read_only():
    calls = ctx["wrapped"].calls
    report["measurements"]["wrapped_calls"] = len(calls)
    report["measurements"]["changed_calls"] = ctx["wrapped"].changed()
    check("every_call_left_the_document_viewports_and_data_unchanged",
          len(calls) >= 15 and not report["measurements"]["changed_calls"])


STEPS = [setup, outline_matches_itself, outline_measures_a_change, hair_silhouette, cut_out_and_busy_references,
         overlay, looks, refusals, timing, read_only]


def run():
    index = ctx.setdefault("step", 0)
    try:
        with bpy.context.temp_override(window=ctx.get("window") or bpy.context.window_manager.windows[0]):
            STEPS[index]()
    except Exception:
        report.update(success=False, failed_step=STEPS[index].__name__, error=traceback.format_exc())
        return finish()
    ctx["step"] += 1
    if ctx["step"] < len(STEPS):
        return 0.25  # Let the event loop run between steps, as in the application.
    report["success"] = all(report["checks"].values())
    return finish()


def finish():
    report.setdefault("success", False)
    if "wrapped" in ctx:
        ctx["wrapped"].close()
    (OUT / "results.json").write_text(json.dumps(report, indent=2, default=str))
    print("ANGELO_VIEW_OUTLINE", json.dumps({"success": report["success"], "failed": sorted(
        name for name, passed in report["checks"].items() if not passed)}), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run, first_interval=1)
