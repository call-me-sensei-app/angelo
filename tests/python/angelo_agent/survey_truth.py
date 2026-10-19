# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Ground truth for the harness's questions (the request survey's and the gate sets'), computed from the scene.

``truth(bpy, scene, spec)`` returns the canonical right answers
(``eval_answers.grade``'s ``accept``) for one question, read from Blender data
before the prompt is sent and never shown to the model:

* ``camera_frame``: whether an object's evaluated bounding box projects fully
  inside the active camera's frame (``camera_frame``; lens shift, sensor fit
  and the render aspect are in Blender's own ``Camera.view_frame``);
* ``mesh_counts``: an object's vertex, edge and face counts (the base mesh,
  and the evaluated one when modifiers change them);
* ``object_names``: every object of the scene, or only those of one ``type`` (the gate's mesh question);
* ``dimensions_location``: an object's Dimensions and Location, in metres (the gate's cube question);
* ``render``: the render engine and output resolution;
* ``shortcut``: an operator's default keymap entry.

A truth too close to call (a box within ``AMBIGUOUS`` of the frame edge) is
returned as ``{"ungraded": reason}``: the harness then holds no answer to
compare with and says so, instead of guessing. ``bpy`` is passed in, so this
module is importable without Blender; nothing here changes the scene.
"""

import eval_answers as answers

# Fractions of the frame: a projected box nearer than this to the frame's edge has no firm yes or no.
AMBIGUOUS = 0.02
# Blender's default keymap (scripts/presets/keyconfig/keymap_data/blender_default.py), used when the
# running process has no key configuration to read (a background process).
DEFAULT_SHORTCUTS = {"mesh.bevel": "ctrl+b"}
_KEY_NAMES = {"ZERO": "0", "ONE": "1", "TWO": "2", "THREE": "3", "FOUR": "4", "FIVE": "5", "SIX": "6", "SEVEN": "7",
              "EIGHT": "8", "NINE": "9"}


def project(bpy, scene, camera, point):
    """A world point in the camera's frame: (x, y) with (0, 0) bottom left and (1, 1) top right, and its depth.

    The formula of ``bpy_extras.object_utils.world_to_camera_view``, with the
    camera's evaluated transform (constraints, parents, animation) and its
    current data, so a lens or shift set a moment ago is already in effect.
    """
    evaluated = camera.evaluated_get(bpy.context.evaluated_depsgraph_get())
    local = evaluated.matrix_world.normalized().inverted() @ point
    depth = -local.z
    frame = list(camera.data.view_frame(scene=scene)[:3])
    if camera.data.type != "ORTHO":
        if depth <= 0.0:
            return 0.5, 0.5, depth
        frame = [-(corner / (corner.z / depth)) for corner in frame]
    left, right, bottom, top = frame[2].x, frame[1].x, frame[1].y, frame[0].y
    return (local.x - left) / (right - left), (local.y - bottom) / (top - bottom), depth


def box_corners(bpy, obj):
    """World-space corners of an object's evaluated bounding box."""
    from mathutils import Vector
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    return [evaluated.matrix_world @ Vector(corner) for corner in evaluated.bound_box]


def camera_frame(bpy, scene, obj, camera=None):
    """How an object's bounding box sits in the camera's frame.

    ``inside``: every corner is in front of the camera and inside the frame.
    ``margin``: the smallest distance from the box to a frame edge in frame
    fractions, negative by how far the box leaves the frame. ``fill``: the
    larger of the box's projected width and height, as a fraction of the frame.
    """
    camera = camera or scene.camera
    if camera is None or camera.type != "CAMERA":
        return {"inside": False, "in_front": False, "reason": "the scene has no active camera"}
    points = [project(bpy, scene, camera, corner) for corner in box_corners(bpy, obj)]
    if any(depth <= camera.data.clip_start for _, _, depth in points):
        return {"inside": False, "in_front": False, "camera": camera.name,
                "reason": "part of the object is behind the camera"}
    low_x, high_x = min(x for x, _, _ in points), max(x for x, _, _ in points)
    low_y, high_y = min(y for _, y, _ in points), max(y for _, y, _ in points)
    margin = min(low_x, low_y, 1.0 - high_x, 1.0 - high_y)
    return {"inside": margin >= 0.0, "in_front": True, "camera": camera.name, "margin": round(margin, 4),
            "fill": round(max(high_x - low_x, high_y - low_y), 4),
            "bounds": [round(value, 4) for value in (low_x, low_y, high_x, high_y)]}


