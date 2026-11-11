# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender fixes Angelo takes from upstream ``main`` ahead of its base release, in the real binary.

Each check states what a person or a script must see, worked out from the data itself and not from a stored
result of one build, so it holds on every later base as well:

* A Cycles render in a kept session (Persistent Data) after the denoiser's guiding passes change gives the picture
  the same render gives in a new session (upstream #166217): OpenImageDenoise on the CPU kept the earlier
  render's albedo and normal images bound unless the prefilter was Accurate, which is the control.

Custom normals under Apply Scale (upstream #121831, taken too) are in test_blender_custom_normals_transform.py,
with Angelo's own paths. Angelo 1.1 adds the cases of the fixes it takes on Blender 5.3.

Background, no provider, account or network. Writes results.json under ANGELO_QA_DIR.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-upstream-fix-backports-qa"))
OUT.mkdir(parents=True, exist_ok=True)
WORK = Path(tempfile.mkdtemp(prefix="backports-", dir=OUT))
state = {"success": False, "checks": {}, "details": {}}


def check(name, passed, detail=None):
    state["checks"][name] = bool(passed)
    if detail is not None:
        state["details"][name] = detail


MAX_PICTURE_DIFFERENCE = 1e-3   # Linear float channels; the same render twice differs by nothing.


def cycles_render(path, passes, size, persistent):
    """Render the scene on the CPU with OpenImageDenoise to ``path`` (float EXR); the error reported, or None."""
    scene = bpy.context.scene
    scene.render.use_persistent_data = persistent
    scene.cycles.denoising_input_passes = passes
    scene.render.resolution_x, scene.render.resolution_y = size
    scene.render.filepath = str(path)
    try:
        bpy.ops.render.render(write_still=True)
    except RuntimeError as error:
        return str(error).strip()
    return None if Path(path).is_file() else "no picture was written"


def picture(path):
    import numpy
    image = bpy.data.images.load(str(path))
    try:
        values = numpy.empty(image.size[0] * image.size[1] * image.channels, dtype=numpy.float32)
        image.pixels.foreach_get(values)
        return values
    finally:
        bpy.data.images.remove(image)


def kept_session_difference(prefilter, first_size, size):
    """Largest channel difference between a render that follows another in a kept session, after the guiding
    passes changed, and the same render in a new session; an error text when either render fails."""
    scene = bpy.context.scene
    scene.cycles.denoising_prefilter = prefilter
    folder = WORK / ("denoise-%s-%dx%d-after-%dx%d" % ((prefilter.lower(),) + size + first_size))
    error = cycles_render(folder / "new-session.exr", "RGB", size, persistent=False)
    error = error or cycles_render(folder / "first.exr", "RGB_ALBEDO_NORMAL", first_size, persistent=True)
    error = error or cycles_render(folder / "kept-session.exr", "RGB", size, persistent=True)
    scene.render.use_persistent_data = False
    if error:
        return error
    expected, found = picture(folder / "new-session.exr"), picture(folder / "kept-session.exr")
    if expected.shape != found.shape:
        return "pictures of %d and %d values" % (expected.size, found.size)
    return float(abs(expected - found).max())


def check_denoiser_change_in_a_kept_session():
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device, scene.cycles.samples, scene.cycles.use_adaptive_sampling = "CPU", 4, False
    scene.cycles.use_denoising, scene.cycles.denoiser, scene.cycles.denoising_use_gpu = True, "OPENIMAGEDENOISE", False
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.render.image_settings.color_depth = "32"
    cases = (("ACCURATE", (64, 48), (64, 48), "the control holds: with the Accurate prefilter"),
             ("FAST", (64, 48), (64, 48), "with the Fast prefilter"),
             ("NONE", (64, 48), (64, 48), "without a prefilter"),
             ("FAST", (48, 64), (96, 80), "with the Fast prefilter and a new picture size"))
    for prefilter, first_size, size, label in cases:
        difference = kept_session_difference(prefilter, first_size, size)
        check("%s, a kept session renders what a new one does after the guiding passes change" % label,
              isinstance(difference, float) and difference <= MAX_PICTURE_DIFFERENCE,
              {"prefilter": prefilter, "first_size": first_size, "size": size, "difference": difference})


def write_results():
    (OUT / "results.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


try:
    # The results are written before the render case: the defect it pins could end the process.
    for case in (check_denoiser_change_in_a_kept_session,):
        write_results()
        try:
            case()
        except Exception:
            check("%s ran to its end" % case.__name__, False, traceback.format_exc())
    state["success"] = bool(state["checks"]) and all(state["checks"].values())
except Exception:
    state["error"] = traceback.format_exc()
write_results()
print("ANGELO_UPSTREAM_FIX_BACKPORTS", json.dumps(state), flush=True)
