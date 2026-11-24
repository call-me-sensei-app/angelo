# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A camera's lens and film look applied to a scene and taken off again (doc/angelo/camera_look.rst).

``apply`` builds the look's node group (``look_nodes``), puts it before the scene's compositing output, drives its
Focal Length, Sensor Width and Frame inputs from the camera and the scene (simple expressions: no Python needed),
sets the camera's bokeh and, when asked, focus breathing (a driver on the focal length; the original is kept), and the
film's white balance through colour management. What it changed is kept on the scene (``STATE``), so ``clear`` puts
the person's settings back.
"""

import json

import bpy

from . import live_windows, look_nodes, look_spec

STATE = "angelo_look"          # Scene property: the applied look and what it replaced, as JSON.
BASE_LENS = "angelo_look_lens"  # Camera data property: the focal length breathing scales.
WHITE_DEFAULT = (6500.0, 10.0)  # Blender's own white balance: no change.


class LookError(look_spec.LookError):
    pass


def _refuse(message):
    return LookError(message, code="VALIDATION")


def sensor_width(camera, scene):
    """The sensor's width across the frame (the fit Blender uses), in millimetres."""
    width = scene.render.resolution_x * scene.render.pixel_aspect_x
    height = scene.render.resolution_y * scene.render.pixel_aspect_y
    fit = camera.sensor_fit
    if fit == "AUTO":
        fit = "HORIZONTAL" if width >= height else "VERTICAL"
    if fit == "VERTICAL":
        return camera.sensor_height * width / height
    return camera.sensor_width


def _drive(socket, expression, variables):
    curve = socket.driver_add("default_value")
    driver = curve.driver
    driver.type = "SCRIPTED"
    for name, id_type, target, path in variables:
        var = driver.variables.new()
        var.name, var.type = name, "SINGLE_PROP"
        var.targets[0].id_type, var.targets[0].id, var.targets[0].data_path = id_type, target, path
    driver.expression = expression
    return driver


def _drive_inputs(scene, node, camera):
    group = scene.compositing_node_group
    for name in ("Focal Length", "Sensor Width", "Frame"):
        node.inputs[name].driver_remove("default_value")
    _drive(node.inputs["Focal Length"], "lens", [("lens", "CAMERA", camera.data, "lens")])
    if camera.data.sensor_fit == "VERTICAL" or (camera.data.sensor_fit == "AUTO"
                                                 and scene.render.resolution_y > scene.render.resolution_x):
        node.inputs["Sensor Width"].default_value = sensor_width(camera.data, scene)   # Fixed for this frame size.
    else:
        _drive(node.inputs["Sensor Width"], "sensor", [("sensor", "CAMERA", camera.data, "sensor_width")])
    _drive(node.inputs["Frame"], "frame", [])
    group.update_tag()


def _lens_animated(camera):
    data = camera.data.animation_data
    return data is not None and data.action is not None


def _breathing(scene, camera, amount):
    data = camera.data
    data.driver_remove("lens")
    if BASE_LENS in data:
        data.lens = data[BASE_LENS]
    if not amount:
        data.pop(BASE_LENS, None)
        return None
    data[BASE_LENS] = data.lens
    unit = scene.unit_settings.scale_length * 1000.0 if scene.unit_settings.system != "NONE" else 1000.0
    curve = data.driver_add("lens")
    driver = curve.driver
    driver.type = "SCRIPTED"
    var = driver.variables.new()
    var.name = look_spec.FOCUS_VARIABLE
    if data.dof.focus_object is not None:
        var.type = "LOC_DIFF"   # The distance to the focus object, as Blender focuses on it.
        var.targets[0].id, var.targets[1].id = camera, data.dof.focus_object
    else:
        var.type = "SINGLE_PROP"
        var.targets[0].id_type, var.targets[0].id, var.targets[0].data_path = "CAMERA", data, "dof.focus_distance"
    driver.expression = look_spec.breathing_expression(data[BASE_LENS], amount, unit)
    return driver


def _white_balance(scene, film, state):
    view = scene.view_settings
    if "white_balance" not in state:
        state["white_balance"] = [view.use_white_balance, view.white_balance_temperature, view.white_balance_tint]
    wanted = (film["temperature"], film["tint"]) if film else WHITE_DEFAULT
    if wanted == WHITE_DEFAULT:
        view.use_white_balance, view.white_balance_temperature, view.white_balance_tint = state["white_balance"]
        return
    view.use_white_balance = True
    view.white_balance_temperature, view.white_balance_tint = wanted


