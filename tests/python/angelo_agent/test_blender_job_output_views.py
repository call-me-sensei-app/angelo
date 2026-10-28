# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One output of a production job, in the pixels each runtime hands a model, in a factory-startup GUI.

Real jobs publish several pictures; ``angelo_view`` with ``image`` (the job)
and ``file`` (one output, as the job's receipt names it) shows one. Every check
plants something in one output and reads it back from the payload a model would
receive: the PNG inside a Codex tool result's ``inputImage`` and inside a
Claude plan round's ``image`` block. An image being returned proves nothing
here; the planted thing has to be in its pixels, and absent from the output it
was not planted in.

* **A baked normal map** (a high-to-low bake of a plane under a pyramid): the
  pyramid's four faces are in the delivered pixels, which equal the published
  file byte for byte although the scene's view transform is AgX with two stops
  of exposure: non-colour data gets no view transform, and the result says so.
  A position map in OpenEXR is shown as its stored values too, with the pixels
  outside 0 to 1 clipped and counted.
* **One stereo view**: a red patch only the right eye can see is in the right
  view's pixels and not in the left's; one File Output item of the same render
  (the beauty, and its mask, which holds no red); one frame of one view of a
  stereo animation in which the patch appears on the second frame.
* **One UDIM tile**: the tile whose face emits another colour shows it, its
  neighbour does not, and the tile without faces holds what it held.
* **What is refused, with what works**: a job with several pictures and no
  ``file`` names them; a name the job did not report; a multi-layer file; a
  bake without an output.
* A delivered view counts as the job's picture (``job:<id>``) and marks the
  job as shown with the output it showed.
* The scene, its render settings and its datablocks are as they were.

Run through ``run_native_suites.py --gui --include
test_blender_job_output_views``; check ``job-output-views.json`` for
``success`` and ``phase == "complete"``. No provider, account or paid request.
"""

import base64
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import addon_utils
import bpy

assert "--factory-startup" in sys.argv, "Use an isolated factory-startup instance"
TEMP_VARIABLE = "TEMP" if sys.platform == "win32" else "TMPDIR"
assert TEMP_VARIABLE in os.environ and not os.environ[TEMP_VARIABLE].startswith("/var/folders/"), \
    "Set a private %s so quit.blend cannot replace the user's session recovery file" % TEMP_VARIABLE
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-job-output-views-qa"))
OUT.mkdir(parents=True, exist_ok=True)
WS = OUT / "workspace"
WS.mkdir(exist_ok=True)
OWN = OUT / "own"  # The person's own folder for their UDIM image: never the workspace.
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import ui  # noqa: E402

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False

import imbuf  # noqa: E402
import numpy  # noqa: E402
import long_jobs_fixtures as lj  # noqa: E402
import production_jobs_fixtures as pj  # noqa: E402
import rendered_view_fixtures as rv  # noqa: E402
import test_native_view_fixture_boot as fixture_boot  # noqa: E402
from angelo_agent import job_ui, request_path, scene_queries, view_cache  # noqa: E402
from angelo_agent.action_world import image_id  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.claude_evidence import ClaudeVisualEvidence  # noqa: E402
from angelo_agent.job_store import TERMINAL  # noqa: E402
from angelo_agent.providers import tool_content  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402
from angelo_agent.scene_queries import object_handle  # noqa: E402

REPORT = OUT / "job-output-views.json"
state = {"phase": "boot", "success": False, "checks": {}, "pixels": {}, "refusals": {}, "seconds": {},
         "paid_requests": 0, "binary": bpy.app.binary_path}
controller = rv.conversation(WS)
references = ReferenceStore(OUT, "7d0a1c1e-2b47-4c0a-8a55-3f1f6d2b9e10")
delivery = SimpleNamespace(references=references, models=[], settings={}, state={"options": {}}, turn_options={})


def check(name, condition):
    state["checks"][name] = bool(condition)
    assert condition, name


def window():
    return bpy.context.window_manager.windows[0]


def executor():
    value = BlenderExecutor(window())
    value.studio_context, value.observation_store = controller, references
    return value


def decoded(data):
    """RGB rows, top row first, of PNG bytes (decoded by Blender, outside the code under test)."""
    buffer = imbuf.load_from_buffer(data)
    with buffer.with_buffer() as rows:
        pixels = numpy.asarray(rows).copy()
    buffer.free()
    return pixels[::-1, :, :3].astype(numpy.int32)


def payloads(arguments, result):
    """The pixels of ``result``'s picture as each runtime receives it: ``{"codex": rows, "claude": rows}``."""
    text, image = tool_content(delivery, "angelo_view", result)
    prefix = "data:image/png;base64,"
    assert (text["type"], image["type"]) == ("inputText", "inputImage") and image["imageUrl"].startswith(prefix)
    evidence = ClaudeVisualEvidence(references)
    evidence.admit({"tool": "angelo_view", "arguments": arguments}, result)
    (block,) = references.claude_inputs(evidence.take_images())
    assert (block["type"], block["source"]["media_type"]) == ("image", "image/png")
    state["text_keys"] = sorted(__import__("json").loads(text["text"]))
    return {"codex": decoded(base64.b64decode(image["imageUrl"][len(prefix):])),
            "claude": decoded(base64.b64decode(block["source"]["data"]))}


def view(job, **arguments):
    """One ``angelo_view`` call for a finished job through the executor: ``(arguments, result)``."""
    arguments = dict({"views": ["SCENE_CAMERA"], "shading": "RENDERED", "resolution": 256, "image": job}, **arguments)
    controller._request_generation += 1
    view_cache.images.clear()
    started = time.perf_counter()
    result = executor().execute("angelo_view", arguments)
    state["last_seconds"] = round(time.perf_counter() - started, 3)
    assert isinstance(result, dict), "A finished picture is answered at once"
    return arguments, result


def refusal(job, **arguments):
    try:
        view(job, **arguments)
    except RuntimeError as error:
        return str(error)
    return ""


def both(name, pixels, predicate):
    """``predicate`` holds for the picture in both runtimes' payloads."""
    for runtime, rows in pixels.items():
        check("%s_%s" % (name, runtime), predicate(rows))


def run_job(request):
    """Generator: approve-and-run one job as the controller does, then wait for its end. Returns its record."""
    executor().precondition("angelo_job_start", request)
    started = executor().execute("angelo_job_start", request)
    assert "job" in started and not started.get("error"), started
    record = job_ui.manager().record(started["job"])
    yield from rv.wait_for(lambda: record["status"] in TERMINAL, seconds=150, what="job " + record["id"])
    assert record["status"] == "done", (record["status"], record["reason"], record["receipt"])
    return record


def published(record, name):
    return decoded(job_ui.output_file(record, name).read_bytes())


def float_pixels(path):
    """An image file's stored float RGBA rows, top row first, as Blender's own image loader reads them as data."""
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        image.colorspace_settings.name = "Non-Color"
        values = numpy.empty(image.size[0] * image.size[1] * 4, dtype=numpy.float32)
        image.pixels.foreach_get(values)
        return values.reshape(image.size[1], image.size[0], 4)[::-1].copy()
    finally:
        bpy.data.images.remove(image)


def untouched(before):
    """The person's render settings, view transform and datablocks, as before the views."""
    scene = window().scene
    now = (rv.rna_values(scene.render), rv.rna_values(scene.view_settings), rv.id_names())
    return rv.differences(before[0], now[0]) == [] and rv.differences(before[1], now[1]) == [] and before[2] == now[2]


def snapshot():
    scene = window().scene
    return rv.rna_values(scene.render), rv.rna_values(scene.view_settings), rv.id_names()


def at(rows, uv):
    """The delivered pixel at a UV position (rows run from the top; V from the bottom)."""
    height, width = rows.shape[:2]
    return rows[height - 1 - int(uv[1] * height), int(uv[0] * width)] / 255.0


def shows_pyramid(rows, tolerance=0.03):
    """A tangent-space normal map holding the planted pyramid: neutral on the flat part, each face leaning its way."""
    near = lambda value, expected: abs(value - expected) <= tolerance  # noqa: E731
    flat, plus_x, minus_x, plus_y, minus_y = (at(rows, pj.NORMAL_PROBES[key]) for key in
                                              ("flat", "plus_x", "minus_x", "plus_y", "minus_y"))
    return (near(flat[0], pj.NEUTRAL) and near(flat[1], pj.NEUTRAL) and near(flat[2], 1.0)
            and near(plus_x[0], pj.LEAN_UP) and near(minus_x[0], pj.LEAN_DOWN)
            and near(plus_y[1], pj.LEAN_UP) and near(minus_y[1], pj.LEAN_DOWN))


def red_pixels(rows):
    return int(((rows[..., 0] > 150) & (rows[..., 1] < 70) & (rows[..., 2] < 70)).sum())


def baked_maps():
    """A normal map and a position map baked from a pyramid onto a plane, under a view transform that must not apply."""
    state["phase"] = "baked_maps"
    scene = window().scene
    lj.cycles_scene(scene)
    scene.render.engine = 'BLENDER_EEVEE'
    low, high = pj.low_plane(scene), pj.high_pyramid(scene)
    view_settings = scene.view_settings
    yield
    bake = {"kind": "BAKE_TEXTURE", "target": object_handle(low), "sources": [object_handle(high)], "extrusion": 0.5,
            "resolution": [64, 64], "samples": 4}
    normal = yield from run_job(dict(bake, bake_type="NORMAL", output="bakes/normal"))
    (entry,) = normal["receipt"]["outputs"]
    check("normal_bake_reports_its_map_and_file", entry["map"] == "NORMAL" and entry["file"].endswith(".png"))
    # Baked under a neutral view; looked at under one that would change every pixel of a picture.
    view_settings.view_transform, view_settings.look, view_settings.exposure = "AgX", "AgX - Punchy", 2.0
    before = snapshot()
    arguments, result = view(normal["id"], file=entry["file"])
    state["seconds"]["baked_map_view"] = state["last_seconds"]
    pixels = payloads(arguments, result)
    truth = published(normal, entry["file"])
    state["pixels"]["normal_map"] = {key: [round(float(v), 3) for v in at(pixels["claude"], uv)]
                                     for key, uv in pj.NORMAL_PROBES.items()}
    both("planted_pyramid_is_in_the_normal_map", pixels, shows_pyramid)
    both("normal_map_equals_the_published_file_byte_for_byte", pixels,
         lambda rows: rows.shape == truth.shape and bool((rows == truth).all()))
    check("normal_map_result_says_stored_values_and_no_transform", result["rendered"].get("values") == "STORED"
          and result["rendered"]["map"] == "NORMAL" and result["rendered"]["file"] == entry["file"]
          and "non-colour data (a NORMAL map)" in result["note"] and "no view transform" in result["note"]
          and "color" not in result["rendered"] and result["rendered"].get("source") == "CURRENT_OUTPUT_FILE"
          and "reads the job's current output file" in result["note"]
          and "including any edits made to that file since the bake" in result["note"])
    arguments, without_name = view(normal["id"])
    check("the_only_published_map_needs_no_name", without_name["rendered"]["file"] == entry["file"])
    check("baked_map_views_left_the_scene", untouched(before))
    # A flat plane alone bakes a flat map: the pyramid in the pixels is the source's, not the tool's.
    view_settings.view_transform, view_settings.look, view_settings.exposure = "Standard", "None", 0.0
    flat = yield from run_job(dict(bake, sources=[object_handle(pj.low_plane(scene, "FlatSource"))], bake_type="NORMAL",
                                   output="bakes/flat"))
    arguments, result = view(flat["id"], file=flat["receipt"]["outputs"][0]["file"])
    both("a_flat_source_shows_no_pyramid", payloads(arguments, result), lambda rows: not shows_pyramid(rows))
    position = yield from run_job(dict(bake, bake_type="POSITION", output="bakes/position"))
    (entry,) = position["receipt"]["outputs"]
    check("position_bake_is_published_as_openexr", entry["file"].endswith(".exr"))
    # Cycles sums a position bake over its samples; the job bakes it with one although four were asked for.
    check("a_position_bake_uses_one_sample", position["detail"]["samples"] == 1 and window().scene.cycles.samples == 16)
    stored = float_pixels(job_ui.output_file(position, entry["file"]))  # Blender's own reading of the file.
    view_settings.view_transform, view_settings.look, view_settings.exposure = "AgX", "AgX - Punchy", 2.0
    before_position = snapshot()
    arguments, result = view(position["id"], file=entry["file"])
    pixels = payloads(arguments, result)
    expected = numpy.clip(stored[..., :3] * 255.0 + 0.5, 0.0, 255.0).astype(numpy.int32)
    probe = {"plus_minus": (0.75, 0.25), "minus_plus": (0.25, 0.75), "plus_plus_edge": (0.99, 0.99)}
    state["pixels"]["position_map"] = {key: {"stored": [round(float(v), 3) for v in stored[63 - int(uv[1] * 64), int(uv[0] * 64), :3]],
                                             "delivered": [int(v) for v in pixels["codex"][63 - int(uv[1] * 64), int(uv[0] * 64)]]}
                                       for key, uv in probe.items()}
    # The texel at UV (0.75, 0.25) has its centre at x 0.516, y -0.484: stored 0.516 is 131 or 132 of 255 (sRGB
    # would show 190, AgX with two stops more another value again), and a negative coordinate is 0.
    both("position_values_are_shown_as_stored", pixels,
         lambda rows: rows.shape == expected.shape and int(numpy.abs(rows - expected).max()) <= 1
         and abs(int(rows[63 - 16, 48, 0]) - 131.5) <= 1.5 and int(rows[63 - 16, 48, 1]) == 0
         and int(rows[63 - 48, 16, 0]) == 0 and abs(int(rows[63 - 48, 16, 1]) - 131.5) <= 1.5)
    outside = float(((stored[..., :3] < 0.0) | (stored[..., :3] > 1.0)).any(axis=-1).mean())
    check("position_result_counts_what_it_clipped", result["rendered"].get("values") == "STORED"
          and abs(result["rendered"].get("clipped", 0) - outside) < 0.002 and outside > 0.5
          and "values outside 0 to 1, which are clipped" in result["note"] and "preview" not in result["rendered"])
    check("position_view_left_the_scene", untouched(before_position))
    view_settings.view_transform, view_settings.look, view_settings.exposure = "Standard", "None", 0.0
    silent = yield from run_job(dict(bake, bake_type="NORMAL"))
    message = refusal(silent["id"])
    state["refusals"]["bake_without_output"] = message
    check("a_bake_without_an_output_says_what_works", "published no file" in message and "output folder" in message)
    for name in ("HighPyramid", "LowPlane", "FlatSource"):
        bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)


