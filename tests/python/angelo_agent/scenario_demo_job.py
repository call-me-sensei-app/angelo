# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Encode a demo recording's window frames into a 1080p H.264 video and a poster (stock Blender, background).

    Blender -b --factory-startup --python-exit-code 1 --python scenario_demo_job.py -- \\
        --frames DIR --video OUT.mp4 --poster OUT.png [--fps 30] [--hold 1]

Started by ``scenario_demo.encode``. The frames (listed in ``frames.json``,
relative to it, by ``scenario_demo.Recorder``) become one image strip in the Video Sequencer,
fitted into 1920 x 1080 (letterboxed when the window is not 16:9), each frame
held ``--hold`` video frames. Colour management is Standard sRGB, because a
window grab is already display-referred. The poster is the last frame. Blender's
own FFmpeg writes the MP4 (H.264, constant quality "high", a keyframe every
second), so the host needs no other encoder.
"""

import argparse
import json
import sys
from pathlib import Path

import bpy

SIZE = (1920, 1080)


def arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", required=True)
    parser.add_argument("--video", required=True)
    parser.add_argument("--poster", required=True)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--hold", type=int, default=1)
    return parser.parse_args(sys.argv[sys.argv.index("--") + 1:])


def render_to(scene, path):
    """Render the sequencer's output to ``path`` (a movie is named by its frame range: it is renamed)."""
    scene.render.filepath = str(path.with_suffix(""))
    movie = scene.render.image_settings.media_type == "VIDEO"
    bpy.ops.render.render(animation=movie, write_still=not movie)
    if not path.is_file():
        Path(scene.render.frame_path(frame=scene.frame_start)).replace(path)


def main():
    args = arguments()
    root = Path(args.frames)
    files = [root / entry["file"] for entry in json.loads((root / "frames.json").read_text())["frames_index"]]
    if any(path.parent != files[0].parent or not path.is_file() for path in files):
        raise FileNotFoundError("Every frame must exist in one folder: %s" % files[0].parent)
    names = [path.name for path in files]
    scene = bpy.context.scene
    render = scene.render
    render.resolution_x, render.resolution_y, render.resolution_percentage = *SIZE, 100
    render.fps, render.fps_base = args.fps, 1.0
    scene.view_settings.view_transform, scene.view_settings.look = "Standard", "None"
    scene.view_settings.exposure, scene.view_settings.gamma = 0.0, 1.0
    editor = scene.sequence_editor_create()
    strip = editor.strips.new_image("Demo", str(files[0]), 1, 1, fit_method="FIT")
    for name in [names[0]] * (args.hold - 1) + [n for n in names[1:] for _ in range(args.hold)]:
        strip.elements.append(name)
    scene.frame_start, scene.frame_end = 1, len(strip.elements)
    settings = render.image_settings
    # The poster: the last frame, the finished scene.
    settings.media_type, settings.file_format, settings.color_mode = "IMAGE", "PNG", "RGB"
    scene.frame_set(scene.frame_end)
    render_to(scene, Path(args.poster))
    settings.media_type, settings.file_format = "VIDEO", "FFMPEG"
    ffmpeg = render.ffmpeg
    ffmpeg.format, ffmpeg.codec = "MPEG4", "H264"
    ffmpeg.constant_rate_factor, ffmpeg.ffmpeg_preset = "HIGH", "GOOD"
    ffmpeg.gopsize, ffmpeg.audio_codec = args.fps, "NONE"
    render.use_file_extension = True
    render_to(scene, Path(args.video))
    print("SCENARIO_DEMO_ENCODED", json.dumps({"video": args.video, "poster": args.poster,
                                              "video_frames": scene.frame_end}), flush=True)
    return 0


sys.exit(main())
