# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The World's environment picture for the web: made off the main thread, once per World state, kept briefly.

``ensure(scene, world)`` starts making the picture ``web_world.plan`` asked for, unless it is made or being made:

* ``IMAGE``: the Environment Texture's own image, read with OpenImageIO on a worker thread (file or packed bytes),
  converted from its colour space to scene-linear with Blender's OpenColorIO configuration, reduced with a box
  filter (every pixel keeps the light it covered, so a small sun keeps its energy) to ``WIDTH`` × ``WIDTH / 2`` at
  most, and turned by the Mapping's angle.
* ``BAKE``: the World written (``bpy.data.libraries.write``, which reads Blender data and changes none) to a private
  folder and rendered by a background Blender (``web_world_bake``) at ``WIDTH`` × ``WIDTH / 2``; a worker thread
  waits for it and reads its EXR.

Either way the result is a Radiance ``.hdr`` (RGBE, run-length scanlines of literals only, which every reader
accepts) whose first row is the top of the sky, in Blender's equirectangular convention, which three.js's own
equirectangular mapping shares once the glTF is Y up: a developer's page loads it with ``HDRLoader`` unchanged.
The worker threads never touch ``bpy``, and nothing on the main thread waits for them: an explicit export polls its
job's ``done`` on a timer (``web_export_wait``), and the live preview asks ``ready_since`` on its timer and exports
again when a picture lands.
"""

import collections
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

WIDTH = 1024            # Pixels around the horizon: enough for lighting and a soft background, ~2 MB as RGBE.
SAMPLES = 16            # A bake's samples per pixel: camera rays only, so these only smooth edges.
BAKE_SECONDS = 120      # A bake that takes longer is stopped; the export then carries no environment.
KEEP = 3                # Pictures kept: the World now and a couple it was a moment ago (an undo, a toggle).

_jobs = collections.OrderedDict()   # key -> _Job, newest last.
_lock = threading.Lock()
_landed = [0]           # Pictures finished this session: the live preview exports again when it moves.


class PictureError(RuntimeError):
    """Why a picture couldn't be made, in words a finding can show (never a local path)."""


def _reason(error):
    """A finding's reason: this module's own words, else only the kind of error (an OS message can name a path)."""
    return str(error)[:200] if isinstance(error, PictureError) else type(error).__name__


class _Job:
    def __init__(self, key):
        self.key = key
        self.status = "pending"   # pending, ready or failed
        self.data = None          # The Radiance file's bytes once ready.
        self.width = self.height = 0
        self.error = ""
        self.ms = 0.0
        self.process = None
        self.done = threading.Event()

    def finish(self, data=None, width=0, height=0, error=""):
        with _lock:
            self.data, self.width, self.height, self.error = data, width, height, error
            self.status = "ready" if data is not None else "failed"
            _landed[0] += 1
        self.done.set()


def rgbe(pixels):
    """Radiance RGBE bytes (H, W, 4) of linear RGB ``pixels`` (H, W, 3); negatives and NaN count as black."""
    rgb = np.nan_to_num(np.maximum(np.asarray(pixels, dtype=np.float64)[..., :3], 0.0), posinf=65504.0)
    peak = rgb.max(axis=2)
    mantissa, exponent = np.frexp(peak)
    lit = peak > 1e-32
    scale = np.where(lit, mantissa * 256.0 / np.where(lit, peak, 1.0), 0.0)
    out = np.empty(rgb.shape[:2] + (4,), dtype=np.uint8)
    out[..., :3] = np.clip(np.floor(rgb * scale[..., None]), 0, 255).astype(np.uint8)
    out[..., 3] = np.where(lit, np.clip(exponent + 128, 0, 255), 0).astype(np.uint8)
    return out