def _with_exposure(preset, stages, resolved, shift):
    """``stages`` with the film stage's exposure moved by ``shift`` stops from what the look itself sets."""
    film = dict((stages or {}).get("film", {}))
    low, high, _default = look_spec.STAGES["film"]["exposure"]
    total = resolved.get("film", {}).get("exposure", 0.0) + shift
    if not low <= total <= high:
        raise _refuse("exposure %+.2f stops puts the film's exposure at %+.2f, beyond %g to %g: lower the look's "
                      "film exposure or choose closer settings" % (shift, total, low, high))
    film["exposure"] = total
    return look_spec.resolve(preset, dict(stages or {}, film=film))


def apply(scene, camera, preset="CLEAN", stages=None, camera_values=None, breathing=0.0, show_in_viewport=False,
          screens=(), exposure=None):
    """Apply a look to ``scene`` seen through ``camera``; returns the receipt. ``breathing`` is the coefficient
    (0: none). ``exposure`` (``look_spec.exposure_stops``) moves the film's exposure by the stops between reference
    settings and the camera's. A CLEAN look with nothing else asked takes the look off."""
    resolved = look_spec.resolve(preset, stages or {})
    shift = None
    if exposure:
        shift = look_spec.exposure_stops(exposure, camera.data.dof.aperture_fstop)
        resolved = _with_exposure(preset, stages, resolved, shift)
    values = dict(look_spec.PRESETS[preset].get("camera", {}))
    values.pop("breathing", None)
    for key, value in (camera_values or {}).items():
        if key not in look_spec.CAMERA or key == "breathing":
            raise _refuse("camera takes: aperture_blades, aperture_rotation, aperture_ratio")
        low, high = look_spec.CAMERA[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise _refuse("camera.%s must be a number from %g to %g" % (key, low, high))
        values[key] = value
    low, high = look_spec.CAMERA["breathing"]
    if not low <= breathing <= high:
        raise _refuse("breathing must be from %g to %g" % (low, high))
    if breathing and _lens_animated(camera):   # Every refusal comes before any change.
        raise _refuse("%s's lens settings are animated; focus breathing would override its focal length"
                      % camera.name)
    if not resolved and not values and not breathing:
        return clear(scene, camera)
    state = json.loads(scene.get(STATE, "{}"))
    dof = camera.data.dof
    if "aperture" not in state:
        state["aperture"] = [dof.aperture_blades, dof.aperture_rotation, dof.aperture_ratio]
    else:   # A look is whole: another preset starts from the person's bokeh, not the last look's.
        dof.aperture_blades, dof.aperture_rotation, dof.aperture_ratio = state["aperture"]
    for key, value in values.items():
        setattr(dof, key, int(value) if key == "aperture_blades" else float(value))
    _breathing(scene, camera, breathing)
    _white_balance(scene, resolved.get("film"), state)
    group = look_nodes.build(resolved) if resolved else None
    if group is not None:
        node = look_nodes.insert(scene, group)
        _drive_inputs(scene, node, camera)
    else:
        look_nodes.remove(scene)
    if show_in_viewport:
        for screen in screens:
            for area in live_windows.screen_areas(screen):
                for space in (s for s in area.spaces if s.type == "VIEW_3D"):
                    space.shading.use_compositor = "CAMERA"
    state.update(preset=preset, stages=resolved, camera=camera.name, breathing=breathing)
    state.pop("exposure_stops", None)
    receipt = {"preset": preset, "stages": list(resolved), "camera": camera.name, "breathing": breathing,
               "group": group.name if group else None, "bokeh": {key: getattr(dof, key) for key in
                                                                 ("aperture_blades", "aperture_rotation",
                                                                  "aperture_ratio")}}
    if shift is not None:
        state["exposure_stops"] = receipt["exposure_stops"] = shift
    scene[STATE] = json.dumps(state)
    return receipt


def clear(scene, camera):
    """Take the look off: the compositor node, breathing, and the bokeh and white balance it replaced."""
    state = json.loads(scene.get(STATE, "{}"))
    look_nodes.remove(scene)
    _breathing(scene, camera, 0.0)
    if "aperture" in state:
        dof = camera.data.dof
        dof.aperture_blades, dof.aperture_rotation, dof.aperture_ratio = state["aperture"]
    if "white_balance" in state:
        view = scene.view_settings
        view.use_white_balance, view.white_balance_temperature, view.white_balance_tint = state["white_balance"]
    scene.pop(STATE, None)
    return {"preset": "CLEAN", "stages": [], "camera": camera.name, "breathing": 0.0, "group": None,
            "cleared": bool(state)}