def red_patch(scene):
    """A red, emitting patch half a unit in front of the camera and to its right: only the right eye sees it."""
    mesh = bpy.data.meshes.new("Patch")
    mesh.from_pydata([(0.25, -0.09, -0.5), (0.45, -0.09, -0.5), (0.45, 0.09, -0.5), (0.25, 0.09, -0.5)], [], [(0, 1, 2, 3)])
    material = bpy.data.materials.new("Patch Red")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    emission, surface = nodes.new("ShaderNodeEmission"), nodes.new("ShaderNodeOutputMaterial")
    emission.inputs["Color"].default_value, emission.inputs["Strength"].default_value = (1.0, 0.0, 0.0, 1.0), 1.0
    material.node_tree.links.new(emission.outputs[0], surface.inputs["Surface"])
    mesh.materials.append(material)
    patch = bpy.data.objects.new("Patch", mesh)
    patch.matrix_world = scene.camera.matrix_world.copy()
    scene.collection.objects.link(patch)
    return patch


def stereo_views():
    """A stereo still and animation whose right eye alone sees a red patch, with File Output items."""
    state["phase"] = "stereo_views"
    scene = window().scene
    scene.render.engine = 'BLENDER_EEVEE'
    scene.eevee.taa_render_samples = 4
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 128, 72, 100
    scene.view_settings.view_transform = "Standard"
    pj.stereo_scene(scene, far_apart=0.2)
    scene.camera.data.stereo.convergence_mode = 'PARALLEL'
    scene.camera.data.clip_start = 0.05
    patch = red_patch(scene)
    pj.compositor(scene, OUT / "persons-renders")
    yield
    # The output/view assertions name frame 1, independently of earlier scene setup.
    still = yield from run_job({"kind": "RENDER_STILL", "output": "renders/shot.png", "frame_start": 1})
    files = {entry["file"]: entry for entry in still["receipt"]["outputs"]}
    state["stereo_outputs"] = sorted(files)
    check("stereo_still_reports_each_view_and_item", {"frame_0001_L.png", "frame_0001_R.png", "Passes/Beauty0001_R.png",
                                                      "Passes/Mask0001_R.png", "Layers/layers0001_L.exr"} <= set(files)
          or {"frame_0001_L.png", "frame_0001_R.png", "Passes/Beauty0001_R.png", "Passes/Mask0001_R.png"} <= set(files))
    before = snapshot()
    arguments, right = view(still["id"], file="frame_0001_R.png")
    state["seconds"]["stereo_view"] = state["last_seconds"]
    right_pixels = payloads(arguments, right)
    # Explicitly simulate acknowledged delivery; packing a payload does not prove model acknowledgment.
    path = request_path.PathFacts()
    path.tool("angelo_view", arguments, right)
    jobs = path.pictures(right["views"], staged=False)
    check("a_view_of_one_output_counts_as_the_jobs_picture", list(jobs) == [still["id"]]
          and jobs.files == ["frame_0001_R.png"] and path.snapshot()["images"] == [[1, None, {"job:" + still["id"]: 1}]])
    request_path._jobs_shown(jobs)
    state["shown"] = dict(still["shown"])
    check("the_job_is_marked_shown_with_the_output", still["shown"].get("files") == ["frame_0001_R.png"]
          and still["shown"].get("images") == 1 and "image_at" in still["shown"])
    arguments, left = view(still["id"], file="frame_0001_L.png")
    left_pixels = payloads(arguments, left)
    state["pixels"]["stereo"] = {"right_red": red_pixels(right_pixels["codex"]), "left_red": red_pixels(left_pixels["codex"])}
    both("planted_patch_is_in_the_right_view", right_pixels, lambda rows: red_pixels(rows) > 40)
    both("planted_patch_is_not_in_the_left_view", left_pixels, lambda rows: red_pixels(rows) == 0)
    both("the_right_view_equals_the_published_file", right_pixels,
         lambda rows: bool((rows == published(still, "frame_0001_R.png")).all()))
    check("view_result_names_the_view", right["rendered"]["view"] == "right" and left["rendered"]["view"] == "left"
          and right["rendered"]["file"] == "frame_0001_R.png" and right["rendered"]["output"] == "renders/shot"
          and "View right from job %s (renders/shot/frame_0001_R.png)" % still["id"] in right["note"])
    arguments, beauty = view(still["id"], file="Passes/Beauty0001_R.png")
    both("the_beauty_item_of_the_right_view_holds_the_patch", payloads(arguments, beauty), lambda rows: red_pixels(rows) > 40)
    arguments, mask = view(still["id"], file="Passes/Mask0001_R.png")
    both("the_mask_item_holds_no_red", payloads(arguments, mask), lambda rows: red_pixels(rows) == 0)
    check("view_result_names_the_node_and_item", (mask["rendered"]["node"], mask["rendered"]["slot"], mask["rendered"]["view"])
          == ("Passes", "Mask", "right") and "File Output Passes: Mask, view right" in mask["note"])
    arguments, part = view(still["id"], file="frame_0001_R.png", region=[0.5, 0.0, 1.0, 1.0])
    both("a_region_of_the_right_view_holds_the_patch", payloads(arguments, part),
         lambda rows: rows.shape[1] == 64 and red_pixels(rows) > 40)
    message = refusal(still["id"])
    state["refusals"]["several_pictures"] = message
    check("without_file_the_refusal_names_the_outputs", "wrote several pictures: pass file with one of its outputs" in message
          and "frame_0001_R.png (view right)" in message and "Passes/Mask0001_L.png (File Output Passes: Mask, view left)" in message)
    message = refusal(still["id"], file="frame_0001_X.png")
    check("a_name_the_job_did_not_report_is_refused", "reports no output frame_0001_X.png" in message)
    layered = next((name for name, entry in files.items() if entry.get("layers")), None)
    if layered is not None:
        message = refusal(still["id"], file=layered)
        state["refusals"]["layers"] = message
        check("a_multi_layer_file_is_refused_with_what_works", "in one OpenEXR file" in message
              and "a File Output item each" in message)
    check("stereo_views_left_the_scene", untouched(before))
    patch.hide_render = True
    patch.keyframe_insert("hide_render", frame=1)
    patch.hide_render = False
    patch.keyframe_insert("hide_render", frame=2)
    yield
    walk = yield from run_job({"kind": "RENDER_ANIMATION", "output": "renders/walk", "frame_start": 1, "frame_end": 2})
    arguments, first = view(walk["id"], file="frame_####_R.png", frame=1)
    both("frame_one_of_the_right_view_has_no_patch", payloads(arguments, first), lambda rows: red_pixels(rows) == 0)
    arguments, second = view(walk["id"], file="frame_####_R.png", frame=2)
    both("frame_two_of_the_right_view_has_the_patch", payloads(arguments, second), lambda rows: red_pixels(rows) > 40)
    arguments, second_left = view(walk["id"], file="frame_####_L.png", frame=2)
    both("frame_two_of_the_left_view_has_none", payloads(arguments, second_left), lambda rows: red_pixels(rows) == 0)
    check("animation_view_reports_its_frames", second["rendered"]["frame"] == 2 and second["rendered"]["frames"] == [1, 2]
          and second["rendered"]["frame_count"] == 2 and "frame_0002_R.png" in second["note"])
    message = refusal(walk["id"], file="frame_####_R.png", frame=9)
    check("a_missing_frame_names_the_ones_written", "no frame 9 of frame_####_R.png: it wrote 2 frames, 1 to 2" in message)
    message = refusal(walk["id"], frame=2)
    check("an_animation_without_file_names_its_patterns", "pass file (and frame for ####)" in message
          and "frame_####_R.png (view right)" in message)
    bpy.data.objects.remove(patch, do_unlink=True)
    scene.render.use_multiview = False
    scene.compositing_node_group = None


