# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Render a region over the last render, checked pixel by pixel (doc/angelo/render_region.rst, "Validation").

Background, local only. For each unrelated scene (render_region_scenes) in Cycles and EEVEE, through the native
operator alone (``render.render(region_over_last=True)``, no add-on):

1. **full**: a full render (multilayer OpenEXR of every layer and pass, saved from the Render Result).
2. **same**: the region rendered again with nothing changed: every pixel of every pass outside the region is
   bit-identical to *full*; inside, the seam is measured (the change along the region's edge against the rest).
3. **changed**: a material inside the frame changes and the region renders again: outside still bit-identical to
   *same*; inside, the Combined pass changed.
4. **revert / reapply**: Revert Region gives *same* back bit for bit, everywhere; applying again gives *changed*.
5. **slots**: in an empty render slot, a region renders over a copy of the last render, and the last render's own
   slot keeps *changed* bit for bit.
6. **after a look**: an agent's RENDERED look renders into its own slot at its own size and puts the person's slot
   back (the add-on's own ``ResultSlot``); the render a region goes over is still the person's, so its size
   (``angelo_last_render_size``, which the RENDER_REGION job checks) is unchanged, the job's size check admits it, and
   a region renders over the person's slot with the outside bit-identical.
7. **normal render**: the scene's render region settings are what they were, a plain render is a plain render and the
   state reads NONE.

Writes render-region-pixels.json under ANGELO_QA_DIR (``--engines`` and ``--scenes`` after ``--`` narrow the run;
``--denoise on|off`` and ``--persistent on`` vary Cycles' denoising and the scene's own Persistent Data). The report is
rewritten, and ``ANGELO_RR_STEP <case> <step>`` printed, before every step, so a crash names the step it was in.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy
import numpy
import OpenImageIO as oiio

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_region_scenes as scenes  # noqa: E402
from angelo_agent import render_region as region_ui  # noqa: E402
from angelo_agent.view_render_restore import ResultSlot  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-render-region-pixels"))
OUT.mkdir(parents=True, exist_ok=True)
REGION = (0.3, 0.25, 0.7, 0.75)    # Blender's fractions: left, bottom, right, top.
ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []


def option(name, default):
    return ARGS[ARGS.index(name) + 1].split(",") if name in ARGS else default


REPORT = {"phase": "start", "success": False, "cases": {}}


def write_report():
    (OUT / "render-region-pixels.json").write_text(json.dumps(REPORT, indent=2))


def step(case, name):
    """Records the step about to run, on disk and on stdout, before it runs."""
    REPORT["phase"] = "%s %s" % (case, name)
    print("ANGELO_RR_STEP", REPORT["phase"], flush=True)
    write_report()


def pixel_rect(region, width, height):
    """The region in pixels, as the native operator rounds it (outward)."""
    import math
    left, bottom, right, top = region
    return (max(0, math.floor(left * width)), min(width, math.ceil(right * width)),
            max(0, math.floor(bottom * height)), min(height, math.ceil(top * height)))


def save(name):
    """Every layer and pass of the Render Result's active slot, as float32 channels by name."""
    path = OUT / (name + ".exr")
    bpy.data.images["Render Result"].save_render(filepath=str(path))
    return read_exr(path)


def read_exr(path):
    """Every channel of every part of a (multi-part) OpenEXR, by its full name."""
    source = oiio.ImageInput.open(str(path))
    channels, part = {}, 0
    while source.seek_subimage(part, 0):
        spec = source.spec()
        pixels = source.read_image(part, 0, 0, spec.nchannels, "float")
        channels.update({name: pixels[:, :, i].copy() for i, name in enumerate(spec.channelnames)})
        part += 1
    source.close()
    return channels


def inside_mask(width, height, rect):
    """True inside the region, in the file's rows (top first; Blender's rows start at the bottom)."""
    xmin, xmax, ymin, ymax = rect
    mask = numpy.zeros((height, width), bool)
    mask[height - ymax:height - ymin, xmin:xmax] = True
    return mask


def compare(a, b, mask=None):
    """Channels that differ (outside ``mask`` when given): names and counts, plus channels missing from either."""
    differ = {}
    for name in sorted(set(a) | set(b)):
        if name not in a or name not in b:
            differ[name] = "missing"
            continue
        unequal = a[name].view(numpy.uint32) != b[name].view(numpy.uint32)   # Bit for bit, NaN included.
        if mask is not None:
            unequal &= ~mask
        if unequal.any():
            differ[name] = int(unequal.sum())
    return differ


def seam(full, same, mask):
    """Mean absolute change of the Combined pass on the region's 2-pixel inner edge, and in the rest of it."""
    channels = [n for n in full if n.endswith("Combined.R") or n.endswith("Combined.G") or n.endswith("Combined.B")]
    delta = sum(numpy.abs(full[n] - same[n]) for n in channels[:3]) / max(1, len(channels[:3]))
    inner = numpy.zeros_like(mask)
    inner[2:-2, 2:-2] = mask[2:-2, 2:-2] & numpy.roll(mask, 2, 0)[2:-2, 2:-2] & numpy.roll(mask, -2, 0)[2:-2, 2:-2] \
        & numpy.roll(mask, 2, 1)[2:-2, 2:-2] & numpy.roll(mask, -2, 1)[2:-2, 2:-2]
    edge = mask & ~inner
    return {"edge": float(delta[edge].mean()) if edge.any() else 0.0,
            "inside": float(delta[inner].mean()) if inner.any() else 0.0}


def region_settings(scene):
    render = scene.render
    return (render.use_border, render.use_crop_to_border, render.border_min_x, render.border_max_x,
            render.border_min_y, render.border_max_y)


def region_render(region=REGION, padding=None):
    extra = {} if padding is None else {"region_padding": padding}
    result = bpy.ops.render.render(region_over_last=True, region=region, **extra)
    return 'FINISHED' in result


def run_case(scene_name, engine):
    builder = scenes.SCENES[scene_name]
    scene, changed_material = builder()
    case = "%s/%s" % (scene_name, engine)
    denoise = option("--denoise", ["on" if scene_name != "city" else "off"])[0] == "on"
    scenes.configure(scene, engine, denoise=denoise)
    scene.render.use_persistent_data = option("--persistent", ["off"])[0] == "on"
    image = bpy.data.images.get("Render Result")
    if image is not None:
        image.render_slots.active_index = 0
    border_before = region_settings(scene)
    width, height = scene.render.resolution_x, scene.render.resolution_y
    mask = inside_mask(width, height, pixel_rect(REGION, width, height))
    checks, facts = {}, {}

    step(case, "full")
    bpy.ops.render.render()
    full = save("%s-%s-full" % (scene_name, engine))
    checks["full_render_has_passes"] = len(full) > 4

    step(case, "same")
    started = time.monotonic()
    checks["same_rendered"] = region_render()
    facts["region_seconds"] = round(time.monotonic() - started, 3)
    checks["same_state_applied"] = scene.render.angelo_region_render == "APPLIED"
    same = save("%s-%s-same" % (scene_name, engine))
    facts["same_outside_differs"] = compare(full, same, mask)
    checks["same_outside_bit_identical"] = not facts["same_outside_differs"]
    facts["seam"] = seam(full, same, mask)

    step(case, "changed")
    bpy.data.materials[changed_material].node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = \
        (0.95, 0.1, 0.1, 1)
    checks["changed_rendered"] = region_render()
    changed = save("%s-%s-changed" % (scene_name, engine))
    facts["changed_outside_differs"] = compare(same, changed, mask)
    checks["changed_outside_bit_identical"] = not facts["changed_outside_differs"]
    combined = [n for n in changed if n.endswith("Combined.R")]
    checks["changed_inside_changed"] = any((same[n] != changed[n])[mask].any() for n in combined)

    step(case, "revert")
    bpy.ops.render.region_revert()
    checks["revert_state"] = scene.render.angelo_region_render == "REVERTED"
    facts["revert_differs"] = compare(same, save("%s-%s-reverted" % (scene_name, engine)))
    checks["revert_bit_identical_everywhere"] = not facts["revert_differs"]
    bpy.ops.render.region_revert()
    facts["reapply_differs"] = compare(changed, save("%s-%s-reapplied" % (scene_name, engine)))
    checks["reapply_bit_identical_everywhere"] = not facts["reapply_differs"]

    step(case, "slot")
    image = bpy.data.images["Render Result"]
    image.render_slots.new()   # An empty slot of this case's own (an earlier case's slot holds its scene).
    empty = len(image.render_slots) - 1
    image.render_slots.active_index = empty
    checks["slot_rendered"] = region_render(region=(0.1, 0.1, 0.4, 0.5))
    slot_one = save("%s-%s-slot1" % (scene_name, engine))
    slot_mask = inside_mask(width, height, pixel_rect((0.1, 0.1, 0.4, 0.5), width, height))
    facts["slot_outside_differs"] = compare(changed, slot_one, slot_mask)
    checks["slot_over_last_render"] = not facts["slot_outside_differs"]
    image.render_slots.active_index = 0
    facts["slot_zero_differs"] = compare(changed, save("%s-%s-slot0" % (scene_name, engine)))
    checks["last_slot_kept"] = not facts["slot_zero_differs"]

    step(case, "look")
    size = tuple(scene.render.angelo_last_render_size)
    checks["size_is_frame"] = size == region_ui.frame_size(scene)
    look, percentage = ResultSlot(), scene.render.resolution_percentage
    look.enter()
    try:
        scene.render.resolution_percentage = percentage // 2
        bpy.ops.render.render()
    finally:
        scene.render.resolution_percentage = percentage
        look.leave()
    checks["look_put_slot_back"] = image.render_slots.active_index == 0
    facts["size_after_look"] = list(scene.render.angelo_last_render_size)
    checks["size_unchanged_by_look"] = tuple(facts["size_after_look"]) == size
    checks["job_admits_after_look"] = region_ui.last_render_size(scene) == region_ui.frame_size(scene)
    checks["after_look_rendered"] = region_render(region=(0.55, 0.2, 0.8, 0.45))
    look_mask = inside_mask(width, height, pixel_rect((0.55, 0.2, 0.8, 0.45), width, height))
    facts["after_look_outside_differs"] = compare(changed, save("%s-%s-after-look" % (scene_name, engine)), look_mask)
    checks["after_look_outside_bit_identical"] = not facts["after_look_outside_differs"]

    step(case, "normal")
    checks["scene_region_settings_restored"] = border_before == region_settings(scene)
    bpy.ops.render.render()
    checks["normal_render_state_none"] = scene.render.angelo_region_render == "NONE"
    return checks, facts


def main():
    report = REPORT
    try:
        for engine in option("--engines", ["CYCLES", "BLENDER_EEVEE"]):
            for scene_name in option("--scenes", list(scenes.SCENES)):
                checks, facts = run_case(scene_name, engine)
                report["cases"]["%s/%s" % (scene_name, engine)] = {"checks": checks, "facts": facts,
                                                                    "passed": all(checks.values())}
                report["phase"] = "%s/%s" % (scene_name, engine)
        report["success"] = bool(report["cases"]) and all(case["passed"] for case in report["cases"].values())
        report["phase"] = "complete"
    except Exception:
        report["error"] = traceback.format_exc()
    write_report()
    print("ANGELO_RENDER_REGION_PIXELS", "PASS" if report["success"] else "FAIL")
    sys.exit(0 if report["success"] else 1)


main()
