# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stereo, multi-view and compositor File Output renders as background jobs, on Blender's own render job.

A stereo scene whose compositor has File Output nodes (PNG items, a multi-layer
EXR, one inside a group, one muted) pointing at a folder outside the workspace
with a name template: the person's setup. Checked on the result: the job
writes exactly the files its approval listed, one per view and per linked item,
each with the pixels Blender writes for that view and item when it renders the
same scene by itself. Checked on the contract: nothing appears in the person's
own folder; the scene's render path, image format (a 32-bit EXR), view format
and every node's directory, file name and extension are exactly as they were
after a finished, a cancelled, a refused and a stale job; a cancelled animation
keeps whole frames only, File Output files included; the jobs block names each
output by view, node and item; a second job renders the first one's published
image; and a scene set to write a video still renders a plain still as before.
The workspace's own path has braces in it, which Blender reads as path
variables unless a job escapes them.

A window is required: run through ``run_native_suites.py --gui --include
test_blender_production_renders``; check ``production-renders.json`` for
``success`` and ``phase == "complete"``.
"""

import json
import sys
import time
from pathlib import Path

import addon_utils
import bpy

from angelo_agent import ui  # noqa: E402  (bundled add-on path)

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False

sys.path.insert(0, str(Path(__file__).resolve().parent))
import long_jobs_fixtures as fx  # noqa: E402
import production_jobs_fixtures as pfx  # noqa: E402
from production_jobs_harness import Harness, ended, job_seconds  # noqa: E402
from angelo_agent import job_render_outputs, job_runners, job_settings  # noqa: E402

harness = Harness("ANGELO_PRODUCTION_RENDERS", "production-renders.json", "/tmp/angelo-production-renders-qa",
                  timeout=540)
ctx = harness.ctx
OUTSIDE, RAW = harness.out / "the persons own folder", harness.out / "blender alone"
OUTSIDE.mkdir(exist_ok=True)
# What one frame of the fixture writes: both views of the image and of each linked item, per node.
PER_FRAME = ["Inner_Out/Grouped%s_L.png", "Inner_Out/Grouped%s_R.png", "Layers/layers%s_L.exr", "Layers/layers%s_R.exr",
             "Passes/Beauty%s_L.png", "Passes/Beauty%s_R.png", "Passes/Mask%s_L.png", "Passes/Mask%s_R.png",
             "frame_%s_L.png", "frame_%s_R.png"]


# Planned and listed too, but fed a single colour: Blender writes a File Output item only for an image.
SILENT = ["Constant/Flat%s_L.png", "Constant/Flat%s_R.png"]


def frame_files(*frames, names=PER_FRAME):
    return sorted(name % ("%04d" % frame) for frame in frames for name in names)


def person_format(scene):
    """The person's own output: a 32-bit RGBA EXR, which a PNG job changes in three settings at once."""
    settings = scene.render.image_settings
    settings.media_type, settings.file_format = 'IMAGE', 'OPEN_EXR'
    settings.color_depth, settings.color_mode, settings.views_format = '32', 'RGBA', 'INDIVIDUAL'


def person_paths(scene):
    scene.render.filepath = str(OUTSIDE / "{blend_name}_own_")
    for name, node in pfx.file_output_nodes(scene).items():
        node.directory, node.file_name = str(OUTSIDE), "{blend_name}_own_"
        node.use_file_extension = name != "Inner Out"  # One node without extensions: put back as it was.


def untouched(label):
    """The person's output settings, selection and mode as they were, and nothing written to their own folder."""
    scene = bpy.context.scene
    now = (pfx.output_settings(scene), pfx.selection_state(harness.window()))
    if now != ctx["before"]:
        harness.state.setdefault("differences", {})[label] = {"before": ctx["before"], "now": now}
    return now == ctx["before"] and job_settings.TAG not in scene and pfx.files_below(OUTSIDE) == [] \
        and harness.hidden_left() == []