def _camera_frame_truth(bpy, scene, spec):
    obj = scene.objects.get(spec["object"])
    if obj is None:
        return {"ungraded": "the object the question names is missing"}
    if scene.camera is None or scene.camera.type != "CAMERA":
        return {"ungraded": "the scene has no active camera"}
    measured = camera_frame(bpy, scene, obj)
    if measured["in_front"] and abs(measured["margin"]) < AMBIGUOUS:
        return {"ungraded": "the object's edge is within %g of the frame edge: no firm yes or no" % AMBIGUOUS,
                "detail": measured}
    return {"accept": ["yes" if measured["inside"] else "no"], "detail": measured}


def _mesh_counts_truth(bpy, scene, spec):
    obj = scene.objects.get(spec["object"])
    if obj is None or obj.type != "MESH":
        return {"ungraded": "the mesh the question names is missing"}
    count = lambda mesh: [len(mesh.vertices), len(mesh.edges), len(mesh.polygons)]
    base = count(obj.data)
    evaluated = count(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data)
    return {"accept": [base] if evaluated == base else [base, evaluated],
            "detail": {"base": base, "evaluated": evaluated}}


def _render_truth(bpy, scene, spec):
    render = scene.render
    engine = answers.engine_name(render.engine)
    accept = [[engine, render.resolution_x, render.resolution_y]]
    if render.resolution_percentage != 100:   # The output size after the percentage is as true an answer.
        percentage = render.resolution_percentage   # Whole pixels, as the renderer counts them.
        accept.append([engine, render.resolution_x * percentage // 100, render.resolution_y * percentage // 100])
    return {"accept": accept, "detail": {"engine": render.engine, "percentage": render.resolution_percentage}}


def keymap_shortcuts(bpy, keymap, operator, properties):
    """An operator's entries in the default key configuration, each as ``ctrl+b`` (none in a background process).

    A platform may bind more than one: macOS has both Cmd+B and Ctrl+B for the edge bevel.
    """
    configuration = bpy.context.window_manager.keyconfigs.default
    entries = configuration.keymaps.get(keymap) if configuration is not None else None
    found = []
    for item in (entries.keymap_items if entries is not None else ()):
        if item.idname != operator or item.value != "PRESS" or item.any or not item.active:
            continue
        if any(getattr(item.properties, name, None) != value for name, value in properties.items()):
            continue
        modifiers = [name for name, held in (("ctrl", item.ctrl), ("cmd", item.oskey), ("alt", item.alt),
                                             ("shift", item.shift)) if held is True or held == 1]
        found.append("+".join(modifiers + [_KEY_NAMES.get(item.type, item.type).lower()]))
    return found


def _shortcut_truth(bpy, scene, spec):
    found = keymap_shortcuts(bpy, spec["keymap"], spec["operator"], spec.get("properties", {}))
    documented = DEFAULT_SHORTCUTS[spec["operator"]]   # Right on every platform's manual page.
    return {"accept": sorted(set(found) | {documented}),
            "detail": {"this_process_keymap": sorted(set(found)), "documented_default": documented}}


def _object_names_truth(bpy, scene, spec):
    wanted = spec.get("type")
    return {"accept": [sorted(obj.name for obj in scene.objects if wanted is None or obj.type == wanted)]}


def _dimensions_location_truth(bpy, scene, spec):
    obj = scene.objects.get(spec["object"])
    if obj is None:
        return {"ungraded": "the object the question names is missing"}
    bpy.context.view_layer.update()   # Dimensions are read from the evaluated bounds.
    measure = lambda place: [round(value, 6) for value in tuple(obj.dimensions) + tuple(place)]
    accept = [measure(obj.location)]
    world = measure(obj.matrix_world.translation)
    if world != accept[0]:   # A parented object: where it is in the world is as true an answer.
        accept.append(world)
    return {"accept": accept, "detail": {"object": obj.name}}


TRUTHS = {
    "camera_frame": _camera_frame_truth,
    "mesh_counts": _mesh_counts_truth,
    "object_names": _object_names_truth,
    "dimensions_location": _dimensions_location_truth,
    "render": _render_truth,
    "shortcut": _shortcut_truth,
}


def truth(bpy, scene, spec):
    """``{"accept": [...], "detail": {...}}`` for one question's ``truth`` spec, or ``{"ungraded": reason}``."""
    return TRUTHS[spec["kind"]](bpy, scene, spec)
