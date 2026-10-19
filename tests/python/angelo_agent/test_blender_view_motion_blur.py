# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""MATERIAL and LIT views of a scene that renders with motion blur draw no blur (view_blur), in a GUI.

Run by ``run_native_suites.py --gui --include test_blender_view_motion_blur`` (offscreen drawing needs a window).
The fixture keys a moving cube, turns the scene's motion blur on and jumps the frame, as the recorded bridge
one-shots did (friction scan G08: their second and third LIT views were smeared, the first clean). For LIT and
MATERIAL it compares each view of one three-view call with the same view drawn alone: alone, a view has no earlier
draw to blur toward, so equal pictures mean no view was blurred toward another. It also checks the result says the
blur is left out, that a Workbench look is unaffected, and that the person's render settings stay as they were.
No provider, account or network. Inspect results.json; a zero exit status alone is not proof.
"""

import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import traceback
import zlib

import bpy
import numpy as np

assert "--factory-startup" in sys.argv, "Use an isolated factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-view-blur-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import scene_queries, view_cache  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402

report = {"checks": {}, "measurements": {}, "binary": bpy.app.binary_path}
ctx = {}
# Mean absolute difference, in 8-bit levels, under which two drawings of one view are the same picture. Drawn
# alone and in a call they matched exactly (0.0) with the fix; on the earlier code (one offscreen per size) the
# same fixture gave LIT 0.0 / 0.9 / 14.7 and MATERIAL 0.0 / 0.1 / 17.3 for FRONT / TOP / ISO.
SAME_PICTURE = 0.25


def check(name, condition):
    report["checks"][name] = bool(condition)


def decode(path):
    """Decode the encoder's own filter-0 RGB(A) PNGs (no Blender Image needed)."""
    data, offset, idat, size, channels = Path(path).read_bytes(), 8, b"", None, 3
    while offset < len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind, body = data[offset + 4:offset + 8], data[offset + 8:offset + 8 + length]
        if kind == b"IHDR":
            size, channels = struct.unpack(">II", body[:8]), {2: 3, 6: 4}[body[9]]
        elif kind == b"IDAT":
            idat += body
        offset += 12 + length
    width, height = size
    raw = np.frombuffer(zlib.decompress(idat), dtype=np.uint8).reshape(height, width * channels + 1)
    assert not raw[:, 0].any(), "unexpected PNG filter"
    return raw[:, 1:].reshape(height, width, channels)[:, :, :3].astype(np.int16)


def view(args):
    result = ctx["executor"].execute("angelo_view", args)
    assert result.get("success"), result
    return result


def setup():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == "VIEW_3D")
    ctx.update(window=window, area=area)
    report["module"] = scene_queries.__file__
    check("checkout_modules_loaded", str(ROOT) in scene_queries.__file__)
    scene = window.scene
    scene.render.engine = "BLENDER_EEVEE"
    cube = scene.objects["Cube"]
    cube.location = (-4, 0, 0)
    cube.keyframe_insert("location", frame=1)
    cube.location = (4, 0, 0)
    cube.keyframe_insert("location", frame=40)
    scene.render.use_motion_blur = True
    scene.frame_set(20)  # Mid-motion, after a jump from frame 1 (the recorded scripts' frame_set).
    window.view_layer.update()
    ctx["settings"] = (scene.render.use_motion_blur, scene.render.motion_blur_shutter, scene.frame_current)
    ctx["refs"] = ReferenceStore(OUT, "0f6c3c87-6b4e-4a35-9b7f-6c4c8f6f0c12")
    ctx["executor"] = BlenderExecutor(window)
    ctx["executor"].observation_store = ctx["refs"]
    ctx["cube"] = scene_queries.object_handle(cube)


def compare(shading):
    views = ["FRONT", "TOP", "ISO"]
    view_cache.clear()
    together = view({"targets": [ctx["cube"]], "views": views, "shading": shading, "resolution": 256})
    alone = []
    for item in views:
        view_cache.clear()  # A cached picture would compare the call's own drawing with itself.
        alone.append(view({"targets": [ctx["cube"]], "views": [item], "shading": shading, "resolution": 256}))
    check("%s_alone_were_drawn" % shading.lower(), not any(v["views"][0]["cache_hit"] for v in alone))
    differences = []
    for index, item in enumerate(views):
        first = decode(together["views"][index]["path"])
        second = decode(alone[index]["views"][0]["path"])
        differences.append(round(float(np.abs(first - second).mean()), 3))
    report["measurements"][shading] = {"mean_level_difference_per_view": dict(zip(views, differences))}
    key = "lit_preview" if shading == "LIT" else "material_preview" if shading == "MATERIAL" else None
    if key is None:
        check("workbench_views_match_alone", max(differences) <= SAME_PICTURE)
        check("workbench_result_says_nothing_of_blur", "NOT_DRAWN" not in json.dumps(together))
        return
    check("%s_views_match_their_drawing_alone" % shading.lower(), max(differences) <= SAME_PICTURE)
    check("%s_result_says_blur_is_left_out" % shading.lower(), together[key].get("motion_blur") == "NOT_DRAWN")


def steps():
    yield setup
    for shading in ("LIT", "MATERIAL", "SOLID"):
        yield lambda shading=shading: compare(shading)

    def settings_kept():
        scene = ctx["window"].scene
        check("render_settings_and_frame_unchanged",
              (scene.render.use_motion_blur, scene.render.motion_blur_shutter, scene.frame_current) == ctx["settings"])
    yield settings_kept


STEPS = list(steps())


def run():
    index = ctx.setdefault("step", 0)
    try:
        with bpy.context.temp_override(window=ctx.get("window") or bpy.context.window_manager.windows[0]):
            STEPS[index]()
    except Exception:
        report.update(success=False, failed_step=index, error=traceback.format_exc())
        return finish()
    ctx["step"] += 1
    if ctx["step"] < len(STEPS):
        return 0.25  # Let the event loop run between captures, as in the application.
    report["success"] = all(report["checks"].values())
    return finish()


def finish():
    report.setdefault("success", False)
    (OUT / "results.json").write_text(json.dumps(report, indent=2, default=str))
    print("ANGELO_VIEW_MOTION_BLUR", json.dumps({"success": report["success"], "failed": sorted(
        name for name, passed in report["checks"].items() if not passed)}), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run, first_interval=1)
