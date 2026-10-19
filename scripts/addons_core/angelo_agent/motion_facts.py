# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where an edit's animated objects are at a few frames, as Blender evaluates them: their geometry, not only origins.

Recorded: 16 events and 5 final misses of the bridge brief (5 of 105
trials, mostly the car) built an animated hierarchy in the wrong frame of reference: a parent inverse taken from a
stale matrix, car-local vertices placed as world ones, parts never parented. In all 5 the animated origin was
exactly right and the geometry was not, and 30 of the 31 bridge trials that wrote a check measured the origin.
So after a script or program that adds or changes an object that animates (``motion_scope``: keys, drivers or
NLA on it, its data or shape keys, a constraint or modifier that reads time or follows something animated, a
parent that moves), the result states (``after_edit``, as ``motion``):

* ``frames``: the scene's first, middle and last frame and the edited objects' own key frames between them, at
  most ``MAX_FRAMES`` (evenly chosen among the keys);
* ``moving``: per hierarchy that holds an edited object and moves (its top object's origin or its geometry box
  changes by more than ``MOVES`` of its size between those frames), at most ``MAX_GROUPS``: the top object (the
  highest of the chain that moves: ``Scope.top``) by handle and name, its parts (names when at most
  ``MAX_PARTS``, else a count), the top's world origin and the world box of the evaluated rendering geometry of
  the whole hierarchy (``object_bounds``) at each frame, the instances its parts make included (Blender's
  instance list, at most ``MAX_INSTANCES``; past them ``instances`` says so and the box is null: a box of some
  instances would read as the whole). Parts are the objects of the hierarchy
  that render (a rendering collection of the view layer, not hidden for render) and are no boolean operand;
  rendering parts the view layer does not evaluate are named in ``parts_not_evaluated``. A group is chosen by the
  edit's changes, never by names;
* ``apart``: parts whose own motion takes their geometry more than ``APART`` of their size from where the
  bone, object, vertices or path they are parented to carries it, within a moving group (``_apart``): the part,
  its parent, the frame and the offset, at most ``MAX_APART``. A fact, never a refusal or an extra round.

``at_frames`` is the same read for one object the model names (``angelo_scene_object`` with ``frames``): its
origin and geometry box, and with rendering children the hierarchy's box, at the frames it chose.