def radiance(pixels):
    """A Radiance .hdr file of linear RGB ``pixels`` (H, W, 3), the first row at the top."""
    data = rgbe(pixels)
    height, width = data.shape[:2]
    header = b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y %d +X %d\n" % (height, width)
    if not 8 <= width <= 0x7FFF:   # Run-length scanlines need this width; flat pixels otherwise.
        return header + data.tobytes()
    planes = data.transpose(0, 2, 1)   # (H, component, W): each component's run of the scanline.
    full, rest = divmod(width, 128)
    parts = []
    if full:
        body = planes[..., :full * 128].reshape(height, 4, full, 128)
        parts.append(np.concatenate([np.full((height, 4, full, 1), 128, np.uint8), body], axis=3)
                     .reshape(height, 4, -1))
    if rest:
        parts.append(np.concatenate([np.full((height, 4, 1), rest, np.uint8), planes[..., full * 128:]], axis=2))
    components = np.concatenate(parts, axis=2).reshape(height, -1)
    start = np.tile(np.array([2, 2, width >> 8, width & 0xFF], np.uint8), (height, 1))
    return header + np.concatenate([start, components], axis=1).tobytes()


def _reduce(pixels, width):
    """``pixels`` (H, W, 3) box-filtered to ``width`` × ``width / 2``, with OpenImageIO."""
    import OpenImageIO as oiio
    height = width // 2
    if pixels.shape[1] == width and pixels.shape[0] == height:
        return pixels
    source = oiio.ImageBuf(oiio.ImageSpec(pixels.shape[1], pixels.shape[0], 3, oiio.FLOAT))
    source.set_pixels(oiio.ROI(), np.ascontiguousarray(pixels, dtype=np.float32))
    reduced = oiio.ImageBufAlgo.resize(source, filtername="box", roi=oiio.ROI(0, width, 0, height, 0, 1, 0, 3))
    return reduced.get_pixels(oiio.FLOAT)[..., :3]


def _to_scene_linear(pixels, config_path, colour_space):
    """``pixels`` (H, W, 3) from Blender's ``colour_space`` to scene-linear; data colour spaces are left alone."""
    import PyOpenColorIO as ocio
    config = ocio.Config.CreateFromFile(config_path)
    space = config.getColorSpace(colour_space)
    if space is None or space.isData():
        return pixels
    processor = config.getProcessor(colour_space, config.getRoleColorSpace("scene_linear"))
    if processor.isNoOp():
        return pixels
    pixels = np.ascontiguousarray(pixels, dtype=np.float32)
    processor.getDefaultCPUProcessor().applyRGB(pixels)
    return pixels


def _read_rgb(path):
    """The first three channels of an image file as float (H, W, 3), first row at the top."""
    import OpenImageIO as oiio
    image = oiio.ImageBuf(path)
    pixels = image.get_pixels(oiio.FLOAT)
    if image.has_error or pixels is None:
        raise PictureError("OpenImageIO could not read the image")
    pixels = pixels.reshape(pixels.shape[0], pixels.shape[1], -1)
    if pixels.shape[2] < 3:
        pixels = np.repeat(pixels[..., :1], 3, axis=2)
    return pixels[..., :3]


