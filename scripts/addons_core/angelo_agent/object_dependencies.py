# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Everything that can move or reshape an object, and whether any of it reads the current frame (no bpy import).

One walker serves both questions the host asks about an object it measured:
which other objects a program must leave alone for the measurement to hold
(``program_orbits.followed_objects``), and whether the measurement holds at
another frame (``program_frames.placement_moves_with_time`` and
``frame_independent``).

An object is reached in one of two roles:

* placement, its world transform: its own channels, its parent's placement,
  and every Object its active constraints point at;
* shape, its evaluated geometry or pose: its data, shape keys, particle
  systems, its modifiers and every Object they point at, the Objects its data
  points at (a curve's bevel or taper), and its pose bones' constraints.

A parent is read for its shape too when the child hangs from its vertices,
bones or path; a constraint target is, when a subtarget names a bone or
vertex group in it. The Objects a constraint, modifier or data block points
at are found from Blender's RNA, never from a list of attribute names: every
pointer property whose type is Object, in it or in a struct nested in it
(the items of an Armature constraint's targets, a Geometry Nodes modifier's
inputs). A modifier counts as frame-independent
only when its type never reads the time and every Object it points at is
reached and found still. Without this, orbits were committed
measured from cameras placed by a constraint's custom space object, a
Mirror or Array modifier's object on a vertex parent and a vertex group of
such a mesh, all of which an attribute list had missed.

What a driver variable's path reads is read step by step in Blender's path
grammar (``dependency_paths``). For an orbit's camera the walk is a
prediction that refuses early; the guarantee is commit reading the camera again
(``camera_guards``).
"""

from .program_frames import (FRAME_INDEPENDENT_MODIFIERS, MAX_FRAME_SCAN_NODES, PLACEMENT_CONSTRAINTS, _animated,
                             _graph_frame_independent)
from .dependency_paths import (INCOMPLETE, overlaps, path_root, read_ids, reads_matrix,  # noqa: F401 (path_root)
                               time_setting, variable_roles)

PLACEMENT, SHAPE = "placement", "shape"
# A pose bone's constraint may also solve a chain to still targets.
POSE_CONSTRAINTS = PLACEMENT_CONSTRAINTS | {"IK"}


class Dependencies:
    """``objects``: every other object reached, once, in walk order; ``scenes``: ``(scene, path root)`` a driver
    reads (``*``: any of its settings);
    ``time``: something reached reads the frame (or the walk was cut short); ``complete``: the walk reached
    everything within its limit."""

    __slots__ = ("objects", "scenes", "time", "complete")

    def __init__(self):
        self.objects, self.scenes, self.time, self.complete = [], [], False, True


def _key(obj):
    pointer = getattr(obj, "as_pointer", None)
    return pointer() if callable(pointer) else id(obj)


def _is_object(value):
    return getattr(getattr(value, "bl_rna", None), "identifier", None) == "Object"


def _properties(struct):
    return getattr(getattr(struct, "bl_rna", None), "properties", ())


def _structs(struct, collections=True, depth=4):
    """``struct`` and the structs nested in it that are not IDs: its pointer properties' values (a Geometry Nodes
    modifier's inputs sit three levels down) and, with ``collections``, its collections' items (an Armature
    constraint's targets). A data block's collections are its geometry, so it is read without them."""
    yield struct
    if depth <= 0:
        return
    for prop in _properties(struct):
        if prop.identifier == "rna_type" or prop.type not in ("POINTER", "COLLECTION"):
            continue
        if prop.type == "COLLECTION" and not collections:
            continue
        value = getattr(struct, prop.identifier, None)
        items = list(value or ()) if prop.type == "COLLECTION" else [value]
        for item in items:
            if item is not None and getattr(item, "id_data", None) is not item:  # IDs are walked as objects.
                yield from _structs(item, collections, depth - 1)


def object_pointers(struct, collections=True):
    """The Objects ``struct`` points at, from its RNA: every pointer property typed Object or Collection (a
    Collection by its objects) in it or in a struct nested in it (``_structs``)."""
    found = []
    for item in _structs(struct, collections):
        for prop in _properties(item):
            kind = getattr(getattr(prop, "fixed_type", None), "identifier", None) if prop.type == "POINTER" else None
            value = getattr(item, prop.identifier, None) if kind in ("Object", "Collection") else None
            if value is not None:
                found.extend(list(getattr(value, "all_objects", ())) if kind == "Collection" else [value])
    return found


def _reads_subtarget(struct):
    """Whether a constraint names a bone or vertex group inside an object it points at (RNA ``*subtarget``)."""
    return any(prop.type == "STRING" and prop.identifier.endswith("subtarget") and getattr(item, prop.identifier, "")
               for item in _structs(struct) for prop in _properties(item))


# An orbit reads the measured camera's position only (action_camera_light_spec.orbit_location), and these change
# only its rotation: their targets and its rotation channels cannot move it, unless another of its constraints
# turns rotation into position (a Pivot), so they are left out only when every active one is of these types.
ROTATION_ONLY_CONSTRAINTS = frozenset({"TRACK_TO", "DAMPED_TRACK", "LOCKED_TRACK", "COPY_ROTATION", "LIMIT_ROTATION"})
ROTATION_PATHS = ("rotation_euler", "rotation_quaternion", "rotation_axis_angle", "rotation_mode",
                  "delta_rotation_euler", "delta_rotation_quaternion")


def _static_driver(driver):
    """Whether a driver's value can change only through what its variables read (never the frame or Python)."""
    import re
    return not (driver.type == "SCRIPTED" and (not getattr(driver, "is_simple_expression", False)
                                               or re.search(r"\bframe\b", driver.expression or "")))


def _curve_paths(animation):
    from .bone_state import animated_paths
    return animated_paths(NS(animation_data=animation)) if animation is not None else set()


class NS:  # A stand-in owner for bone_state.animated_paths.
    def __init__(self, **fields):
        self.__dict__.update(fields)


MAX_OWNER_IDS = 4096  # IDs a walk may visit finding the objects and scenes that use a driven block.
MAX_POINTER_NODES = 4096  # Geometry Nodes nodes a walk reads for Object and Collection pointers.


def _kind(block):
    return getattr(getattr(block, "bl_rna", None), "identifier", None)


def _scene_root(scene, block):
    """The scene setting that points at ``block`` (``world``), else ``*``."""
    for prop in _properties(scene):
        if prop.type == "POINTER" and prop.identifier != "rna_type":
            value = getattr(scene, prop.identifier, None)
            if value is not None and _key(value) == _key(block):
                return prop.identifier
    return "*"


def _direct_pointers(struct):
    """The Objects ``struct``'s own pointer properties name (a Collection by its objects), nothing nested."""
    found = []
    for prop in _properties(struct):
        kind = getattr(getattr(prop, "fixed_type", None), "identifier", None) if prop.type == "POINTER" else None
        value = getattr(struct, prop.identifier, None) if kind in ("Object", "Collection") else None
        if value is not None:
            found.extend(list(getattr(value, "all_objects", ())) if kind == "Collection" else [value])
    return found


def walk(obj, limit, roles=(PLACEMENT,), position_only=False):
    """The ``Dependencies`` of ``obj`` in ``roles``; more than ``limit`` objects ends the walk incomplete.

    ``position_only``: only ``obj``'s world position matters (an orbit's camera), not its rotation.
    """
    found, seen, order, queue = Dependencies(), set(), set(), [(obj, role) for role in roles]
    budget, graphs, scanned, blocks, owned = [MAX_FRAME_SCAN_NODES], set(), set(), set(), {}
    read_paths, users_of = set(), []  # (scene, path) read once; Blender's user map, built once (owners).
    nodes, itself, rotation_ignored = [0], [False], [False]

    def reach(item, *wanted, reads_matrix=True):
        if item is None:
            return
        if item is obj and reads_matrix:
            itself[0] = True  # Something it depends on reads its matrix back: its rotation may place it after all.
        if _key(item) not in order and item is not obj:
            order.add(_key(item))
            found.objects.append(item)
            if len(found.objects) > limit:
                found.time, found.complete = True, False
        queue.extend((item, role) for role in wanted)

    def follow_scene(scene, path):
        """A scene setting a driver reads (``path``, scene-relative; ``*``: any): followed by its first step; the
        frame, or what cannot be named, is time, and so are keys and drivers on what it reads."""
        root = path_root(path)
        if root == "camera" and any(marker.camera is not None for marker in getattr(scene, "timeline_markers", ())):
            found.time = True  # Camera markers switch the scene camera with the frame.
        found.time |= time_setting(path)
        if all(_key(scene) != _key(seen_scene) or seen_root != root for seen_scene, seen_root in found.scenes):
            found.scenes.append((scene, root))
        if (_key(scene), path) in read_paths:
            return
        read_paths.add((_key(scene), path))
        data = getattr(scene, "animation_data", None)
        if data is not None and found.complete:
            keyed = _curve_paths(NS(action=data.action, drivers=[], nla_tracks=data.nla_tracks))
            found.time |= any(overlaps(item, path) for item in keyed)
            for curve in data.drivers:
                if overlaps(curve.data_path, path):
                    found.time |= not _static_driver(curve.driver)
                    for variable in curve.driver.variables:
                        for target in variable.targets:
                            variable_target(variable, target)

    def owners(block):
        """The objects and scenes that use a data block, by Blender's user map (built once a walk)."""
        if _key(block) in owned:
            return owned[_key(block)]
        objects, scenes = [], []
        if not users_of:
            try:
                import bpy
                users_of.append(bpy.data.user_map())
            except (ImportError, AttributeError):  # Off-host (host tests): no user map, so no owners.
                users_of.append({})
        level, visited = [block], {_key(block)}
        while level and found.complete and users_of[0]:
            following = []
            for item in level:
                for user in users_of[0].get(item, ()):
                    kind = _kind(user)
                    if kind == "Scene":  # Its own users are windows and workspaces, never what it shows.
                        scenes.append((user, _scene_root(user, item)))
                        continue
                    if _key(user) in visited:
                        continue
                    visited.add(_key(user))
                    if kind == "Object":
                        objects.append(user)
                    else:
                        following.append(user)
            if len(visited) > MAX_OWNER_IDS:
                found.time, found.complete = True, False  # Never a short list that reads as complete.
                break
            level = following
        owned[_key(block)] = objects, scenes
        return objects, scenes

    def data_block(block):
        """A driven non-object ID: its animation (embedded node tree included) and what uses it. An embedded ID
        (a material's tree) is reached through its owner, which the path crossed first."""
        animation(block)
        if getattr(block, "is_embedded_data", False) or not found.complete:
            return
        objects, scenes = owners(block)
        for owner in objects:
            reach(owner, PLACEMENT, SHAPE, reads_matrix=False)  # It reads the block, not the owner's matrix.
        for scene, root in scenes:
            follow_scene(scene, root)

    def animation(block, ignored=()):
        """A block's keys mark time; its drivers' targets are reached, and a driver that reads the frame marks it.
        A driver variable reads its target ID, or another its path leads to (``active_material.``, an embedded
        node tree): that ID's own animation counts, and the objects and scenes that use it are reached."""
        if block is None or _key(block) in blocks or not found.complete:
            return
        blocks.add(_key(block))
        data = getattr(block, "animation_data", None)
        if data is not None:
            keyed = _curve_paths(NS(action=data.action, drivers=[], nla_tracks=data.nla_tracks))
            found.time |= any(not (ignored and path.startswith(ignored)) for path in keyed)
            if ignored and any(path.startswith(ignored) for path in keyed):
                rotation_ignored[0] = True
            for curve in data.drivers:
                if ignored and curve.data_path.startswith(ignored):
                    rotation_ignored[0] = True
                    continue
                found.time |= not _static_driver(curve.driver)
                for variable in curve.driver.variables:
                    for target in variable.targets:
                        variable_target(variable, target)
        tree = getattr(block, "node_tree", None)  # A material's, world's or light's own tree: keys of its own.
        if tree is not None and _kind(block) != "Object":
            embedded(tree)

    def embedded(tree):
        """A node tree's keys and drivers, and those of the groups nested in it."""
        animation(tree)
        for node in getattr(tree, "nodes", ()):
            nodes[0] += 1
            if nodes[0] > MAX_POINTER_NODES:
                found.time, found.complete = True, False
                return
            group = getattr(node, "node_tree", None)
            if group is not None and _key(group) not in blocks:
                embedded(group)

    def variable_target(variable, target):
        """What one driver target reads: every ID its path crosses, in order (``dependency_paths.read_ids``). A
        context property is read from each scene the object is in, or each of their view layers."""
        if not found.complete:
            return
        path = getattr(target, "data_path", "") or ""
        if variable.type == "CONTEXT_PROP":
            for scene in list(getattr(obj, "users_scene", ())):
                if getattr(target, "context_property", "") == "ACTIVE_SCENE":
                    crossing(variable, target, scene, path, path)
                    continue
                for layer in getattr(scene, "view_layers", ()):
                    crossing(variable, target, layer, path, layer.path_from_id() + "." + path if path else "*")
            return
        other = getattr(target, "id", None)
        if other is not None:
            crossing(variable, target, other, path if variable.type == "SINGLE_PROP" else "", path)

    def crossing(variable, target, base, path, scene_path):
        read = read_ids(base, path)
        if read is INCOMPLETE:
            found.time, found.complete = True, False  # A path that resolves but cannot be walked proves nothing.
            return
        if read is None:  # A path that resolves to nothing: the driver reads nothing now, and proves nothing.
            found.time = True
            read = read_ids(base, "")
        for index, block in enumerate(read.blocks):
            kind = _kind(block)
            if kind == "Object":  # The target by what the variable reads; one crossed on the way, fully.
                matrix = reads_matrix(variable, target, read) if index == 0 else True
                roles = variable_roles(variable, target, path) if index == 0 and read.within else (PLACEMENT, SHAPE)
                reach(block, *roles, reads_matrix=matrix)
                animation(block)  # Its keys and drivers decide what the variable reads.
            elif kind == "Scene":
                follow_scene(block, scene_path if index == 0 and variable.type in ("SINGLE_PROP", "CONTEXT_PROP")
                             else "*")
            else:
                data_block(block)

    def constraint(item, allowed):
        if getattr(item, "mute", False) or getattr(item, "influence", 1.0) == 0.0:
            return  # Keyed or driven mute and influence are the owner's animation, found already.
        known = item.type in allowed
        found.time |= not known  # Follow Path, Shrinkwrap, the trackers...: a shape or the time itself.
        shape = not known or _reads_subtarget(item)
        for target in object_pointers(item):
            reach(target, PLACEMENT, *((SHAPE,) if shape else ()))

    def graph(tree):
        """A Geometry Nodes group and the groups nested in it: their time and every Object they point at."""
        if tree is None or tree in scanned:
            return
        scanned.add(tree)
        if tree not in graphs:  # One scan per walk, however many modifiers share the group.
            found.time |= not _graph_frame_independent(tree, graphs, budget)
        animation(tree)
        for node in getattr(tree, "nodes", ()):
            nodes[0] += 1
            if nodes[0] > MAX_POINTER_NODES:
                found.time, found.complete = True, False  # Never a short list that reads as complete.
                return
            for target in object_pointers(node):
                reach(target, PLACEMENT, SHAPE)
            graph(getattr(node, "node_tree", None))

    while queue and found.complete:
        item, role = queue.pop()
        if (_key(item), role) in seen:
            continue  # A cycle adds no time of its own.
        seen.add((_key(item), role))
        rotation_free = position_only and item is obj and role == PLACEMENT and all(
            entry.type in ROTATION_ONLY_CONSTRAINTS for entry in getattr(item, "constraints", ())
            if not getattr(entry, "mute", False) and getattr(entry, "influence", 1.0) != 0.0)
        animation(item, ROTATION_PATHS if rotation_free else ())
        found.time |= getattr(item, "rigid_body", None) is not None
        if role == PLACEMENT:
            parent = getattr(item, "parent", None)
            if parent is not None:
                hangs = getattr(item, "parent_type", "OBJECT") != "OBJECT" or parent.type in ("CURVE", "LATTICE")
                reach(parent, PLACEMENT, *((SHAPE,) if hangs else ()))
            for entry in getattr(item, "constraints", ()):
                if not rotation_free:
                    constraint(entry, PLACEMENT_CONSTRAINTS)
            continue
        data = getattr(item, "data", None)
        animation(data)
        animation(getattr(data, "shape_keys", None))
        found.time |= bool(len(getattr(item, "particle_systems", ())))
        for modifier in getattr(item, "modifiers", ()):
            if modifier.type == "NODES":
                graph(modifier.node_group)
            else:
                found.time |= modifier.type not in FRAME_INDEPENDENT_MODIFIERS
            for target in object_pointers(modifier):
                reach(target, PLACEMENT, SHAPE)  # Its placement relative to the owner and its own shape both count.
        for target in object_pointers(data, collections=False) if data is not None else ():
            reach(target, PLACEMENT, SHAPE)
        for target in _direct_pointers(item):  # An instanced collection: its own pointers, never nested settings.
            reach(target, PLACEMENT, SHAPE)
        pose = getattr(item, "pose", None)
        for bone in getattr(pose, "bones", ()) if pose is not None else ():
            for entry in bone.constraints:
                constraint(entry, POSE_CONSTRAINTS)
    if position_only and itself[0] and (rotation_ignored[0] or any(
            entry.type in ROTATION_ONLY_CONSTRAINTS for entry in getattr(obj, "constraints", ()))):
        return walk(obj, limit, roles, position_only=False)  # Its rotation is read back: it can place it.
    return found
