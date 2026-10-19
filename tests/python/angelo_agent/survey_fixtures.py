# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scene states the harness makes before a turn it grades, so its check can tell a result from a guess.

A check that the factory scene already satisfies, or a question whose answer
is the factory default, proves nothing. ``apply(bpy, scene, fixture)`` makes
the state the task's ``fixture`` names, as a person might have left it, and
returns the steps taken and a ``restore`` callable:

* transient, undone after the turn is graded so later tasks see the scene the
  agent built: ``probe_object`` (an Empty with a name no default scene has, or
  with ``mesh`` a small mesh plate, for a question about meshes),
  ``object_offset`` (an object moved by a fixed offset, so that where it is
  can only be read from the scene, not recalled from the chat),
  ``scene_values`` (settings moved off their defaults) and ``camera_frame``
  (the active camera's lens and shift set so an object is clearly inside the
  frame, or clearly cut by its right edge; the camera object never moves, so
  constraints, parents and animation keep working);
* lasting, the starting point of an edit: ``world_color`` and
  ``orphan_material``.

The harness makes these, never the agent; the task record keeps the steps as
``harness_fixture``. ``bpy`` is passed in, so this module imports without
Blender. A state that cannot be made raises ``FixtureError`` and the task is
recorded as ungraded with that reason.
"""

import survey_truth

# camera_frame: the object's projected box spans this fraction of the frame, centred ("inside") or with its
# centre on the frame's right edge ("cropped"); either way its edge is at least MARGIN from the frame edge.
SPAN, MARGIN = 0.5, 0.1
CAMERA_STATES = ("inside", "cropped")


class FixtureError(RuntimeError):
    """A precondition could not be made; the message says why."""


def _probe_object(bpy, scene, fixture):
    if scene.objects.get(fixture["name"]) is not None:
        raise FixtureError("an object named %s already exists" % fixture["name"])
    mesh = None
    if fixture.get("mesh"):   # A 0.5 m plate: one face is enough to be a mesh object like any other.
        mesh = bpy.data.meshes.new(fixture["name"])
        mesh.from_pydata([(-.25, -.25, 0), (.25, -.25, 0), (.25, .25, 0), (-.25, .25, 0)], [], [(0, 1, 2, 3)])
        mesh.update()
    probe = bpy.data.objects.new(fixture["name"], mesh)
    probe.location = fixture.get("location", (4.0, 4.0, 0.0))
    scene.collection.objects.link(probe)
    name, identity = probe.name, getattr(probe, "session_uid", None)

    def restore():
        # By identity first: a turn that renamed the probe must not leave it in the scene.
        found = next((obj for obj in bpy.data.objects if identity is not None
                      and getattr(obj, "session_uid", None) == identity), None) or bpy.data.objects.get(name)
        data = found.data if found is not None else None
        if found is not None:
            bpy.data.objects.remove(found)
        if data is not None and not data.users:   # The plate leaves no orphan behind.
            bpy.data.meshes.remove(data)
    return ["fixture:probe_object"], restore


def _object_offset(bpy, scene, fixture):
    """Move an object by ``location`` for the turn: its place is then what the scene holds, not what the chat said."""
    obj = scene.objects.get(fixture["object"])
    if obj is None:
        raise FixtureError("the object %s is missing" % fixture["object"])
    saved = tuple(obj.location)
    obj.location = [value + offset for value, offset in zip(saved, fixture["location"])]

    def restore():
        found = scene.objects.get(fixture["object"])
        if found is not None:
            found.location = saved
    return ["fixture:object_offset"], restore


def _holder(scene, path):
    owner, _, name = path.rpartition(".")
    return (scene.path_resolve(owner) if owner else scene), name


def _scene_values(bpy, scene, fixture):
    saved = {}
    for path, value in fixture["values"].items():
        owner, name = _holder(scene, path)
        saved[path] = getattr(owner, name)
        setattr(owner, name, value)

    def restore():
        for path, value in saved.items():
            owner, name = _holder(scene, path)
            setattr(owner, name, value)
    return ["fixture:scene_values"], restore


def _box_centre(measured):
    low_x, low_y, high_x, high_y = measured["bounds"]
    return (low_x + high_x) / 2, (low_y + high_y) / 2


def _camera_frame(bpy, scene, fixture):
    obj, camera = scene.objects.get(fixture["object"]), scene.camera
    if obj is None or camera is None or camera.type != "CAMERA":
        raise FixtureError("the object or the active camera is missing")
    data = camera.data
    zoom_property = "ortho_scale" if data.type == "ORTHO" else "lens"
    saved = {name: getattr(data, name) for name in ("shift_x", "shift_y", zoom_property)}

    def restore():
        for name, value in saved.items():
            setattr(data, name, value)
    measure = lambda: survey_truth.camera_frame(bpy, scene, obj, camera)
    try:
        data.shift_x = data.shift_y = 0.0
        first = measure()
        if not first["in_front"] or first["fill"] <= 0.0:
            raise FixtureError("the active camera does not face the object")
        # Projected size is proportional to the focal length (and to 1 / the orthographic scale).
        zoom = SPAN / first["fill"]
        setattr(data, zoom_property, saved[zoom_property] / zoom if data.type == "ORTHO" else
                min(5000.0, max(1.0, saved[zoom_property] * zoom)))
        centre = _box_centre(measure())
        # Lens shift moves the image linearly; its slope depends on the sensor fit and the render aspect.
        slopes = []
        for axis, name in enumerate(("shift_x", "shift_y")):
            setattr(data, name, 0.1)
            slopes.append((_box_centre(measure())[axis] - centre[axis]) / 0.1)
            setattr(data, name, 0.0)
        if any(abs(slope) < 1e-6 for slope in slopes):
            raise FixtureError("lens shift does not move this camera's image")
        target_x = 0.5 if fixture["state"] == "inside" else 1.0
        data.shift_x = (target_x - centre[0]) / slopes[0]
        data.shift_y = (0.5 - centre[1]) / slopes[1]
        final = measure()
        wanted = fixture["state"] == "inside"
        if final["inside"] != wanted or abs(final["margin"]) < MARGIN:
            raise FixtureError("the camera could not be set so the object is clearly %s the frame (margin %s)"
                               % ("inside" if wanted else "cut by", final.get("margin")))
    except Exception:
        restore()
        raise
    return ["fixture:camera_frame:" + fixture["state"]], restore


def _world_color(bpy, scene, fixture):
    world = scene.world
    if world is None:
        world = scene.world = bpy.data.worlds.new("World")
    color = tuple(fixture["color"])
    world.color = color
    tree = getattr(world, "node_tree", None)
    background = next((node for node in tree.nodes if node.type == "BACKGROUND"), None) if tree is not None else None
    if background is not None and not background.inputs["Color"].is_linked:
        background.inputs["Color"].default_value = color + (1.0,)
    return ["fixture:world_color"], None


def _orphan_material(bpy, scene, fixture):
    if bpy.data.materials.get(fixture["name"]) is None:
        bpy.data.materials.new(fixture["name"]).use_fake_user = False
    return ["fixture:orphan_material"], None


FIXTURES = {"probe_object": _probe_object, "object_offset": _object_offset, "scene_values": _scene_values,
            "camera_frame": _camera_frame, "world_color": _world_color, "orphan_material": _orphan_material}
TRANSIENT = frozenset({"probe_object", "object_offset", "scene_values", "camera_frame"})


def apply(bpy, scene, fixture):
    """Make one precondition; ``(steps, restore)`` where ``restore`` undoes a transient one, else None."""
    steps, restore = FIXTURES[fixture["kind"]](bpy, scene, fixture)
    bpy.context.view_layer.update()
    return steps, restore
