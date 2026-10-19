# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The pixels each runtime is handed by angelo_view's rendered pictures, in a factory-startup GUI.

Every check plants something visible and reads it back from the payload a
model would receive: the PNG inside a Codex tool result's ``inputImage`` and
inside a Claude plan round's ``image`` block. An image being returned proves
nothing here; the planted thing has to be in its pixels.

* **Planted defects in a look** (EEVEE and Cycles): a wrong, blue light; a
  missing (black) sky where a red one is expected; a transparent film, which
  must arrive as a checkerboard and not as black; an over-exposed frame.
* **The colours of the render window**: a look, and the Render Result shown
  with ``image``, equal Blender's own "as render" save of its own render, with
  a view transform, a look and an exposure, and when the output overrides the
  colour management.
* **A region**: a two-pixel defect of a full-resolution frame is absent from
  the whole frame scaled down and present in the region, in a look and in a
  finished 4K file. The scene's resolution and render region are as before.
* **Finished files**: a quarter-opaque PNG, an OpenEXR with values above 1 (as
  Blender's own display save shows it) and with a half-transparent part, a
  16-bit PNG, a multilayer OpenEXR that Blender rendered, and frames of a real
  animation job whose sky changes colour.
* **An edit after a look works**, the next look shows it, and so does the
  edit after that (Undo after looks is in ``test_blender_rendered_view_safety``).

Run through ``run_native_suites.py --gui --include
test_blender_rendered_view_pixels``; check ``rendered-view-pixels.json`` for
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
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-rendered-view-pixels-qa"))
OUT.mkdir(parents=True, exist_ok=True)
WS = OUT / "workspace"
(WS / "renders").mkdir(parents=True, exist_ok=True)
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
import rendered_view_fixtures as fx  # noqa: E402
from angelo_agent import job_ui, scene_queries, view_cache  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.claude_evidence import ClaudeVisualEvidence  # noqa: E402
from angelo_agent.job_store import new_record  # noqa: E402
from angelo_agent.providers import tool_content  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402
from angelo_agent.view_picture import CHECKER  # noqa: E402

REPORT = OUT / "rendered-view-pixels.json"
state = {"phase": "boot", "success": False, "checks": {}, "pixels": {}, "seconds": {}, "paid_requests": 0,
         "binary": bpy.app.binary_path}
controller = fx.conversation(WS)
references = ReferenceStore(OUT, "5c1f2f0e-6a8e-4b7e-9c56-2a54b5b8d1a1")
delivery = SimpleNamespace(references=references, models=[], settings={}, state={"options": {}}, turn_options={})
GREYS = {(CHECKER[0],) * 3, (CHECKER[1],) * 3}


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
    return pixels[::-1, :, :3].astype(numpy.int32)


def file_pixels(path):
    return decoded(Path(path).read_bytes())


def payloads(arguments, result):
    """The pixels of ``result``'s picture as each runtime receives it: ``{"codex": rows, "claude": rows}``."""
    text, image = tool_content(delivery, "angelo_view", result)
    prefix = "data:image/png;base64,"
    assert (text["type"], image["type"]) == ("inputText", "inputImage") and image["imageUrl"].startswith(prefix)
    evidence = ClaudeVisualEvidence(references)
    evidence.admit({"tool": "angelo_view", "arguments": arguments}, result)
    (block,) = references.claude_inputs(evidence.take_images())
    assert (block["type"], block["source"]["media_type"]) == ("image", "image/png")
    return {"codex": decoded(base64.b64decode(image["imageUrl"][len(prefix):])),
            "claude": decoded(base64.b64decode(block["source"]["data"]))}


def view(**arguments):
    """Generator: one ``angelo_view`` call through the executor; ``(arguments, result)``."""
    arguments = dict({"views": ["SCENE_CAMERA"], "shading": "RENDERED", "resolution": 256}, **arguments)
    controller._request_generation += 1  # Each call its own request: the looks' total is not the subject here.
    view_cache.images.clear()
    started = time.perf_counter()
    result = executor().execute("angelo_view", arguments)
    if not isinstance(result, dict):
        result = yield from fx.pending_result(result)
    state["last_seconds"] = round(time.perf_counter() - started, 3)
    return arguments, result


def both(name, pixels, predicate):
    """``predicate`` holds for the picture in both runtimes' payloads."""
    for runtime, rows in pixels.items():
        check("%s_%s" % (name, runtime), predicate(rows))


def corner(rows):
    """Mean colour of the top-left tenth: sky, behind the Cube."""
    return rows[:max(1, rows.shape[0] // 10), :max(1, rows.shape[1] // 10)].reshape(-1, 3).mean(axis=0)


def floor(rows):
    return rows[-max(1, rows.shape[0] // 8):, :].reshape(-1, 3).mean(axis=0)


def plant_job(path, kind="RENDER_STILL"):
    store = job_ui.manager().store
    record = dict(new_record(store.allocate(), controller.state["id"], "", {"kind": kind, "output": "renders/" + path.name},
                             now=time.time()), status="done", detail={"output_path": str(path), "engine": "CYCLES", "samples": 64})
    store.add(record)
    return record["id"]


def write(size, fill, path, kind="BYTE", file_type="PNG", planes=32):
    buffer = imbuf.new(size, planes=planes, buffer_type=kind)
    with buffer.with_buffer(write=True) as rows:
        fill(numpy.asarray(rows))
    buffer.file_type = file_type
    imbuf.write(buffer, filepath=str(path))
    return path


def own_render(name):
    """Blender's own render of the scene and its own "as render" save in the scene's view: the ground truth."""
    scene = window().scene
    settings = scene.render.image_settings
    with fx.in_view3d():
        bpy.ops.render.render()
    kept = (settings.file_format, settings.color_mode, settings.color_depth, settings.color_management)
    settings.color_management, settings.file_format, settings.color_mode, settings.color_depth = "FOLLOW_SCENE", "PNG", "RGB", "8"
    path = OUT / ("truth_%s.png" % name)
    fx.render_result().save_render(str(path), scene=scene)
    settings.file_format, settings.color_mode, settings.color_depth, settings.color_management = kept
    return file_pixels(path)


def planted_defects(engine):
    short = "eevee" if "EEVEE" in engine else "cycles"
    scene = window().scene
    state["phase"] = "defects_" + short
    fx.blue_sun(scene, engine)  # The wrong light: blue where a white one was meant.
    yield
    arguments, result = yield from view()
    both("wrong_light_is_blue_%s" % short, payloads(arguments, result), lambda rows: fx.is_blue(floor(rows)))
    fx.red_world(scene, engine)
    arguments, result = yield from view()
    both("sky_is_red_%s" % short, payloads(arguments, result), lambda rows: fx.is_red(corner(rows)))
    fx._world(scene, (0.0, 0.0, 0.0))  # The missing sky.
    arguments, result = yield from view()
    both("missing_sky_is_black_%s" % short, payloads(arguments, result), lambda rows: corner(rows).max() < 12)
    fx.red_world(scene, engine)
    scene.render.film_transparent = True  # A transparent hole behind the Cube.
    before = fx.rna_values(scene.render)
    arguments, result = yield from view()
    check("transparent_film_is_said_%s" % short, 0.5 < result["rendered"]["transparent"] < 0.95
          and "grey checkerboard" in result["note"])
    both("transparent_film_is_a_checkerboard_%s" % short, payloads(arguments, result),
         lambda rows: {tuple(pixel) for pixel in rows[:8, :40].reshape(-1, 3).tolist()} == GREYS
         and fx.is_red(rows[rows.shape[0] // 2, rows.shape[1] // 2]))
    check("transparent_look_left_settings_%s" % short, fx.differences(before, fx.rna_values(scene.render)) == [])
    lit = executor().execute("angelo_view", {"views": ["SCENE_CAMERA"], "shading": "LIT", "resolution": 256})
    check("lit_says_the_film_is_transparent_%s" % short, lit["lit_preview"].get("transparent_film") is True
          and "transparent in a render" in lit["note"])
    scene.render.film_transparent = False


def render_window_colours(engine):
    """A look and the shown Render Result against Blender's own "as render" save of an over-exposed sky."""
    short = "eevee" if "EEVEE" in engine else "cycles"
    scene = window().scene
    state["phase"] = "colours_" + short
    fx.red_world(scene, engine)
    background = next(node for node in scene.world.node_tree.nodes if node.bl_idname == "ShaderNodeBackground")
    background.inputs["Color"].default_value, background.inputs["Strength"].default_value = (1.0, 0.5, 0.2, 1.0), 4.0
    yield
    cases = (("standard", "Standard", "None", 0.0, False), ("agx_punchy_under", "AgX", "AgX - Punchy", -1.0, False),
             ("filmic_over", "Filmic", "None", 0.5, False), ("output_override", "AgX", "None", 0.0, True))
    for name, transform, look, exposure, override in cases:
        view_settings, output = scene.view_settings, scene.render.image_settings
        view_settings.view_transform, view_settings.look, view_settings.exposure = transform, look, exposure
        output.color_management = "FOLLOW_SCENE"
        truth = corner(own_render("%s_%s" % (short, name)))
        if override:  # The person's files are written in another view than their render window shows.
            output.color_management = "OVERRIDE"
            output.view_settings.view_transform, output.view_settings.exposure = "Standard", 2.0
        before = fx.rna_values(scene.render)
        arguments, result = yield from view()
        look_pixels = payloads(arguments, result)
        shown_arguments, shown = yield from view(image="RENDER_RESULT")
        shown_pixels = payloads(shown_arguments, shown)
        state["pixels"]["%s_%s" % (short, name)] = {"blender": [round(v, 1) for v in truth],
                                                    "look": [round(v, 1) for v in corner(look_pixels["claude"])],
                                                    "render_result": [round(v, 1) for v in corner(shown_pixels["codex"])]}
        both("look_equals_blenders_own_save_%s_%s" % (short, name), look_pixels,
             lambda rows: numpy.abs(corner(rows) - truth).max() <= 2.5)
        both("render_result_equals_blenders_own_save_%s_%s" % (short, name), shown_pixels,
             lambda rows: numpy.abs(corner(rows) - truth).max() <= 2.5)
        check("colour_facts_%s_%s" % (short, name), result["rendered"]["color"]["view_transform"] == transform
              and result["rendered"]["color"].get("exposure", 0.0) == exposure and not result["uncertain"]
              and shown["rendered"]["color"]["view_transform"] == transform)
        check("colours_left_settings_%s_%s" % (short, name), fx.differences(before, fx.rna_values(scene.render)) == [])
    check("over_exposure_is_in_the_pixels_%s" % short, state["pixels"][short + "_standard"]["look"][0] > 250
          and state["pixels"][short + "_agx_punchy_under"]["look"][0] < 230)
    scene.render.image_settings.color_management = "FOLLOW_SCENE"
    scene.view_settings.view_transform, scene.view_settings.look, scene.view_settings.exposure = "Standard", "None", 0.0


def small_defect(engine):
    """A red dot two pixels across in the full-resolution frame: lost in the whole frame, there in its region."""
    short = "eevee" if "EEVEE" in engine else "cycles"
    scene = window().scene
    state["phase"] = "region_" + short
    fx.reset(scene, engine)
    fx._world(scene, (1.0, 1.0, 1.0))
    fx._remove(("Cube", "Dot"))
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 1920, 1080, 50
    camera = scene.camera
    mesh = bpy.data.meshes.new("Dot")
    half = 0.0075 / 2  # Two of 1920 pixels at ten units from a 50 mm camera.
    mesh.from_pydata([(-half, -half, 0), (half, -half, 0), (half, half, 0), (-half, half, 0)], [], [(0, 1, 2, 3)])
    material = bpy.data.materials.new("Dot Red")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    emission, output = nodes.new("ShaderNodeEmission"), nodes.new("ShaderNodeOutputMaterial")
    emission.inputs["Color"].default_value = (1.0, 0.0, 0.0, 1.0)
    material.node_tree.links.new(emission.outputs[0], output.inputs["Surface"])
    mesh.materials.append(material)
    dot = bpy.data.objects.new("Dot", mesh)
    dot.matrix_world = camera.matrix_world.copy()
    dot.location = camera.matrix_world @ __import__("mathutils").Vector((0.0, 0.0, -10.0))
    scene.collection.objects.link(dot)
    window().view_layer.update()
    yield
    before = fx.rna_values(scene.render)

    def redness(rows):
        return int((rows[..., 0] - rows[..., 1]).max())
    arguments, result = yield from view(resolution=256)
    whole = payloads(arguments, result)
    arguments, result = yield from view(resolution=512, region=[0.45, 0.45, 0.55, 0.55])
    state["seconds"]["region_look_%s" % short] = state["last_seconds"]
    part = payloads(arguments, result)
    state["pixels"]["dot_" + short] = {"whole_frame_redness": redness(whole["codex"]), "region_redness": redness(part["codex"]),
                                       "region_size": [result["views"][0]["width"], result["views"][0]["height"]],
                                       "frame_size": result["rendered"]["frame_size"]}
    both("dot_is_lost_in_the_whole_frame_%s" % short, whole, lambda rows: redness(rows) < 40)
    both("dot_is_in_the_region_%s" % short, part, lambda rows: redness(rows) > 120)
    check("region_is_full_resolution_and_no_more_%s" % short, result["rendered"]["frame_size"] == [1920, 1080]
          and (result["views"][0]["width"], result["views"][0]["height"]) == (192, 108)
          and result["views"][0]["region"] == [0.45, 0.45, 0.55, 0.55])
    check("region_left_settings_%s" % short, fx.differences(before, fx.rna_values(scene.render)) == [])
    fx._remove(("Dot",))


def finished_files():
    state["phase"] = "files"
    scene = window().scene
    fx.reset(scene, "BLENDER_EEVEE")
    scene.view_settings.view_transform = "AgX"
    folder = WS / "renders"
    before, blocks = fx.rna_values(scene.render), fx.id_names()

    def cutout(rows):
        rows[:, :256] = (255, 0, 0, 64)   # A quarter opaque.
        rows[:, 256:] = (0, 255, 0, 255)
    arguments, result = yield from view(image=plant_job(write((512, 256), cutout, folder / "cutout.png")), resolution=512)
    quarter = 64 / 255
    blends = {tuple(round(value * quarter + grey * (1 - quarter)) for value in (255, 0, 0)) for grey in CHECKER[:2]}
    both("quarter_opaque_red_is_not_solid_red", payloads(arguments, result),
         lambda rows: {tuple(p) for p in rows[:, :250].reshape(-1, 3).tolist()} == blends and tuple(rows[5, 400]) == (0, 255, 0))
    check("transparency_is_said", result["rendered"]["transparent"] == 0.5 and "checkerboard" in result["note"])

    def hot(rows):
        rows[:, :256] = (4.0, 2.0, 0.5, 1.0)       # Far above 1.
        rows[:, 256:384] = (0.18, 0.18, 0.18, 1.0)
        rows[:, 384:] = (0.09, 0.09, 0.09, 0.5)   # Half transparent, premultiplied as OpenEXR stores it.
    exr = write((512, 256), hot, folder / "final.exr", kind="FLOAT", file_type="OPEN_EXR")
    image = bpy.data.images.load(str(exr), check_existing=False)  # Blender's own display save of the file.
    settings = scene.render.image_settings
    kept = (settings.file_format, settings.color_mode, settings.color_depth)
    settings.file_format, settings.color_mode, settings.color_depth = "PNG", "RGBA", "8"
    image.save_render(str(OUT / "truth_exr.png"), scene=scene)
    settings.color_depth = "16"
    image.save_render(str(folder / "deep.png"), scene=scene)
    settings.color_depth = "8"
    settings.file_format, settings.color_mode, settings.color_depth = kept
    bpy.data.images.remove(image)
    truth = file_pixels(OUT / "truth_exr.png")
    before, blocks = fx.rna_values(scene.render), fx.id_names()
    started = time.perf_counter()
    arguments, result = yield from view(image=plant_job(exr), resolution=512)
    state["seconds"]["openexr_512x256"] = round(time.perf_counter() - started, 3)
    pixels = payloads(arguments, result)
    state["pixels"]["openexr"] = {"blender": truth[5, 5].tolist(), "delivered": pixels["claude"][5, 5].tolist()}
    both("openexr_above_one_is_blenders_own_display_save", pixels,
         lambda rows: numpy.abs(rows[5, 5] - truth[5, 5]).max() <= 2 and numpy.abs(rows[5, 300] - truth[5, 300]).max() <= 2
         and rows[5, 5].max() < 250)
    both("openexr_half_transparent_part_is_a_checkerboard", pixels,
         lambda rows: len({tuple(p) for p in rows[:, 390:500].reshape(-1, 3).tolist()}) == 2)
    check("openexr_is_called_a_preview", result["rendered"]["preview"] == "DISPLAY_REFERRED"
          and result["rendered"]["color"] == {"view_transform": "AgX"} and "8-bit preview" in result["note"]
          and result["rendered"]["transparent"] == 0.25)
    check("openexr_left_settings_and_no_datablock", fx.differences(before, fx.rna_values(scene.render)) == []
          and fx.id_names() == blocks and result["side_effects"] == [])
    arguments, result = yield from view(image=plant_job(folder / "deep.png"), resolution=512)
    both("sixteen_bit_png_shows_its_display_values", payloads(arguments, result),
         lambda rows: numpy.abs(rows[5, 5] - truth[5, 5]).max() <= 2)
    check("sixteen_bit_is_named", result["rendered"]["source_depth"] == 16)

    def frame_4k(rows):
        rows[:] = (255, 255, 255, 255)
        rows[1159:1161, 3000:3002] = (255, 0, 0, 255)  # Rows count from the bottom: 1000 from the top.
    job = plant_job(write((3840, 2160), frame_4k, folder / "final_4k.png", planes=24))
    arguments, result = yield from view(image=job, resolution=512)
    state["seconds"]["finished_4k_to_512"] = state["last_seconds"]
    whole = payloads(arguments, result)
    arguments, result = yield from view(image=job, resolution=512, region=[0.75, 0.42, 0.82, 0.5])
    state["seconds"]["finished_4k_region"] = state["last_seconds"]
    part = payloads(arguments, result)
    both("file_defect_is_lost_in_the_whole_frame", whole, lambda rows: int(rows[..., 1].min()) > 200)
    both("file_defect_is_in_the_region", part,
         lambda rows: int(((rows[..., 0] == 255) & (rows[..., 1] == 0) & (rows[..., 2] == 0)).sum()) == 4)
    check("file_region_is_its_own_pixels", result["rendered"]["region_pixels"] == [269, 173]
          and (result["views"][0]["width"], result["views"][0]["height"]) == (269, 173))
    shot = folder / "shot"
    shot.mkdir()
    for number, colour in ((1, (255, 0, 0, 255)), (2, (0, 255, 0, 255)), (3, (0, 0, 255, 255))):
        write((320, 180), lambda rows, colour=colour: rows.__setitem__(slice(None), colour), shot / ("frame_%04d.png" % number))
    job = plant_job(shot, kind="RENDER_ANIMATION")
    arguments, result = yield from view(image=job, frame=2)
    both("the_chosen_frame_of_an_animation_is_shown", payloads(arguments, result), lambda rows: tuple(rows[9, 9]) == (0, 255, 0))
    arguments, result = yield from view(image=job)
    check("the_first_frame_without_a_number", tuple(file_pixels(result["views"][0]["path"])[9, 9]) == (255, 0, 0)
          and result["rendered"]["frames"] == [1, 3])
    try:
        executor().execute("angelo_view", {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "image": job, "frame": 9})
        check("a_missing_frame_names_the_ones_written", False)
    except RuntimeError as error:
        check("a_missing_frame_names_the_ones_written", "no frame 9: it wrote 3 frames, 1 to 3" in str(error))
    check("files_left_no_datablock", fx.id_names() == blocks)
    check("files_left_only_their_pictures", not [path for path in references.directory.glob("*") if path.stat().st_size == 0])


def real_render_files():
    """Files Blender's own renders wrote: a multilayer OpenEXR, and the frames of a real animation job."""
    state["phase"] = "real_files"
    scene = window().scene
    fx.red_world(scene, "BLENDER_EEVEE")
    scene.view_layers[0].use_pass_normal = True
    settings = scene.render.image_settings
    kept = (getattr(settings, "media_type", None), settings.file_format, settings.color_depth, scene.render.filepath)
    if kept[0] is not None:
        settings.media_type = "MULTI_LAYER_IMAGE"
    settings.file_format = "OPEN_EXR_MULTILAYER"
    scene.render.filepath = str(WS / "renders" / "layers.exr")
    with fx.in_view3d():
        bpy.ops.render.render(write_still=True)
    if kept[0] is not None:
        settings.media_type = kept[0]
    settings.file_format = kept[1]
    settings.color_depth, scene.render.filepath = kept[2], kept[3]
    scene.view_layers[0].use_pass_normal = False
    yield
    blocks = fx.id_names()
    arguments, result = yield from view(image=plant_job(WS / "renders" / "layers.exr"))
    both("multilayer_openexr_shows_its_combined_image", payloads(arguments, result), lambda rows: fx.is_red(corner(rows)))
    check("multilayer_openexr_says_what_is_not_shown", result["rendered"].get("multilayer") is True
          and "Only the combined image is shown" in result["note"] and fx.id_names() == blocks)
    sky = next(node for node in scene.world.node_tree.nodes if node.bl_idname == "ShaderNodeBackground").inputs["Color"]
    for frame, colour in ((1, (1.0, 0.0, 0.0, 1.0)), (3, (0.0, 0.0, 1.0, 1.0))):  # A red sky that turns blue.
        sky.default_value = colour
        sky.keyframe_insert("default_value", frame=frame)
    yield
    request = {"kind": "RENDER_ANIMATION", "output": "renders/sky", "frame_start": 1, "frame_end": 3}
    executor().precondition("angelo_job_start", request)
    job = executor().execute("angelo_job_start", request)["job"]
    record = job_ui.manager().record(job)
    yield from fx.wait_for(lambda: record["status"] not in {"queued", "starting", "running", "stopping"},
                           what="the animation job")
    check("animation_job_wrote_its_frames", record["status"] == "done" and len(list((WS / "renders/sky").glob("frame_*.png"))) == 3)
    arguments, first = yield from view(image=job, frame=1)
    both("frame_one_of_a_real_animation_job_is_red", payloads(arguments, first), lambda rows: fx.is_red(corner(rows)))
    arguments, last = yield from view(image=job, frame=3)
    both("frame_three_of_it_is_blue", payloads(arguments, last), lambda rows: fx.is_blue(corner(rows)))
    check("its_frames_are_reported", last["rendered"]["frames"] == [1, 3] and last["rendered"]["frame_count"] == 3
          and last["rendered"]["frame"] == 3 and first["rendered"]["frame"] == 1)


def an_edit_after_a_look():
    state["phase"] = "edit"
    scene = window().scene
    fx.blue_sun(scene, "BLENDER_EEVEE")  # A lit floor under a black sky: a new object's shaded sides stand out.
    yield
    arguments, result = yield from view()
    first = payloads(arguments, result)["codex"]
    operations = [{"tool": "angelo_create_cube", "arguments": {"name": "After The Look", "size": 0.4, "location": [3.0, -3.0, 2.5]}}]
    edit = executor().execute("angelo_apply_batch", {"operations": operations})
    check("an_edit_after_a_look_succeeds", not edit.get("error") and bpy.data.objects.get("After The Look") is not None)
    arguments, result = yield from view()
    second = payloads(arguments, result)["codex"]
    changed = numpy.abs(second - first).max(axis=2) > 60
    state["pixels"]["edit_changed_pixels"] = int(changed.sum())
    check("the_next_look_shows_the_edit", int(changed.sum()) > 40)
    again = executor().execute("angelo_apply_batch", {"operations": [dict(operations[0], arguments=dict(
        operations[0]["arguments"], name="And Again", location=[2.0, -1.0, 2.0]))]})
    check("a_second_edit_after_that_look_succeeds", not again.get("error") and bpy.data.objects.get("And Again") is not None)


def scenario():
    state["module"] = scene_queries.__file__
    check("checkout_modules_loaded", str(ROOT) in scene_queries.__file__)
    (OUT / "recovery").mkdir(exist_ok=True)
    bpy.context.preferences.filepaths.temporary_directory = str(OUT / "recovery")
    yield
    for engine in ("BLENDER_EEVEE", "CYCLES"):
        yield from planted_defects(engine)
        yield from render_window_colours(engine)
    for engine in ("BLENDER_EEVEE", "CYCLES"):
        yield from small_defect(engine)  # Removes the Cube: after the scenes that look at it.
    yield from finished_files()
    yield from real_render_files()
    yield from an_edit_after_a_look()


fx.drive(scenario, state, REPORT, "ANGELO_RENDERED_VIEW_PIXELS")
