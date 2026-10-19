# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The World's sun and brightness facts (world_light) against Blender's own render and save, in the compiled app.

Run by ``run_native_suites.py --include test_blender_world_light`` (BACKGROUND). It checks that the facts are true,
not only shaped right:

* the bearing and elevation ``world_light`` states for a Sky's sun are where Cycles draws the sun's disc (a narrow
  camera aimed there sees the disc; aimed a quarter turn away, or at the bearing a counter-clockwise-from-+X
  reading would give, it does not);
* ``shown`` (an HDR read through ``save_copy`` as a BMP in the scene's view transform) equals what Blender's own
  ``Image.save_render`` writes for the same scene-linear values, in Standard, AgX and Filmic with exposure and a look;
  Standard is also checked against the sRGB formula;
* the measured sky table's interpolation against a Cycles render of the zenith between its rows (the error is
  recorded in the report);
* a script result and the settings read carry the facts.

No provider, account or network; tiny Cycles CPU renders.
"""

import json
import math
import os
from pathlib import Path
import sys
import tempfile
import traceback

import bpy
import numpy

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import world_light, world_light_table, world_surface  # noqa: E402
from angelo_agent.script_after import notes, world_before  # noqa: E402

OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.file_preview_type = "NONE"
report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
TEMP = Path(tempfile.mkdtemp(prefix="angelo-world-light-qa-"))


def check(name, condition, **observed):
    report["checks"][name] = bool(condition)
    if observed:
        report["observed"][name] = observed


def setup():
    scene = bpy.context.scene
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    world = bpy.data.worlds.new("Light QA")
    scene.world = world
    tree = world.node_tree
    sky = tree.nodes.new("ShaderNodeTexSky")
    tree.links.new(sky.outputs["Color"], tree.nodes["Background"].inputs["Color"])
    camera = bpy.data.objects.new("Light QA camera", bpy.data.cameras.new("Light QA camera"))
    scene.collection.objects.link(camera)
    scene.camera = camera
    try:
        scene.render.engine = "CYCLES"
        scene.cycles.samples, scene.cycles.device = 4, "CPU"
    except (TypeError, AttributeError):
        pass  # A build without Cycles: the render checks are skipped and say so.
    scene.render.resolution_x = scene.render.resolution_y = 16
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format, scene.render.image_settings.color_depth = "OPEN_EXR", "32"
    return scene, sky, camera


def mean_render(scene, path):
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        pixels = numpy.empty(len(image.pixels), dtype=numpy.float32)
        image.pixels.foreach_get(pixels)
        return pixels.reshape(-1, 4)[:, :3].mean(axis=0)
    finally:
        bpy.data.images.remove(image)


def aim(camera, bearing, elevation):
    """Point the camera's -Z toward a bearing and elevation (world_light's convention)."""
    direction = world_light.angles  # Only to keep one reading of the convention in view.
    b, e = math.radians(bearing), math.radians(elevation)
    from mathutils import Vector
    forward = Vector((math.cos(e) * math.sin(b), math.cos(e) * math.cos(b), math.sin(e)))
    camera.rotation_euler = forward.to_track_quat("-Z", "Y").to_euler()
    assert direction(forward) is not None


def sun_is_where_it_is_said(scene, sky, camera):
    sky.sky_type, sky.sun_disc = "MULTIPLE_SCATTERING", True
    sky.sun_size, sky.sun_intensity = math.radians(4.0), 1.0
    camera.data.lens = 200.0  # About 10 degrees across: the disc fills the middle.
    results = {}
    for rotation, elevation in ((90.0, 20.0), (225.0, 8.0), (300.0, 45.0)):
        sky.sun_rotation, sky.sun_elevation = math.radians(rotation), math.radians(elevation)
        facts, _ = world_light.sky(sky)
        bearing, up = facts["sun_bearing_deg"], facts["sun_elevation_deg"]
        aim(camera, bearing, up)
        said = float(mean_render(scene, TEMP / "said.exr").max())
        # A quarter turn away, and the bearing a counter-clockwise-from-+X reading would give where it differs.
        others = [(bearing + 90.0) % 360.0] + ([(90.0 - bearing) % 360.0] if abs((90.0 - bearing) % 360.0 - bearing) > 20
                                               else [])
        other = 0.0
        for wrong in others:
            aim(camera, wrong, up)
            other = max(other, float(mean_render(scene, TEMP / "other.exr").max()))
        results["%g/%g" % (rotation, elevation)] = {"bearing": bearing, "elevation": up, "said": said, "other": other}
        check("sun_disc_at_bearing_%g_elevation_%g" % (rotation, elevation),
              bearing == round(rotation % 360, 2) and said > 20 * max(other, 1e-6), said=said, other=other)
    report["observed"]["sun_renders"] = results


def shown_matches_blenders_own_save(scene):
    values = [(0.18, 0.18, 0.18), (0.2, 0.4, 0.8), (3.0, 1.2, 0.4), (0.02, 0.03, 0.05)]
    image = bpy.data.images.new("Light QA linear", width=len(values), height=1, float_buffer=True)
    image.pixels.foreach_set(numpy.array([c for rgb in values for c in (*rgb, 1.0)], dtype=numpy.float32))
    scene.render.image_settings.file_format, scene.render.image_settings.color_depth = "PNG", "8"
    scene.render.image_settings.color_mode = "RGB"
    worst = {}
    try:
        for view, look, exposure in (("Standard", "None", 0.0), ("AgX", "None", 0.0), ("AgX", "AgX - Punchy", 1.0),
                                     ("Filmic", "None", -0.5)):
            scene.view_settings.view_transform = view
            scene.view_settings.look = look
            scene.view_settings.exposure = exposure
            target = TEMP / ("own-%s.png" % view)
            image.save_render(str(target), scene=scene)
            saved = bpy.data.images.load(str(target), check_existing=False)
            try:
                own = numpy.empty(len(saved.pixels), dtype=numpy.float32)
                saved.pixels.foreach_get(own)
                own = own.reshape(-1, 4)[:, :3]  # Bytes as 0-1 (a byte image's pixels are its stored values).
            finally:
                bpy.data.images.remove(saved)
            shown = numpy.array(world_light.displayed(scene, values), dtype=numpy.float32)
            error = float(numpy.abs(shown - own).max())
            worst["%s/%s/%g" % (view, look, exposure)] = round(error * 255, 2)
            check("shown_equals_save_render_%s_%s" % (view, look.replace(" ", "_")), error <= 2.5 / 255,
                  error_levels=round(error * 255, 2))
        scene.view_settings.view_transform, scene.view_settings.look, scene.view_settings.exposure = "Standard", "None", 0
        srgb = world_light.displayed(scene, [(0.18, 0.18, 0.18)])[0][0]
        check("standard_is_the_srgb_formula", abs(srgb - (1.055 * 0.18 ** (1 / 2.4) - 0.055)) <= 1.5 / 255, srgb=srgb)
    finally:
        bpy.data.images.remove(image)
        scene.render.image_settings.file_format, scene.render.image_settings.color_depth = "OPEN_EXR", "32"
    report["observed"]["shown_vs_save_render_levels"] = worst


def table_against_a_render(scene, sky, camera):
    sky.sky_type, sky.sun_disc, sky.sun_rotation = "MULTIPLE_SCATTERING", False, 0.0
    camera.data.lens = 50.0
    camera.rotation_euler = (math.pi, 0.0, 0.0)  # The zenith, as the table was measured.
    errors = {}
    for elevation in (12.0, 40.0):
        sky.sun_elevation = math.radians(elevation)
        rendered = mean_render(scene, TEMP / "zenith.exr")
        rows, _ = world_light_table.reading("MULTIPLE_SCATTERING", elevation, {})
        table = numpy.array(rows[2][1])
        error = float(numpy.abs(table - rendered).max() / max(float(rendered.max()), 1e-6))
        errors["%g" % elevation] = {"rendered": [round(float(v), 4) for v in rendered],
                                    "table": [round(float(v), 4) for v in table], "relative": round(error, 3)}
        check("table_zenith_within_a_quarter_at_%g" % elevation, error < 0.25, relative=round(error, 3))
    report["observed"]["table_interpolation"] = errors


def results_carry_the_facts(scene, sky):
    sky.sky_type, sky.sun_disc = "MULTIPLE_SCATTERING", True
    sky.sun_rotation, sky.sun_elevation = 0.0, math.radians(10.0)
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", "SUN"))
    scene.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(80.0), 0.0, math.radians(180.0))
    bpy.context.view_layer.update()
    window = bpy.context.window_manager.windows[0] if bpy.context.window_manager.windows else None
    holder = type("W", (), {"scene": scene})()
    before = world_before(holder)
    sky.sun_rotation = math.radians(225.0)  # A script that turns only the sky's sun.
    result = notes(holder, {"success": True}, "pass", before)
    shown = result.get("world") or {}
    check("script_that_turns_the_sun_reports_it", shown.get("sky", {}).get("sun_bearing_deg") == 225.0,
          world=shown)
    lights = shown.get("sun_lights") or [{}]
    check("sun_light_beside_the_sky", "from_sky_deg" in lights[0] and "bearing_deg" in lights[0], lights=lights)
    samples = (shown.get("brightness") or {}).get("samples") or [{}]
    check("brightness_is_shown_in_the_view_transform", all("shown" in row for row in samples), samples=samples)
    from angelo_agent import scene_settings
    if window is not None:
        with bpy.context.temp_override(window=window):
            read = scene_settings.observe(window)
    else:
        read = scene_settings.observe(type("W", (), {"scene": scene, "view_layer": bpy.context.view_layer})())
    check("settings_read_says_what_the_world_shows", (read.get("world_shows") or {}).get("shows") == "SKY",
          world_shows=read.get("world_shows"))


try:
    scene, sky, camera = setup()
    if scene.render.engine == "CYCLES":  # setup() asked for it; a build without Cycles keeps another engine.
        sun_is_where_it_is_said(scene, sky, camera)
        table_against_a_render(scene, sky, camera)
    else:
        report["observed"]["cycles"] = "not built: the render checks were not run"
    shown_matches_blenders_own_save(scene)
    results_carry_the_facts(scene, sky)
    report["success"] = all(report["checks"].values()) and bool(report["checks"])
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    (OUT / "world-light.json").write_text(json.dumps(report, indent=2, default=str))
failed = sorted(name for name, passed in report["checks"].items() if not passed)
print("WORLD_LIGHT failed checks:", json.dumps(failed))
assert report["success"], failed or report.get("error")
