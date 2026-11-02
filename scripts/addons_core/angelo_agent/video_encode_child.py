# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run inside a background child Angelo: encode finished image frames into one H.264 MP4 (``job_render_video``).

Never imported by the add-on. The parent starts ``Angelo --background --factory-startup ... --python <this file>
-- <request JSON>``; the request names the frames by their pattern, their frame rate, the video's size and the MP4's
path (ending in .mp4, which Blender then writes exactly, with no frame range added).
The frames are display-referred PNG or JPEG (the render already applied the scene's view transform), so the encode
scene shows them as they are: the Standard view, sRGB in and out. Blender's own Sequencer and its bundled FFmpeg do
the encoding, so the app needs no other program. The last line printed is ``ANGELO-VIDEO <json>`` with the file it
wrote, or the child ends with a non-zero exit status.
"""

import json
import os
import sys
from pathlib import Path

import bpy

MARK = "ANGELO-VIDEO "


def _escape(path):
    """A path as Blender's output path templates read it literally: braces doubled."""
    return str(path).replace("{", "{{").replace("}", "}}")


def parent_alive(pid):
    """Whether the Angelo that started this child still runs: a crashed parent never leaves an encoder behind."""
    if not pid:
        return True
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    if os.getppid() != int(pid):   # Re-parented: the parent ended.
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def frame_files(request):
    """The frame files in order, named as the render job named them (job_render_outputs.file_name)."""
    start, end, step = request["frames"]
    folder = Path(request["directory"])
    return [folder / ("%s%.*d%s%s" % (request["prefix"], request["digits"], frame, request["suffix"],
                                       request["extension"])) for frame in range(start, end + 1, step)]


def encode(request):
    frames = frame_files(request)
    width, height = request["size"]
    scene = bpy.data.scenes.new("Angelo video")
    render = scene.render
    # The size first: a strip is fitted to the scene's size when it is made.
    render.resolution_x, render.resolution_y, render.resolution_percentage = width, height, 100
    editor = scene.sequence_editor_create()
    strip = editor.strips.new_image("frames", str(frames[0]), 1, 1, fit_method="STRETCH")
    for frame in frames[1:]:
        strip.elements.append(frame.name)
    strip.colorspace_settings.name = "sRGB"
    scene.frame_start, scene.frame_end = 1, len(frames)
    render.fps, render.fps_base = int(request["fps"]), float(request["fps_base"])
    render.use_sequencer, render.use_compositing = True, False
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure, scene.view_settings.gamma = 0.0, 1.0
    scene.sequencer_colorspace_settings.name = "sRGB"
    image = render.image_settings
    image.media_type = "VIDEO"
    image.file_format = "FFMPEG"
    ffmpeg = render.ffmpeg
    ffmpeg.format, ffmpeg.codec, ffmpeg.audio_codec = "MPEG4", "H264", "NONE"
    ffmpeg.constant_rate_factor, ffmpeg.ffmpeg_preset = "HIGH", "GOOD"
    ffmpeg.gopsize = max(1, round(render.fps / render.fps_base))
    render.use_file_extension = True
    render.filepath = _escape(request["dest"])
    parent = request.get("parent")

    def check_parent(*_args):
        if not parent_alive(parent):
            os._exit(3)   # The app is gone: its job's staging folder is the next start's to tidy.
    bpy.app.handlers.frame_change_pre.append(check_parent)
    bpy.ops.render.render(animation=True, scene=scene.name)
    return render.frame_path(frame=scene.frame_start)


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    request = json.loads(argv[0])
    written = encode(request)
    print(MARK + json.dumps({"file": written}), flush=True)


main()
