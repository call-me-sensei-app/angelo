# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A render job writes the deliverable stock Blender would: the scene's own format, shot names and frame step.

VFX audit A2. On Blender's own render job, through the real executor:

* a multilayer OpenEXR (DWAA, half float) of two view layers with Depth, Normal
  and Cryptomatte passes, named ``sh010_v003.###`` on the scene's frame step of
  2: exactly frames 1, 3 and 5, each file whole, holding every layer's passes
  and the scene's codec, and the approval says so;
* a 16-bit TIFF still in the scene's own format, named with its suffix;
* the scene's own plain file name used as the frame pattern when no name is
  given, as F12 names it;
* stereo views in their own files under a dotted shot name (``st010.v1.##``);
* a scene writing a video refused before anything runs;
* Stereo 3D views on a multilayer OpenEXR: Blender 5.2 never holds that pair
  (choosing the multilayer media type turns the views into Multi-View), and
  the job then writes them Multi-View, as F12 does;
* the person's image settings, frame step and output path exactly as they were.

A window is required: run through ``run_native_suites.py --gui --include
test_blender_render_deliverables``; check ``render-deliverables.json`` for
``success`` and ``phase == "complete"``.
"""

import struct
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
from angelo_agent import job_frame_check, job_render_scene, job_settings  # noqa: E402

harness = Harness("ANGELO_RENDER_DELIVERABLES", "render-deliverables.json", "/tmp/angelo-render-deliverables-qa",
                  timeout=420)
ctx = harness.ctx
DWAA = 8   # OpenEXR's compression code (ImfCompression.h).


def exr_channels(path):
    """Channel names and compression of a single- or multi-part OpenEXR, read from its header."""
    data = Path(path).read_bytes()
    read = lambda offset, count: data[offset:offset + count]   # noqa: E731
    (version,) = struct.unpack("<i", read(4, 4))
    headers, _end = job_frame_check._exr_headers(read, len(data), version)
    names, codecs = [], set()
    for header in headers:
        raw = header["channels"][1]
        position = 0
        while raw[position:position + 1] != b"\0":
            end = raw.index(b"\0", position)
            names.append(raw[position:end].decode())
            position = end + 1 + 16
        codecs.add(header["compression"][1][0])
    return names, codecs


def person_settings(scene):
    image = scene.render.image_settings
    return (image.file_format, image.color_depth, getattr(image, "exr_codec", None), image.media_type,
            scene.frame_step, scene.render.filepath)


def image_format(image, media_type, file_format=None):
    """Set a format the way Blender 5.2 allows it: the media type first (MULTI_LAYER_IMAGE is the multilayer OpenEXR
    and its only format; IMAGE offers the other stills), then the format within it. The suite's first GUI run set
    file_format OPEN_EXR_MULTILAYER under IMAGE and Blender refused the enum."""
    image.media_type = media_type
    if file_format is not None:
        image.file_format = file_format


def step(harness):
    phase, scene, check = harness.state["phase"], bpy.context.scene, harness.check
    if phase == "boot":
        fx.cycles_scene(scene)
        scene.cycles.samples = 1
        scene.render.resolution_x = scene.render.resolution_y = 48
        scene.view_layers.new("FG")
        for layer in scene.view_layers:
            layer.use_pass_z = layer.use_pass_normal = layer.use_pass_cryptomatte_object = True
        image = scene.render.image_settings
        image_format(image, 'MULTI_LAYER_IMAGE')   # Its format is OPEN_EXR_MULTILAYER (and its depth 32 until set).
        image.exr_codec, image.color_depth = 'DWAA', '16'
        scene.frame_step = 2
        scene.render.filepath = "//person/output_"
        ctx["person"] = person_settings(scene)
        ctx["multi"], result = harness.start_job({"kind": "RENDER_ANIMATION", "output": "shots/sh010",
                                                  "frame_start": 1, "frame_end": 5, "format": "SCENE",
                                                  "name": "sh010_v003.###"})
        approval = harness.ctx["approvals"][ctx["multi"]["id"]]["body"]
        check("approval_names_the_scenes_settings_and_step",
              "OPEN_EXR_MULTILAYER" in approval and "DWAA" in approval and "2" in approval)
        check("approval_lists_the_shot_pattern", "sh010_v003.###.exr" in approval)
        harness.go("multi")
    elif phase == "multi" and ended(ctx["multi"]):
        record, folder = ctx["multi"], harness.workspace / "shots/sh010"
        names = sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []
        check("multilayer_done", record["status"] == "done")
        check("shot_names_on_the_frame_step", names == ["sh010_v003.001.exr", "sh010_v003.003.exr",
                                                         "sh010_v003.005.exr"])
        whole = [job_frame_check.whole(lambda o, c, d=(folder / n).read_bytes(): d[o:o + c],
                                       (folder / n).stat().st_size, ".exr") for n in names]
        check("every_file_whole", bool(names) and all(answer is True for answer in whole))
        channels, codecs = exr_channels(folder / names[0]) if names else ([], set())
        ctx["channels"] = channels[:40]
        check("every_layer_and_pass_in_one_file", all(any(name.startswith(prefix) for name in channels) for prefix in
                                                      ("ViewLayer.Depth", "FG.Depth", "ViewLayer.Normal",
                                                       "FG.Normal", "ViewLayer.CryptoObject", "FG.CryptoObject")))
        check("the_scenes_codec", codecs == {DWAA})
        check("receipt_names_the_pattern", any(entry.get("file") == "sh010_v003.###.exr"
                                               for entry in record["receipt"].get("outputs", [])))
        check("person_settings_unchanged", person_settings(scene) == ctx["person"] and job_settings.TAG not in scene)
        image = scene.render.image_settings
        image_format(image, 'IMAGE', 'TIFF')
        image.color_depth, image.tiff_codec = '16', 'DEFLATE'
        ctx["tiff"], result = harness.start_job({"kind": "RENDER_STILL", "output": "plates/plate", "format": "SCENE"})
        check("a_still_takes_the_scenes_suffix", result.get("output") == "plates/plate.tif")
        harness.go("tiff")
    elif phase == "tiff" and ended(ctx["tiff"]):
        path = harness.workspace / "plates/plate.tif"
        data = path.read_bytes() if path.is_file() else b""
        check("tiff_still_done_and_whole", ctx["tiff"]["status"] == "done" and job_frame_check.whole(
            lambda offset, count: data[offset:offset + count], len(data), ".tif") is True)
        image = scene.render.image_settings
        image_format(image, 'IMAGE', 'PNG')
        scene.frame_step = 1
        scene.render.filepath = "//person/shotA_"
        ctx["named"], _ = harness.start_job({"kind": "RENDER_ANIMATION", "output": "shots/plain", "frame_start": 1,
                                            "frame_end": 2, "format": "SCENE"})
        harness.go("named")
    elif phase == "named" and ended(ctx["named"]):
        folder = harness.workspace / "shots/plain"
        check("the_scenes_own_name_as_f12", sorted(path.name for path in folder.iterdir()) ==
              ["shotA_0001.png", "shotA_0002.png"] if folder.is_dir() else False)
        image = scene.render.image_settings
        image.media_type = 'VIDEO'
        video = harness.refused({"kind": "RENDER_ANIMATION", "output": "shots/video", "format": "SCENE"})
        check("a_video_scene_is_refused", video is not None and "video" in video)
        image_format(image, 'IMAGE', 'PNG')
        scene.render.use_multiview = True
        scene.render.views_format = 'STEREO_3D'
        image.views_format = 'STEREO_3D'
        image_format(image, 'MULTI_LAYER_IMAGE')   # The person picks a multilayer EXR for their stereo scene.
        check("a_multilayer_exr_takes_stereo_views_as_multi_view", image.views_format == 'MULTIVIEW')
        check("the_job_writes_them_multi_view_as_f12", job_render_scene.views_format(
            scene, image.file_format, job_render_scene.active_views(scene)) == "MULTIVIEW")
        image_format(image, 'IMAGE', 'PNG')
        image.views_format = 'INDIVIDUAL'   # Stereo views in their own files, under a dotted shot name.
        ctx["stereo"], _ = harness.start_job({"kind": "RENDER_ANIMATION", "output": "shots/stereo", "frame_start": 1,
                                             "frame_end": 1, "format": "PNG", "name": "st010.v1.##"})
        harness.go("stereo")
    elif phase == "stereo" and ended(ctx["stereo"]):
        folder = harness.workspace / "shots/stereo"
        check("stereo_views_under_a_dotted_name", ctx["stereo"]["status"] == "done" and folder.is_dir()
              and sorted(path.name for path in folder.iterdir()) == ["st010.v1.01_L.png", "st010.v1.01_R.png"])
        scene.render.use_multiview = False
        check("no_hidden_staging_left", harness.hidden_left() == [])
        harness.finish()
        return None
    return 0.2


bpy.app.timers.register(harness.run(step), first_interval=1.0, persistent=True)
