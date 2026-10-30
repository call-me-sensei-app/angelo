# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A RENDERED look changes nothing of the person's, stays inside its bounds and can be stopped (GUI).

Real render jobs on tiny scenes, through ``BlenderExecutor`` and the shared job
manager, with a stand-in conversation: no provider, account or network.

* **Unchanged.** A scene with deliberately unusual settings (EXR output at 32
  bits, a quarter-size frame, 77 samples, caches on) is compared before and
  after a look: every value below the scene, every data-block of the file, the
  selection, mode and frame, the viewport, the preferences, the saved file and
  the workspace are the same, the person's own render is still in its slot of
  the Render Result, and Undo still undoes the person's last edit. The one
  thing added is the render slot looks use. While the look renders, its
  settings are on the scene (so the comparison is of a real override), recorded
  for restore. The same holds for a JPEG output with EEVEE and a video output,
  and for showing the person's own Render Result (``image``).
* **Bounds.** A look stopped at its time budget returns a partial image and
  says so; one whose budget ends while Blender still prepares the scene goes on
  to its first samples and returns those; only the request's total ends a look
  without an image, and without an error.
* **Stopped.** Cancel from the card and a stopped turn end the render, leave no
  image behind and restore everything.
* **Refused.** A scene without a camera, and a second look while one renders,
  start nothing. Scene edits wait while a look renders.
* **Saved meanwhile.** A file saved while a look rendered opens with the
  person's settings.

Run through ``run_native_suites.py --gui --include test_blender_rendered_view_safety``;
check ``rendered-view-safety.json`` for ``success`` and ``phase == "complete"``.
"""

import hashlib
import os
import sys
import tempfile
import time
from pathlib import Path

import addon_utils
import bpy

assert "--factory-startup" in sys.argv, "Use an isolated factory-startup instance"
TEMP_VARIABLE = "TEMP" if sys.platform == "win32" else "TMPDIR"
assert TEMP_VARIABLE in os.environ and not os.environ[TEMP_VARIABLE].startswith("/var/folders/"), \
    "Set a private %s so quit.blend cannot replace the user's session recovery file" % TEMP_VARIABLE
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-rendered-view-safety-qa"))
OUT.mkdir(parents=True, exist_ok=True)
WS, PROJECT = OUT / "workspace", OUT / "project"
for folder in (WS, PROJECT):
    folder.mkdir(exist_ok=True)
(WS / "notes.txt").write_text("the person's file")
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import ui  # noqa: E402

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False

import rendered_view_fixtures as fx  # noqa: E402
import test_native_view_fixture_boot as fixture_boot  # noqa: E402
from angelo_agent import job_host, job_settings, job_ui, scene_queries, view_render  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.input_policy import interaction_active  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402

REPORT = OUT / "rendered-view-safety.json"
state = {"phase": "boot", "success": False, "checks": {}, "differences": {}, "seconds": {}, "paid_requests": 0,
         "binary": bpy.app.binary_path}
ctx = {}
controller = fx.conversation(WS)
references = ReferenceStore(OUT, "7b6a8a52-0c7e-4f2e-9a57-3c1d5c0f7e21")
VIEWPORT = ("type", "light", "color_type", "show_xray", "use_scene_lights", "use_scene_world", "studio_light")


def check(name, condition):
    state["checks"][name] = bool(condition)
    assert condition, name


def window():
    return bpy.context.window_manager.windows[0]


def executor():
    value = BlenderExecutor(window())
    value.studio_context, value.observation_store = controller, references
    return value


def start(resolution=256, **arguments):
    controller._request_generation += 1  # Each look of this suite is its own request.
    return executor().execute("angelo_view", dict({"views": ["SCENE_CAMERA"], "shading": "RENDERED",
                                                   "resolution": resolution}, **arguments))


def reference_files():
    return sorted(path.name for path in references.directory.glob("*")) if references.directory.exists() else []


def persons_render_digest():
    """The person's own render, as their slot of the Render Result holds it (None for a video output format)."""
    path = OUT / "persons-render-check.img"
    path.unlink(missing_ok=True)
    try:
        fx.render_result().save_render(str(path), scene=window().scene)
    except RuntimeError:
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def snapshot():
    scene, layer = window().scene, window().view_layer
    area = next(a for a in window().screen.areas if a.type == "VIEW_3D")
    space = area.spaces.active
    preferences = bpy.context.preferences
    result = fx.render_result()
    return {"scene": fx.rna_values(scene), "custom": sorted(scene.keys()), "ids": fx.id_names(),
            "selection": sorted(o.name for o in layer.objects if o.select_get(view_layer=layer)),
            "active": layer.objects.active and layer.objects.active.name, "mode": bpy.context.mode,
            "frame": scene.frame_current, "dirty": bpy.data.is_dirty,
            "viewport": {key: getattr(space.shading, key) for key in VIEWPORT},
            "preferences": (preferences.view.render_display_type, preferences.is_dirty),
            "slots": ([slot.name for slot in result.render_slots], result.render_slots.active_index),
            "saved_file": hashlib.sha256(Path(bpy.data.filepath).read_bytes()).hexdigest(),
            "project": fx.folder_listing(PROJECT), "workspace": fx.folder_listing(WS),
            "render_cache": sorted(path.name for path in Path(bpy.app.tempdir).rglob("*.exr"))}