Read by ``motion_frames.Sampler`` (Blender's evaluation, everything put back; bounded, else ``not_measured`` with
the reason; ``frame_not_restored`` if the frame did not come back). Fewer than two frames read tell no motion and
say so. Byte cost: about 60 B per group and frame (an origin and a box), plus up to ``MAX_PARTS`` names of at most
64 characters per group (about 100 B typical, 530 B at most). Main thread.
"""

import math
from array import array

from .anim_channels import channelbag
from .motion_scope import MAX_MEMBERS, Scope, parts
from .object_bounds import DIGITS, GEOMETRY_TYPES, rounded, world_bounds

MAX_FRAMES = 5
MAX_ASKED_FRAMES = 8
MAX_GROUPS = 6
MAX_PARTS = 8
MAX_KEYED = 64  # Objects whose keys are listed for the frames.
MAX_APART = 8
MAX_SIGNED_CURVES = 64
MAX_SIGNED_KEYS = 1024  # Key points of one object read whole (``motion_signature``),
SIGNED_SAMPLES = 16  # then this many of each longer curve.
MAX_INSTANCES = 10000  # Instances read per frame (Blender's iteration about 9 us each, the boxes 1 us).
PADDED = frozenset({"CURVE", "SURFACE", "FONT"})  # Evaluated boxes that add each point's radius.
INSTANCING = frozenset({"NODES", "PARTICLE_SYSTEM"})  # Modifiers whose output can hold instances.
MOVES = 0.001  # A group moves when its origin or box changes by more than this share of its size,
APART = 0.05  # a part is apart when it is more than this share of its size from where its parent carries it,
FLOOR = 1e-6  # and each by a micrometre at least (relative: a millimetre model reads like a bridge).
# scene_diff fields whose change can move an object or its geometry in time (a material or a name cannot).
SHAPED = frozenset({"MESH", "CURVE", "SURFACE", "LATTICE"})  # Types whose data has shape keys.
MOVING_FIELDS = frozenset({"matrix", "vertices", "polygons", "modifiers", "geometry", "type", "motion"})


def changed_names(changes):
    """Names of the objects a script added, or modified in a way that can move them (``scene_diff``'s rows)."""
    found = []
    for row in (changes or {}).get("added") or ():
        if isinstance(row, dict) and isinstance(row.get("name"), str):
            found.append(row["name"])
    for row in (changes or {}).get("modified") or ():
        fields = set(row.get("fields") or ()) if isinstance(row, dict) else set()
        if isinstance(row, dict) and isinstance(row.get("name"), str) and (not fields or fields & MOVING_FIELDS):
            found.append(row["name"])
    return list(dict.fromkeys(found))


def motion_signature(obj, data=None, kind=None):
    """A digest of what moves ``obj`` in time beside its matrix (``scene_signature``): keys, drivers and NLA on the
    object, its data and its shape keys, its constraints and its parent, so an edit that only keys it (a shape key
    too) is a change the read-back follows. An object's keys are read whole with ``foreach_get`` up to
    ``MAX_SIGNED_KEYS`` of its own; a curve past that is its count and ``SIGNED_SAMPLES`` keys evenly spaced, ends
    included. The digest of one object never depends on another's keys (a budget shared across the scene would
    make an untouched object read as changed). ``data`` and ``kind``: the object's data and type when the
    caller read them already. 0 for an object nothing moves that way; never raises: an unreadable part reads as -1."""
    try:
        data = obj.data if data is None else data
        if (obj.animation_data is None and obj.parent is None and not len(obj.constraints)
                and (data is None or (getattr(data, "animation_data", None) is None
                                      and ((kind or obj.type) not in SHAPED or data.shape_keys is None)))):
            return 0  # The common still object: five reads, nothing built (it runs before and after every edit).
        constraints, parent = obj.constraints, obj.parent
        shapes = data.shape_keys if data is not None and (kind or obj.type) in SHAPED else None
        owners = [block for block in (obj, data, shapes) if getattr(block, "animation_data", None) is not None]
        if not owners and parent is None and not len(constraints):
            return 0
        budget = [MAX_SIGNED_KEYS]
        parts = [getattr(parent, "name", None), obj.parent_type, obj.parent_bone,
                 tuple((c.type, getattr(getattr(c, "target", None), "name", None), round(float(c.influence), 4))
                       for c in constraints)]
        for owner in owners:
            parts.append(_keys(owner, budget))
        return hash(tuple(parts))
    except (AttributeError, TypeError, ValueError, ReferenceError, RuntimeError):
        return -1


def _keys(owner, budget):
    animation = owner.animation_data
    found = [getattr(animation.action, "name", None), len(animation.drivers), len(animation.nla_tracks)]
    bag = channelbag(owner)
    for curve in (list(bag.fcurves)[:MAX_SIGNED_CURVES] if bag is not None else ()):
        points = curve.keyframe_points
        count = len(points)
        if count <= budget[0]:
            budget[0] -= count
            values = array("f", [0.0]) * (2 * count)
            points.foreach_get("co", values)
            found.append((curve.data_path, curve.array_index, values.tobytes()))
        else:
            step = (count - 1) / (SIGNED_SAMPLES - 1)
            found.append((curve.data_path, curve.array_index, count,
                          tuple(tuple(points[round(index * step)].co) for index in range(SIGNED_SAMPLES))))
    return tuple(found)


def after_edit(window, names=(), objects=(), clock=None, mode=None):
    """``motion`` for the objects an edit added or changed (by ``names`` in the bound scene, or ``objects``), or None
    when none of them animates (``motion_scope``)."""
    scene, scope = window.scene, Scope()
    found = [scene.objects.get(name) for name in names] + list(objects)
    edited = [obj for obj in dict.fromkeys(found) if obj is not None and _alive(obj)]
    moving = [obj for obj in edited if scope.animates(obj)]
    if not moving:
        return None
    tops = list(dict.fromkeys(scope.top(obj) for obj in moving))
    pointers = {top.as_pointer() for top in tops}  # A top inside another's hierarchy is a part of that group.
    tops = [top for top in tops if not any(above.as_pointer() in pointers for above in _chain(top)[1:])]

    def plan():
        groups = parts(window.view_layer, tops)
        members = [m for top in tops for m in groups[top.as_pointer()]["parts"]]
        return groups, frames_for(scene, members + moving)
    return _read(window, tops, plan, clock, mode)


def motion_at_frames(window, handle, frames, mode=None):
    """``at_frames`` for the object ``handle`` names (``angelo_scene_object`` with ``frames``); never raises."""
    from .scene_queries import resolve_object
    try:
        return at_frames(window, resolve_object(window, handle), frames, mode=mode)
    except Exception as error:  # noqa: BLE001  The object's other sections stand without it.
        return {"frames": list(frames), "not_measured": "the read failed (%s)" % type(error).__name__}


def at_frames(window, obj, frames, clock=None, mode=None):
    """``at_frames`` for ``obj`` at the ``frames`` the model chose (``angelo_scene_object`` with ``frames``)."""
    frames = sorted(dict.fromkeys(int(frame) for frame in frames))[:MAX_ASKED_FRAMES]

    def plan():
        if not obj.evaluated_get(window.view_layer.depsgraph).is_evaluated:
            return "%r is not evaluated in this view layer (an excluded collection or disabled object)" % (
                obj.name[:64])
        return parts(window.view_layer, [obj], include_self=True), frames
    read = _sample(window, [obj], plan, clock, mode)
    if isinstance(read, str):
        return {"frames": frames, "not_measured": read}
    samples, done, groups, _frames, restored = read
    own = [s[obj.as_pointer()] for s in samples]
    digits = _digits(own[0]["bounds"])
    result = {"frames": done, "origin": [_round(row["origin"], digits) for row in own],
              "bounds": [row["own"] for row in own]}
    if len(groups[obj.as_pointer()]["parts"]) > 1:
        result["with_children"] = [row["bounds"] for row in own]
    _notes(result, groups[obj.as_pointer()], own)
    if len(done) < len(frames):
        result["not_read"] = [frame for frame in frames if frame not in done]
    if not restored:
        result["frame_not_restored"] = True
    return result


def frames_for(scene, objects):
    """The first, middle and last frame and the objects' own key frames between them, at most ``MAX_FRAMES``."""
    start, end = int(scene.frame_start), int(scene.frame_end)
    if end <= start:
        return [start]
    keys = set()
    for obj in list({obj.as_pointer(): obj for obj in objects}.values())[:MAX_KEYED]:
        for owner in _chain(obj):
            keys.update(_key_frames(owner))
    keys = sorted(frame for frame in keys if start < frame < end and frame != (start + end) // 2)
    base = [start, (start + end) // 2, end]
    room = MAX_FRAMES - len(base)
    if len(keys) > room:
        keys = [keys[round(index * (len(keys) - 1) / max(1, room - 1))] for index in range(room)] if room else []
    return sorted(dict.fromkeys(base + keys))


def _key_frames(owner):
    try:
        bag = channelbag(owner)
    except (AttributeError, TypeError, ReferenceError):
        return ()
    frames = set()
    for curve in (bag.fcurves if bag is not None else ())[:256]:
        frames.update(int(round(point.co[0])) for point in curve.keyframe_points[:512])
    return frames


def _chain(obj):
    found, seen = [], set()
    while obj is not None and obj.as_pointer() not in seen and len(found) < 32:
        seen.add(obj.as_pointer())
        found.append(obj)
        obj = obj.parent
    return found


def _alive(obj):
    try:
        return obj.name is not None
    except ReferenceError:
        return False


def _measure(tops, groups, depsgraph, over=None):
    """``{pointer: {"origin", "own", "bounds", "matrix", "center", "space"}}`` of each top (its hierarchy's box, its
    instances included: ``_instance_boxes``) and part (its own evaluated box, and its parent space: ``_space``).
    ``over``: tops whose instances passed ``MAX_INSTANCES`` at an earlier frame of this read, whose box no frame
    states, so their instances are not walked again (added to as found)."""
    rows, instancers, over = {}, _instancers(tops, groups), set() if over is None else over
    extra, unread = _instance_boxes(depsgraph, {key: top for key, top in instancers.items() if top not in over})
    unread = unread | (over & set(instancers.values()))
    over.update(unread)
    for top in tops:
        boxes = list(extra.get(top.as_pointer(), ()))
        for part in groups[top.as_pointer()]["parts"]:
            own = world_bounds(part, depsgraph=depsgraph) if part.type in GEOMETRY_TYPES else None
            matrix = part.matrix_world.copy()
            center = ([(own[0][axis] + own[1][axis]) / 2 for axis in range(3)] if own is not None
                      else list(matrix.translation))
            rows[part.as_pointer()] = {"origin": list(matrix.translation), "own": own, "matrix": matrix,
                                       "center": center, "space": _space(part) if part.parent is not None else None}
            if own is not None:
                boxes.append(own)
        row = rows.setdefault(top.as_pointer(), {"origin": list(top.matrix_world.translation), "own": None,
                                                 "matrix": top.matrix_world.copy(), "center": None, "space": None})
        row["bounds"] = (rounded([[min(box[0][axis] for box in boxes) for axis in range(3)],
                                  [max(box[1][axis] for box in boxes) for axis in range(3)]]) if boxes else None)
        if top.as_pointer() in unread:  # A box of the first instances read would be stated as the whole one
            row["bounds"] = None  # (otherwise a 14,400-instance grid's edge read 3 m short at every frame).
            row["instances"] = "more than %d: the box is not measured" % MAX_INSTANCES
    return rows


def _instancers(tops, groups):
    """``{part pointer: top pointer}`` of the parts whose output can hold instances."""
    return {part.as_pointer(): top.as_pointer() for top in tops for part in groups[top.as_pointer()]["parts"]
            if part.is_instancer or any(modifier.type in INSTANCING for modifier in part.modifiers)}


def _space(part):
    """The world matrix of what ``part`` is parented to (its parent object, bone, vertices or path) or None. Without
    an active constraint Blender's own composition gives it exactly (world = space @ parent inverse @ basis); with
    one, the parent object's matrix, or its bone's (a vertex or path parent then has none)."""
    try:
        if not any(constraint.enabled and constraint.influence for constraint in part.constraints):
            return part.matrix_world @ (part.matrix_parent_inverse @ part.matrix_basis).inverted()
        parent = part.parent
        if part.parent_type == "BONE":
            bone = parent.pose.bones.get(part.parent_bone) if parent.pose is not None else None
            return parent.matrix_world @ bone.matrix if bone is not None else None
        if part.parent_type in ("OBJECT", "ARMATURE", "LATTICE") and not (
                parent.type == "CURVE" and getattr(parent.data, "use_path", False)):
            return parent.matrix_world.copy()
    except (ValueError, AttributeError, ReferenceError):  # A singular local matrix: no parent space to read.
        return None
    return None


def _instance_boxes(depsgraph, instancers):
    """``({top pointer: [world box of its instances]}, {top pointers whose instances were not all read})``: what the
    instancers among the parts make (Geometry Nodes, collection, particle, vertex and face instances), as Blender's
    own instance list gives them (``depsgraph.object_instances``), at most ``MAX_INSTANCES``. An object's own box
    leaves its instances out. Each instance's box is its object's box through its matrix, the exact axis-aligned box
    of the eight corners (centre through the matrix, half extents through its absolute rotation part), for all of a
    top's instances at once (corner by corner in Python took 60 us an instance, 10,000 more than
    the whole budget; Blender's own iteration costs about 9 us of it)."""
    if not instancers:
        return {}, set()
    import numpy
    matrices, count = {}, 0
    for instance in depsgraph.object_instances:
        if not instance.is_instance or instance.parent is None:
            continue
        key = instancers.get(instance.parent.original.as_pointer())
        if key is None:
            continue
        box = _local_box(instance.object)  # Per instance: the iterator reuses one object for other geometry.
        if box is None:
            continue
        count += 1
        if count > MAX_INSTANCES:
            return {}, set(instancers.values())
        rows = matrices.setdefault(key, ([], []))
        rows[0].append(instance.matrix_world.copy())  # The iterator's own memory, the next instance's next.
        rows[1].append(box)
    found = {}
    for key, (rows, boxes) in matrices.items():
        array, local_boxes = numpy.array(rows, dtype=numpy.float64), numpy.array(boxes, dtype=numpy.float64)
        rotation = array[:, :3, :3]
        centre = numpy.einsum("nij,nj->ni", rotation, local_boxes[:, 0]) + array[:, :3, 3]
        half = numpy.einsum("nij,nj->ni", numpy.abs(rotation), local_boxes[:, 1])
        found[key] = [[(centre - half).min(0).tolist(), (centre + half).max(0).tolist()]]
    return found, set()


def _local_box(obj):
    """``(centre, half extents)`` of ``obj``'s own box in its space, or None for an object without geometry. A curve
    object's is its original's, as ``object_bounds.world_bounds`` reads it: the evaluated one adds each point's
    radius (otherwise a curve in a collection instance read 1 m wider on every side)."""
    if obj.type not in GEOMETRY_TYPES:
        return None
    original = obj.original if obj.type in PADDED and obj.original is not None else obj
    box = original.bound_box  # Blender's box from its minimum and maximum: corner 0 and corner 6.
    first, last = tuple(box[0]), tuple(box[6])
    low, high = [min(pair) for pair in zip(first, last)], [max(pair) for pair in zip(first, last)]
    return [(a + b) / 2 for a, b in zip(low, high)], [(b - a) / 2 for a, b in zip(low, high)]


def _sample(window, tops, plan, clock, mode):
    """``([measure per frame read], frames read, groups, frames, restored)`` or the reason nothing was read.
    ``plan()`` gives the groups and frames (or a reason) once ``Sampler.open`` evaluated the edit's own pending
    updates, so every part is evaluated."""
    from .motion_frames import Sampler
    samples, done, over = [], [], set()
    with Sampler(window, **({"clock": clock} if clock is not None else {}), mode=mode) as sampler:
        reason = sampler.open()
        if reason:
            return reason
        planned = plan()
        if isinstance(planned, str):
            return planned
        groups, frames = planned
        if _instancers(tops, groups):
            sampler.uncounted(lambda: __import__("numpy"))  # A session's first import is no cost of the scene.
        for frame in _first_ends(frames):  # Each frame's cost is its evaluation and this read.
            if not sampler.at(frame, lambda: _measure(tops, groups, window.view_layer.depsgraph, over)):
                break
            samples.append(sampler.value)
            done.append(frame)
    if not done:
        return "a frame's evaluation would take longer than a read may"
    order = sorted(range(len(done)), key=done.__getitem__)
    return [samples[i] for i in order], [done[i] for i in order], groups, frames, sampler.restored


def _first_ends(frames):
    """``frames`` in the order a budget should read them: the first and last, then outwards from the middle, so a
    read cut short still spans the range."""
    frames = sorted(frames)
    if len(frames) <= 2:
        return frames
    middle = (frames[0] + frames[-1]) / 2
    return [frames[0], frames[-1]] + sorted(frames[1:-1], key=lambda frame: abs(frame - middle))


def _read(window, tops, plan, clock, mode):
    read = _sample(window, tops, plan, clock, mode)
    if isinstance(read, str):
        return {"not_measured": read}
    samples, done, groups, frames, restored = read
    if len(done) < 2 <= len(frames):  # One frame tells no motion: say so, not nothing.
        return {"not_measured": "only frame %d could be evaluated in the time a read may take" % done[0],
                "frames": done}
    moving, apart = [], []
    for top in tops:
        key = top.as_pointer()
        rows = [sample[key] for sample in samples]
        size = _diagonal(rows[0]["bounds"])
        if not _moves(rows, size) and not any(row.get("instances") for row in rows):  # Unmeasured may move.
            continue
        members, digits = groups[key]["parts"], _digits(rows[0]["bounds"])
        group = {"handle": top, "name": top.name[:64],
                 "parts": [m.name[:64] for m in members] if len(members) <= MAX_PARTS else len(members),
                 "origin": [_round(row["origin"], digits) for row in rows], "bounds": [row["bounds"] for row in rows]}
        _notes(group, groups[key], rows)
        moving.append(group)
        apart.extend(_apart(members, samples, done, size))
    if not moving:
        return None if restored else {"frames": done, "frame_not_restored": True}
    for group in moving[:MAX_GROUPS]:  # Handles only for the groups shown (500 cost 24 ms).
        group["handle"] = _handle(group["handle"])
    result = {"frames": done, "moving": moving[:MAX_GROUPS]}
    if len(moving) > MAX_GROUPS:
        result["moving_more"] = len(moving) - MAX_GROUPS
    if apart:
        result["apart"] = apart[:MAX_APART]
    if len(done) < len(frames):
        result["not_read"] = [frame for frame in frames if frame not in done]
    if not restored:
        result["frame_not_restored"] = True
    return result


def _notes(entry, group, rows):
    """What the read left out of a group: instances past ``MAX_INSTANCES``, rendering parts the view layer does
    not evaluate, members past ``motion_scope.MAX_MEMBERS``."""
    note = next((row["instances"] for row in rows if row.get("instances")), None)
    if note:
        entry["instances"] = note
    unread = group["unread"]
    if unread:
        entry["parts_not_evaluated"] = ([m.name[:64] for m in unread] if len(unread) <= MAX_PARTS else len(unread))
    if group["more"]:
        entry["parts_more"] = "more than %d objects; the first were read" % MAX_MEMBERS


def _moves(rows, size):
    """Whether the origin or box changes by more than ``MOVES`` of the group's size (``FLOOR`` at least)."""
    tolerance = max(FLOOR, MOVES * size)
    first = rows[0]
    for row in rows[1:]:
        if any(abs(a - b) > tolerance for a, b in zip(row["origin"], first["origin"])):
            return True
        if (row["bounds"] is None) != (first["bounds"] is None):
            return True
        if row["bounds"] is not None and any(abs(a - b) > tolerance
                                             for corner, start in zip(row["bounds"], first["bounds"])
                                             for a, b in zip(corner, start)):
            return True
    return False


def _apart(members, samples, frames, size):
    """Parts whose own motion (keys, drivers, a constraint) takes their first-frame geometry centre more than
    ``APART`` of their size away from where their parent space alone carries it (``_space``: the bone, vertices or
    path they are parented to, too). The centre moves rigidly with each matrix, so a rig's deformation of its
    skin is no offset, and a wheel spinning on its own hub barely is (the parent object's matrix
    alone would list every bone-parented and deformed part)."""
    found = []
    for part in members:
        rows = [sample.get(part.as_pointer()) for sample in samples]
        first = rows[0]
        if part.parent is None or part.parent.as_pointer() not in samples[0] or first is None or first["space"] is None:
            continue  # A top's still parent is outside its group: its own keys there are no news.
        try:
            own, carried = first["matrix"].inverted() @ _vector(first["center"]), \
                first["space"].inverted() @ _vector(first["center"])
        except ValueError:
            continue  # A zero-scale matrix: nothing to predict.
        tolerance = max(FLOOR, APART * (_diagonal(first["own"]) or size))
        worst = None
        for row, frame in zip(rows[1:], frames[1:]):
            if row is None or row["space"] is None:
                break
            offset = row["matrix"] @ own - row["space"] @ carried
            if offset.length > tolerance and (worst is None or offset.length > worst[0]):
                worst = (offset.length, frame, list(offset))
        if worst is not None:
            digits = _digits(first["own"])
            found.append({"part": part.name[:64], "parent": part.parent.name[:64], "frame": worst[1],
                          "offset": _round(worst[2], digits)})
    return found


def _diagonal(box):
    return sum((high - low) ** 2 for low, high in zip(*box)) ** .5 if box is not None else 0.0


def _digits(box):
    """``object_bounds.rounded``'s decimals for ``box``: 1 mm, finer for a small one."""
    extent = max((high - low for low, high in zip(*box)), default=0.0) if box is not None else 0.0
    return max(DIGITS, 2 - math.floor(math.log10(extent))) if extent > 0.0 else DIGITS


def _round(values, digits):
    return [round(float(value), digits) + 0.0 for value in values]


def _vector(values):
    from mathutils import Vector
    return Vector(values)


def _handle(obj):
    from .scene_queries import object_handle
    return object_handle(obj)
