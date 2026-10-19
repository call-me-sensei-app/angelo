# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual angelo_view pixels and artist-state safety in a factory-startup GUI.

Run from the repository root with the compiled binary (not --background:
offscreen drawing needs a window). ``run_native_suites.py --gui --include
test_blender_view_observation`` isolates it; by hand, with ``<qa>`` a new
disposable folder (Windows: set TEMP instead of TMPDIR):

  ANGELO_DISABLE_DEFAULT_CHAT=1 ANGELO_QA_DIR=<qa> \\
    TMPDIR=<qa>/tmp/ HOME=<qa>/home BLENDER_USER_RESOURCES=<qa>/user \\
    ANGELO_CONNECTIONS_DIR=<qa>/accounts ANGELO_STATE_DIR=<qa>/state \\
    /path/to/Angelo --factory-startup --disable-autoexec --no-window-focus \\
    --python tests/python/angelo_agent/test_blender_view_observation.py

Create the tmp and home directories first. A private TMPDIR and HOME keep
Blender's quit.blend and other temporary files out of the user's real
"Recover Last Session" location; the fixture also points Blender's temporary
directory preference at ANGELO_QA_DIR/recovery.

Captures run on successive UI-timer ticks so the event loop (notifiers,
redraws, depsgraph handlers) runs between them, as in the application. No
provider, account or paid request is used. Inspect results.json and the log;
a zero exit status alone is not proof.
"""

import json
import os
import statistics
import struct
import sys
import tempfile
import traceback
import zlib
from pathlib import Path

import bpy
import gpu
import numpy as np

assert "--factory-startup" in sys.argv, "Use an isolated factory-startup instance"
TEMP_VARIABLE = "TEMP" if sys.platform == "win32" else "TMPDIR"  # Blender's temp folder (BLI_temp_dir_base)
assert TEMP_VARIABLE in os.environ and not os.environ[TEMP_VARIABLE].startswith("/var/folders/"), \
    "Set a private %s so quit.blend cannot replace the user's session recovery file" % TEMP_VARIABLE
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-view-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import scene_queries, view_cache  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor, track_scene_changes  # noqa: E402
from angelo_agent.providers import tool_content  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402

report = {"checks": {}, "measurements": {}, "paid_requests": 0, "binary": bpy.app.binary_path}
ctx = {}
SHADING_KEYS = ("type", "light", "color_type", "single_color", "show_shadows", "show_cavity", "background_type",
                "background_color", "show_xray", "show_backface_culling", "show_object_outline",
                "show_specular_highlight", "use_dof", "wireframe_color_type", "show_xray_wireframe",
                "use_scene_lights", "use_scene_world", "render_pass", "use_compositor", "studiolight_rotate_z",
                "studiolight_intensity", "studiolight_background_alpha", "studiolight_background_blur",
                "studio_light", "xray_alpha")
REGION_KEYS = ("view_matrix", "view_perspective", "view_distance", "view_location", "view_rotation",
               "use_clip_planes", "is_perspective")


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def frozen(value):
    if isinstance(value, (str, bool, int, float)) or value is None:
        return value
    try:
        return tuple(frozen(item) for item in value)
    except TypeError:
        return repr(value)


def artist_state():
    window, area = ctx["window"], ctx["area"]
    space = area.spaces.active
    layer = window.view_layer
    return {"shading": {key: frozen(getattr(space.shading, key)) for key in SHADING_KEYS},
            "overlay": (space.overlay.show_overlays, space.overlay.wireframe_threshold, space.overlay.wireframe_opacity),
            "region": {key: frozen(getattr(space.region_3d, key)) for key in REGION_KEYS},
            "space": (space.lens, space.clip_start, space.clip_end, frozen(space.camera and space.camera.name)),
            "selection": sorted(o.name for o in layer.objects if o.select_get(view_layer=layer)),
            "active": layer.objects.active.name, "mode": bpy.context.mode, "frame": window.scene.frame_current,
            "scene": window.scene.name, "dirty": bpy.data.is_dirty, "gpu_viewport": tuple(gpu.state.viewport_get())}


def id_counts():
    counts = {}
    for name in dir(bpy.data):
        try:
            value = getattr(bpy.data, name)
        except Exception:
            continue
        if isinstance(value, bpy.types.bpy_prop_collection):
            counts[name] = len(value)
    return counts


def decode(path):
    """Decode the encoder's own filter-0 RGB PNGs (no Blender Image needed)."""
    data, offset, idat, size = Path(path).read_bytes(), 8, b"", None
    while offset < len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind, body = data[offset + 4:offset + 8], data[offset + 8:offset + 8 + length]
        if kind == b"IHDR":
            size = struct.unpack(">II", body[:8])
        elif kind == b"IDAT":
            idat += body
        offset += 12 + length
    width, height = size
    raw = np.frombuffer(zlib.decompress(idat), dtype=np.uint8).reshape(height, width * 3 + 1)
    assert not raw[:, 0].any(), "unexpected PNG filter"
    return raw[:, 1:].reshape(height, width, 3).astype(np.int16)