def unchanged(label, before, *, new_slot):
    """Everything is as before the look; ``new_slot``: this was the first look of the file (its slot is new)."""
    after = snapshot()
    slots, active = before.pop("slots")
    now, now_active = after.pop("slots")
    found = fx.differences(before, after)
    state["differences"][label] = found[:20]
    check(label + "_nothing_changed", not found)
    check(label + "_render_slots", now == (slots if view_render.SLOT in slots else slots + [view_render.SLOT])
          and now_active == active and (view_render.SLOT not in slots) == new_slot)
    check(label + "_no_restore_tag_left", job_settings.TAG not in window().scene)
    before["slots"] = (now, now_active)


def looked_unchanged(label, *, new_slot=False, engine="CYCLES", region=None):
    """Generator: one look of the person's scene (or of a region of it), compared before, during and after."""
    scene = window().scene
    before, persons_render, files = snapshot(), persons_render_digest(), reference_files()
    pending = start(region=region) if region else start()
    record = job_ui.manager().active()
    during = scene.render
    # A region half the frame wide: a frame twice the picture, and Blender's render region set for the job.
    frame = (512, 288, 100) if region else (256, 144, 100)
    check(label + "_runs_as_a_job_with_its_own_settings",
          record["kind"] == "RENDER_PREVIEW" and (during.resolution_x, during.resolution_y, during.resolution_percentage)
          == frame and during.image_settings.file_format == "PNG" and job_settings.TAG in scene
          and bool(during.use_border and during.use_crop_to_border) == bool(region)
          and not during.use_render_cache and Path(during.filepath).parent == references.directory)
    check(label + "_renders_into_its_own_slot",
          fx.render_result().render_slots[fx.render_result().render_slots.active_index].name == view_render.SLOT)
    check(label + "_is_an_interaction_while_it_renders", interaction_active(window()))
    result = yield from fx.pending_result(pending)
    check(label + "_returned_one_image", result["success"] and len(result["views"]) == 1
          and result["rendered"]["engine"] == engine and result["side_effects"] == []
          and fx.png_size(result["views"][0]["path"]) == (256, 144))
    check(label + "_wrote_only_its_reference", len(reference_files()) == len(files) + 1)
    unchanged(label, before, new_slot=new_slot)
    check(label + "_persons_render_still_in_its_slot", persons_render_digest() == persons_render)
    check(label + "_is_no_interaction_once_ended", not interaction_active(window()))
    return result


