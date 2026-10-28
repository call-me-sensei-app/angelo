# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What each placement of ``worker_merge`` did, checked without evaluating anything the check does not read
(break-it E2E-7 M-1: revealing the worker's disabled objects evaluated their geometry twice on the lead's thread).

An object whose world matrix is its parent chain of stored transforms (``simple``: no constraint of any state,
nothing played on it, no rigid body, parented plainly to an object that is not a curve, and so up its chain) is checked
on that chain: as built from the stored data, and placed from what the placement stored, so a skew Blender's transform
cannot hold shows, hidden or not, with no evaluation at all. Any other object is checked by its effect: Blender
evaluates the scene as built and once placed (after the workers' view state is applied), only when the merge holds such
an object, and its evaluated world is compared; one Blender does not evaluate (disabled in viewports, excluded) is
listed once as not measured, never as a warning (break-it E2E-8 M-2). Keys on a root's transform, drivers on any
object's transform, and keys or drivers on any object's constraints are reported apart, as moving later: Blender applies
them at the next frame change, after the placement, and a driver may read where the object now is. They are measured
too. At the merge frame keys act on nothing, so a key never explains a miss: a copy is counted there as off now only
when its own drivers set its transform and nothing else acts on it (no constraint, no rigid body, a placement its basis
can hold): those channels follow its drivers, not the placement (break-it E2E-11 M-2: a wheel whose spin is driven by
its place). Any other miss is warned with its cause, like any object's (E2E-12 H-1, E2E-13 H-1, M-1). Each warned
object is listed once per part, with up to three of its copies and how many in all, so no number of copies crowds
another object out (E2E-11 M-2). A placed root is written in the form nearest its channels as built: its scale's
signs and its rotation's angles, sign or axis (``keep_signs``). A root its active action alone keys is also checked
for where those keys put it at the next frame change: keys on only some channels put back their values beside the
placed ones, and a copy that lands in neither its placed pose nor its keys' own pose is warned (E2E-15 H-1).
"""

import math


def constrained(obj):
    return any(not c.mute and c.influence > 0 and getattr(c, "is_valid", True) for c in obj.constraints)


TRANSFORM_PATHS = frozenset({"location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale",
                             "delta_location", "delta_rotation_euler", "delta_rotation_quaternion", "delta_scale",
                             "rotation_mode", "matrix_parent_inverse", "parent_type", "track_axis", "up_axis"})


def _keyed_paths(action, slot):
    """The data paths ``action``'s unmuted channels for ``slot`` key."""
    if action is None or slot is None:
        return set()
    return {curve.data_path for layer in action.layers for strip in layer.strips
            for bag in (strip.channelbag(slot),) if bag is not None for curve in bag.fcurves if not curve.mute}


def _played(obj):
    """Every data path Blender plays on ``obj``: unmuted drivers, the active action, and the NLA as Blender evaluates it
    (only when on, only its solo track when one is soloed, never a muted track or strip; break-it E2E-7 L-3)."""
    data = obj.animation_data
    if data is None:
        return set()
    paths = {driver.data_path for driver in data.drivers if not driver.mute} | _keyed_paths(data.action, data.action_slot)
    if getattr(data, "use_nla", True):
        tracks = [track for track in data.nla_tracks if not track.mute]
        solo = [track for track in tracks if getattr(track, "is_solo", False)]
        for track in solo or tracks:
            for strip in _strips(track.strips):
                paths |= _keyed_paths(getattr(strip, "action", None), getattr(strip, "action_slot", None))
    return paths


def _strips(strips):
    for strip in strips:
        if strip.mute:
            continue   # Silent, a meta strip's children with it.
        yield strip
        yield from _strips(getattr(strip, "strips", ()))


def keyed_transform(obj):
    """Whether keys or drivers set ``obj``'s own transform as Blender plays them: Blender applies them at the next frame
    change, after the placement (a frame re-evaluation here would also reset the person's own unkeyed edits)."""
    return bool(_played(obj) & TRANSFORM_PATHS)


def driven_transform(obj):
    """Whether drivers set ``obj``'s own transform, child or root: a driver may read where it is, so a placement can
    change what it sets (break-it E2E-11 M-2: a wheel spun by a driver of its X)."""
    data = obj.animation_data
    return data is not None and bool({driver.data_path for driver in data.drivers if not driver.mute} & TRANSFORM_PATHS)


def bone_moved(obj):
    """Whether keys or drivers on its rig move the bone ``obj`` is parented to, or a bone above it (break-it E2E-15 L-3:
    a child of a driven bone was told its parent did not move with the placement)."""
    rig = obj.parent
    if obj.parent_type != "BONE" or rig is None or rig.pose is None or obj.parent_bone not in rig.pose.bones:
        return False
    from bpy.utils import escape_identifier
    bone, prefixes = rig.pose.bones[obj.parent_bone], []
    while bone is not None:
        prefixes.append('pose.bones["%s"]' % escape_identifier(bone.name))
        bone = bone.parent
    return any(path.startswith(tuple(prefixes)) for path in _played(rig))


def keyed_constraints(obj):
    """Whether keys or drivers set any of ``obj``'s constraints (an influence, a mute, a setting): what they do then
    changes from frame to frame, whatever the stored values say (break-it E2E-8 M-3)."""
    return any(path.startswith("constraints[") for path in _played(obj))


# Object paths animation may set that never move the object: its look, visibility, display, modifiers, materials and
# custom properties (a driver that reads one is on a path that moves it, and counts there). Any other path counts.
STILL_PATHS = ("color", "hide_", "show_", "display", "empty_display", "modifiers[", "active_material", "material_slots",
               "pass_index", "visible_", "lineart", "lightgroup", "is_holdout", "is_shadow_catcher", '["')


def _moved_by_more(obj):
    """Something besides its stored transform and its parent's world may set ``obj``'s world matrix: any constraint,
    whatever its stored mute or influence (animation can change both), animation on a path that can move it (break-it
    E2E-9 L-3: a keyed colour never did), a rigid body, a parent type other than an object, or a curve parent."""
    return (len(obj.constraints) > 0 or any(not path.startswith(STILL_PATHS) for path in _played(obj))
            or obj.parent_type != "OBJECT" or obj.rigid_body is not None
            or (obj.parent is not None and obj.parent.type == "CURVE"))


def needs_evaluation(objects):
    """The objects whose world only Blender's evaluation gives: moved by more than their stored transform, or under
    one that is."""
    inside, found = set(objects), {}

    def evaluated_only(obj):
        if obj not in found:
            found[obj] = False   # A parent loop never recurses forever.
            parent = obj.parent
            found[obj] = _moved_by_more(obj) or (parent in inside and evaluated_only(parent))
        return found[obj]
    return {obj for obj in objects if evaluated_only(obj)}


def _far(matrix):
    return max(abs(matrix[i][3]) for i in range(3))


def chain(obj, cache):
    """``(world, reach)``: ``obj``'s world matrix from its stored transforms up its parent chain, and the largest
    translation it is composed from, which float32 rounding scales with (break-it E2E-7 L-1)."""
    if obj not in cache:
        if obj.parent is None:
            world = obj.matrix_basis.copy()
            cache[obj] = world, _far(world)
        else:
            parent, reach = chain(obj.parent, cache)
            world = parent @ obj.matrix_parent_inverse @ obj.matrix_basis
            cache[obj] = world, max(reach, _far(obj.matrix_parent_inverse), _far(world))
    return cache[obj]


def evaluated(view_layer):
    """Blender evaluates ``view_layer`` now; its depsgraph (an object is evaluated when it has a copy there)."""
    view_layer.update()
    return view_layer.depsgraph


def _evaluated_world(obj, depsgraph):
    copy = obj.evaluated_get(depsgraph) if depsgraph is not None else obj
    return copy.matrix_world.copy() if copy.as_pointer() != obj.as_pointer() else None


AIM_ONLY = frozenset({"TRACK_TO", "DAMPED_TRACK", "LOCKED_TRACK", "COPY_ROTATION"})   # Read a direction, not a place.


def _targets(constraint):
    """The objects ``constraint`` reads (its target, or each of an Armature constraint's targets)."""
    yield getattr(constraint, "target", None)
    yield from (item.target for item in getattr(constraint, "targets", ()))


def _read_by(obj):
    """The objects whose place ``obj``'s constraints read; an aim-only constraint's target adds no rounding to where
    ``obj`` is (break-it E2E-10 L-2)."""
    for constraint in obj.constraints:
        if constraint.type not in AIM_ONLY:
            yield from _targets(constraint)


CHILD_OF_ALL = tuple("use_%s_%s" % (kind, axis) for kind in ("location", "rotation", "scale") for axis in "xyz")


def carried(obj, inside):
    """Whether a placement reaches ``obj`` through an object of its part, as it reaches a child through its parent, so
    it must not move ``obj`` as well (break-it E2E-10: a Child Of follower moved as a root was placed twice). Exact by
    construction: its one acting Child Of of the part takes every channel at full influence in world space, so it
    applies its target's placed world to ``obj``'s own matrix, and nothing acting before it reads the part, so what
    acts before it still acts as built. Any other follower is moved as a root, and measured like one."""
    acting = [c for c in obj.constraints if not c.mute and c.influence > 0 and getattr(c, "is_valid", True)]
    following = [index for index, c in enumerate(acting) if c.type == "CHILD_OF" and c.target in inside]
    if len(following) != 1:
        return False
    child_of = acting[following[0]]
    return (child_of.influence >= 1.0 and all(getattr(child_of, name) for name in CHILD_OF_ALL)
            and not getattr(child_of, "set_inverse_pending", False)   # Blender sets it against the placed target.
            and child_of.owner_space == "WORLD" and child_of.target_space == "WORLD"
            and not any(source in inside for before in acting[:following[0]] for source in _targets(before)))


def built(objects, slow, depsgraph):
    """``{object: (world, reach, basis)}`` of a part as built: the stored chain and basis for every object, the
    evaluated world and basis for an object only evaluation gives (``slow``) where Blender evaluated it, and none where
    it did not."""
    cache, worlds = {}, {}
    for obj in objects:
        if obj in slow:
            world = _evaluated_world(obj, depsgraph)
            if world is not None:
                worlds[obj] = (world, max(_far(world), chain(obj, cache)[1]),
                               obj.evaluated_get(depsgraph).matrix_basis.copy())
        else:
            worlds[obj] = (*chain(obj, cache), obj.matrix_basis.copy())
    return worlds


ROUNDING = 64 * 6e-8   # float32 rounding of a matrix composed from translations as large as ``reach``.
MOVE = 1e-5            # A move, in scene units: a hundredth of a millimetre in a metre scene.
TURN = 1e-3            # Of an axis's own length (Blender's own Euler write can lose more near +-90 deg on a thin axis).


def _wrong(world, expected, reach):
    """Each axis of the 3x3 part against its own length, so a thin axis's turn shows beside a long one (break-it
    E2E-9 L-2); the translation against ``MOVE`` plus the rounding a composition from translations as large as
    ``reach`` carries, never against the distance itself, so a far placement hides no move (E2E-8 L-3, E2E-9 L-1)."""
    for j in range(3):
        length = sum(expected[i][j] ** 2 for i in range(3)) ** 0.5
        if max(abs(world[i][j] - expected[i][j]) for i in range(3)) > TURN * length + 1e-9:
            return True
    return max(abs(world[i][3] - expected[i][3]) for i in range(3)) > MOVE + ROUNDING * reach


NOT_MEASURED = ("Blender does not evaluate these while they are disabled or excluded, so where they land was not "
                "checked; what acts after a placement (constraints, keys, drivers, a rigid body or a special parent) may "
                "move them")
MOVES_LATER = ("Their keys or drivers move these after the placement, at the next frame change or at other frames, whatever "
               "their place now; off_now counts the copies their own drivers already set away from the placement, with "
               "nothing else acting on them: those channels follow the drivers, not the placement")


def _holdable(matrix):
    """Whether a basis can hold ``matrix``'s 3x3: no zero-length axis, and its axes at right angles (to ``TURN``)."""
    axes = [[matrix[i][j] for i in range(3)] for j in range(3)]
    lengths = [sum(value * value for value in axis) ** 0.5 for axis in axes]
    return min(lengths) > 1e-12 and all(abs(sum(a * b for a, b in zip(axes[i], axes[j])))
                                        <= TURN * lengths[i] * lengths[j] for i, j in ((0, 1), (0, 2), (1, 2)))


def _drivers_explain(obj, copy, expected, worlds, depsgraph, inside, reach, transform=None):
    """Whether a measured miss of ``obj`` is its own drivers' doing, decided by the effect. At the merge frame keys act
    on nothing (Blender plays them at the next frame change), so a key never explains a miss (break-it E2E-13 H-1,
    M-1). The drivers do only when all of the copy's world left of its own basis, which they set, is where the
    placement puts it (E2E-14 M-1, L-1: a bone parent's Limit, an inert constraint). For a root that is its link as
    built carried by the placement (``transform``; E2E-15 L-2: a constraint acting alike as built and as placed), and
    its basis must be able to hold the placement; for a child it is its link to its placed parent, as built."""
    if not driven_transform(obj) or obj.rigid_body is not None or obj not in worlds or depsgraph is None:
        return False
    evaluated = copy.evaluated_get(depsgraph)
    if evaluated.as_pointer() == copy.as_pointer():
        return False
    from mathutils import Matrix
    try:
        link = evaluated.matrix_world @ evaluated.matrix_basis.inverted()
        place = transform if transform is not None else Matrix.Identity(4)
        want = (expected @ worlds[obj][2].inverted() if obj.parent in inside
                else place @ worlds[obj][0] @ worlds[obj][2].inverted() @ place.inverted())
    except ValueError:   # A basis with a zero-scale axis: not decided by its drivers.
        return False
    return (obj.parent in inside or _holdable(expected)) and not _wrong(link, want, reach)


def rotation(obj):
    """``obj``'s own rotation channels as a 3x3 matrix, in its rotation mode."""
    from mathutils import Matrix
    if obj.rotation_mode == "QUATERNION":
        return obj.rotation_quaternion.to_matrix()
    if obj.rotation_mode == "AXIS_ANGLE":
        angle, *axis = obj.rotation_axis_angle
        return Matrix.Rotation(angle, 3, axis)
    return obj.rotation_euler.to_matrix()


def _angle(first, second):
    trace = sum((first.transposed() @ second)[i][i] for i in range(3))
    return math.acos(max(-1.0, min(1.0, (trace - 1.0) / 2.0)))


CHANNELS = ("location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale", "delta_location",
            "delta_rotation_euler", "delta_rotation_quaternion", "delta_scale")


def as_built(obj):
    """What a placement keeps of a root as built (``keep_signs``, ``keys_mix``): its stored basis, its rotation as a
    matrix, and every stored transform channel."""
    return {"basis": obj.matrix_basis.copy(), "rotation": rotation(obj),
            "channels": {path: tuple(getattr(obj, path)) for path in CHANNELS}}


def _turns_nothing(transform):
    """Whether ``transform`` only moves and scales by one positive factor: its 3x3 exactly a multiple of the identity,
    as a placement with no rotation and a uniform scale builds it."""
    m = transform.to_3x3()
    return (m[0][0] > 0 and m[0][0] == m[1][1] == m[2][2]
            and all(m[i][j] == 0 for i in range(3) for j in range(3) if i != j))


def _keep(obj, as_built, transform=None):
    """The placed basis written as near its channels as built. Blender writes a placed basis in its own form: a negative
    determinant with every scale axis negative and the rotation turned by pi, and its own Euler angles, quaternion sign
    or axis-angle; keys on only some channels then put back their values beside the rewritten ones, and the copy flips
    or turns at the next frame change (break-it E2E-13 H-2, E2E-14 H-1; E2E-15 H-1: a part built upside down, X = pi,
    keyed on Z, rewritten even by a plain move). The same matrix (scale and rotation both times D, a diagonal of +-1
    with determinant 1) with the scale signs nearest the built ones, then the rotation nearest the built one, always
    written nearest its built channels: an Euler compatible with the built one, a quaternion of the built sign and
    length, an axis-angle about the built axis's side with the angle nearest the built one. A ``transform`` that turns
    nothing keeps every rotation channel exactly as built and scales the built scale: the same matrix, exact by
    construction, so no rounding of Blender's write reaches a channel a key leaves alone. ``as_built``:
    ``as_built(obj)``."""
    from itertools import product
    from mathutils import Euler, Matrix, Quaternion, Vector
    channels = as_built["channels"]
    if transform is not None and _turns_nothing(transform):
        for path in ("rotation_euler", "rotation_quaternion", "rotation_axis_angle"):
            setattr(obj, path, channels[path])
        obj.scale = [value * transform[0][0] for value in channels["scale"]]
        return
    current = [-1.0 if value < 0 else 1.0 for value in obj.scale]
    built_signs = [-1.0 if value < 0 else 1.0 for value in channels["scale"]]
    own = rotation(obj)
    choices = [flips for flips in product((1.0, -1.0), repeat=3) if flips[0] * flips[1] * flips[2] == 1.0]
    flips = min(choices, key=lambda flips: (sum(c * f != b for c, f, b in zip(current, flips, built_signs)),
                                            _angle(as_built["rotation"], own @ Matrix.Diagonal(flips))))
    turn = own @ Matrix.Diagonal(flips)
    if flips != (1.0, 1.0, 1.0):
        obj.scale = [value * flip for value, flip in zip(obj.scale, flips)]
    if obj.rotation_mode == "QUATERNION":
        quaternion, before = turn.to_quaternion(), Quaternion(channels["rotation_quaternion"])
        if quaternion.dot(before) < 0:
            quaternion.negate()
        obj.rotation_quaternion = quaternion * (before.magnitude if before.magnitude > 1e-9 else 1.0)
    elif obj.rotation_mode == "AXIS_ANGLE":
        axis, angle = turn.to_quaternion().to_axis_angle()
        angle_before, *axis_before = channels["rotation_axis_angle"]
        if axis.dot(Vector(axis_before)) < 0:
            axis, angle = -axis, -angle
        angle += 2 * math.pi * round((angle_before - angle) / (2 * math.pi))
        obj.rotation_axis_angle = (angle, *axis)
    else:
        obj.rotation_euler = turn.to_euler(obj.rotation_mode, Euler(channels["rotation_euler"], obj.rotation_mode))


def keep_signs(obj, as_built, transform=None):
    """``_keep``, and the basis it wrote kept in ``as_built["written"]`` by copy: an evaluation writes drivers' values
    back into the stored channels, so a check reads what the placement wrote from there (break-it E2E-15 L-1)."""
    _keep(obj, as_built, transform)
    as_built.setdefault("written", {})[obj] = obj.matrix_basis.copy()


def why(obj):
    """Why a measured copy of ``obj`` did not end where its placement puts it, in the lead's words. Keys and drivers
    are never the reason given: an object they move is reported as moving later (break-it E2E-11 M-2)."""
    if obj.rigid_body is not None:
        return "a rigid body: the simulation sets its transform"
    if bone_moved(obj):
        return "its rig's keys or drivers move the bone it is parented to, after the placement"
    if constrained(obj):
        return "constrained: its constraints act after the placement and move it away from where it was placed"
    if obj.parent is not None:
        return "what it is parented to or follows did not move with the placement exactly"
    if any(abs(value) < 1e-12 for value in obj.scale):
        return "a zero scale on an axis: Blender cannot keep its rotation through a placement"
    return "skewed: a non-uniform placement scale across its rotation cannot be held, so it is not exact"


COPIES = 3   # Copy names listed per object in a once-per-part list; the count says how many in all.


def _once(table, part, obj, copy, placement=None):
    entry = table.setdefault(part, {}).setdefault(obj.name, {"object": obj.name, "copies": [], "count": 0})
    entry["count"] += 1
    if len(entry["copies"]) < COPIES:
        entry["copies"].append(copy.name)
        if placement is not None:
            entry.setdefault("placements", []).append(placement)
    return entry


def _expected(obj, copy, transform, worlds, instance, inside, slow, depsgraph, cache):
    """Where the placement puts an evaluated copy: under its parent's placed world when its parent is in the part, so a
    parent's own error is the parent's to report, not every child's (break-it E2E-10 L-3); else the placement applied
    to its world as built."""
    parent = obj.parent
    if parent in inside and parent in worlds:
        placed = _evaluated_world(instance[parent], depsgraph) if parent in slow else chain(instance[parent], cache)[0]
        if placed is not None:
            try:
                return placed @ worlds[parent][0].inverted() @ worlds[obj][0], worlds[parent][1]
            except ValueError:   # A parent with a zero-scale axis: compared in world space.
                pass
    return transform @ worlds[obj][0], 0.0


def _reason(entry, placement, reason):
    """``reason`` for the copy of ``entry``'s object at ``placement`` (``misplaced`` chooses the object's reason)."""
    entry.setdefault("reasons", {}).setdefault(placement, reason)


def misplaced(checks, depsgraph):
    """``(warned, unmeasured, later)``, each {part: {object name: the object once, with up to ``COPIES`` copies and
    how many in all}} (break-it E2E-8 M-2, E2E-9 M-1, E2E-11 M-2). ``warned``: placed copies not where the placement
    applied to their world as built puts them (by the stored chain when that is the world, by the evaluated world when
    only evaluation gives it), with the placements and why. ``unmeasured``: copies Blender evaluates in neither pass.
    ``later``: copies keys or drivers move after the placement; measured too, ``off_now`` counting those off now by
    their own drivers alone (any other miss is warned, E2E-12 H-1, E2E-13 H-1)."""
    from .worker_keys import NEITHER, budget, keys_mix
    warned, unmeasured, later, spend = {}, {}, {}, budget()
    frame = depsgraph.scene.frame_current_final if depsgraph is not None else None
    for objects, worlds, placements, roots, slow, *kept in checks:
        built = kept[0] if kept else {}   # {root: as_built(root)}.
        moving = ({root for root in roots if keyed_transform(root)}
                  | {obj for obj in objects if driven_transform(obj) or keyed_constraints(obj) or bone_moved(obj)})
        inside = set(objects)
        for index, transform, instance in placements:
            cache = {}
            for obj in objects:
                copy = instance[obj]
                if obj in moving:   # Listed, and measured too: it may be off now as well (break-it E2E-10 M-1).
                    _once(later, id(objects), obj, copy)
                if obj not in slow:
                    if obj.parent in inside:
                        # Its own transform and parent inverse are untouched by a placement, so it stays exactly where
                        # its parent puts it: only a root can be off, and is reported (Blender's float32 Euler write
                        # turning a root by 3e-4 rad is never a child's move, break-it E2E-9 m10).
                        continue
                    world, reach = chain(copy, cache)
                    expected, reach = transform @ worlds[obj][0], max(reach, worlds[obj][1], _far(transform))
                    # Its world is Blender's own write of the placed basis: off only where no basis can hold the
                    # placement (a skew, a zero scale), never by that write's rounding (break-it E2E-14 L-2).
                    wrong = not _holdable(expected) and _wrong(world, expected, reach)
                else:
                    world = _evaluated_world(copy, depsgraph)
                    if world is None or obj not in worlds:
                        _once(unmeasured, id(objects), obj, copy)
                        continue
                    # Its own chain as built and placed, and what its constraints read: a far target's rounding
                    # reaches it (break-it E2E-8 L-2), an unrelated far object in the part never does (E2E-9 L-1).
                    expected, parent_reach = _expected(obj, copy, transform, worlds, instance, inside, slow, depsgraph,
                                                       cache)
                    written = built[obj].get("written", {}).get(copy) if obj in roots and obj in built else None
                    exact = transform @ built[obj]["basis"] if written is not None else None
                    if exact is not None and _holdable(exact):   # Against Blender's own write of a placed basis it
                        try:    # can hold, as a simple root is: never its float32 rounding (break-it E2E-15 L-1); one
                            expected = expected @ exact.inverted() @ written   # it cannot hold (a skew) stays a miss.
                        except ValueError:   # A zero-scale axis: compared as placed.
                            pass
                    reach = max([worlds[obj][1], _far(world), _far(transform), parent_reach]
                                + [worlds[source][1] for source in _read_by(obj) if source in worlds])
                    wrong = _wrong(world, expected, reach)
                    unchecked = spend["unchecked"]
                    if not wrong and obj in moving and obj in roots and obj in built \
                            and keys_mix(copy, built[obj], frame, transform, spend):
                        _reason(_once(warned, id(objects), obj, copy, index + 1), index + 1, NEITHER)
                        continue
                    if spend["unchecked"] > unchecked:   # Past the time budget: said, never judged by guess (E2E-18 M-2).
                        entry = later[id(objects)][obj.name]
                        entry["keys_unchecked"] = entry.get("keys_unchecked", 0) + 1
                if wrong and obj in moving and _drivers_explain(obj, copy, expected, worlds, depsgraph, inside, reach,
                                                                transform):
                    entry = later[id(objects)][obj.name]
                    entry["off_now"] = entry.get("off_now", 0) + 1
                elif wrong:
                    _reason(_once(warned, id(objects), obj, copy, index + 1), index + 1, why(obj))
    for found in warned.values():
        for entry in found.values():
            reasons = entry.pop("reasons")
            # One reason for every placement, or each placement's own (break-it E2E-17 M-2: a keyed root's skew was
            # reported as a later keys mix; E2E-18 L-1: one reason beside placements it was not about).
            if len(set(reasons.values())) > 1:
                entry["why_by_placement"] = {str(number): r for number, r in sorted(reasons.items())}
            else:
                entry["why"] = next(iter(reasons.values()))
    return warned, unmeasured, later