def foreground(pixels):
    """Pixels differing from the corner background by more than 8 levels."""
    return int((np.abs(pixels - pixels[0, 0]).max(axis=2) > 8).sum())


def view(args):
    result = ctx["executor"].execute("angelo_view", args)
    check("tool_reports_read_only_" + str(len(ctx["calls"])), result["read_only"] and not result["scene_changed"])
    ctx["calls"].append(result)
    for item in result["views"]:
        target = OUT / f"{len(ctx['calls']):02d}_{item['view'][:12]}_{result['shading'].lower()}.png"
        target.write_bytes(Path(item["path"]).read_bytes())
    return result


def setup():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == "VIEW_3D")
    ctx.update(window=window, area=area, region=next(r for r in area.regions if r.type == "WINDOW"), calls=[])
    report["module"] = scene_queries.__file__
    check("checkout_modules_loaded", str(ROOT) in scene_queries.__file__)
    bpy.app.handlers.depsgraph_update_post.append(track_scene_changes)  # As ui.register() does.
    (OUT / "recovery").mkdir(exist_ok=True)
    bpy.context.preferences.filepaths.temporary_directory = str(OUT / "recovery")
    scene = window.scene
    with bpy.context.temp_override(window=window, area=area, region=ctx["region"]):
        bpy.ops.mesh.primitive_uv_sphere_add(radius=1, location=(3, 0, 1))
    hero = bpy.context.active_object
    hero.name = "Hero"
    material = bpy.data.materials.new("Fresh green emission")  # Never drawn before its MATERIAL view.
    nodes = material.node_tree.nodes
    principled = nodes.get("Principled BSDF")
    principled.inputs["Base Color"].default_value = (0, 1, 0, 1)
    principled.inputs["Emission Color"].default_value = (0, 1, 0, 1)
    principled.inputs["Emission Strength"].default_value = 1.0
    hero.data.materials.append(material)
    cube = scene.objects["Cube"]
    for obj in window.view_layer.objects:
        obj.select_set(obj == cube, view_layer=window.view_layer)
    window.view_layer.objects.active = cube
    space = area.spaces.active
    # Distinctive artist display, including both stored studio-light fields.
    space.shading.type = "MATERIAL"
    space.shading.studio_light = "city.exr"
    space.shading.type = "SOLID"
    space.shading.light = "STUDIO"
    space.shading.studio_light = "rim.sl"
    space.shading.light = "MATCAP"
    space.shading.color_type = "RANDOM"
    space.shading.show_xray = True
    space.shading.show_backface_culling = True
    space.overlay.wireframe_threshold = .4
    space.region_3d.use_clip_planes = True
    window.view_layer.update()
    ctx["refs"] = ReferenceStore(OUT, "0f6c3c87-6b4e-4a35-9b7f-6c4c8f6f0c11")
    ctx["executor"] = BlenderExecutor(window)
    ctx["executor"].observation_store = ctx["refs"]
    ctx["hero"], ctx["cube"] = scene_queries.object_handle(hero), scene_queries.object_handle(cube)
    ctx["hero_object"] = hero