def scenario():
    state["module"] = scene_queries.__file__
    check("checkout_modules_loaded", str(ROOT) in scene_queries.__file__)
    (OUT / "recovery").mkdir(exist_ok=True)
    bpy.context.preferences.filepaths.temporary_directory = str(OUT / "recovery")
    scene = window().scene
    area = next(a for a in window().screen.areas if a.type == "VIEW_3D")
    area.spaces.active.shading.light, area.spaces.active.shading.color_type = "MATCAP", "RANDOM"
    yield

    # The person's scene, their own render, their saved file and their last edit.
    state["phase"] = "unchanged_cycles_exr"
    fx.persons_scene(scene)
    with fx.in_view3d():
        bpy.ops.wm.save_as_mainfile(filepath=str(PROJECT / "person.blend"))
        bpy.ops.render.render()  # The person's F12: the Render Result a look must not replace.
    yield
    cube = bpy.data.objects["Cube"]
    for obj in window().view_layer.objects:
        obj.select_set(obj == cube)
    window().view_layer.objects.active = cube
    with fx.in_view3d():
        bpy.ops.ed.undo_push(message="The person's scene")
        cube.location.z = 1.5  # Their last edit, with its own Undo step.
        bpy.ops.ed.undo_push(message="The person's move")
    yield
    yield from looked_unchanged("cycles_exr", new_slot=True)
    yield from looked_unchanged("cycles_exr_again")
    yield from looked_unchanged("cycles_exr_region", region=[0.25, 0.25, 0.75, 0.75])
    # The person's own render, shown on request: saved once as a PNG, their EXR settings put back.
    before, persons_render, files = snapshot(), persons_render_digest(), reference_files()
    shown = executor().execute("angelo_view", {"views": ["SCENE_CAMERA"], "shading": "RENDERED", "resolution": 256,
                                               "image": "RENDER_RESULT"})
    check("render_result_shown_is_the_persons_render", shown["success"] and shown["rendered"]["slot"] != view_render.SLOT
          and shown["rendered"]["source_size"] == [480, 270] and shown["side_effects"] == []
          and fx.is_red(fx.mean(fx.decode_png(shown["views"][0]["path"]), (0.0, 0.0, 0.2, 0.2))))
    check("render_result_shown_wrote_only_its_reference", len(reference_files()) == len(files) + 1)
    unchanged("render_result_shown", before, new_slot=False)
    check("render_result_shown_left_the_persons_render", persons_render_digest() == persons_render)
    with fx.in_view3d():
        bpy.ops.ed.undo()
    yield
    check("undo_still_undoes_the_persons_last_edit", abs(bpy.data.objects["Cube"].location.z) < 1e-6
          and window().scene.cycles.samples == 77)
    with fx.in_view3d():
        bpy.ops.ed.redo()
    yield
    check("redo_still_redoes_it", abs(bpy.data.objects["Cube"].location.z - 1.5) < 1e-6)

    state["phase"] = "unchanged_eevee_jpeg"
    scene = window().scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.image_settings.file_format = "JPEG"
    scene.render.image_settings.quality = 61
    with fx.in_view3d():
        bpy.ops.render.render()
    yield
    yield from looked_unchanged("eevee_jpeg", new_slot=view_render.SLOT not in snapshot()["slots"][0],
                                engine="BLENDER_EEVEE")

    state["phase"] = "unchanged_video"
    settings = scene.render.image_settings
    if hasattr(settings, "media_type"):
        settings.media_type = "VIDEO"
    else:
        settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format, scene.render.ffmpeg.codec = "MKV", "H264"
    yield
    yield from looked_unchanged("video", engine="BLENDER_EEVEE")
    if hasattr(settings, "media_type"):
        settings.media_type = "IMAGE"
    settings.file_format = "PNG"

    state["phase"] = "refused"
    before, files = snapshot(), reference_files()
    camera, scene.camera = scene.camera, None
    try:
        start()
        check("no_camera_refused", False)
    except RuntimeError as error:
        check("no_camera_refused", "no active camera" in str(error) and job_ui.manager().active() is None)
    scene.camera = camera
    check("no_camera_started_nothing", reference_files() == files and not fx.differences(before["scene"], snapshot()["scene"]))

    state["phase"] = "partial"
    fx.slow_scene(scene)
    yield
    # A four-second budget, and no further stretch of sampling: this scene samples from its first second.
    view_render.RENDER_SECONDS, stretch, view_render.SAMPLE_SECONDS = 4, view_render.SAMPLE_SECONDS, 0
    pending = start(resolution=1024)
    record = job_ui.manager().active()
    try:
        start()
        check("second_look_refused_while_one_renders", False)
    except RuntimeError as error:
        check("second_look_refused_while_one_renders", "is running" in str(error) and record["id"] in str(error))
    try:
        executor().precondition("angelo_create_cube", {"name": "Blocked", "size": 1, "location": [0, 0, 0]})
        check("scene_edits_wait_while_a_look_renders", False)
    except job_host.JobBusyError as error:
        check("scene_edits_wait_while_a_look_renders", record["id"] in str(error))
    result = yield from fx.pending_result(pending)
    state["partial"] = {"rendered": result.get("rendered"), "uncertainty": result.get("uncertainty"), "note": result.get("note")}
    rendered = result["rendered"]
    check("budget_stop_returns_a_partial_image_that_says_so",
          result.get("success") and not rendered["complete"] and result["uncertain"]
          and result["uncertainty"][0]["code"] == "render_partial" and 0 < rendered["samples_finished"] < rendered["samples"]
          and fx.png_size(result["views"][0]["path"]) == (1024, 576) and 4 <= rendered["seconds"] < 30)
    check("partial_says_how_its_seconds_split", abs(rendered["prepare_seconds"] + rendered["sampling_seconds"]
                                                    - rendered["seconds"]) < 0.25 and "It took " in result["note"]
          and "preparing the scene for rendering" in result["note"])
    check("partial_restored_settings", (scene.render.resolution_x, scene.cycles.samples) == (1920, 4096)
          and job_settings.TAG not in scene and job_ui.manager().active() is None)
    state["seconds"]["budget_stop_latency"] = round(rendered["seconds"] - 4, 2)

    state["phase"] = "cancel"
    view_render.RENDER_SECONDS, view_render.SAMPLE_SECONDS = 120, stretch
    files = reference_files()
    pending = start(resolution=1024)
    record = job_ui.manager().active()
    yield from fx.wait_for(lambda: record["progress"] > 0, what="the look's first sample")
    job_ui.manager().cancel(record["id"], by="user")  # The card's and the status bar's Cancel.
    result = yield from fx.pending_result(pending)
    check("cancel_returns_no_image_and_no_error", result["views"] == [] and "error" not in result
          and result["rendered"]["ended"] == "user" and "cancelled this look" in result["note"])
    check("cancel_left_nothing", reference_files() == files and job_settings.TAG not in scene
          and scene.render.resolution_x == 1920 and job_ui.manager().active() is None)

    state["phase"] = "turn_stopped"
    pending = start(resolution=1024)
    record = job_ui.manager().active()
    yield from fx.wait_for(lambda: record["progress"] > 0, what="the look's first sample")
    pending.cancel()  # What Controller.cancel_tools does when the person stops the turn.
    yield from fx.wait_for(lambda: job_ui.manager().active() is None, what="the stopped look to end")
    check("stopped_turn_left_nothing", reference_files() == files and job_settings.TAG not in scene
          and scene.render.resolution_x == 1920 and record["id"] not in job_ui.manager().store.records)

    state["phase"] = "preparing"
    fx.heavy_to_prepare(scene)
    yield
    view_render.RENDER_SECONDS = 0.3
    with fx.HeldPreparation() as held:   # The look's budget ends while Blender holds before its first sample.
        pending = start()
        yield from fx.wait_for(lambda: held.held.is_set(), what="the look's render to start preparing")
        yield from fx.wait_for(lambda: time.monotonic() - pending.runner.began > pending.runner.limit,
                               what="the look's budget to end during preparation")
        held.release()
        result = yield from fx.pending_result(pending)
    view_render.RENDER_SECONDS = 120
    state["preparing"] = {"rendered": result.get("rendered"), "uncertainty": result.get("uncertainty"),
                          "held_on_the_main_thread": held.threads}
    # Whatever had rendered at the first look after preparation: a few samples, or, on this small frame, all.
    check("a_budget_that_ends_during_preparation_still_returns_a_picture",
          result.get("success") and len(result["views"]) == 1 and result["rendered"]["seconds"] > 0.3
          and (result["rendered"]["complete"] or result["uncertainty"][0]["code"] == "render_partial"))
    files = reference_files()
    view_render.RENDER_REQUEST_SECONDS, view_render.MINIMUM_SECONDS = 0.3, 0
    with fx.HeldPreparation() as held:   # The request's total ends the look while Blender holds before its first sample.
        pending = start()
        yield from fx.wait_for(lambda: held.held.is_set(), what="the look's render to start preparing")
        yield from fx.wait_for(lambda: pending.record["status"] != "running",
                               what="the request's total to stop the look during preparation")
        held.release()
        result = yield from fx.pending_result(pending)
    view_render.RENDER_REQUEST_SECONDS, view_render.MINIMUM_SECONDS = 360, 15
    state["request_total"] = dict(result, held_on_the_main_thread=held.threads)
    check("the_preparation_holds_ran_on_the_render_thread",   # A hold on the main thread would have stalled the suite.
          state["preparing"]["held_on_the_main_thread"] == [False] and held.threads == [False])
    check("the_requests_total_ends_a_look_without_an_image_or_an_error",
          result["views"] == [] and "error" not in result and result["rendered"]["ended"] == "time_budget"
          and "still preparing the scene" in result["note"] and reference_files() == files
          and job_settings.TAG not in scene)
    bpy.data.objects["Cube"].modifiers.remove(bpy.data.objects["Cube"].modifiers["Preparation"])

    state["phase"] = "saved_meanwhile"
    fx.slow_scene(scene)
    scene.cycles.samples = 55
    yield
    pending = start(resolution=1024)
    record = job_ui.manager().active()
    yield from fx.wait_for(lambda: record["progress"] > 0, what="the look's first sample")
    saved = OUT / "saved-during-a-look.blend"
    with fx.in_view3d():
        bpy.ops.wm.save_as_mainfile(filepath=str(saved), copy=True)
    job_ui.manager().cancel(record["id"], by="user")
    yield from fx.pending_result(pending)
    check("live_scene_restored_after_the_saved_look", scene.cycles.samples == 55 and scene.render.resolution_x == 1920)
    bpy.ops.wm.open_mainfile(filepath=str(saved))
    yield
    yield
    scene = window().scene
    check("a_file_saved_during_a_look_opens_with_the_persons_settings",
          scene.cycles.samples == 55 and (scene.render.resolution_x, scene.render.resolution_y) == (1920, 1080)
          and scene.render.image_settings.file_format == "PNG" and job_settings.TAG not in scene
          and not scene.render.filepath.endswith(".png"))

    state["phase"] = "layer_samples"
    import rendered_view_sample_checks
    yield from rendered_view_sample_checks.run(window, start, fx.pending_result, check, state)


fixture_boot.drive(scenario, state, REPORT, "ANGELO_RENDERED_VIEW_SAFETY")
