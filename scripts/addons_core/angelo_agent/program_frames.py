# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether an object's evaluated state can depend on the current frame.

A program staged at one frame stays valid at another only when nothing it
binds is animated or time-dependent; these helpers decide that conservatively."""

# Modifier types whose result reads only their settings, the owner and the Objects they point at, never the scene
# time or another ID (a texture, a collection, a cache). Anything else (Wave, Build, Ocean, Displace, Boolean,
# simulations...) may, so its object is not rebound. Each Object such a modifier points at must itself be still
# (object_dependencies): a Mirror across an animated empty moves.
FRAME_INDEPENDENT_MODIFIERS = frozenset({
    "BEVEL", "SUBSURF", "SOLIDIFY", "MIRROR", "ARRAY", "WELD", "TRIANGULATE", "WEIGHTED_NORMAL", "EDGE_SPLIT",
    "DECIMATE", "REMESH", "MULTIRES", "CURVE", "LATTICE", "HOOK", "SIMPLE_DEFORM", "CAST", "SMOOTH",
    "LAPLACIANSMOOTH", "CORRECTIVE_SMOOTH", "SHRINKWRAP", "WIREFRAME", "SCREW", "SKIN", "ARMATURE", "MESH_DEFORM",
    "SURFACE_DEFORM"})


FRAME_INDEPENDENT_TYPES = frozenset({"MESH", "EMPTY", "CAMERA", "LIGHT"})


# Geometry nodes that read the time, the camera, images or other objects. A
# graph holding any of them (or any keyed/driven value) is treated as time-dependent.
TIME_DEPENDENT_NODES = frozenset({
    "GeometryNodeInputSceneTime", "GeometryNodeSimulationInput", "GeometryNodeSimulationOutput", "GeometryNodeBake",
    "GeometryNodeObjectInfo", "GeometryNodeCollectionInfo", "GeometryNodeSelfObject", "GeometryNodeInputActiveCamera",
    "GeometryNodeImageTexture", "GeometryNodeInputImage", "GeometryNodeImageInfo", "GeometryNodeTool3DCursor"})


MAX_FRAME_SCAN_NODES = 512


def _animated(block):
    animation = getattr(block, "animation_data", None) if block is not None else None
    return animation is not None and (animation.action is not None or len(animation.drivers) > 0
                                      or len(animation.nla_tracks) > 0)


def _graph_frame_independent(tree, seen, budget):
    """No time-reading node and no keyed or driven value anywhere in a node group (bounded)."""
    if tree is None or tree in seen:
        return tree is not None
    seen.add(tree)
    if _animated(tree):
        return False
    for node in tree.nodes:
        budget[0] -= 1
        if budget[0] < 0 or node.bl_idname in TIME_DEPENDENT_NODES:
            return False
        group = getattr(node, "node_tree", None)
        if group is not None and not _graph_frame_independent(group, seen, budget):
            return False
    return True


def frame_independent(obj):
    """Conservatively, whether nothing about ``obj`` can change with the current frame (its shape included)."""
    from .object_dependencies import SHAPE, walk
    if obj.type not in FRAME_INDEPENDENT_TYPES or obj.parent is not None or len(obj.constraints):
        return False
    if getattr(obj, "rigid_body", None) is not None:
        return False
    return not walk(obj, MAX_PLACEMENT_OBJECTS, roles=(SHAPE,)).time


# Constraints whose result follows only the owner's channels and their targets' placement (a bone subtarget: the
# armature's pose). Any other type (Follow Path, Shrinkwrap, Clamp To, the motion-tracking solvers, a Transform
# Cache...) reads a target's shape or the time itself.
PLACEMENT_CONSTRAINTS = frozenset({
    "COPY_LOCATION", "COPY_ROTATION", "COPY_SCALE", "COPY_TRANSFORMS", "TRACK_TO", "DAMPED_TRACK", "LOCKED_TRACK",
    "CHILD_OF", "LIMIT_DISTANCE", "LIMIT_LOCATION", "LIMIT_ROTATION", "LIMIT_SCALE", "MAINTAIN_VOLUME", "TRANSFORM",
    "PIVOT", "STRETCH_TO", "FLOOR", "ACTION", "ARMATURE"})
MAX_PLACEMENT_OBJECTS = 64


def placement_moves_with_time(obj):
    """Conservatively, whether ``obj``'s world position can change with the current frame (an orbit's camera: its
    rotation alone never moves it, so its rotation-only constraints and rotation channels are left out).

    Only what places it counts (``object_dependencies.walk``): its own
    animation, drivers and physics, and those of everything its parent chain
    and constraints read, a vertex, bone or path parent's shape and a
    subtarget's included. A parent or a Track To on objects nothing animates
    leaves it in place (otherwise such a camera's
    orbits were refused on every frame change). Any other constraint type, or
    more than ``MAX_PLACEMENT_OBJECTS`` objects, counts as moving.
    """
    from .object_dependencies import walk
    return walk(obj, MAX_PLACEMENT_OBJECTS, position_only=True).time


def writes_frame(program):
    """Whether a canonical program sets the scene's current frame (a ``property_set`` of ``frame_current``)."""
    return any(operation["kind"] == "property_set" and "frame_current" in (operation["parameters"].get("values") or {})
               for operation in program["operations"])