def _from_image(job, source, config_path, colour_space, turn, scratch):
    started = time.perf_counter()
    try:
        if isinstance(source, bytes):   # A packed image: OpenImageIO reads files.
            path = os.path.join(scratch, "packed")
            with open(path, "wb") as handle:
                handle.write(source)
            source = path
        pixels = _to_scene_linear(_read_rgb(source), config_path, colour_space)
        width = max(8, min(WIDTH, pixels.shape[1]) // 2 * 2)
        pixels = _reduce(pixels, width)
        if turn:
            pixels = np.roll(pixels, int(round(turn * width)) % width, axis=1)
        job.ms = (time.perf_counter() - started) * 1000.0
        job.finish(radiance(pixels), width, width // 2)
    except Exception as error:  # A picture that can't be made: the export carries none and says so.
        job.finish(error=_reason(error))
    finally:
        _remove(scratch)


def _wait_for_bake(job, command, out, scratch):
    started = time.perf_counter()
    log_path = os.path.join(scratch, "bake.log")
    try:
        with open(log_path, "wb") as log:
            options = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT, "cwd": scratch}
            if sys.platform == "win32":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
            else:
                options["start_new_session"] = True
            job.process = subprocess.Popen(command, **options)
            try:
                code = job.process.wait(timeout=BAKE_SECONDS)
            except subprocess.TimeoutExpired:
                job.process.kill()
                job.process.wait()
                raise PictureError("the bake took longer than %d s" % BAKE_SECONDS)
        if code != 0 or not os.path.isfile(out):
            raise PictureError("the background Blender ended without a picture (exit %s)" % code)
        pixels = _read_rgb(out)
        job.ms = (time.perf_counter() - started) * 1000.0
        job.finish(radiance(pixels), pixels.shape[1], pixels.shape[0])
    except Exception as error:  # The export carries no environment and a finding says why.
        job.finish(error=_reason(error))
    finally:
        job.process = None
        _remove(scratch)


def _remove(folder):
    import shutil
    shutil.rmtree(folder, ignore_errors=True)


def _start_image(job, world, config_path):
    import bpy
    from . import private_roots
    image = bpy.data.images[world["image"]]
    source = bytes(image.packed_file.data) if image.packed_file else \
        bpy.path.abspath(image.filepath, library=image.library)
    scratch = private_roots.mkdtemp("angelo-web-world-")
    return threading.Thread(target=_from_image, name="Angelo web environment", daemon=True,
                            args=(job, source, config_path, image.colorspace_settings.name, world["turn"], scratch)), \
        scratch


def _start_bake(job, scene):
    import bpy
    from . import private_roots
    scratch = private_roots.mkdtemp("angelo-web-world-")
    library, out = os.path.join(scratch, "world.blend"), os.path.join(scratch, "world.exr")
    try:
        bpy.data.libraries.write(library, {scene.world}, path_remap="ABSOLUTE", fake_user=True)
    except BaseException:
        _remove(scratch)
        raise
    script = Path(__file__).with_name("web_world_bake.py")
    command = [bpy.app.binary_path, "--background", "--factory-startup", "--disable-autoexec", "--offline-mode",
               "--python-exit-code", "1", "--python", str(script), "--", library, scene.world.name,
               scene.render.engine, out, str(WIDTH), str(WIDTH // 2), str(SAMPLES), str(scene.frame_current)]
    return threading.Thread(target=_wait_for_bake, name="Angelo web world bake", daemon=True,
                            args=(job, command, out, scratch)), scratch


def ensure(scene, world, config_path):
    """The job making ``world``'s picture (``web_world.plan`` of ``scene``), started now if needed. Main thread."""
    key = world["key"]
    with _lock:
        job = _jobs.get(key)
        if job is not None:
            _jobs.move_to_end(key)
            return job
        job = _jobs[key] = _Job(key)
        while len(_jobs) > KEEP:
            _drop(_jobs.popitem(last=False)[1])
    try:
        worker, scratch = _start_image(job, world, config_path) if world["kind"] == "IMAGE" else _start_bake(job, scene)
    except Exception as error:  # Not even started (an unwritable folder, an image gone): failed, with the reason.
        job.finish(error=_reason(error))
        return job
    try:
        worker.start()
    except Exception as error:
        # No thread to make it on (``thread_start``): failed now, and forgotten so that the next export of this World
        # asks again (a kept failure would answer it).
        _remove(scratch)
        with _lock:
            if _jobs.get(key) is job:
                del _jobs[key]
        job.finish(error=_reason(error))
    return job


def ready_since(count):
    """Pictures finished since ``count`` was read (``landed``): the live preview exports again then."""
    return _landed[0] != count


def landed():
    return _landed[0]


def _drop(job):
    process = job.process
    if process is not None and process.poll() is None:
        process.kill()


def stop():
    """Stop running bakes and forget every picture (the preview stopped, the add-on unloads)."""
    with _lock:
        jobs = list(_jobs.values())
        _jobs.clear()
    for job in jobs:
        _drop(job)
