# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Video from the scene (1.0.8, release plan section B): format MP4 render jobs and shots from the camera.

On Blender's own render job, through the real executor and job manager:

* a format MP4 animation publishes its 12 PNG frames and ``<output>.mp4`` beside them: a complete MP4 that Blender
  reads back as 12 frames at the job's size and the scene's rate, named in the approval and the jobs block;
* format MP4 on a still is refused before anything runs;
* Make Video from This Shot's capture (``shot_capture.start``) renders the first and last frame in EEVEE at 16:9
  and the camera move as a Workbench MP4, hands the shot to its callback once every part ended, and puts the
  person's engine, resolution and image settings back;
* the operator, its menus and its + menu row are registered, and the operator polls on the camera.

On a build without FFmpeg (the development build) the suite checks the refusal instead: format MP4 is refused before
anything runs and the shot's move says why, while its frames still render; ``full_feature_build`` in the receipt says
which binary ran. A window is required: run through ``run_native_suites.py --gui
--include test_blender_shot_video``; check ``shot-video.json`` for ``success`` and ``phase == "complete"``.
"""

import sys
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
from production_jobs_harness import Harness, ended  # noqa: E402
from angelo_agent import chat_destinations, job_render_video, job_settings, shot_capture  # noqa: E402

harness = Harness("ANGELO_SHOT_VIDEO", "shot-video.json", "/tmp/angelo-shot-video-qa", timeout=600)
ctx = harness.ctx
FRAMES = 12


def person_settings(scene):
    image = scene.render.image_settings
    return (scene.render.engine, scene.render.resolution_x, scene.render.resolution_y, image.media_type,
            image.file_format, scene.render.filepath, scene.cycles.samples)


def movie(path):
    """``(frames, (width, height), fps)`` of a movie as Blender reads it back."""
    clip = bpy.data.movieclips.load(str(path))
    try:
        return clip.frame_duration, tuple(clip.size), round(clip.fps, 3)
    finally:
        bpy.data.movieclips.remove(clip)


def animate_camera(scene):
    """A camera that moves around the cube and keeps it framed: the first and last frame differ and both show it."""
    camera = scene.camera
    track = camera.constraints.new('TRACK_TO')
    track.target = bpy.data.objects["Cube"]
    camera.location = (7.0, -7.0, 5.0)
    camera.keyframe_insert("location", frame=1)
    camera.location = (9.0, 3.0, 5.0)
    camera.keyframe_insert("location", frame=FRAMES)
    scene.frame_start, scene.frame_end = 1, FRAMES


def step(harness):
    phase, scene, check = harness.state["phase"], bpy.context.scene, harness.check
    if phase == "boot":
        ctx["video"] = harness.state["full_feature_build"] = job_render_video.available()
        fx.cycles_scene(scene)
        scene.cycles.samples = 1
        animate_camera(scene)
        ctx["person"] = person_settings(scene)
        still = harness.refused({"kind": "RENDER_STILL", "output": "s/a.png", "format": "MP4"})
        check("mp4_still_refused", still is not None and "RENDER_ANIMATION" in still)
        if not ctx["video"]:   # The development build: refused, said why, nothing started.
            refusal = harness.refused({"kind": "RENDER_ANIMATION", "output": "anims/turn", "format": "MP4"})
            check("mp4_refused_without_ffmpeg", refusal is not None and "FFmpeg" in refusal)
            return start_shot(harness)
        ctx["anim"], result = harness.start_job({"kind": "RENDER_ANIMATION", "output": "anims/turn", "format": "MP4",
                                                 "frame_start": 1, "frame_end": FRAMES, "resolution": [97, 54]})
        approval = harness.ctx["approvals"][ctx["anim"]["id"]]["body"]
        check("approval_names_the_video", "turn.mp4" in approval)
        harness.go("anim")
    elif phase == "anim" and ended(ctx["anim"]):
        record, folder = ctx["anim"], harness.workspace / "anims/turn"
        names = sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []
        ctx["anim_names"], ctx["anim_receipt"] = names[:20], record["receipt"]
        check("mp4_job_done", record["status"] == "done")
        check("frames_and_video_published", names == ["frame_%04d.png" % n for n in range(1, FRAMES + 1)]
              + ["turn.mp4"])
        video = folder / "turn.mp4"
        check("video_is_complete", job_render_video.mp4_whole(video))
        read = movie(video) if video.is_file() else None
        ctx["anim_movie"] = read
        check("video_frames_size_rate", read == (FRAMES, (96, 54), round(scene.render.fps / scene.render.fps_base, 3)))
        check("receipt_names_the_video", record["receipt"].get("video") == "turn.mp4")
        entry = next((item for item in harness.jobs_block() if item.get("job") == record["id"]), {})
        check("jobs_block_names_the_video", entry.get("video") == "turn.mp4")
        check("person_settings_unchanged_after_mp4", person_settings(scene) == ctx["person"]
              and job_settings.TAG not in scene)
        return start_shot(harness)
    elif phase == "shot" and ctx["shot_done"]:
        shot = ctx["shot"]
        ctx["shot_result"] = {"files": {k: str(Path(v).name) for k, v in shot.files.items()}, "errors": shot.errors}
        check("first_frame_16_9", fx.is_png(shot.files.get("first", ""), 256, 144))
        check("last_frame_16_9", fx.is_png(shot.files.get("last", ""), 256, 144))
        engines = {part: (harness.jobs().record(job) or {}).get("detail", {}).get("engine")
                   for part, job in shot.jobs.items()}
        ctx["shot_engines"] = engines
        check("parts_rendered_in_their_own_engines", engines == {"first": "BLENDER_EEVEE", "last": "BLENDER_EEVEE",
                                                                 **({"motion": "BLENDER_WORKBENCH"} if ctx["video"]
                                                                    else {})})
        check("first_and_last_differ", "first" in shot.files and "last" in shot.files
              and Path(shot.files["first"]).read_bytes() != Path(shot.files["last"]).read_bytes())
        if not ctx["video"]:
            check("motion_refused_with_its_reason", "FFmpeg" in shot.errors.get("motion", "")
                  and "motion" not in shot.files)
            return finish_shot(harness, scene)
        check("shot_has_every_part", set(shot.files) == {"first", "last", "motion"} and not shot.errors)
        motion = shot.files.get("motion")
        check("motion_is_complete_mp4", motion is not None and job_render_video.mp4_whole(motion))
        read = movie(motion) if motion is not None else None
        ctx["motion_movie"] = read
        check("motion_frames_and_size", read is not None and read[:2] == (FRAMES, (854, 480)))
        check("shot_in_its_own_folder", motion is not None and Path(motion).parent.parent.name.startswith("Camera-"))
        return finish_shot(harness, scene)
    return 0.2


def start_shot(harness):
    ctx["shot_done"] = []
    ctx["shot"] = shot_capture.start(harness.window(), harness.controller, aspect="16:9", short_side=144,
                                     on_done=ctx["shot_done"].append)
    expected = {"first", "last", "motion"} if ctx["video"] else {"first", "last"}
    harness.check("shot_queued_its_parts", set(ctx["shot"].jobs) == expected)
    harness.go("shot")
    return 0.2


def finish_shot(harness, scene):
    check = harness.check
    check("person_settings_unchanged_after_shot", person_settings(scene) == ctx["person"]
          and job_settings.TAG not in scene)
    check("operator_registered_and_polls", hasattr(bpy.types, "ANGELO_OT_shot_to_video")
          and bpy.ops.angelo.shot_to_video.poll())
    check("plus_menu_row", chat_destinations.feature("shot_to_video") is not None)
    check("render_menu_entry", any(getattr(f, "__name__", "") == "menu_item"
                                   for f in bpy.types.TOPBAR_MT_render._dyn_ui_initialize()))
    check("no_hidden_staging_left", harness.hidden_left() == [])
    harness.finish()
    return None


bpy.app.timers.register(harness.run(step), first_interval=1.0, persistent=True)