def solid_three_views():
    ctx["before"], ctx["ids"] = artist_state(), id_counts()
    result = view({"targets": [ctx["hero"], ctx["cube"]], "views": ["FRONT", "ISO", "TOP"], "shading": "SOLID"})
    ctx["solid"] = result
    for item in result["views"]:
        pixels = decode(item["path"])
        check("solid_pixels_nonempty_" + item["view"], pixels.shape == (512, 512, 3) and foreground(pixels) > 2000)
    check("solid_targets_have_exact_revisions", all(t["revision_exact"] for t in result["targets"]))
    check("solid_artist_state_unchanged", artist_state() == ctx["before"])
    check("solid_no_new_main_ids", id_counts() == ctx["ids"])
    check("solid_no_cache_hit_first", not any(v["cache_hit"] for v in result["views"]))
    report["solid_result"] = {k: result[k] for k in ("timing", "uncertainty", "cache", "targets")}


def repeat_hits_cache():
    result = view({"targets": [ctx["hero"], ctx["cube"]], "views": ["FRONT", "ISO", "TOP"], "shading": "SOLID"})
    check("repeat_after_idle_ticks_is_cache_hit", all(v["cache_hit"] for v in result["views"]))
    check("repeat_bytes_identical", all(Path(a["path"]).read_bytes() == Path(b["path"]).read_bytes()
                                        for a, b in zip(result["views"], ctx["solid"]["views"])))
    check("repeat_new_view_ids", not {v["view_id"] for v in result["views"]} & {v["view_id"] for v in ctx["solid"]["views"]})
    report["measurements"]["cache_hit_3_views_512"] = result["timing"]