def udim_tiles():
    """A UDIM bake whose second tile's face emits another colour: the defect is in that tile's picture only."""
    state["phase"] = "udim_tiles"
    scene = window().scene
    scene.render.bake.margin = 0
    plane = pj.udim_object(scene)
    defect = plane.data.materials[0].copy()
    defect.name = "Defect"
    defect.node_tree.nodes["Emit"].inputs["Color"].default_value = (0.0, 0.0, 1.0, 1.0)
    plane.data.materials.append(defect)
    plane.data.polygons[1].material_index = 1  # The face in tile 1002.
    skin = pj.udim_image("Skin", OWN / "skin")
    yield
    record = yield from run_job({"kind": "BAKE_TEXTURE", "target": object_handle(plane), "bake_type": "EMIT",
                                 "image": image_id(skin), "samples": 4, "output": "bakes/skin"})
    files = {entry.get("tile"): entry for entry in record["receipt"]["outputs"]}
    check("udim_bake_reports_each_tile", {number: entry["file"] for number, entry in files.items()}
          == {1001: "Skin.1001.png", 1002: "Skin.1002.png", 1003: "Skin.1003.png"})
    before = snapshot()
    centre = lambda rows: tuple(int(v) for v in rows[rows.shape[0] // 2, rows.shape[1] // 2])  # noqa: E731
    shown = {}
    for number in (1001, 1002, 1003):
        arguments, result = view(record["id"], file=files[number]["file"])
        shown[number] = (payloads(arguments, result), result)
    state["seconds"]["udim_tile_view"] = state["last_seconds"]
    state["pixels"]["udim_centres"] = {number: centre(pixels["codex"]) for number, (pixels, _) in shown.items()}
    both("planted_colour_is_in_tile_1002", shown[1002][0], lambda rows: centre(rows)[2] > 240 and max(centre(rows)[:2]) < 15)
    both("tile_1001_holds_the_other_colour", shown[1001][0], lambda rows: centre(rows)[0] > 200 and centre(rows)[2] < 120)
    both("the_tile_without_faces_holds_what_it_held", shown[1003][0],
         lambda rows: bool((rows == published(record, "Skin.1003.png")).all()) and centre(rows)[0] < 120)
    for number in (1001, 1002):
        both("tile_%d_equals_its_published_file" % number, shown[number][0],
             lambda rows, number=number: bool((rows == published(record, files[number]["file"])).all()))
    result = shown[1002][1]
    check("view_result_names_the_tile", result["rendered"]["tile"] == 1002 and result["rendered"]["map"] == "EMIT"
          and result["rendered"]["name"] == "Skin" and "values" not in result["rendered"]
          and "Tile 1002 of Skin (EMIT map) from job %s (bakes/skin/Skin.1002.png)" % record["id"] in result["note"])
    message = refusal(record["id"])
    state["refusals"]["several_tiles"] = message
    check("a_udim_bake_without_file_names_its_tiles", "baked several images or tiles" in message
          and "Skin.1002.png (tile 1002 of Skin (EMIT map))" in message)
    check("udim_views_left_the_scene", untouched(before))


def scenario():
    state["module"] = scene_queries.__file__
    check("checkout_modules_loaded", str(ROOT) in scene_queries.__file__)
    (OUT / "recovery").mkdir(exist_ok=True)
    bpy.context.preferences.filepaths.temporary_directory = str(OUT / "recovery")
    yield
    yield from baked_maps()
    yield from stereo_views()
    yield from udim_tiles()
    check("no_hidden_staging_left", not sorted(WS.rglob(".angelo-job-*")))
    check("views_left_only_their_pictures", not [path for path in references.directory.glob("*") if path.stat().st_size == 0])


fixture_boot.drive(scenario, state, REPORT, "ANGELO_JOB_OUTPUT_VIEWS")