# The scene settings whose RNA update sends Blender's frame-change notifier (``NC_SCENE | ND_FRAME``): after a commit
# that writes one, Blender runs its whole frame update (``ED_update_for_newframe``: camera markers, frame-change
# handlers, a time-source evaluation), as it does for a new frame. ``test_native_camera_guards`` reads them from
# ``rna_scene.cc`` (without the full set, orbits were committed beside a preview-range or time-map write).
FRAME_UPDATE_PATHS = frozenset({
    "frame_current", "frame_subframe", "frame_float", "frame_step", "lock_frame_selection_to_range", "allow_preroll",
    "use_preview_range", "frame_preview_start", "frame_preview_end", "show_subframe", "show_keys_from_selected_only",
    "render.frame_map_old", "render.frame_map_new"})
FRAME_PATHS = frozenset({"frame_current", "frame_subframe", "frame_float"})  # The frame itself.


def frame_updates(program):
    """The settings of ``FRAME_UPDATE_PATHS`` a canonical program writes, in order (empty: none)."""
    return [path for operation in program["operations"] if operation["kind"] == "property_set"
            for path in (operation["parameters"].get("values") or {}) if path in FRAME_UPDATE_PATHS]


_ROTATION = ("rotation_euler", "rotation_quaternion", "rotation_axis_angle")
_TRANSFORM = ("location",) + _ROTATION + ("scale", "delta_location", "delta_rotation_euler",
                                          "delta_rotation_quaternion", "delta_scale")


def _quoted(name):
    from .bone_state import quoted
    return quoted(name)


def _setting_paths(kind, params, current_mode=None):
    """The RNA paths, relative to the object, that a configure kind writes (as an F-curve or driver names them)."""
    if kind in ("camera_configure", "light_configure"):
        from .action_camera_light_spec import CAMERA_RNA, LIGHT_RNA, data_fields
        names = CAMERA_RNA if kind == "camera_configure" else LIGHT_RNA
        return (_ROTATION if params.get("look_at") is not None else ()) + tuple(
            "data." + names[field] for field in data_fields(kind, params))
    if kind == "constraint_configure":
        prefix = "constraints[%s]." % _quoted(params["constraint"])
        return tuple(prefix + path for path in (["influence"] if params["influence"] is not None else [])
                     + list(params["settings"]))
    if kind == "modifier_configure":
        from .action_schema_types import ADAPTIVE_SETTINGS
        from .action_schemas import MODIFIER_RNA_NAMES, also_writes
        kind_of, renamed, names = params["type"], MODIFIER_RNA_NAMES.get(params["type"], {}), []
        for key, value in params["settings"].items():
            if key == "adaptive" and kind_of == "SUBSURF":
                names += [ADAPTIVE_SETTINGS[field] for field, item in (value or {}).items() if item is not None]
            elif renamed.get(key, key) is not None and not (key == "mirror_object" and value is None):
                names.append(renamed.get(key, key))
        prefix = "modifiers[%s]." % _quoted(params["modifier"])
        return tuple(prefix + name for name in names + list(also_writes(kind_of, params["settings"])))
    if kind == "bone_set":  # The channels the kernel checks and writes (action_bones_spec.pose_paths).
        from .action_bones_spec import pose_paths
        pose, rest = "pose.bones[%s]." % _quoted(params["bone"]), "data.bones[%s]." % _quoted(params["bone"])
        return (tuple(pose + name for name in pose_paths(params, current_mode))
                + tuple(rest + name for name in params["settings"]))
    return ()


def unkeyed_writes(operation, current_mode=None):
    """``(reference, path)`` pairs a canonical operation sets without a key, where an F-curve or driver could hold them.

    Paths are relative to the object (``data.`` and ``active_material.``
    reach those IDs, as ``property_set`` writes them), and a configure kind's
    are the F-curve paths of what it writes (``constraints["name"].influence``,
    ``modifiers["name"].width``, ``pose.bones["name"].location``, a camera's or
    light's ``data.lens``; without them these were refused only at
    staging). Kinds that key or
    restructure animation, and kinds whose values no channel holds (a rename,
    material slots, topology, modifiers added or applied, an object's view-layer
    ``hidden``), give none: staging
    evaluates the program at its frame, and its checks refuse anything else an
    animation decides. ``current_mode(reference, bone)``: a bone's rotation
    mode now, which decides whether a mode change rewrites a channel
    (``action_bones_spec.pose_paths``; unknown: it does).
    """
    from .batch_spec import moved
    kind, params, target = operation["kind"], operation["parameters"], operation["target"]
    if kind == "property_set":
        paths = tuple(params["values"])  # An object's rotation_mode is the transform kinds' (NOT_ALLOWED here).
    elif kind == "transform":
        paths = (("location",) * (params["location"] is not None) + _ROTATION * (params["rotation"] is not None)
                 + ("scale",) * (params["scale"] is not None))
    elif kind == "transform_apply":
        paths = ("location",) * params["location"] + _ROTATION * params["rotation"] + ("scale",) * params["scale"]
    elif kind == "origin_set":
        paths = ("location",)
    elif kind == "parent_set":
        # Only unparenting that keeps the world transform writes channels (action_object.parent_set): a new parent
        # keeps it through the parent inverse.
        paths = ("location",) + _ROTATION + ("scale",) if params["keep_transform"] and params["parent"] is None else ()
    elif kind in ("camera_configure", "light_configure", "constraint_configure", "modifier_configure", "bone_set"):
        paths = _setting_paths(kind, params, current_mode(target, params["bone"])
                               if kind == "bone_set" and callable(current_mode) else None)
    else:
        return [(source, path) for source in moved(operation) for path in _TRANSFORM]
    return [(target, path) for path in paths]


def animated_path(block, path):
    """Whether an Action, driver or NLA track holds ``path`` of ``block`` (an object or a scene): the check
    staging applies (``validation_properties._animated``), ``data.``, ``active_material.`` and ``world.`` included."""
    from .validation_properties import _animated as staged_check
    return staged_check(block, path)