def blender_alone(scene):
    """Blender renders frame 1 by itself with a job's format and file names, into ``RAW``."""
    scene.render.image_settings.file_format = 'PNG'  # From the EXR, as a PNG job sets it.
    pfx.point_scene_at(scene, RAW / ("timed" if "raw_warm" in ctx else "first"))
    ctx["raw_started"] = time.monotonic()
    with job_settings.DisplayNone(), bpy.context.temp_override(window=harness.window()):
        bpy.ops.render.render('INVOKE_DEFAULT', animation=True, frame_start=1, frame_end=1, use_viewport=False)


def extensions_match_blender(scene):
    """The planner's extension table against Blender's own answer for every format in it."""
    settings, wrong = scene.render.image_settings, {}
    for file_format, extension in job_render_outputs.EXTENSIONS.items():
        settings.media_type = 'MULTI_LAYER_IMAGE' if file_format == "OPEN_EXR_MULTILAYER" else 'IMAGE'
        settings.file_format = file_format
        if scene.render.file_extension != extension:
            wrong[file_format] = scene.render.file_extension
    settings.media_type, settings.file_format, settings.jpeg2k_codec = 'IMAGE', 'JPEG2000', 'J2K'
    if scene.render.file_extension != job_render_outputs.JPEG2000_CODESTREAM:
        wrong["JPEG2000/J2K"] = scene.render.file_extension
    settings.jpeg2k_codec = 'JP2'
    harness.state["extension_mismatches"] = wrong
    return not wrong


