# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Game physics findings: the mistakes that make an engine import different physics than the person set up.

The settings (``Object.angelo_physics``) and the OMI writer live in the glTF add-on
(``io_scene_gltf2/angelo_physics``); this module only checks them, for the Game Physics panel
(``game_physics_ui``) and for anyone else who asks (``scene_findings``). ``findings`` is pure: it takes plain
records (``records`` reads them from Blender), so host tests run it without Blender. Each finding has the shape of
``web_compat``'s: a code, a severity, the object it is about, and English message and fix templates with named
placeholders, translated only where they are drawn. doc/angelo/game_physics.rst has the contract.

Godot 4.4 to 4.7.2 is the reference for what an engine does; each rule is in its source
(``modules/gltf/extensions/physics``, ``editor/import/3d/resource_importer_scene.cpp``).
"""

import re

from .ui_text import n_

ERROR, WARN, INFO = "error", "warn", "info"
SUFFIXES = ("convcolonly", "colonly", "convcol", "col", "rigid")   # Godot's physics name suffixes, longest first.
MOVING = ("RIGID", "KINEMATIC")
TOLERANCE = 1e-6
# code -> (severity, message, fix)
TEMPLATES = {
    "shape_indirect": (WARN, n_("{subject} is animated inside its body {body}, so Godot gives its shape a separate "
                                "static body"),
                       n_("Parent it directly to {body} without animation of its own, or give it a body")),
    "colonly_empty": (WARN, n_("Godot ignores the {suffix} name suffix on an empty in a glTF file"),
                      n_("Give {subject} a collision shape here and remove the suffix")),
    "suffix_and_physics": (WARN, n_("{subject} has Godot's {suffix} name suffix and game physics, so Godot would "
                                    "build its physics twice"),
                           n_("Remove the suffix from its name")),
    "trimesh_moving": (WARN, n_("{subject} has a Mesh shape in a moving body; engines collide exact meshes reliably "
                                "only when they stay still"),
                       n_("Use Convex Hull")),
    "trimesh_trigger": (WARN, n_("{subject} has a Mesh shape in a trigger; a mesh has no inside, so the trigger can "
                                 "miss what's in it"),
                        n_("Use Convex Hull")),
    "no_mesh": (ERROR, n_("{subject} has no mesh for a Convex Hull or Mesh shape"),
                n_("Use a box, sphere, capsule or cylinder")),
    "flat_shape": (WARN, n_("{subject}'s collision shape has no thickness"),
                   n_("Set its size by hand, or use a Mesh shape")),
    "body_without_shape": (WARN, n_("{subject} is a body with no collision shape, so nothing can touch it"),
                           n_("Choose a shape for it or for one of its children")),
    "scaled_body": (WARN, n_("{subject} is scaled: Godot resets a rigid body's scale and can't stretch a shape "
                             "unevenly"),
                    n_("Apply its scale (Object > Apply > Scale)")),
    "collision_only_without_shape": (WARN, n_("{subject} is collision only but has no shape, so it's exported as a "
                                              "visible mesh"),
                                     n_("Choose a shape")),
    "not_geometry": (WARN, n_("{subject} can't carry game physics; only meshes and empties can"),
                     n_("Put the physics on a mesh or an empty")),
    "suffix_unsupported": (WARN, n_("Godot's name suffixes can't express {subject}'s physics, so it's exported "
                                    "without it"),
                           n_("Use Standard glTF (OMI)")),
    "suffix_approximate": (INFO, n_("Godot's name suffixes build {subject}'s collision from its mesh, not the chosen "
                                    "shape"),
                           n_("Use Standard glTF (OMI) for the exact shape")),
}


def _finding(code, subject, **params):
    severity, message, fix = TEMPLATES[code]
    return {"code": code, "severity": severity, "subject": subject, "message": message, "fix": fix,
            "params": dict(params, subject=subject)}


def name_suffix(name):
    """The Godot physics suffix in an object's name ("-colonly"), as Godot's importer tests it (``_teststr``): after
    a Blender duplicate number and trailing digits, spaces and underscores, the name ends in "-<suffix>" or holds
    "$<suffix>"."""
    stem = re.sub(r"\.\d+$", "", name or "")
    stem = re.sub(r"[\d\s_]+$", "", stem).lower()
    for suffix in SUFFIXES:
        if stem.endswith("-" + suffix) or "$" + suffix in stem:
            return "-" + suffix
    return None


def _flat(record):
    """Whether a primitive shape would have a zero dimension (a plane fitted as a box, a line as a cylinder)."""
    shape = record["shape"]
    if shape not in ("BOX", "SPHERE", "CAPSULE", "CYLINDER"):
        return False
    if record["fit"] == "MANUAL" or record.get("extents") is None:
        if shape == "BOX":
            return min(record["size"]) <= TOLERANCE
        return record["radius"] <= TOLERANCE or (shape == "CYLINDER" and record["height"] <= TOLERANCE)
    extents = sorted(record["extents"])
    if shape == "BOX":
        return extents[0] <= TOLERANCE
    if shape == "SPHERE":
        return extents[2] <= TOLERANCE
    return extents[1] <= TOLERANCE   # A capsule's or cylinder's radius comes from its second-largest extent.


def _needs_own_node(record):
    """Whether the exporter writes this object's shape on a node of its own (its mesh stays visible, or the shape
    sits off the object's origin), so the shape follows the object only while the object doesn't move in its body."""
    fitted = record["shape"] in ("BOX", "SPHERE", "CAPSULE", "CYLINDER") and record.get("fit") == "AUTO"
    return (record["has_geometry"] and (not record["collision_only"] or fitted)) or any(
        abs(value) > TOLERANCE for value in record.get("offset", (0.0, 0.0, 0.0)))


def findings(records, physics_format="OMI"):
    """Findings for ``records`` (``records()``'s dictionaries), errors first."""
    by_name = {record["name"]: record for record in records}
    out = []
    for record in records:
        name, body, shape = record["name"], record["body"], record["shape"]
        marked = body != "NONE" or shape != "NONE"
        suffix = record.get("suffix")
        if suffix and not marked and record["type"] == "EMPTY" and suffix.endswith("colonly"):
            out.append(_finding("colonly_empty", name, suffix=suffix))
        if not marked:
            continue
        if suffix and physics_format == "OMI":
            out.append(_finding("suffix_and_physics", name, suffix=suffix))
        if record["type"] not in ("MESH", "CURVE", "SURFACE", "FONT", "META", "EMPTY"):
            out.append(_finding("not_geometry", name))
            continue
        if physics_format == "GODOT_SUFFIXES":
            note = record.get("suffix_note")
            if note in ("suffix_empty", "suffix_body"):
                out.append(_finding("suffix_unsupported", name))
            elif note in ("suffix_hull", "suffix_mesh"):
                out.append(_finding("suffix_approximate", name))
            continue
        owner, path = _owner(record, by_name)
        if shape in ("CONVEX", "TRIMESH") and not record["has_geometry"]:
            out.append(_finding("no_mesh", name))
        elif _flat(record):
            out.append(_finding("flat_shape", name))
        if shape == "TRIMESH" and owner is not None:
            if owner["body"] in MOVING:
                out.append(_finding("trimesh_moving", name))
            elif owner["body"] == "TRIGGER":
                out.append(_finding("trimesh_trigger", name))
        if shape != "NONE" and owner is not None and owner is not record and (
                len(path) > 1 or _needs_own_node(record)) and any(item["animated"] for item in path):
            out.append(_finding("shape_indirect", name, body=owner["name"]))
        if record["collision_only"] and shape == "NONE":
            out.append(_finding("collision_only_without_shape", name))
        if body != "NONE":
            if not _has_shape(record, records, by_name):
                out.append(_finding("body_without_shape", name))
            scale = record["scale"]
            uneven = max(scale) - min(scale) > 1e-4
            if uneven or (body == "RIGID" and any(abs(value - 1.0) > 1e-4 for value in scale)):
                out.append(_finding("scaled_body", name))
    order = {ERROR: 0, WARN: 1, INFO: 2}
    return sorted(out, key=lambda item: (order[item["severity"]], item["code"], item["subject"]))


def _owner(record, by_name):
    """(nearest body record at or above ``record``, the records from just below it down to ``record``)."""
    chain = []
    current = record
    while current is not None:
        if current["body"] != "NONE":
            return current, list(reversed(chain))
        chain.append(current)
        current = by_name.get(current.get("parent"))
    return None, []


def _has_shape(body_record, records, by_name):
    if body_record["shape"] != "NONE":
        return True
    for record in records:
        if record is body_record or record["shape"] == "NONE" or record["body"] != "NONE":
            continue
        owner, _path = _owner(record, by_name)
        if owner is body_record:
            return True
    return False


def english(finding):
    """The finding as an agent or a log reads it: message and fix filled in, in English."""
    params = finding["params"]
    return {"code": finding["code"], "severity": finding["severity"], "subject": finding["subject"],
            "message": finding["message"].format(**params), "fix": finding["fix"].format(**params)}


# --- Reading Blender (main thread).

# An object's own transform, as the exporter writes it on its node (TRS channels).
TRANSFORM_PATHS = frozenset({"location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale",
                             "delta_location", "delta_rotation_euler", "delta_rotation_quaternion", "delta_scale"})


def _has_animation(obj):
    data = obj.animation_data
    return data is not None and (data.action is not None or len(data.nla_tracks) > 0 or len(data.drivers) > 0)


def _animated_paths(obj):
    """The data paths animated on ``obj`` itself: its action's slot, its NLA strips' slots and its drivers."""
    data = obj.animation_data
    if data is None:
        return set()
    from bpy_extras import anim_utils
    pairs = [(data.action, data.action_slot)] + [(strip.action, strip.action_slot) for track in data.nla_tracks
                                                 for strip in track.strips]
    paths = {curve.data_path for curve in data.drivers}
    for action, slot in pairs:
        bag = anim_utils.action_get_channelbag_for_slot(action, slot) if action is not None and slot else None
        paths.update(curve.data_path for curve in (bag.fcurves if bag is not None else ()))
    return paths


def moves(own_paths, parent_type, parent_animated):
    """Whether an object moves inside its parent: its own transform is animated, or it follows a bone of an animated
    armature. An armature's posed bones move what they deform without moving the deformed object, and the exporter
    keeps such a mesh's node still (its skin does the moving), so a skinned mesh's shape is hoisted like any other."""
    return bool(own_paths & TRANSFORM_PATHS) or (parent_type == "BONE" and parent_animated)


_TRANSFORMS = {}   # Object.session_uid -> (key, its animated transform paths): a findings draw reads, not scans.


def _transform_paths(obj):
    """The transform paths animated on ``obj`` itself. Scanning reads every F-curve of every action it has (a
    character can carry a hundred), so the answer is kept until an Action changes (``fitting.animation`` counts
    those, and loads, undos and redos), or the object's action, slot, number of curves in it, NLA tracks or drivers
    change (a key inserted by a script before anything is evaluated)."""
    data = obj.animation_data
    if data is None:
        return frozenset()
    from bpy_extras import anim_utils
    from io_scene_gltf2.angelo_physics import fitting
    slot = data.action_slot
    bag = anim_utils.action_get_channelbag_for_slot(data.action, slot) if data.action is not None and slot else None
    key = (fitting.animation[0], data.action.as_pointer() if data.action else None, slot.handle if slot else None,
           len(bag.fcurves) if bag is not None else 0, len(data.nla_tracks), len(data.drivers))
    cached = _TRANSFORMS.get(obj.session_uid)
    if cached is None or cached[0] != key:
        cached = (key, frozenset(_animated_paths(obj) & TRANSFORM_PATHS))
        _TRANSFORMS[obj.session_uid] = cached
    return cached[1]


def _animated(obj):
    parent = obj.parent
    return moves(_transform_paths(obj), obj.parent_type,
                 parent is not None and parent.type == "ARMATURE" and _has_animation(parent))


def records(objects):
    """Plain records of the objects a check needs: those with physics or a Godot suffix, and every object above one
    (a body may be an unmarked object's ancestor). Reading never creates an object's physics storage."""
    from io_scene_gltf2.angelo_physics import fitting, props, shapes
    from io_scene_gltf2.angelo_physics.godot_suffixes import godot_suffix
    rows, seen = [], set()

    def add(obj):
        if obj.name in seen:
            return
        seen.add(obj.name)
        marked = props.marked(obj)
        settings = obj.angelo_physics if marked else None
        row = {"name": obj.name, "type": obj.type, "parent": obj.parent.name if obj.parent else None,
               "body": settings.body if marked else "NONE", "shape": settings.shape if marked else "NONE",
               "suffix": name_suffix(obj.name), "animated": _animated(obj), "scale": tuple(obj.scale),
               "has_geometry": obj.type in ("MESH", "CURVE", "SURFACE", "FONT", "META"),
               "collision_only": bool(marked and settings.collision_only)}
        if marked:
            row.update(fit=settings.fit, size=tuple(settings.size), radius=settings.radius, height=settings.height,
                       offset=tuple(settings.offset) if settings.fit == "MANUAL" else (0.0, 0.0, 0.0))
            if row["has_geometry"] and settings.fit == "AUTO":   # Measured as the export fits it, never in a draw.
                minimum, maximum = fitting.cached_bounds(obj) or shapes.bounds(obj.bound_box)
                row["extents"] = tuple(high - low for low, high in zip(minimum, maximum))
            row["suffix_note"] = godot_suffix(row["body"], row["shape"], row["has_geometry"],
                                              row["collision_only"])[1]
        rows.append(row)
        if obj.parent is not None:
            add(obj.parent)

    for obj in objects:
        if props.marked(obj) or name_suffix(obj.name):
            add(obj)
    return rows


def scene_findings(scene, objects=None):
    """Findings for ``objects`` (default: the scene's), in the scene's physics format."""
    from io_scene_gltf2.angelo_physics import props
    return findings(records(scene.objects if objects is None else objects), props.scene_format(scene))
