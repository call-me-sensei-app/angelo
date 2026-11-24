# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The lens and film look in the running app (doc/angelo/camera_look.rst, "Validation").

Background, Cycles on the CPU at a tiny size. A uniform world seen through the camera makes every look measurable:
the natural vignette's corner-to-centre ratio is cos^4 of the corner's angle (``look_spec.natural_vignette``) for two
lenses; NOIR's film has no colour; grain is luminance noise that grows with its amount and moves between frames. The
look's node goes before the compositing output and keeps what the person built there; reapplying keeps one look node
and drops the old group; its drivers need no Python. Focus breathing follows ``look_spec.breathing_lens`` and is
refused on an animated lens. CLEAN puts back the compositor, bokeh, focal length and white balance. Exposure in stops
between reference settings and the camera's moves the film stage's exposure, and a 2-stop shift renders 4x brighter.
``angelo.camera_look`` from its properties alone (the agent's path) and its refusals. Writes look.json under
ANGELO_QA_DIR.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import traceback
from pathlib import Path

import bpy
import numpy as np

from angelo_agent import look_nodes, look_spec, look_system  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-look"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"phase": "start", "success": False, "checks": {}, "details": {}, "facts": {}}
VIGNETTE_TOLERANCE = 0.01   # Of the ratio: a 1-sample CPU render of a uniform world is exact to float precision.


def write_report():
    (OUT / "look.json").write_text(json.dumps(REPORT, indent=1, default=float), encoding="utf-8")


def check(name, passed, detail=""):
    REPORT["checks"][name] = bool(passed)
    REPORT["details"][name] = detail


def scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene_ = bpy.context.scene
    scene_.render.engine = "CYCLES"
    scene_.cycles.device, scene_.cycles.samples, scene_.cycles.use_denoising = "CPU", 1, False
    scene_.render.resolution_x, scene_.render.resolution_y, scene_.render.resolution_percentage = 96, 54, 100
    scene_.view_settings.view_transform = "Standard"
    scene_.world = bpy.data.worlds.new("Grey")
    scene_.world.color = (0.5, 0.45, 0.4)
    camera = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
    scene_.collection.objects.link(camera)
    scene_.camera = camera
    return scene_, camera


def render(scene_, name):
    path = str(OUT / ("%s.exr" % name))
    scene_.render.filepath = path
    scene_.render.image_settings.file_format, scene_.render.image_settings.color_depth = "OPEN_EXR", "32"
    bpy.ops.render.render(write_still=True)
    image = bpy.data.images.load(path, check_existing=False)
    pixels = np.array(image.pixels[:], dtype=np.float32).reshape(image.size[1], image.size[0], 4)
    bpy.data.images.remove(image)
    return pixels


def vignette(scene_, camera):
    for lens in (18.0, 50.0):
        camera.data.lens = lens
        look_system.apply(scene_, camera, "CLEAN", {"vignette": {"amount": 1.0}})
        px = render(scene_, "vignette-%d" % lens)
        h, w = px.shape[:2]
        x, y = (0.5 - w / 2) / (w / 2), (0.5 - h / 2) / (h / 2)
        expected = look_spec.natural_vignette(x, y, lens, camera.data.sensor_width, w / h)
        ratio = float(px[0, 0, 0] / px[h // 2, w // 2, 0])
        check("vignette_cos4_%dmm" % lens, abs(ratio - expected) <= VIGNETTE_TOLERANCE * expected,
              "corner/centre %.5f, cos^4 %.5f" % (ratio, expected))


def film_and_grain(scene_, camera):
    look_system.apply(scene_, camera, "NOIR", {"grain": {"amount": 0.0}})
    px = render(scene_, "noir")
    check("noir_has_no_colour", float(np.abs(px[..., 0] - px[..., 2]).max()) < 1e-4)
    spread = {}
    for amount in (0.05, 0.15):
        look_system.apply(scene_, camera, "CLEAN", {"grain": {"amount": amount, "size": 0.004}})
        spread[amount] = float(render(scene_, "grain-%g" % amount)[..., 1].std())
    check("grain_grows_with_its_amount", spread[0.15] > 2.0 * spread[0.05] > 0.0, "std %s" % spread)
    first = render(scene_, "grain-frame-1")
    scene_.frame_set(2)
    second = render(scene_, "grain-frame-2")
    scene_.frame_set(1)
    check("grain_moves_between_frames", float(np.abs(first - second).max()) > 1e-3)


def compositor(scene_, camera):
    look_system.apply(scene_, camera, "CLEAN")
    group, _made = look_nodes.compositing_group(scene_)
    output = next(n for n in group.nodes if n.bl_idname == "NodeGroupOutput")
    blur = group.nodes.new("CompositorNodeBlur")   # The person's own step before the output.
    source = output.inputs[0].links[0].from_socket
    group.links.new(source, blur.inputs["Image"])
    group.links.new(blur.outputs["Image"], output.inputs[0])
    look_system.apply(scene_, camera, "VINTAGE")
    look = group.nodes[look_nodes.NODE_NAME]
    first_group = look.node_tree.name
    check("look_before_the_output_after_the_persons_nodes",
          look.inputs["Image"].links[0].from_node == blur and output.inputs[0].links[0].from_node == look)
    look_system.apply(scene_, camera, "ANAMORPHIC")
    looks = [n for n in group.nodes if n.bl_idname == "CompositorNodeGroup" and n.node_tree
             and n.node_tree.name.startswith(look_nodes.GROUP_NAME)]
    check("reapplying_keeps_one_look", len(looks) == 1 and first_group not in bpy.data.node_groups,
          "%d look node(s); first group %s" % (len(looks), "kept" if first_group in bpy.data.node_groups else "gone"))
    drivers = list(group.animation_data.drivers) + list(camera.data.animation_data.drivers
                                                        if camera.data.animation_data else [])
    check("drivers_need_no_python", drivers and all(d.driver.is_simple_expression for d in drivers),
          [d.data_path for d in drivers])
    check("bokeh_from_the_preset", camera.data.dof.aperture_ratio == 2.0 and camera.data.dof.aperture_blades == 0)
    look_system.apply(scene_, camera, "CLEAN")
    check("clean_keeps_the_persons_nodes", look_nodes.NODE_NAME not in group.nodes
          and output.inputs[0].links[0].from_node == blur)


def breathing(scene_, camera):
    camera.data.lens = 35.0
    camera.data.dof.focus_distance = 0.6
    look_system.apply(scene_, camera, "VINTAGE", breathing=0.15)
    bpy.context.view_layer.update()
    expected = look_spec.breathing_lens(35.0, 0.15, 600.0)
    check("breathing_follows_focus", abs(camera.data.lens - expected) < 1e-3,
          "%.4f mm, expected %.4f" % (camera.data.lens, expected))
    look_system.apply(scene_, camera, "CLEAN")
    check("clean_restores_the_focal_length", abs(camera.data.lens - 35.0) < 1e-6 and not (
        camera.data.animation_data and camera.data.animation_data.drivers))
    camera.data.keyframe_insert("lens", frame=1)
    dof = camera.data.dof
    before = (dof.aperture_blades, dof.aperture_ratio, camera.data.lens, scene_.get(look_system.STATE),
              scene_.view_settings.use_white_balance)
    try:
        look_system.apply(scene_, camera, "VINTAGE", breathing=0.15)
        refused = False
    except look_spec.LookError:
        refused = True
    after = (dof.aperture_blades, dof.aperture_ratio, camera.data.lens, scene_.get(look_system.STATE),
             scene_.view_settings.use_white_balance)
    check("breathing_refused_on_an_animated_lens_changing_nothing", refused and before == after,
          "%s -> %s" % (before, after))
    camera.data.animation_data_clear()


def restored(scene_, camera):
    view = scene_.view_settings
    before = (view.use_white_balance, camera.data.dof.aperture_blades, camera.data.dof.aperture_ratio)
    look_system.apply(scene_, camera, "WARM_PORTRAIT")
    check("warm_portrait_sets_white_balance", view.use_white_balance and view.white_balance_temperature == 5200.0)
    look_system.apply(scene_, camera, "CLEAN")
    after = (view.use_white_balance, camera.data.dof.aperture_blades, camera.data.dof.aperture_ratio)
    check("clean_restores_bokeh_and_white_balance", before == after == (False, 0, 1.0), "%s -> %s" % (before, after))


def exposure(scene_, camera):
    """The stops helper: the film stage's exposure moves by the stops between the reference settings and the
    camera's (its own f-stop unless the setting names one), on top of the preset's, and the render follows."""
    reference = {"f_stop": 8.0, "shutter": 1 / 100, "iso": 100}
    look_system.apply(scene_, camera, "CLEAN", {"film": {"exposure": 0.0}})
    plain = float(render(scene_, "exposure-0")[..., 1].mean())
    camera.data.dof.aperture_fstop = 4.0
    receipt = look_system.apply(scene_, camera, "CLEAN", exposure={"reference": reference,
                                                                   "setting": {"shutter": 1 / 100, "iso": 100}})
    state = json.loads(scene_.get(look_system.STATE, "{}"))
    check("exposure_two_stops_from_the_cameras_f_stop", abs(receipt.get("exposure_stops", 0) - 2.0) < 1e-9
          and abs(state["stages"]["film"]["exposure"] - 2.0) < 1e-9, "receipt %s" % receipt)
    brighter = float(render(scene_, "exposure-2")[..., 1].mean())
    check("exposure_render_is_four_times_brighter", abs(brighter / plain - 4.0) < 0.02,
          "%.5f / %.5f = %.4f" % (brighter, plain, brighter / plain))
    look_system.apply(scene_, camera, "WARM_PORTRAIT", {"film": {"exposure": 0.5}},
                      exposure={"reference": reference, "setting": {"f_stop": 8.0, "shutter": 1 / 50, "iso": 100}})
    state = json.loads(scene_.get(look_system.STATE, "{}"))
    check("exposure_adds_to_the_looks_own", abs(state["stages"]["film"]["exposure"] - 1.5) < 1e-9
          and state["stages"]["film"]["temperature"] == 5200.0, state["stages"].get("film"))
    before = scene_.get(look_system.STATE)
    try:
        look_system.apply(scene_, camera, "CLEAN", {"film": {"exposure": 4.0}},
                          exposure={"reference": reference, "setting": {"shutter": 1 / 100, "iso": 400}})
        refused = False
    except look_spec.LookError as error:
        refused = True
        REPORT["facts"]["exposure_beyond_the_range"] = str(error)
    check("exposure_beyond_the_film_range_refused_changing_nothing", refused and scene_.get(look_system.STATE) == before)
    look_system.apply(scene_, camera, "CLEAN")
    camera.data.dof.aperture_fstop = 2.8


def operator(scene_, camera):
    if not hasattr(bpy.types, "ANGELO_OT_camera_look"):
        from angelo_agent import look_ui
        look_ui.register()   # The add-on is not enabled in a factory-startup background run.
    result = bpy.ops.angelo.camera_look("EXEC_DEFAULT", preset="DOCUMENTARY",
                                        stages=json.dumps({"grain": {"amount": 0.2}}))
    state = json.loads(scene_.get(look_system.STATE, "{}"))
    check("operator_applies", result == {"FINISHED"} and state.get("stages", {}).get("grain", {}).get("amount") == 0.2)
    camera.data.dof.aperture_fstop = 5.6
    result = bpy.ops.angelo.camera_look("EXEC_DEFAULT", preset="CLEAN", exposure=json.dumps(
        {"reference": {"f_stop": 5.6, "shutter": 1 / 50, "iso": 200}, "setting": {"shutter": 1 / 50, "iso": 800}}))
    state = json.loads(scene_.get(look_system.STATE, "{}"))
    # The camera's f-stop is a 32-bit float (5.6 reads back 5.5999999), so its stops are 2 within float32's precision.
    check("operator_exposure_json", result == {"FINISHED"}
          and abs(state.get("stages", {}).get("film", {}).get("exposure", 0) - 2.0) < 1e-6)
    # A person's dialog fields: the camera's f-stop against the reference fields, the same stops.
    result = bpy.ops.angelo.camera_look("EXEC_DEFAULT", preset="CLEAN", match_exposure=True, shutter=1 / 25, iso=200,
                                        reference_f_stop=5.6, reference_shutter=1 / 50, reference_iso=200)
    state = json.loads(scene_.get(look_system.STATE, "{}"))
    check("operator_exposure_fields", result == {"FINISHED"}
          and abs(state.get("stages", {}).get("film", {}).get("exposure", 0) - 1.0) < 1e-6)
    refusals = {"bad_value": dict(preset="NOIR", stages=json.dumps({"film": {"contrast": 5}})),
                "unknown_stage": dict(preset="NOIR", stages=json.dumps({"bokeh": {}})),
                "no_camera": dict(preset="NOIR", camera="Nothing"),
                "breathing_without_amount": dict(preset="NOIR", breathing=True),
                "exposure_not_json": dict(preset="NOIR", exposure="f/4"),
                "exposure_without_reference": dict(preset="NOIR", exposure=json.dumps(
                    {"setting": {"shutter": 0.02, "iso": 100}}))}
    for name, props in refusals.items():
        before = scene_.get(look_system.STATE)
        try:
            refused = bpy.ops.angelo.camera_look("EXEC_DEFAULT", **props) == {"CANCELLED"}
        except RuntimeError as error:
            refused = True
            REPORT["facts"]["refusal_" + name] = str(error).strip()[:200]
        check("refuses_" + name, refused and scene_.get(look_system.STATE) == before)


def main():
    try:
        scene_, camera = scene()
        for phase in (vignette, film_and_grain, compositor, breathing, restored, exposure, operator):
            REPORT["phase"] = phase.__name__
            phase(scene_, camera)
        REPORT["phase"] = "complete"
        REPORT["failed_checks"] = [name for name, passed in REPORT["checks"].items() if not passed]
        REPORT["success"] = bool(REPORT["checks"]) and not REPORT["failed_checks"]
    except Exception:
        REPORT["error"] = traceback.format_exc()
    write_report()
    print("ANGELO_LOOK", "PASS" if REPORT["success"] else "FAIL")
    sys.exit(0 if REPORT["success"] else 1)


main()