def step(harness):
    phase, scene, check = harness.state["phase"], bpy.context.scene, harness.check
    if phase == "boot":
        fx.cycles_scene(scene)
        scene.render.film_transparent = True
        scene.view_settings.view_transform = 'Standard'
        harness.rebind_workspace(harness.out / "work {space}")
        harness.workspace.mkdir(exist_ok=True)
        check("extension_table_is_blenders", extensions_match_blender(scene))
        pfx.stereo_scene(scene)
        pfx.compositor(scene, OUTSIDE)
        person_format(scene)
        blender_alone(scene)
        harness.go("alone")
    elif phase == "alone" and not bpy.app.is_job_running("RENDER"):
        ctx["raw_warm"] = True  # The first render of a session also loads the renderer: the second one is timed.
        person_format(scene)
        blender_alone(scene)
        harness.go("alone_timed")
    elif phase == "alone_timed" and not bpy.app.is_job_running("RENDER"):
        ctx["raw_seconds"] = round(time.monotonic() - ctx["raw_started"], 3)
        check("blender_alone_writes_the_same_names", pfx.files_below(RAW / "timed") == frame_files(1))
        ctx["raw"] = {name: pfx.pixel_hash(RAW / "timed" / name) for name in frame_files(1)}
        person_format(scene)
        person_paths(scene)
        ctx["before"] = (pfx.output_settings(scene), pfx.selection_state(harness.window()))
        ctx["still"], result = harness.start_job({"kind": "RENDER_STILL", "output": "renders/shot"})
        facts, approval = ctx["facts"]["j1"], ctx["approvals"]["j1"]
        harness.state["approval"] = approval
        planned = sorted(output["file"] for output in facts.get("outputs", []))
        check("approval_lists_every_file_before_it_starts", planned == frame_files(1, names=PER_FRAME + SILENT)
              and facts["output"] == "renders/shot"
              and all(Path(name).name in approval["body"] for name in planned) and "renders/shot" in approval["body"])
        check("approval_says_what_each_file_is", "View left" in approval["body"] and "View right" in approval["body"]
              and "File Output Passes: Mask" in approval["body"] and "File Output Layers: layers Beauty, Mask" in approval["body"]
              and "Loose" not in approval["body"] and "Muted" not in approval["body"])
        check("the_still_is_a_folder_of_its_files", result["output"] == "renders/shot")
        harness.go("still")
    elif phase == "still" and ended(ctx["still"]):
        record, folder = ctx["still"], harness.workspace / "renders/shot"
        harness.state["receipt_still"] = record["receipt"]
        check("stereo_still_done", record["status"] == "done" and record["receipt"].get("files") == len(PER_FRAME))
        check("an_item_fed_no_image_is_reported_not_invented", record["receipt"].get("missing") == frame_files(
            1, names=SILENT) and "wrote no file" in record["receipt"].get("note", ""))
        check("published_exactly_the_listed_files", pfx.files_below(folder) == frame_files(1))
        mine = {name: pfx.pixel_hash(folder / name) for name in pfx.files_below(folder)}
        harness.state["pixels"] = {"job": mine, "blender_alone": ctx["raw"]}
        check("every_view_and_item_has_blenders_own_pixels", mine == ctx["raw"])
        check("the_two_views_differ", mine.get("frame_0001_L.png") != mine.get("frame_0001_R.png"))
        check("the_items_differ", mine.get("Passes/Beauty0001_L.png") != mine.get("Passes/Mask0001_L.png"))
        header = (folder / "Layers/layers0001_L.exr").read_bytes()[:8192] if (folder / "Layers/layers0001_L.exr").is_file() else b""
        check("the_multi_layer_file_holds_both_layers", b"Beauty." in header and b"Mask." in header)
        check("outputs_identified_by_view_node_and_item", sorted(record["receipt"].get("outputs", []), key=str) == sorted([
            {"file": "frame_0001_L.png", "view": "left"}, {"file": "frame_0001_R.png", "view": "right"},
            {"file": "Passes/Beauty0001_L.png", "node": "Passes", "slot": "Beauty", "view": "left"},
            {"file": "Passes/Beauty0001_R.png", "node": "Passes", "slot": "Beauty", "view": "right"},
            {"file": "Passes/Mask0001_L.png", "node": "Passes", "slot": "Mask", "view": "left"},
            {"file": "Passes/Mask0001_R.png", "node": "Passes", "slot": "Mask", "view": "right"},
            {"file": "Layers/layers0001_L.exr", "node": "Layers", "layers": ["Beauty", "Mask"], "view": "left"},
            {"file": "Layers/layers0001_R.exr", "node": "Layers", "layers": ["Beauty", "Mask"], "view": "right"},
            {"file": "Inner_Out/Grouped0001_L.png", "node": "Inner Out", "slot": "Grouped", "view": "left"},
            {"file": "Inner_Out/Grouped0001_R.png", "node": "Inner Out", "slot": "Grouped", "view": "right"}], key=str))
        entry = next((item for item in harness.jobs_block() if item["job"] == record["id"]), {})
        harness.state["jobs_block_still"] = entry
        check("jobs_block_carries_each_output", entry.get("outputs") == record["receipt"].get("outputs")
              and entry.get("output") == "renders/shot" and str(harness.out) not in str(entry))
        from angelo_agent import job_ui
        check("each_output_is_reached_by_the_name_the_job_gave_it",
              job_ui.output_file(record, "Passes/Mask0001_R.png") == folder / "Passes/Mask0001_R.png"
              and job_ui.output_file(record, "frame_0001_L.png") == folder / "frame_0001_L.png"
              and job_ui.output_file(record, "Passes/Loose0001_L.png") is None
              and job_ui.output_file(record, "../shot/frame_0001_L.png") is None and job_ui.output_file(record) is None)
        check("scene_untouched_after_still", untouched("still"))
        harness.state["overhead_s"]["stereo_still_with_file_outputs"] = {
            "job": job_seconds(record), "blender_alone": ctx["raw_seconds"],
            "overhead": round(job_seconds(record) - ctx["raw_seconds"], 3)}
        # A second job at the same output is never refused and never replaces anything: it writes under the next
        # free name, which its approval states (job_output_names; test_native_job_free_names).
        again = {"kind": "RENDER_STILL", "output": "renders/shot"}
        message = harness.refused(again)
        card = harness.executor().precondition("angelo_job_start", again) if message is None else None
        writes = (getattr(card, "shown", None) or {}).get("writes")   # Shown on the card, never compared at the click.
        check("a_second_job_at_a_taken_name_writes_the_next_free_one", message is None
              and str(writes or "").startswith("renders/shot-j") and folder.is_dir())
        ctx["anim"], _ = harness.start_job({"kind": "RENDER_ANIMATION", "output": "renders/anim", "frame_start": 1,
                                           "frame_end": 3})
        check("animation_files_are_listed_as_patterns", sorted(
            output["file"] for output in ctx["facts"][ctx["anim"]["id"]]["outputs"]) == sorted(
            name % "####" for name in PER_FRAME + SILENT))
        harness.go("anim")
    elif phase == "anim" and ended(ctx["anim"]):
        record = ctx["anim"]
        check("stereo_animation_done", record["status"] == "done"
              and pfx.files_below(harness.workspace / "renders/anim") == frame_files(1, 2, 3)
              and record["receipt"].get("files") == 3 * len(PER_FRAME)
              and {"file": "Passes/Mask####_R.png", "node": "Passes", "slot": "Mask", "view": "right"}
              in record["receipt"]["outputs"])
        from angelo_agent import job_ui
        check("an_animations_output_is_reached_by_pattern_and_frame",
              job_ui.output_file(record, "Passes/Mask####_R.png", frame=2) == harness.workspace / "renders/anim/Passes/Mask0002_R.png"
              and job_ui.output_file(record, "Passes/Mask####_R.png", frame=4) is None
              and job_ui.output_file(record, "Passes/Mask####_R.png") is None)
        check("scene_untouched_after_animation", untouched("animation"))
        # The person's EXR keeps both views in one file: an EXR job does the same and says which views it holds.
        scene.render.image_settings.views_format = 'MULTIVIEW'
        ctx["before"] = (pfx.output_settings(scene), pfx.selection_state(harness.window()))
        ctx["exr"], _ = harness.start_job({"kind": "RENDER_STILL", "output": "renders/both", "format": "OPEN_EXR"})
        harness.go("exr")
    elif phase == "exr" and ended(ctx["exr"]):
        record, folder = ctx["exr"], harness.workspace / "renders/both"
        expected = sorted(["frame_0001.exr"] + [name for name in frame_files(1) if not name.startswith("frame_")])
        check("a_multi_view_exr_holds_both_views_in_one_file", record["status"] == "done"
              and pfx.files_below(folder) == expected
              and {"file": "frame_0001.exr", "views": ["left", "right"]} in record["receipt"].get("outputs", [])
              and b"left" in (folder / "frame_0001.exr").read_bytes()[:8192])
        check("scene_untouched_after_multi_view_exr", untouched("multi_view_exr"))
        scene.render.image_settings.views_format = 'INDIVIDUAL'
        ctx["before"] = (pfx.output_settings(scene), pfx.selection_state(harness.window()))
        ctx["slow"], _ = harness.start_job({"kind": "RENDER_ANIMATION", "output": "renders/cancelled", "frame_start": 1,
                                           "frame_end": 40, "samples": 256, "resolution": [96, 96]})
        harness.go("slow")
    elif phase == "slow" and harness.running_with_progress(ctx["slow"]) and ctx["slow"]["detail"].get("frames_done", 0) >= 1:
        check("the_persons_paths_are_replaced_only_while_it_runs", str(harness.workspace) in scene.render.filepath.replace(
            "{{", "{").replace("}}", "}") and job_settings.TAG in scene and pfx.files_below(OUTSIDE) == [])
        harness.cancel(ctx["slow"])
        harness.go("slow_end")
    elif phase == "slow_end" and ended(ctx["slow"]):
        harness.stopped("stereo_animation")
        harness.progress_ok(ctx["slow"], "stereo_animation")
        record, kept = ctx["slow"], pfx.files_below(harness.workspace / "renders/cancelled")
        frames = len(kept) // len(PER_FRAME)
        harness.state["cancelled"] = {"kept_files": len(kept), "frames": frames, "receipt": record["receipt"]}
        check("cancelled_animation_keeps_whole_frames_of_every_output", record["status"] == "cancelled" and frames >= 1
              and kept == frame_files(*range(1, frames + 1)) and record["receipt"]["kept"] == "%d/40 frames" % frames)
        check("scene_untouched_after_cancel", untouched("cancel"))
        scene.render.views_format = 'MULTIVIEW'  # Blender needs a camera per view then, and says so itself.
        ctx["before"] = (pfx.output_settings(scene), pfx.selection_state(harness.window()))
        ctx["refused"], result = harness.start_job({"kind": "RENDER_STILL", "output": "renders/refused"})
        check("blenders_own_refusal_reaches_the_agent", result.get("status") == "failed"
              and "not a multi-view camera" in result.get("error", "") and ended(ctx["refused"])
              and not (harness.workspace / "renders/refused").exists())
        check("scene_untouched_after_blender_refused", untouched("refused"))
        scene.render.views_format = 'STEREO_3D'
        ctx["before"] = (pfx.output_settings(scene), pfx.selection_state(harness.window()))
        # A queued job is approved for the files planned then: a File Output node unmuted meanwhile changes them.
        ctx["blocker"], _ = harness.start_job({"kind": "RENDER_ANIMATION", "output": "renders/blocker", "frame_start": 1,
                                              "frame_end": 40, "samples": 256, "resolution": [96, 96]})
        ctx["stale"], _ = harness.start_job({"kind": "RENDER_STILL", "output": "renders/stale", "queue": True})
        pfx.file_output_nodes(scene)["Muted"].mute = False
        harness.cancel(ctx["blocker"])
        harness.go("stale")
    elif phase == "stale" and ended(ctx["blocker"]) and ended(ctx["stale"]):
        check("a_queued_job_whose_outputs_changed_is_refused_at_start", ctx["stale"]["status"] == "failed"
              and ctx["stale"]["reason"] == job_runners.CHANGED and not (harness.workspace / "renders/stale").exists())
        pfx.file_output_nodes(scene)["Muted"].mute = True
        check("scene_untouched_after_stale_job", untouched("stale"))
        # A second job on the result: the compositor shows the first job's published left view, and a plain
        # still of a scene set to write a video reproduces it.
        scene.render.use_multiview = False
        ctx["shown"] = pfx.show_image_through_compositor(scene, harness.workspace / "renders/shot/frame_0001_L.png")
        scene.render.image_settings.media_type = 'VIDEO'
        ctx["video_format"] = (scene.render.image_settings.media_type, scene.render.image_settings.file_format)
        ctx["second"], result = harness.start_job({"kind": "RENDER_STILL", "output": "renders/second"})
        check("a_plain_still_stays_one_file", result["output"] == "renders/second.png"
              and "outputs" not in ctx["facts"][ctx["second"]["id"]])
        harness.go("second")
    elif phase == "second" and ended(ctx["second"]):
        record, published, first = ctx["second"], harness.workspace / "renders/second.png", harness.workspace / "renders/shot"
        centres = {"second": pfx.picture_centre(published) if published.is_file() else None,
                   "left": pfx.picture_centre(first / "frame_0001_L.png"), "right": pfx.picture_centre(first / "frame_0001_R.png")}
        harness.state["second_job_picture_centres"] = centres
        # The same picture as the left view it shows, which sits elsewhere than the right view by their parallax.
        check("second_job_reproduces_the_first_jobs_view", record["status"] == "done" and centres["second"] is not None
              and centres["right"] is not None and abs(centres["left"][0] - centres["right"][0]) > 4.0
              and abs(centres["second"][0] - centres["left"][0]) < 1.5 and abs(centres["second"][1] - centres["left"][1]) < 1.5
              and "outputs" not in record["receipt"])
        check("a_video_output_is_put_back", (scene.render.image_settings.media_type,
                                            scene.render.image_settings.file_format) == ctx["video_format"]
              and job_settings.TAG not in scene)
        check("no_hidden_staging_left", harness.hidden_left() == [] and pfx.files_below(OUTSIDE) == [])
        harness.finish()
        return None
    return 0.1


bpy.app.timers.register(harness.run(step), first_interval=1.0, persistent=True)