def wireframe_and_material():
    result = view({"targets": [ctx["hero"]], "views": ["FRONT"], "shading": "WIREFRAME"})
    pixels = decode(result["views"][0]["path"])
    check("wireframe_pixels_nonempty", 500 < foreground(pixels) < 512 * 512 // 2)
    check("wireframe_artist_state_unchanged", artist_state() == ctx["before"])
    jobs_before = bpy.app.is_job_running("SHADER_COMPILATION")
    result = view({"targets": [ctx["hero"]], "views": ["FRONT"], "shading": "MATERIAL"})
    pixels = decode(result["views"][0]["path"])
    green = int(((pixels[:, :, 1] > pixels[:, :, 0] + 40) & (pixels[:, :, 1] > pixels[:, :, 2] + 40)).sum())
    report["material_first"] = {"green_pixels": green, "foreground": foreground(pixels), "timing": result["timing"],
                                "uncertainty": result["uncertainty"], "jobs_before": jobs_before,
                                "jobs_after": bpy.app.is_job_running("SHADER_COMPILATION")}
    check("material_fresh_shader_drawn_on_first_capture", green > 20000)
    check("material_not_uncertain", not result["uncertain"])
    check("material_artist_state_unchanged", artist_state() == ctx["before"])
    check("wireframe_material_no_new_main_ids", id_counts() == ctx["ids"])


def reuse_and_revision_miss():
    iso = next(v for v in ctx["solid"]["views"] if v["view"] == "ISO")
    reused = view({"targets": [ctx["hero"], ctx["cube"]], "views": [iso["view_id"]], "shading": "SOLID"})
    check("reuse_identical_camera", reused["views"][0]["camera"]["matrix_world"] == iso["camera"]["matrix_world"]
          and reused["views"][0]["camera"]["projection_matrix"] == iso["camera"]["projection_matrix"])
    ctx["hero_object"].location.x += 1.5  # A real edit: depsgraph update on the next tick, new revision.
    ctx["moved_from"] = iso


def after_edit():
    iso = ctx["moved_from"]
    old_revision = next(t for t in ctx["solid"]["targets"] if t["object"] == ctx["hero"])["revision"]
    result = view({"targets": [ctx["hero"], ctx["cube"]], "views": ["FRONT", iso["view_id"]], "shading": "SOLID"})
    new_revision = next(t for t in result["targets"] if t["object"] == ctx["hero"])["revision"]
    check("revision_changed_after_edit", new_revision != old_revision)
    check("edit_misses_cache", not any(v["cache_hit"] for v in result["views"]))
    after = result["views"][1]
    check("reuse_after_edit_identical_camera", after["camera"]["matrix_world"] == iso["camera"]["matrix_world"])
    check("reuse_after_edit_pixels_changed", (decode(after["path"]) != decode(iso["path"])).any())
    ctx["before"] = artist_state()  # The fixture's own edit dirtied the file.


def fit_and_scene_camera():
    result = view({"targets": [], "views": ["FIT", "SCENE_CAMERA"], "shading": "SOLID", "resolution": 384})
    fit, camera = result["views"]
    check("fit_pixels_nonempty", foreground(decode(fit["path"])) > 1000)
    render = ctx["window"].scene.render
    check("scene_camera_aspect", (camera["width"], camera["height"]) == (384, round(384 * render.resolution_y / render.resolution_x)))
    check("scene_camera_pixels_nonempty", foreground(decode(camera["path"])) > 500)
    check("scene_camera_perspective", camera["camera"]["projection"] == "PERSPECTIVE" and camera["camera"]["lens_mm"] > 0)
    check("whole_scene_coverage", result["coverage"] == "VISIBLE_SCENE")
    check("fit_scene_camera_artist_state_unchanged", artist_state() == ctx["before"])
    content = tool_content(ctx["delivery"], "angelo_view", result)
    check("provider_receives_two_png_images", [c["type"] for c in content] == ["inputText", "inputImage", "inputImage"])


def artist_in_material_preview():
    space = ctx["area"].spaces.active
    space.shading.type = "MATERIAL"
    before = artist_state()
    for shading in ("SOLID", "WIREFRAME", "MATERIAL"):
        result = view({"targets": [ctx["hero"]], "views": ["RIGHT"], "shading": shading})
        check("artist_material_viewport_restored_after_" + shading, artist_state() == before)
        check("artist_material_viewport_pixels_" + shading, foreground(decode(result["views"][0]["path"])) > 500)
    space.shading.type = "SOLID"


def measure():
    ids = id_counts()
    for shading in ("SOLID", "MATERIAL"):
        samples = []
        for _ in range(5):
            view_cache.images.clear()
            result = view({"targets": [ctx["hero"], ctx["cube"]], "views": ["ISO"], "shading": shading})
            check("measured_capture_drawn_" + shading, not result["views"][0]["cache_hit"])
            samples.append(result["timing"])
        report["measurements"][shading.lower() + "_512_uncached"] = {
            key: {"median": round(statistics.median(s[key] for s in samples), 3),
                  "min": round(min(s[key] for s in samples), 3), "max": round(max(s[key] for s in samples), 3)}
            for key in samples[0]}
    check("measurement_no_new_main_ids", id_counts() == ids)
    check("measurement_artist_state_unchanged", artist_state() == ctx["before"])


def sculpt_mode_material():
    window, hero = ctx["window"], ctx["hero_object"]
    for obj in window.view_layer.objects:
        obj.select_set(obj == hero, view_layer=window.view_layer)
    window.view_layer.objects.active = hero
    with bpy.context.temp_override(window=window, area=ctx["area"], region=ctx["region"]):
        bpy.ops.object.mode_set(mode="SCULPT")
    ctx["sculpt_before"] = artist_state()
    ctx["sculpt_positions"] = bytes(hero.data.angelo_sculpt_positions)
    view_cache.images.clear()
    result = view({"targets": [ctx["hero"]], "views": ["FRONT"], "shading": "MATERIAL"})
    check("sculpt_target_has_no_exact_revision", result["targets"][0]["revision_exact"] is False)
    check("sculpt_material_pixels_nonempty", foreground(decode(result["views"][0]["path"])) > 2000)
    check("sculpt_material_not_cached", len(view_cache.images) == 0)
    check("sculpt_artist_state_unchanged", artist_state() == ctx["sculpt_before"])


def sculpt_mode_after_update():
    hero = ctx["hero_object"]
    check("sculpt_mode_kept_after_depsgraph_update", hero.mode == "SCULPT" and bpy.context.mode == "SCULPT")
    check("sculpt_positions_unchanged_after_material_view", bytes(hero.data.angelo_sculpt_positions) == ctx["sculpt_positions"])
    check("sculpt_state_unchanged_after_update", artist_state() == ctx["sculpt_before"])
    with bpy.context.temp_override(window=ctx["window"], area=ctx["area"], region=ctx["region"]):
        bpy.ops.object.mode_set(mode="OBJECT")


def lifecycle():
    view_id = ctx["solid"]["views"][0]["view_id"]
    scene_queries.invalidate()  # Load/Undo/Redo rotate the epoch even before ui clears caches.
    for handle_name in ("hero", "cube"):
        ctx[handle_name] = scene_queries.object_handle(ctx["hero_object"] if handle_name == "hero" else ctx["window"].scene.objects["Cube"])
    try:
        view({"targets": [ctx["hero"]], "views": [view_id], "shading": "SOLID"})
    except ValueError as error:
        check("reuse_rejected_after_epoch_change", "another document" in str(error))
    else:
        check("reuse_rejected_after_epoch_change", False)
    view_cache.clear()
    check("lifecycle_clear_empties_stores", len(view_cache.images) == 0 and len(view_cache.cameras) == 0)
    space = ctx["area"].spaces.active
    space.shading.type = "MATERIAL"
    lookdev = space.shading.studio_light
    space.shading.type = "SOLID"
    space.shading.light = "STUDIO"
    studio = space.shading.studio_light
    space.shading.light = "MATCAP"
    check("both_stored_studio_lights_restored", (lookdev, studio) == ("city.exr", "rim.sl"))
    images = [image.name for image in bpy.data.images]
    check("no_angelo_image_ids", not any("Angelo" in name for name in images))


STEPS = [setup, solid_three_views, repeat_hits_cache, wireframe_and_material, reuse_and_revision_miss,
         after_edit, fit_and_scene_camera, artist_in_material_preview, measure, sculpt_mode_material,
         sculpt_mode_after_update, lifecycle]


def run():
    step = STEPS[ctx.setdefault("step", 0)]
    try:
        if step is fit_and_scene_camera:
            from types import SimpleNamespace
            ctx["delivery"] = SimpleNamespace(references=ctx["refs"], models=[], settings={}, state={"options": {}},
                                              turn_options={})
        with bpy.context.temp_override(window=ctx.get("window") or bpy.context.window_manager.windows[0]):
            step()
        report.setdefault("completed_steps", []).append(step.__name__)
    except Exception:
        report.update(success=False, failed_step=step.__name__, error=traceback.format_exc())
        return finish()
    ctx["step"] += 1
    if ctx["step"] < len(STEPS):
        return 0.25  # Let the event loop process notifiers, redraws and depsgraph handlers.
    report["success"] = True
    return finish()


def finish():
    if track_scene_changes in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(track_scene_changes)
    report.setdefault("success", False)
    (OUT / "results.json").write_text(json.dumps(report, indent=2, default=str))
    print("ANGELO_VIEW_OBSERVATION", json.dumps({"success": report["success"], "checks": len(report["checks"]),
                                                 "failed": report.get("failed_step")}), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run, first_interval=1)
